"""RPC monitor routes + ETH gas (per-tx fee) summary.

RPC endpoints carry live on/off state from the registry (rpc_eth/rpc_rbh/rpc_sol
control the primary endpoint for each of the three managed chains).
"""

from __future__ import annotations

from fastapi import APIRouter

from datetime import datetime

from .. import db, registry, supervisor
from ..config import settings
from ..scanners import scfg
from ..util import clean_list, ist_date_str

router = APIRouter(prefix="/api/rpc", tags=["rpc"])

# Which registry toggle governs a chain's RPC.
_RPC_TOGGLE = {"eth": "rpc_eth", "rbh": "rpc_rbh", "sol": "rpc_sol"}


def _mask(url: str) -> str:
    """Hide the API key most providers put in the path/query."""
    if not url:
        return ""
    for sep in ("/v2/", "api-key=", "/v3/", "?key="):
        if sep in url:
            head, _, _tail = url.partition(sep)
            return f"{head}{sep}****"
    return url


async def _build() -> list[dict]:
    """Endpoints actually configured in .env — no invented latency or uptime.

    `status` reflects what we truly know: whether the endpoint is configured,
    whether its toggle is on, and (for WSS) whether the worker holding that
    socket is currently running.
    """
    enabled = await registry.enabled_map()
    live = supervisor.diagnostics().get("workers", {})

    rows = [
        ("Ethereum", "eth", "rpc_eth", "eth", scfg.ETH_RPC_HTTP, scfg.ETH_RPC_WSS),
        ("Robinhood Chain", "rbh", "rpc_rbh", "rbh", scfg.RBH_RPC_HTTP, scfg.RBH_RPC_WSS),
        ("Solana", "sol", "rpc_sol", "sol", scfg.SOL_RPC_HTTP, scfg.SOL_RPC_WSS),
    ]

    out = []
    for name, chain, toggle, worker, http, wss in rows:
        on = bool(enabled.get(toggle, True))
        for kind, url in (("WSS", wss), ("HTTP", http)):
            if not url:
                status = "not configured"
            elif not on:
                status = "disabled"
            elif kind == "WSS":
                status = "connected" if live.get(worker) else "stopped"
            else:
                status = "configured"
            out.append({
                "name": f"{name} {kind}",
                "chain": chain,
                "kind": kind,
                "url": _mask(url),
                "enabled": on,
                "configured": bool(url),
                "status": status,
            })
    return out


@router.get("/endpoints")
async def endpoints():
    return {"items": clean_list(await _build())}


@router.get("/stats")
async def stats():
    items = await _build()
    return {
        "total": len(items),
        "configured": sum(1 for e in items if e["configured"]),
        "connected": sum(1 for e in items if e["status"] == "connected"),
        "disabled": sum(1 for e in items if e["status"] == "disabled"),
        "unconfigured": sum(1 for e in items if not e["configured"]),
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


@router.get("/gas/dates")
async def gas_dates():
    """IST days that have a high-gas buy, newest first — the History dropdown.

    Parsed before sorting: DD-MM-YYYY compared as text puts 31-01 after 01-02,
    which put the dropdown out of order at every month boundary.
    """
    docs = await db.get_collection("gas_alerts").find({}).to_list(5000)
    days = {ist_date_str(d.get("created_at") or 0) for d in docs if d.get("created_at")}
    return {"dates": sorted(days, key=lambda x: datetime.strptime(x, "%d-%m-%Y"),
                            reverse=True)}


@router.get("/gas/recent")
async def gas_recent(limit: int = 50, q: str | None = None, date: str | None = None):
    """Recent high-gas early buys — feeds the dashboard's ETH Gas Fees panel.

    `date` pins the panel to one IST day (History); without it the panel is the
    live view. Search applies to either, so a search on a past day filters that
    day rather than being ignored.
    """
    gas_on = await registry.is_enabled("eth_gas_fees")
    docs = await db.get_collection("gas_alerts").find({}).to_list(5000)
    docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    if date:
        docs = [d for d in docs if ist_date_str(d.get("created_at") or 0) == date]
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
    # total counts everything that matched, not just the page — otherwise a day
    # with more hits than `limit` reports the limit as its total.
    return {"enabled": gas_on, "total": len(docs), "items": out}
