"""Per-day counters for the Forwarder page.

"Messages Today" and "Forwarded Today" used to sum a `today` field that
nothing ever incremented, so both were permanently 0. These are the real
counts: every premium/source message the userbot sees, and every send that
actually lands in a destination.

Two things this deliberately does NOT do:

  • one Mongo write per message — the userbot mirrors 100+ premium groups, so
    counts are accumulated in memory and flushed every FLUSH_SECONDS with a
    single $inc per key, off the handler's path
  • a midnight reset job — the day is part of the document key, so a new IST
    day simply starts counting into new documents and yesterday's are left for
    the TTL index to expire
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import db

IST = timezone(timedelta(hours=5, minutes=30))
FLUSH_SECONDS = 10

SOURCE = "source"
DEST = "dest"

_pending: dict[tuple[str, str], int] = defaultdict(int)
_flusher: asyncio.Task | None = None


def today_key() -> str:
    return datetime.now(IST).strftime("%d-%m-%Y")


def bare_key(chat_id) -> str:
    """Normalise a chat id to the form counts are keyed by.

    Telegram hands the same chat around as -1003952803806, 3952803806 or
    -5015581029 depending on where it came from, so the rule lives here and
    both the counting side and the dashboard call it. A channel name is left
    alone — the four signal sources are keyed by name.
    """
    s = str(chat_id).strip()
    if not s.lstrip("-").isdigit():
        return s
    if s.startswith("-100"):
        return s[4:]
    return s.lstrip("-")


def bump(scope: str, key, n: int = 1) -> None:
    """Count one message. Synchronous and allocation-cheap — this sits in the
    message path, so it must never await or touch the database."""
    if key is None:
        return
    _pending[(scope, bare_key(key))] += n
    _ensure_flusher()


def _ensure_flusher() -> None:
    global _flusher
    if _flusher is not None and not _flusher.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return          # no loop (imports, tests) — counts stay in memory
    _flusher = loop.create_task(_flush_loop(), name="fwd-counter-flush")


async def _flush_loop() -> None:
    while True:
        try:
            await asyncio.sleep(FLUSH_SECONDS)
            await flush()
        except asyncio.CancelledError:
            await flush()
            return
        except Exception:
            pass        # a failed flush must never kill the forwarder


async def flush() -> None:
    """Write out what has accumulated. Safe to call at any time."""
    if not _pending:
        return
    batch = dict(_pending)
    _pending.clear()
    day = today_key()
    now = datetime.now(timezone.utc)
    col = db.get_collection("forwarder_counters")
    for (scope, key), n in batch.items():
        try:
            await col.update_one(
                {"scope": scope, "key": key, "day": day},
                {"$inc": {"count": n},
                 "$setOnInsert": {"dt": now, "created_at": time.time()}},
                upsert=True,
            )
        except Exception:
            # Put it back so the count is not lost on a transient failure.
            _pending[(scope, key)] += n


async def today(scope: str) -> dict[str, int]:
    """{key: count} for the current IST day, including what is still in memory."""
    out: dict[str, int] = {}
    day = today_key()
    try:
        docs = await db.get_collection("forwarder_counters").find(
            {"scope": scope, "day": day}
        ).to_list(2000)
        for d in docs:
            out[str(d.get("key"))] = int(d.get("count") or 0)
    except Exception:
        pass
    for (s, key), n in _pending.items():
        if s == scope:
            out[key] = out.get(key, 0) + n
    return out
