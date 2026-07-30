"""Alerts routes — including the SOL→ETH / SOL→RBH cross-chain match panels."""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Query

from .. import db
from ..util import clean_list, ist_date_str

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# flow id -> the `chain` value the scanners write on a cross-chain alert
_FLOW_CHAIN = {"eth": "eth", "rbh": "robinhood"}
_FLOW_SLUG = {"eth": "eth", "rbh": "robinhood"}


# Fields a search looks in — same set the reference dashboard searched
# (token + SOL side, symbol and address), plus the message.
_SEARCH_FIELDS = ("token_symbol", "token_address", "sol_symbol", "sol_address", "message")


def _build_filter(severity: str | None, chain: str | None, q: str | None) -> dict:
    flt: dict = {}
    if severity:
        flt["severity"] = severity
    if chain:
        flt["chain"] = chain
    if q:
        # Escaped: a user typing "0x…" or "." must not be read as a regex.
        rx = {"$regex": re.escape(q), "$options": "i"}
        flt["$or"] = [{f: rx} for f in _SEARCH_FIELDS]
    return flt


def _gas_as_alert(doc: dict) -> dict:
    """A high-gas early buy, shaped like an alert row.

    Gas hits live in their own `gas_alerts` collection (own TTL, own Detections
    section), but the Alerts page is the "everything that fired" view, so they
    are projected into the same shape at read time rather than written twice.
    """
    sym = doc.get("symbol") or "?"
    fee = doc.get("fee_eth") or 0
    age = doc.get("age_seconds")
    age_txt = f", {age}s after the first buy" if age is not None else ""
    return {
        "type": "High Gas Early Buy",
        "severity": "high",
        "status": "new",
        "chain": doc.get("chain") or "eth",
        "message": f"{sym} — an early buy paid {float(fee):.6f} ETH in gas{age_txt}",
        "created_at": doc.get("created_at"),
        "token_symbol": sym,
        "token_address": doc.get("address"),
        "tx_hash": doc.get("tx_hash"),
        "fee_eth": fee,
        "dex": doc.get("dex"),
        "source": "gas_alerts",
    }


async def _gas_rows(flt: dict) -> list[dict]:
    """Gas hits matching the same filter, already in alert shape."""
    docs = await db.get_collection("gas_alerts").find({}).to_list(2000)
    rows = [_gas_as_alert(d) for d in docs]
    if not flt:
        return rows

    def keeps(row: dict) -> bool:
        if "severity" in flt and row["severity"] != flt["severity"]:
            return False
        if "chain" in flt and row["chain"] != flt["chain"]:
            return False
        if "$or" in flt:
            # Same fields, same escaped term — reuse the compiled pattern.
            pattern = next(iter(flt["$or"][0].values()))["$regex"]
            rx = re.compile(pattern, re.I)
            if not any(rx.search(str(row.get(f) or "")) for f in _SEARCH_FIELDS):
                return False
        return True

    return [r for r in rows if keeps(r)]


# Widest window read when a whole day has to be assembled. Retention keeps the
# feed to 30 days, so this is a guard rail rather than a real limit.
_DAY_SCAN_CAP = 2000


@router.get("")
async def list_alerts(
    severity: str | None = None,
    chain: str | None = None,
    q: str | None = None,
    date: str | None = None,        # DD-MM-YYYY (IST) — History filter
    limit: int = Query(50, le=200),
    skip: int = 0,
):
    flt = _build_filter(severity, chain, q)
    col = db.get_collection("alerts")
    # Take enough of each feed to fill the page after merging, then sort the
    # two together — a page must not be all of one kind just because that
    # collection happened to be read first. A past day cannot be served from
    # the newest rows, so that case reads a wider window.
    take = _DAY_SCAN_CAP if date else skip + limit
    docs = await col.find(flt).sort("created_at", -1).limit(take).to_list(take)
    gas = await _gas_rows(flt)

    merged = clean_list(docs) + gas
    if date:
        merged = [d for d in merged if ist_date_str(d.get("created_at") or 0) == date]
        total = len(merged)
    else:
        total = await col.count_documents(flt) + len(gas)
    merged.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    return {"total": total, "items": merged[skip:skip + limit]}


@router.get("/dates")
async def alert_dates():
    """Days (IST, newest first) that have alerts — the History dropdown.

    Both feeds, so a day with only high-gas buys is still offered.
    """
    days: set[str] = set()
    for name in ("alerts", "gas_alerts"):
        for d in await db.get_collection(name).find({}).to_list(_DAY_SCAN_CAP):
            ts = d.get("created_at")
            if ts:
                days.add(ist_date_str(ts))
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"), reverse=True)}


@router.get("/stats")
async def alert_stats():
    col = db.get_collection("alerts")
    gas = await db.get_collection("gas_alerts").count_documents({})
    return {
        # Gas hits are always high severity, so they only move total + high.
        "total": await col.count_documents({}) + gas,
        "high": await col.count_documents({"severity": "high"}) + gas,
        "medium": await col.count_documents({"severity": "medium"}),
        "low": await col.count_documents({"severity": "low"}),
    }


# ── Cross-chain match panels (SOL→ETH / SOL→RBH) ───────────────────────────────

def _match_cc(doc: dict, q: str) -> bool:
    # Same fields the reference dashboard searched, plus dex/message. sol_symbol
    # matters because a match is looked up by the SOL ticker as often as by the
    # destination-chain one.
    q = q.lower()
    for key in ("token_symbol", "token_name", "token_address", "sol_symbol",
                "sol_address", "dex", "message"):
        if q in str(doc.get(key, "")).lower():
            return True
    return False


@router.get("/crosschain")
async def crosschain(
    flow: str = Query("eth", pattern="^(all|eth|rbh)$"),
    q: str | None = None,
    date: str | None = None,        # DD-MM-YYYY (IST) — History filter
    limit: int = Query(100, le=500),
):
    """SOL→ETH / SOL→RBH ticker matches fired by the cross-chain scanners."""
    # "all" merges both flows into one section. SOL is always the source side,
    # so the only thing that varies is the destination chain — which is exactly
    # what the flow filter selects.
    flt: dict = {"type": "Cross-Chain Match"}
    if flow != "all":
        flt["chain"] = _FLOW_CHAIN[flow]
    docs = await db.get_collection("alerts").find(flt).to_list(1000)
    docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    if date:
        docs = [d for d in docs if ist_date_str(d.get("created_at", 0)) == date]
    if q:
        docs = [d for d in docs if _match_cc(d, q)]
    docs = docs[:limit]
    for d in docs:
        # Per row: in the merged view the destination chain differs row to row,
        # so a single slug would send half the links to the wrong chain's page.
        slug = _FLOW_SLUG.get(flow) or d.get("chain") or "eth"
        d["gmgn_url"] = f"https://gmgn.ai/{slug}/token/{d.get('token_address', '')}"
        d["sol_gmgn_url"] = f"https://gmgn.ai/sol/token/{d.get('sol_address', '')}"
    return {"flow": flow, "total": len(docs), "items": clean_list(docs)}


@router.get("/crosschain/dates")
async def crosschain_dates(flow: str = Query("eth", pattern="^(all|eth|rbh)$")):
    """Days (IST, newest first) that have cross-chain matches — History dropdown."""
    flt: dict = {"type": "Cross-Chain Match"}
    if flow != "all":
        flt["chain"] = _FLOW_CHAIN[flow]
    docs = await db.get_collection("alerts").find(flt).to_list(2000)
    days = {ist_date_str(d.get("created_at", 0)) for d in docs if d.get("created_at")}
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"), reverse=True)}
