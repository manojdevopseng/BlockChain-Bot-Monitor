"""Mongo-backed storage — drop-in replacement for the reference `core/storage.py`.

The scanners call these functions *synchronously* (they are never awaited in the
reference code), so we keep the same synchronous signatures. Reads come from an
in-memory cache preloaded once at startup (`preload()`); writes update the cache
and are flushed to Mongo fire-and-forget on the running event loop.

Persistent scanner state (dedup sets, dicts, watchlist, pending) lives in the
`scanner_state` collection keyed by name. Fired cross-chain alerts are also
projected into the dashboard `alerts` + `tokens` collections so the UI shows
real detections.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .slog import get_logger

log = get_logger(__name__)

# ── In-memory caches (preloaded from Mongo before scanners construct) ───────────
_sets: dict[str, set] = {}
_dicts: dict[str, dict] = {}
_watchlist: list = []
_pending: dict = {}

# Hard safety cap on the sol_pending doc so it can never approach Mongo's 16 MB
# per-document limit (the scanner already bounds to 5000; this is belt-and-braces).
_PENDING_SAFETY_CAP = 6000

# ── Write coalescing ────────────────────────────────────────────────────────────
# The SOL scanner calls save_pending() once every ~5s cycle. To make sure these
# writes NEVER pile up or block the event loop we coalesce per state-name: keep
# only the latest payload and run at most one in-flight write per name, off the
# loop (Motor encodes BSON + does socket I/O on its own thread pool — there is no
# json.dumps and no indent, so the 273 MB / main-thread-freeze failure mode from
# the file-based version cannot recur).
_latest_state: dict[str, tuple[str, Any]] = {}
_state_writer_active: set[str] = set()


# Background tasks are held here: asyncio only keeps a weak reference to a
# bare create_task(), so without this a write can be garbage-collected mid-flight.
_bg_tasks: set = set()


def _schedule(coro) -> None:
    """Run a write on the event loop without making the caller wait.

    The scanners are synchronous at the point they record an alert or a token,
    so the coroutine is handed to the loop instead of awaited. With no loop
    running (imports, tests) the coroutine is closed rather than left dangling.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = loop.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def _queue_state(name: str, kind: str, data: Any) -> None:
    """Record the latest payload for `name` and ensure a single drain task runs."""
    _latest_state[name] = (kind, data)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (e.g. import-time) — cache still holds it
    if name not in _state_writer_active:
        _state_writer_active.add(name)
        loop.create_task(_drain_state(name))


async def _drain_state(name: str) -> None:
    from .. import db
    try:
        while name in _latest_state:
            kind, data = _latest_state.pop(name)
            try:
                await db.get_collection("scanner_state").update_one(
                    {"name": name},
                    {"$set": {"name": name, "kind": kind, "data": data, "saved_at": time.time()}},
                    upsert=True,
                )
            except Exception as exc:  # noqa: BLE001
                # Not silent: this is what persists the SOL watchlist, the
                # pending map and the dedup sets. If it stops working the
                # scanners keep running and quietly lose their state on the
                # next restart.
                log.error(f"could not persist scanner state '{name}': {exc}")
    finally:
        _state_writer_active.discard(name)


async def preload() -> None:
    """Load all persisted scanner state from Mongo into the caches. Idempotent."""
    from .. import db
    global _watchlist, _pending
    try:
        docs = await db.get_collection("scanner_state").find({}).to_list(1000)
    except Exception:
        docs = []
    for d in docs:
        name, kind, data = d.get("name"), d.get("kind"), d.get("data")
        if kind == "set":
            _sets[name] = set(data or [])
        elif kind == "dict":
            _dicts[name] = dict(data or {})
        elif name == "sol_watchlist":
            _watchlist = list(data or [])
        elif name == "sol_pending":
            _pending = dict(data or {})


# ── Sets (dedup guards) ─────────────────────────────────────────────────────────

def load_set(name: str) -> set:
    return set(_sets.get(name, set()))


def save_set(name: str, data) -> None:
    items = sorted(data)
    _sets[name] = set(items)
    _queue_state(name, "set", items)


# ── Dicts ────────────────────────────────────────────────────────────────────────

def load_dict(name: str) -> dict:
    return dict(_dicts.get(name, {}))


def save_dict(name: str, data: dict) -> None:
    snapshot = dict(data)
    _dicts[name] = snapshot
    _queue_state(name, "dict", snapshot)


