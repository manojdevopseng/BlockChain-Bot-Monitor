"""How many tokens one X account has launched.

The launch rows expire after fifteen days, which is right for a feed and wrong
for a count: a serial launcher would have its history reset every fortnight and
the number would never grow past a couple of weeks. So the tally lives in its
own collection with no TTL on it, and the rows carry only their own place in it.

Two numbers, and they answer different questions:

    handle_seq   on the launch row — "this was the Nth token from that account".
                 Fixed at the moment of the launch, so a row read a week later
                 still says something true about when it happened.
    launches     here — "that account has N tokens, as of now". This is the one
                 that grows, and the one worth colouring a column by.

What is deliberately not counted: a handle that came from a link to somebody's
post rather than to their profile (`handle_source == "post"`). That is not the
account behind the launch, and counting it would credit launches to accounts
that never made one. Measured when this was written: 4,725 of 18,197 handled
rows were post links.

Handles are folded to lower case, because X treats them that way and @ClockInCoin
and @clockincoin are one account.
"""

from __future__ import annotations

import time
from typing import Iterable, Optional

from . import db

# How many distinct tickers to remember per account. Enough to tell the two
# kinds of repeat launcher apart — one name over and over (a project retrying)
# versus a different name every time (a bot) — without the document growing
# without limit. Measured: @doodadswtf, 61 launches, all called DOODADS;
# @pumpdotpons, 61 launches, 61 different names.
_KEEP_SYMBOLS = 25


def _col():
    return db.get_collection("x_accounts")


def key(handle: str) -> str:
    return (handle or "").strip().lstrip("@").lower()


def counts_towards(handle: str, handle_source: str = "") -> bool:
    """Is this handle one that should be tallied at all?"""
    return bool(key(handle)) and str(handle_source or "") != "post"


async def note(handle: str, symbol: str = "", launchpad: str = "",
               when: Optional[float] = None) -> int:
    """Record one launch by this account, and return which number it was.

    One atomic $inc, so two launches landing in the same instant get 1 and 2
    rather than both getting 1. Returns 0 when the handle is not one that
    counts, which the caller can store as "no sequence" rather than as "first".
    """
    who = key(handle)
    if not who:
        return 0
    now = when or time.time()
    update: dict = {
        "$inc": {"launches": 1},
        "$max": {"last_seen": now},
        "$min": {"first_seen": now},
    }
    if symbol:
        # Capped by $slice, so the document cannot grow with the launch count.
        update["$push"] = {"symbols": {"$each": [symbol[:32]],
                                       "$slice": -_KEEP_SYMBOLS}}
    if launchpad:
        update["$addToSet"] = {"launchpads": launchpad}
    try:
        doc = await _col().find_one_and_update({"_id": who}, update,
                                               upsert=True, return_document=True)
    except Exception:  # noqa: BLE001
        # A tally that cannot be written must not stop a launch being recorded.
        return 0
    return int((doc or {}).get("launches") or 1)


async def get(handle: str) -> dict:
    who = key(handle)
    if not who:
        return {}
    return await _col().find_one({"_id": who}) or {}


async def many(handles: Iterable[str]) -> dict[str, dict]:
    """One query for a page of rows, keyed by lower-case handle."""
    wanted = sorted({key(h) for h in handles if key(h)})
    if not wanted:
        return {}
    out: dict[str, dict] = {}
    async for doc in _col().find({"_id": {"$in": wanted}}):
        out[str(doc["_id"])] = doc
    return out


async def top(limit: int = 25) -> list[dict]:
    """The busiest accounts, most launches first."""
    rows = await _col().find({}).sort("launches", -1).to_list(limit)
    return [{"handle": r["_id"], "launches": r.get("launches", 0),
             "symbols": r.get("symbols") or [],
             "launchpads": r.get("launchpads") or [],
             "first_seen": r.get("first_seen"), "last_seen": r.get("last_seen")}
            for r in rows]


async def backfill() -> int:
    """Seed the tally from the launch rows still on file.

    Without this every account reads as its first launch on the day the feature
    ships, which is wrong for the 2,365 accounts that already have more than
    one. Runs once: an account already in the collection is left alone, so this
    is safe on every boot and never double-counts.
    """
    rows = db.get_collection("launchpad_tokens")
    pipeline = [
        {"$match": {"handle": {"$ne": None},
                    "handle_source": {"$ne": "post"}}},
        {"$group": {"_id": {"$toLower": "$handle"},
                    "launches": {"$sum": 1},
                    "first_seen": {"$min": "$open_timestamp"},
                    "last_seen": {"$max": "$open_timestamp"},
                    "symbols": {"$addToSet": "$symbol"},
                    "launchpads": {"$addToSet": "$launchpad"}}},
    ]
    seeded = 0
    async for group in rows.aggregate(pipeline):
        who = str(group["_id"] or "")
        if not who or await _col().find_one({"_id": who}, {"_id": 1}):
            continue
        await _col().insert_one({
            "_id": who,
            "launches": int(group.get("launches") or 0),
            "first_seen": group.get("first_seen"),
            "last_seen": group.get("last_seen"),
            "symbols": [s for s in (group.get("symbols") or []) if s][-_KEEP_SYMBOLS:],
            "launchpads": [p for p in (group.get("launchpads") or []) if p],
            "seeded": True,
        })
        seeded += 1
    return seeded
