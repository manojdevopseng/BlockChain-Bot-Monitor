"""Robinhood Launchpad Monitor — every launch from a watched launchpad.

The launchpad-centric view, where /api/rbhx is the profile-centric one. Both
are filled by the same worker in the same pass over the same socket, so this
router only reads.

Rows expire on their own through a MongoDB TTL index on `dt`.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from .. import db, registry
from ..scanners import launchpads, scfg
from ..util import clean_list, gmgn_url, ist_date_str

router = APIRouter(prefix="/api/launchpad", tags=["launchpad"])

# Widest window a search or a past day reads before counting.
_SCAN_CAP = 20000


def _pad_ids() -> list[str]:
    return [pad.id for pad in launchpads.all_launchpads()]


def _matches(row: dict, q: str) -> bool:
    ql = q.lower()
    return any(ql in str(row.get(f) or "").lower()
               for f in ("address", "handle", "symbol", "name", "excerpt", "website"))


@router.get("/pads")
async def pads():
    """The filter tabs, built from the launchpads that are actually configured
    rather than a list typed into the frontend — a new adapter shows up here
    the moment its address is set.

    `enabled` is its own switch in Settings; a launchpad that is off keeps its
    tab and its history, it just stops taking new launches.
    """
    enabled = await registry.enabled_map()
    return {"items": [{"id": pad.id, "label": pad.label,
                       "factories": len(pad.factories),
                       "enabled": bool(enabled.get(f"launchpad_{pad.id}", True))}
                      for pad in launchpads.all_launchpads()]}


@router.get("/tokens")
async def tokens(
    pad: str = Query("all"),
    q: str | None = None,
    min_followers: int = 0,
    with_x: bool = False,             # only launches that carry an X account
    date: str | None = None,          # DD-MM-YYYY (IST) — History
    limit: int = Query(100, le=500),
):
    """The panel. `total` counts everything that matched, not the page."""
    if pad != "all" and pad not in _pad_ids():
        raise HTTPException(404, f"unknown launchpad '{pad}' — have {_pad_ids()}")
    col = db.get_collection("launchpad_tokens")
    flt: dict = {}
    if pad != "all":
        flt["launchpad"] = pad
    if min_followers > 0:
        flt["followers"] = {"$gte": min_followers}
    if with_x:
        flt["handle"] = {"$ne": None}
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
async def dates(pad: str = Query("all")):
    """Days the History dropdown offers, newest first."""
    flt = {} if pad == "all" else {"launchpad": pad}
    days = [d for d in await db.get_collection("launchpad_tokens").distinct("day", flt) if d]
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"), reverse=True)}


@router.get("/stats")
async def stats():
    col = db.get_collection("launchpad_tokens")
    enabled = await registry.enabled_map()
    today = ist_date_str(time.time())
    return {
        "total": await col.count_documents({}),
        "today": await col.count_documents({"day": today}),
        "with_x": await col.count_documents({"handle": {"$ne": None}}),
        "per_pad": {pad.id: await col.count_documents({"launchpad": pad.id})
                    for pad in launchpads.all_launchpads()},
        "enabled": bool(enabled.get("launchpad_monitor", True)),
        "retention_days": scfg.LAUNCHPAD_RETENTION_DAYS,
        "dev_buy_max_eth": scfg.RBHX_DEV_BUY_MAX_ETH,
        "own_endpoints": scfg.RBHX_OWN_ENDPOINTS,
    }


@router.delete("/tokens/{address}")
async def delete_token(address: str):
    """Drop one row off the page."""
    res = await db.get_collection("launchpad_tokens").delete_one(
        {"address": {"$regex": f"^{re.escape(address)}$", "$options": "i"}})
    if not res.deleted_count:
        raise HTTPException(404, f"no row for {address}")
    return {"address": address, "removed": True}
