"""Telegram forwarder routes — sources, destinations, per-source toggle,
and the premium-caller address detection panels (ETH / RBH / SOL)."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

from .. import db
from ..util import clean_list

router = APIRouter(prefix="/api/forwarder", tags=["forwarder"])

# GMGN token-page slug per chain (for the "view on GMGN" link).
_GMGN_SLUG = {"eth": "eth", "rbh": "robinhood", "sol": "sol"}


def _gmgn_url(chain: str, address: str) -> str:
    slug = _GMGN_SLUG.get(chain, chain)
    return f"https://gmgn.ai/{slug}/token/{address}"


@router.get("/sources")
async def sources():
    docs = await db.get_collection("forwarder_sources").find({}).to_list(500)
    return {"items": clean_list(docs)}


@router.get("/destinations")
async def destinations():
    docs = await db.get_collection("forwarder_dests").find({}).to_list(500)
    return {"items": clean_list(docs)}


@router.get("/stats")
async def stats():
    src = db.get_collection("forwarder_sources")
    dst = db.get_collection("forwarder_dests")
    srcs = await src.find({}).to_list(500)
    dsts = await dst.find({}).to_list(500)
    return {
        "total_sources": len(srcs),
        "total_groups": 174,
        "messages_today": sum(s.get("today", 0) for s in srcs),
        "forwarded_today": sum(d.get("today", 0) for d in dsts),
    }


@router.patch("/sources/{name}")
async def toggle_source(name: str, payload: dict = Body(...)):
    if "enabled" not in payload:
        raise HTTPException(400, "body must include 'enabled'")
    res = await db.get_collection("forwarder_sources").update_one(
        {"name": name}, {"$set": {"enabled": bool(payload["enabled"])}}
    )
    if not res.matched_count:
        raise HTTPException(404, f"unknown source '{name}'")
    return {"name": name, "enabled": bool(payload["enabled"])}


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
    dates = sorted({d.get("date") for d in docs if d.get("date")}, reverse=True)
    return {"dates": dates}


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
