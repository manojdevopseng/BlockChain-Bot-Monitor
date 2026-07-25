"""RPC monitor routes + ETH gas (per-tx fee) summary.

RPC endpoints carry live on/off state from the registry (rpc_eth/rpc_rbh/rpc_sol
control the primary endpoint for each of the three managed chains).
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import db, registry
from ..config import settings
from ..util import clean_list

router = APIRouter(prefix="/api/rpc", tags=["rpc"])

# Which registry toggle governs a chain's RPC.
_RPC_TOGGLE = {"eth": "rpc_eth", "rbh": "rpc_rbh", "sol": "rpc_sol"}


@router.get("/endpoints")
async def endpoints():
    docs = await db.get_collection("rpc_endpoints").find({}).to_list(100)
    enabled = await registry.enabled_map()
    for e in docs:
        toggle = _RPC_TOGGLE.get(e.get("chain"))
        e["enabled"] = enabled.get(toggle, True) if toggle else True
        if not e["enabled"]:
            e["status"] = "disabled"
    return {"items": clean_list(docs)}


@router.get("/stats")
async def stats():
    docs = await db.get_collection("rpc_endpoints").find({}).to_list(100)
    healthy = sum(1 for e in docs if e.get("status") == "healthy")
    degraded = sum(1 for e in docs if e.get("status") == "degraded")
    down = sum(1 for e in docs if e.get("status") == "down")
    lats = [e["latency_ms"] for e in docs if e.get("latency_ms")]
    return {
        "total": len(docs),
        "healthy": healthy,
        "degraded": degraded,
        "down": down,
        "avg_latency_ms": round(sum(lats) / len(lats)) if lats else 0,
        "requests_1h": sum(e.get("requests_1h", 0) for e in docs),
    }


@router.get("/gas")
async def gas():
    """ETH Gas Fees summary — high-gas early-buy hits.

    Each record is a buy that paid >= MIN_FEE_ETH in gas within a token's
    monitor window (see scanners/swap_monitor.py).
    """
    gas_on = await registry.is_enabled("eth_gas_fees")
    docs = await db.get_collection("gas_alerts").find({}).to_list(1000)
    fees = sorted(d.get("fee_eth", 0) for d in docs if d.get("fee_eth"))
    base = {
        "enabled": gas_on,
        "min_fee_eth": settings.min_fee_eth,
        "window_seconds": settings.monitor_window_seconds,
        "first_buy_window_seconds": settings.first_buy_window_seconds,
    }
    if not fees:
        return {**base, "count": 0, "avg_eth": 0, "min_eth": 0, "max_eth": 0}
    return {
        **base,
        "count": len(fees),
        "avg_eth": round(sum(fees) / len(fees), 6),
        "min_eth": round(fees[0], 6),
        "max_eth": round(fees[-1], 6),
    }


@router.get("/gas/recent")
async def gas_recent(limit: int = 50, q: str | None = None):
    """Recent high-gas early buys — feeds the dashboard's ETH Gas Fees panel."""
    gas_on = await registry.is_enabled("eth_gas_fees")
    docs = await db.get_collection("gas_alerts").find({}).to_list(1000)
    docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    if q:
        ql = q.lower()
        docs = [d for d in docs
                if ql in f"{d.get('symbol','')} {d.get('name','')} "
                         f"{d.get('address','')} {d.get('tx_hash','')}".lower()]
    out = []
    for d in docs[:limit]:
        addr = d.get("address", "")
        out.append({
            "symbol": d.get("symbol"),
            "name": d.get("name") or d.get("symbol"),
            "address": addr,
            "fee_eth": d.get("fee_eth"),
            "age_seconds": d.get("age_seconds"),
            "tx_hash": d.get("tx_hash"),
            "dex": d.get("dex"),
            "created_at": d.get("created_at"),
            "gmgn_url": f"https://gmgn.ai/eth/token/{addr}" if addr else None,
        })
    return {"enabled": gas_on, "total": len(out), "items": out}
