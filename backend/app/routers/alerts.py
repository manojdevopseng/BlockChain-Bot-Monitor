"""Alerts routes — including the SOL→ETH / SOL→RBH cross-chain match panels."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from .. import db
from ..util import clean_list

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

IST = timezone(timedelta(hours=5, minutes=30))

# flow id -> the `chain` value the scanners write on a cross-chain alert
_FLOW_CHAIN = {"eth": "eth", "rbh": "robinhood"}
_FLOW_SLUG = {"eth": "eth", "rbh": "robinhood"}


def _day_str(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%d-%m-%Y")


@router.get("")
async def list_alerts(
    severity: str | None = None,
    chain: str | None = None,
    q: str | None = None,
    limit: int = Query(50, le=200),
    skip: int = 0,
):
    flt: dict = {}
    if severity:
        flt["severity"] = severity
    if chain:
        flt["chain"] = chain
    if q:
        flt["message"] = {"$regex": q, "$options": "i"}
    col = db.get_collection("alerts")
    total = await col.count_documents(flt)
    docs = await col.find(flt).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return {"total": total, "items": clean_list(docs)}


@router.get("/stats")
async def alert_stats():
    col = db.get_collection("alerts")
    return {
        "total": await col.count_documents({}),
        "high": await col.count_documents({"severity": "high"}),
        "medium": await col.count_documents({"severity": "medium"}),
        "low": await col.count_documents({"severity": "low"}),
    }


# ── Cross-chain match panels (SOL→ETH / SOL→RBH) ───────────────────────────────

def _match_cc(doc: dict, q: str) -> bool:
    q = q.lower()
    for key in ("token_symbol", "token_address", "sol_address", "dex", "message"):
        if q in str(doc.get(key, "")).lower():
            return True
    return False


@router.get("/crosschain")
async def crosschain(
    flow: str = Query("eth", pattern="^(eth|rbh)$"),
    q: str | None = None,
    date: str | None = None,        # DD-MM-YYYY (IST) — History filter
    limit: int = Query(100, le=500),
):
    """SOL→ETH / SOL→RBH ticker matches fired by the cross-chain scanners."""
    chain = _FLOW_CHAIN[flow]
    docs = await db.get_collection("alerts").find(
        {"type": "Cross-Chain Match", "chain": chain}
    ).to_list(1000)
    docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    if date:
        docs = [d for d in docs if _day_str(d.get("created_at", 0)) == date]
    if q:
        docs = [d for d in docs if _match_cc(d, q)]
    docs = docs[:limit]
    slug = _FLOW_SLUG[flow]
    for d in docs:
        d["gmgn_url"] = f"https://gmgn.ai/{slug}/token/{d.get('token_address', '')}"
        d["sol_gmgn_url"] = f"https://gmgn.ai/sol/token/{d.get('sol_address', '')}"
    return {"flow": flow, "total": len(docs), "items": clean_list(docs)}


@router.get("/crosschain/dates")
async def crosschain_dates(flow: str = Query("eth", pattern="^(eth|rbh)$")):
    """Days (IST, newest first) that have cross-chain matches — History dropdown."""
    docs = await db.get_collection("alerts").find(
        {"type": "Cross-Chain Match", "chain": _FLOW_CHAIN[flow]}
    ).to_list(2000)
    days = {_day_str(d.get("created_at", 0)) for d in docs if d.get("created_at")}
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"), reverse=True)}
