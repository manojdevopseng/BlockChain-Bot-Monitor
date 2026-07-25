"""Rate limiter + retry — ported from the reference repo (api/rate_limiter.py).

Only the imports changed (config → scfg, logger → slog). The behaviour is
identical: the lock is held during the sleep so outbound calls are spaced by
exactly `1/rate` seconds.
"""

import asyncio
import time

import aiohttp

from app.scanners import scfg as config
from app.scanners.slog import get_logger

log = get_logger(__name__)

_auth_alert_sent = False
_last_cf_warn: float = 0.0
_CF_WARN_INTERVAL: float = 300.0   # 5 minutes


async def _send_auth_alert() -> None:
    """Fire a Telegram notification when GMGN fingerprint expires (401)."""
    global _auth_alert_sent
    if _auth_alert_sent or not config.TELEGRAM_ENABLED:
        return
    _auth_alert_sent = True
    try:
        url  = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        body = {
            "chat_id":    config.TELEGRAM_CHAT_ID,
            "parse_mode": "HTML",
            "text": (
                "⚠️ <b>GMGN Auth Failed (401)</b>\n\n"
                "GMGN fingerprint expired. Put fresh values in <code>.env</code>:\n\n"
                "<code>GMGN_DEVICE_ID</code>\n"
                "<code>GMGN_FP_DID</code>\n"
                "<code>GMGN_CLIENT_ID</code>"
            ),
        }
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=10))
    except Exception:
        pass  # notification failure should never crash the bot


class RateLimiter:
    """Serializes API calls to a fixed rate. Lock held during the sleep."""

    def __init__(self, rate: float = config.API_RATE_LIMIT) -> None:
        self._delay = 1.0 / rate
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last_call + self._delay - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


async def with_retry(coro_fn, *args, **kwargs):
    """Call an async function with exponential backoff on failure."""
    backoff = config.RETRY_BACKOFF_BASE
    for attempt in range(1, config.RETRY_MAX + 1):
        try:
            return await coro_fn(*args, **kwargs)
        except Exception as exc:
            if attempt == config.RETRY_MAX:
                raise
            exc_str = str(exc)
            if "401" in exc_str:
                log.warning(f"Auth failure (401) — skipping retries: {exc}")
                asyncio.create_task(_send_auth_alert())
                raise
            elif "403" in exc_str or "429" in exc_str:
                wait = 30.0
                global _last_cf_warn
                now = time.monotonic()
                if now - _last_cf_warn > _CF_WARN_INTERVAL:
                    _last_cf_warn = now
                    log.warning(
                        f"Cloudflare/rate-limit block — backing off {wait}s "
                        f"(further blocks suppressed for {int(_CF_WARN_INTERVAL // 60)}m)"
                    )
                await asyncio.sleep(wait)
                backoff = config.RETRY_BACKOFF_BASE
            else:
                log.warning(f"Attempt {attempt} failed: {exc} — retrying in {backoff}s")
                await asyncio.sleep(backoff)
                backoff *= config.RETRY_BACKOFF_BASE