# ── Watchlist (live SOL watch window) ───────────────────────────────────────────

def load_watchlist() -> list:
    return list(_watchlist)


def save_watchlist(items: list) -> None:
    global _watchlist
    snapshot = list(items)
    _watchlist = snapshot
    _queue_state("sol_watchlist", "list", snapshot)


# ── Pending (sol_scanner) ───────────────────────────────────────────────────────

def load_pending() -> dict:
    return dict(_pending)


def save_pending(pending: dict) -> None:
    global _pending
    snapshot = dict(pending)
    # Defensive: never let the persisted doc approach Mongo's 16 MB limit even if
    # the scanner's own bound (5000) were ever bypassed — keep the newest entries.
    if len(snapshot) > _PENDING_SAFETY_CAP:
        newest = sorted(
            snapshot.items(),
            key=lambda kv: float((kv[1] or {}).get("open_timestamp", 0) or 0),
            reverse=True,
        )[:_PENDING_SAFETY_CAP]
        snapshot = dict(newest)
    _pending = snapshot
    _queue_state("sol_pending", "dict", snapshot)


# ── Fired alerts → dashboard alerts + tokens ────────────────────────────────────

def save_alert_record(record: dict) -> None:
    """Persist a fired cross-chain alert into the dashboard collections."""
    _schedule(_persist_alert(dict(record)))


async def _persist_alert(record: dict) -> None:
    from .. import db
    from ..ws_hub import hub
    from datetime import datetime, timezone
    ts = float(record.get("alert_timestamp") or time.time())
    # BSON Date the TTL index expires on (see db._ensure_ttl).
    dt = datetime.fromtimestamp(ts, timezone.utc)
    chain = record.get("chain") or "eth"
    dex = record.get("dex") or record.get("wallet_tag") or ""
    sym = record.get("token_symbol") or "?"
    sol_sym = record.get("sol_symbol") or sym
    fee_eth = record.get("fee_eth")
    alert_doc = {
        "type": "Cross-Chain Match",
        "severity": "high",
        "chain": chain,
        "message": f"{sol_sym} → {sym} matched on {chain} ({dex})",
        "status": "new",
        "created_at": ts,
        "token_symbol": sym,
        "token_address": record.get("token_address"),
        "tx_hash": record.get("tx_hash"),
        "fee_eth": fee_eth,
        # The SOL side is searchable in Recent Alerts, same as the reference
        # dashboard — a match is only meaningful with both ends visible.
        "sol_symbol": sol_sym,
        "sol_address": record.get("sol_address"),
        "sol_mcap_usd": record.get("sol_mcap_usd"),
        "dex": dex,
        "dt": dt,
    }
    token_doc = {
        "symbol": sym,
        "chain": chain,
        "address": record.get("token_address"),
        "pair": f"{sym}/WETH",
        "type": "new",
        "mcap_usd": record.get("sol_mcap_usd") or 0,
        "volume_24h": 0,
        "dex": dex,
        "tx_hash": record.get("tx_hash"),
        "fee_eth": fee_eth,
        "created_at": ts,
        "dt": dt,
    }
    try:
        await db.get_collection("alerts").insert_one(alert_doc)
        await db.get_collection("tokens").update_one(
            {"address": token_doc["address"]}, {"$set": token_doc}, upsert=True
        )
        await hub.broadcast(
            "alert",
            {k: v for k, v in alert_doc.items() if k not in ("_id", "dt")},
        )
    except Exception as exc:  # noqa: BLE001
        log.error(f"could not save cross-chain alert {sym} ({chain}): {exc}")


# ── Reference API compatibility (unused by the 3 scanners, kept for parity) ─────

def save_token(token) -> None:  # pragma: no cover - parity stub
    _schedule(_persist_token(token))


async def _persist_token(token) -> None:
    from .. import db
    try:
        doc = token.to_dict() if hasattr(token, "to_dict") else dict(token)
        doc.setdefault("created_at", time.time())
        await db.get_collection("tokens").update_one(
            {"address": doc.get("address")}, {"$set": doc}, upsert=True
        )
    except Exception as exc:  # noqa: BLE001
        log.error(f"could not save token {getattr(token, 'symbol', '?')}: {exc}")


def save_alert(alert) -> None:  # pragma: no cover - parity stub
    rec = alert.to_dict() if hasattr(alert, "to_dict") else dict(alert)
    save_alert_record(rec)
