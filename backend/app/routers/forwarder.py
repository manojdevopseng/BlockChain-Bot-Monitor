"""Telegram forwarder routes — sources, destinations, per-source toggle,
and the premium-caller address detection panels (ETH / RBH / SOL)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, HTTPException, Query

from .. import db, fwd_counters, registry
from ..scanners import scfg
from ..util import clean_list

router = APIRouter(prefix="/api/forwarder", tags=["forwarder"])

# GMGN token-page slug per chain (for the "view on GMGN" link).
_GMGN_SLUG = {"eth": "eth", "rbh": "robinhood", "sol": "sol"}


def _gmgn_url(chain: str, address: str) -> str:
    slug = _GMGN_SLUG.get(chain, chain)
    return f"https://gmgn.ai/{slug}/token/{address}"


# The four signal channels the userbot listens to, and the registry switch that
# gates each one. Names come from .env — nothing hardcoded.
def _signal_channels() -> list[tuple[str, str, str]]:
    """(channel name, registry service id, what it feeds)"""
    return [
        (scfg.SOURCE_CALL,   "bbcanalyser2",           "first-call signals → DEST_SIGNALS"),
        (scfg.SOURCE_BUYBOT, "forwarder",              "new-group signals → DEST_SIGNALS"),
        (scfg.SOURCE_DEXS,   "dexsignalcall",          "DEX signals → DEST_DEXS"),
        (scfg.SOURCE_OTTO,   "eth_otto_group",         "Otto deployments → DEST_OTTO"),
    ]


def _destinations() -> list[tuple[str, str, str]]:
    """(env key, chat id, what gets sent there)"""
    return [
        ("DEST_OTTO",               scfg.DEST_OTTO,               "Otto method/function hash matches"),
        ("DEST_SIGNALS",            scfg.DEST_SIGNALS,            "CallAnalyser2 first-calls + BuyBotTracker"),
        ("DEST_DEXS",               scfg.DEST_DEXS,               "DEX signals from dexssignal"),
        ("DEST_PREMIUM_ETH_CALLER", scfg.DEST_PREMIUM_ETH_CALLER, "ETH addresses seen in premium groups"),
        ("DEST_PREMIUM_ALL",        scfg.DEST_PREMIUM_ALL,        "Raw mirror of every premium message"),
    ]


@router.get("/sources")
async def sources():
    """What the userbot actually reads: the four signal channels plus every
    enabled premium group.

    This used to list the `forwarder_sources` collection, which only ever holds
    groups added from the dashboard — so it was empty while the userbot was
    reading 111 groups, and its toggle wrote to a field nothing consumed.
    """
    counts = await fwd_counters.today(fwd_counters.SOURCE)
    enabled = await registry.enabled_map()

    items = [
        {
            "key": name, "name": name, "kind": "channel",
            "subtitle": feeds,
            "enabled": bool(enabled.get(service, True)),
            "service": service,
            "today": counts.get(name, 0),
        }
        for name, service, feeds in _signal_channels() if name
    ]

    for g in await db.get_collection("premium_groups").find({}).to_list(5000):
        gid = g.get("id")
        if gid is None:
            continue
        # The chat id is always on the row, next to the name — knowing which
        # group an id belongs to is the whole point of showing the name.
        subtitle = str(gid)
        if g.get("username"):
            subtitle = f"@{g['username']} · {gid}"
        items.append({
            "key": str(gid), "kind": "group",
            "name": g.get("name") or str(gid),
            "chat_id": gid,
            "subtitle": subtitle,
            "named": bool(g.get("name")),
            "enabled": g.get("enabled", True) is not False,
            "today": counts.get(fwd_counters.bare_key(gid), 0),
        })
    return {"items": items}


@router.get("/destinations")
async def destinations():
    """Where the userbot forwards, straight from .env."""
    counts = await fwd_counters.today(fwd_counters.DEST)
    return {"items": [
        {
            "key": key, "chat_id": cid, "purpose": purpose,
            "configured": bool(cid),
            "today": counts.get(fwd_counters.bare_key(cid), 0) if cid else 0,
        }
        for key, cid, purpose in _destinations()
    ]}


@router.get("/stats")
async def stats():
    src_counts = await fwd_counters.today(fwd_counters.SOURCE)
    dst_counts = await fwd_counters.today(fwd_counters.DEST)
    groups = await db.get_collection("premium_groups").count_documents({"enabled": {"$ne": False}})
    channels = len([n for n, _s, _f in _signal_channels() if n])
    return {
        "total_sources": groups + channels,
        "total_groups": groups,
        "messages_today": sum(src_counts.values()),
        "forwarded_today": sum(dst_counts.values()),
        "destinations": len([c for _k, c, _p in _destinations() if c]),
    }


@router.patch("/sources/{key}")
async def toggle_source(key: str, payload: dict = Body(...)):
    """Switch a source off where the forwarder actually looks.

    A premium group flips `premium_groups.enabled` — the field `_load_premium_ids`
    reads. A signal channel flips its registry service, the same switch as
    Settings → Bots, so the two can never disagree.
    """
    if "enabled" not in payload:
        raise HTTPException(400, "body must include 'enabled'")
    enabled = bool(payload["enabled"])

    for name, service, _feeds in _signal_channels():
        if name and key == name:
            svc = await registry.set_enabled(service, enabled)
            if svc is None:
                raise HTTPException(404, f"unknown service '{service}'")
            return {"key": key, "kind": "channel", "enabled": enabled}

    try:
        gid = int(key)
    except ValueError:
        raise HTTPException(404, f"unknown source '{key}'")
    res = await db.get_collection("premium_groups").update_one(
        {"id": gid}, {"$set": {"enabled": enabled}}
    )
    if not res.matched_count:
        raise HTTPException(404, f"unknown premium group '{key}'")
    return {"key": key, "kind": "group", "enabled": enabled}


# ── Premium-caller address detections (ETH / RBH / SOL panels) ──────────────────

def _match_q(doc: dict, q: str) -> bool:
    q = q.lower()
    if q in str(doc.get("symbol", "")).lower():
        return True
    if q in str(doc.get("name", "")).lower():
        return True
    if q in str(doc.get("address", "")).lower():
        return True
    return any(q in str(g).lower() for g in (doc.get("groups") or []))


@router.get("/detections")
async def detections(
    chain: str = Query("eth", pattern="^(eth|rbh|sol)$"),
    q: str | None = None,
    multi: bool = False,              # "Multi 2+" filter — count >= 2 groups
    limit: int = Query(100, le=500),
):
    flt: dict = {"chain": chain}
    if multi:
        flt["count"] = {"$gte": 2}
    docs = await db.get_collection("premium_detections").find(flt).to_list(1000)
    docs.sort(key=lambda d: d.get("ts", 0), reverse=True)
    if q:
        docs = [d for d in docs if _match_q(d, q)]
    docs = docs[:limit]
    for d in docs:
        d["gmgn_url"] = _gmgn_url(chain, d.get("address", ""))
    return {"total": len(docs), "items": clean_list(docs)}


@router.get("/detections/stats")
async def detections_stats(chain: str = Query("eth", pattern="^(eth|rbh|sol)$")):
    col = db.get_collection("premium_detections")
    return {
        "chain": chain,
        "total": await col.count_documents({"chain": chain}),
        "multi": await col.count_documents({"chain": chain, "count": {"$gte": 2}}),
    }


@router.get("/detections/dates")
async def detection_dates(chain: str = Query("eth", pattern="^(eth|rbh|sol)$")):
    """Archived dates (History dropdown) for a chain, newest first."""
    docs = await db.get_collection("premium_archive").find({"chain": chain}).to_list(400)
    days = {d.get("date") for d in docs if d.get("date")}
    # Parse before sorting. DD-MM-YYYY sorted as text puts 31-01 after 01-02,
    # so the dropdown ran out of order across every month boundary.
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"), reverse=True)}


@router.get("/detections/history")
async def detection_history(
    chain: str = Query("eth", pattern="^(eth|rbh|sol)$"),
    date: str = "",
):
    """One archived day's detections (same shape as live)."""
    doc = await db.get_collection("premium_archive").find_one({"chain": chain, "date": date})
    items = (doc or {}).get("items", [])
    for d in items:
        d["gmgn_url"] = _gmgn_url(chain, d.get("address", ""))
    return {"date": date, "total": len(items), "items": items}
