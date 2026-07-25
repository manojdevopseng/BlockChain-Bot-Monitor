"""Scanner logging bridge.

`get_logger(name)` returns a standard logger that (a) prints to the console and
(b) forwards every record into the Mongo `logs` collection + the WebSocket hub,
so the dashboard Logs page and live feed show real scanner activity.

The scanners call this exactly like the reference `utils/logger.get_logger`, so
their call sites (`log.info(...)`) are unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from ..config import settings

_SERVICE_MAP = {
    "sol_scanner": "Sol Scanner",
    "eth_scanner": "Eth Scanner",
    "eth_trending_scanner": "Eth Scanner",
    "robinhood_scanner": "Robinhood Scanner",
    "cross_chain_common": "Cross-Chain",
    "gmgn_client": "GMGN",
    "client": "GMGN",
    "ws_provider": "WS Provider",
    "onchain_detector": "Detector",
    "forwarder": "Forwarder",
    "rate_limiter": "GMGN",
    "gas_tracker": "Gas Tracker",
}


def _service_name(logger_name: str) -> str:
    tail = logger_name.rsplit(".", 1)[-1]
    return _SERVICE_MAP.get(tail, tail.replace("_", " ").title())


class _RepeatFilter(logging.Filter):
    """Collapse identical WARNING/ERROR lines that repeat in a tight loop.

    A scanner that fails every 5s (e.g. a Cloudflare 403 loop) would otherwise
    write ~17k identical error documents a day. The first occurrence is logged
    normally; repeats inside LOG_DEDUP_SECONDS are counted and reported on the
    next write, so nothing is hidden — you see "(+119 repeats in 5m)".

    Matching is on the exact message, so two genuinely different errors are
    never collapsed into one. INFO/DEBUG are never throttled — real detections
    ("New V2 pair — token=0x…") must always come through.
    """

    def __init__(self) -> None:
        super().__init__()
        self._last: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        window = settings.log_dedup_seconds
        if window <= 0 or record.levelno < logging.WARNING:
            return True

        try:
            key = f"{record.levelname}|{record.name}|{record.getMessage()}"
        except Exception:
            return True

        now = time.time()
        last = self._last.get(key)
        if last is not None and (now - last) < window:
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            return False        # drop this repeat

        skipped = self._suppressed.pop(key, 0)
        if skipped:
            mins = int(window // 60) or 1
            record.msg = f"{record.getMessage()}  (+{skipped} repeats in {mins}m)"
            record.args = ()
        self._last[key] = now

        if len(self._last) > 500:       # bound memory on a 24/7 process
            cutoff = now - window * 2
            for k in [k for k, v in self._last.items() if v < cutoff]:
                self._last.pop(k, None)
                self._suppressed.pop(k, None)
        return True


_repeat_filter = _RepeatFilter()


class _MongoLogHandler(logging.Handler):
    """Fire-and-forget: push each record to Mongo + WS without blocking."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            doc = {
                "level": record.levelname,
                "service": _service_name(record.name),
                "message": record.getMessage(),
                "ts": time.time(),
                # BSON Date consumed by the TTL index (see db._ensure_ttl):
                # mongod deletes this doc once it is older than the retention.
                "dt": datetime.now(timezone.utc),
            }
        except Exception:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # emitted outside the event loop (e.g. import time) — skip DB write
        loop.create_task(_persist(doc))


async def _persist(doc: dict) -> None:
    # Imported lazily to avoid a circular import at module load.
    from .. import db
    from ..ws_hub import hub
    try:
        await db.get_collection("logs").insert_one(dict(doc))
        # `dt` is a datetime (TTL field) and isn't JSON-serializable — the
        # float `ts` already carries the time for the UI.
        await hub.broadcast("log", {k: v for k, v in doc.items() if k not in ("dt", "_id")})
    except Exception:
        pass

    # Any ERROR also goes to the BlockChainBot Telegram group (throttled there
    # per unique error, so a failing loop can't flood the chat).
    if doc.get("level") == "ERROR":
        try:
            from .. import notifier
            await notifier.notify_error(doc.get("service", "?"), doc.get("message", ""))
        except Exception:
            pass


_mongo_handler = _MongoLogHandler()
_mongo_handler.setLevel(logging.INFO)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s")
)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"scanners.{name}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    # Attached to the logger (not a handler) so console, MongoDB and the
    # WebSocket feed all see the same collapsed stream.
    logger.addFilter(_repeat_filter)
    logger.addHandler(_console_handler)
    logger.addHandler(_mongo_handler)
    logger.propagate = False
    return logger
