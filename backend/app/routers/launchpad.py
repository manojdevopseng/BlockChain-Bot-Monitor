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

from fastapi import APIRouter, Body, HTTPException, Query

from .. import db, registry
from ..scanners import launchpads, scfg
from ..keywords import compile_keyword
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
        "watched": await col.count_documents({"watched": True}),
        "skip_entries": await db.get_collection("launchpad_skip").count_documents({}),
        "watch_entries": await db.get_collection("launchpad_watch").count_documents({}),
        "today": await col.count_documents({"day": today}),
        "with_x": await col.count_documents({"handle": {"$ne": None}}),
        "per_pad": {pad.id: await col.count_documents({"launchpad": pad.id})
                    for pad in launchpads.all_launchpads()},
        "enabled": bool(enabled.get("launchpad_monitor", True)),
        "retention_days": scfg.LAUNCHPAD_RETENTION_DAYS,
        "dev_buy_max_eth": scfg.RBHX_DEV_BUY_MAX_ETH,
        "dev_buy_strong_eth": scfg.RBHX_DEV_BUY_STRONG_ETH,
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


# ── Keywords matched against the account's bio ─────────────────────────────────
#
# Whole-word and case-insensitive, the same rule the forwarder's own keywords
# use — "AI" matches "AI agent", not "said". Seeded once and edited here after
# that: a keyword deleted stays deleted, and the worker re-reads the list a
# minute later, so nothing restarts.
#
# Declared above the /{kind} list routes below, which would otherwise swallow
# "keywords" as a list name.

@router.get("/keywords")
async def get_keywords():
    docs = await db.get_collection("rbhx_keywords").find({}).to_list(500)
    return {"items": [d["word"] for d in docs]}


@router.post("/keywords")
async def set_keyword(payload: dict = Body(...)):
    action = payload.get("action")
    word = str(payload.get("value") or "").strip()
    if action not in ("add", "remove") or not word:
        raise HTTPException(400, "action must be add/remove with a non-empty value")
    col = db.get_collection("rbhx_keywords")
    if action == "add":
        if await col.find_one({"word": {"$regex": f"^{re.escape(word)}$", "$options": "i"}}):
            docs = await col.find({}).to_list(500)
            return {"items": [d["word"] for d in docs], "note": "already exists"}
        await col.insert_one({"word": word, "regex": compile_keyword(word),
                              "added_at": time.time()})
    else:
        await col.delete_many({"word": {"$regex": f"^{re.escape(word)}$", "$options": "i"}})
    docs = await col.find({}).to_list(500)
    return {"items": [d["word"] for d in docs]}


# ── The two username lists ─────────────────────────────────────────────────────
#
# The same feature the X Monitor has, with its own entries: skip drops an
# account's future launches, watch marks them. Separate collections on purpose —
# editing one panel's list must not edit the other's.
#
# Both expire on their own after LAUNCHPAD_RETENTION_DAYS, through the same TTL
# index every other collection here uses: a list you stop maintaining stops
# shaping the panel rather than silencing an account forever.

# X handles are 1-15 of [A-Za-z0-9_]. Anything else is a typo, not a handle.
_HANDLE_RE = re.compile(r"^@?([A-Za-z0-9_]{1,15})$")


def _clean_handle(value) -> str:
    m = _HANDLE_RE.match(str(value or "").strip())
    if not m:
        raise HTTPException(400, f"{value!r} is not an X username — 1-15 letters, "
                                 "digits or underscore, with or without the @")
    return m.group(1).lower()


def _list_col(kind: str):
    if kind not in ("skip", "watch"):
        raise HTTPException(404, f"unknown list '{kind}'")
    return db.get_collection(f"launchpad_{kind}")


@router.get("/{kind}")
async def list_entries(kind: str):
    rows = await _list_col(kind).find({}).sort("added_at", -1).to_list(1000)
    now = time.time()
    days = scfg.LAUNCHPAD_RETENTION_DAYS
    return {"items": [{
        "handle": r.get("handle"),
        "note": r.get("note") or "",
        "added_at": r.get("added_at"),
        # Shown rather than left to be worked out: the point of the TTL is that
        # it is visible when an entry is about to stop applying.
        "expires_in_days": max(0, round(days - (now - (r.get("added_at") or now)) / 86400, 1)),
    } for r in rows]}


@router.post("/{kind}")
async def add_entry(kind: str, payload: dict = Body(...)):
    handle = _clean_handle(payload.get("handle"))
    now = time.time()
    await _list_col(kind).update_one(
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
