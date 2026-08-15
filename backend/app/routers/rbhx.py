"""Robinhood — X — Token Monitor: the panel, and the two username lists.

Rows and both lists expire on their own through MongoDB TTL indexes (see
db.ensure_indexes), so nothing here deletes on a schedule.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException, Query

from .. import db, registry
from ..scanners import scfg
from ..util import clean_list, gmgn_url, ist_date_str

router = APIRouter(prefix="/api/rbhx", tags=["rbhx"])

# Widest window a search or a past day reads before counting.
_SCAN_CAP = 20000

# X handles are 1-15 of [A-Za-z0-9_]. Anything else is a typo, not a handle,
# and storing it would mean a list entry that can never match.
_HANDLE_RE = re.compile(r"^@?([A-Za-z0-9_]{1,15})$")


def _clean_handle(value) -> str:
    m = _HANDLE_RE.match(str(value or "").strip())
    if not m:
        raise HTTPException(400, f"{value!r} is not an X username — 1-15 letters, "
                                 "digits or underscore, with or without the @")
    return m.group(1).lower()


def _matches(row: dict, q: str) -> bool:
    ql = q.lower()
    return any(ql in str(row.get(f) or "").lower()
               for f in ("address", "handle", "symbol", "name", "excerpt"))


@router.get("/tokens")
async def tokens(
    q: str | None = None,
    min_followers: int = 0,
    date: str | None = None,          # DD-MM-YYYY (IST) — History
    limit: int = Query(100, le=500),
):
    """The panel. `total` counts everything that matched, not the page."""
    col = db.get_collection("rbhx_tokens")
    flt: dict = {}
    if min_followers > 0:
        flt["followers"] = {"$gte": min_followers}
    if date:
        flt["day"] = date
    if not q:
        total = await col.count_documents(flt)
        docs = await col.find(flt).sort("open_timestamp", -1).limit(limit).to_list(limit)
    else:
        docs = await col.find(flt).sort("open_timestamp", -1).to_list(_SCAN_CAP)
        docs = [d for d in docs if _matches(d, q)]
        total = len(docs)
        docs = docs[:limit]
    for d in docs:
        d["gmgn_url"] = gmgn_url("rbh", d.get("address", ""))
    return {"total": total, "items": clean_list(docs)}


@router.get("/dates")
async def dates():
    """Days the History dropdown offers, newest first."""
    days = [d for d in await db.get_collection("rbhx_tokens").distinct("day") if d]
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"), reverse=True)}


@router.get("/stats")
async def stats():
    col = db.get_collection("rbhx_tokens")
    enabled = await registry.enabled_map()
    return {
        "total": await col.count_documents({}),
        "today": await col.count_documents({"day": ist_date_str(time.time())}),
        "watched": await col.count_documents({"watched": True}),
        "skip_entries": await db.get_collection("rbhx_skip").count_documents({}),
        "watch_entries": await db.get_collection("rbhx_watch").count_documents({}),
        "enabled": bool(enabled.get("rbhx_monitor", True)),
        "own_endpoints": scfg.RBHX_OWN_ENDPOINTS,
        "retention_days": scfg.RBHX_RETENTION_DAYS,
        "dev_buy_max_eth": scfg.RBHX_DEV_BUY_MAX_ETH,
        "dev_buy_strong_eth": scfg.RBHX_DEV_BUY_STRONG_ETH,
        "dev_buy_window": scfg.RBHX_DEV_BUY_WINDOW,
        "launchpads": len(scfg.RBHX_LAUNCHPADS),
    }


@router.delete("/tokens/{address}")
async def delete_token(address: str):
    """Drop one row. The watch list is for accounts worth following, and a
    followed account still posts things you do not want on the page."""
    res = await db.get_collection("rbhx_tokens").delete_one(
        {"address": {"$regex": f"^{re.escape(address)}$", "$options": "i"}})
    if not res.deleted_count:
        raise HTTPException(404, f"no row for {address}")
    return {"address": address, "removed": True}


# ── The two username lists ─────────────────────────────────────────────────────
#
# Same shape, opposite jobs: skip drops an account's future tokens, watch marks
# them. Both expire on their own after RBHX_RETENTION_DAYS — an entry you stop
# maintaining stops applying, rather than silently shaping the feed forever.

def _list_col(kind: str):
    if kind not in ("skip", "watch"):
        raise HTTPException(404, f"unknown list '{kind}'")
    return db.get_collection(f"rbhx_{kind}")


@router.get("/{kind}")
async def list_entries(kind: str):
    rows = await _list_col(kind).find({}).sort("added_at", -1).to_list(1000)
    now = time.time()
    days = scfg.RBHX_RETENTION_DAYS
    return {"items": [{
        "handle": r.get("handle"),
        "note": r.get("note") or "",
        "added_at": r.get("added_at"),
        # Shown rather than left to be worked out: the whole point of the TTL
        # is that it is visible when an entry is about to stop applying.
        "expires_in_days": max(0, round(days - (now - (r.get("added_at") or now)) / 86400, 1)),
    } for r in rows]}


@router.post("/{kind}")
async def add_entry(kind: str, payload: dict = Body(...)):
    handle = _clean_handle(payload.get("handle"))
    col = _list_col(kind)
    now = time.time()
    await col.update_one(
        {"handle": handle},
        {"$set": {"handle": handle, "note": str(payload.get("note") or "")[:200],
                  # Re-adding restarts the clock, which is what "I still want
                  # this" means.
                  "added_at": now, "dt": datetime.utcnow()}},
        upsert=True,
    )
    return {"handle": handle, "kind": kind, "added": True}


@router.delete("/{kind}/{handle}")
async def remove_entry(kind: str, handle: str):
    res = await _list_col(kind).delete_one({"handle": _clean_handle(handle)})
    if not res.deleted_count:
        raise HTTPException(404, f"@{handle} is not on the {kind} list")
    return {"handle": handle, "kind": kind, "removed": True}
