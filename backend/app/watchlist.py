"""The live SOL watch list.

One place, because there were two answers to "how many tokens are we watching":
the /watching command read `scanner_state.sol_watchlist` and said 54, while the
dashboard counted `tokens` documents with type "watching" — a type nothing in
the codebase has ever written — and said 0.

A ticker is only watched until its `expires_at` (SOL_WATCH_WINDOW from the
moment it triggered), so an entry past that is history, not a live watch.
"""

from __future__ import annotations

import time

from . import db


async def active() -> list[dict]:
    """Tickers being watched right now, soonest to expire first."""
    doc = await db.get_collection("scanner_state").find_one({"name": "sol_watchlist"})
    items = (doc or {}).get("data") or []
    now = time.time()
    live = [d for d in items if isinstance(d, dict) and d.get("expires_at", 0) > now]
    live.sort(key=lambda d: d.get("expires_at", 0))
    return live


async def count() -> int:
    return len(await active())
