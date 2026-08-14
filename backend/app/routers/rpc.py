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
    """Every configured endpoint slot, and what we truly know about each.

    One row per slot, not per chain: the pools carry up to three endpoints and
    a fallback that exists but is never shown is a fallback nobody trusts. The
    status vocabulary is deliberately small and honest —

        connected       this WSS is the one the socket is on, and it is up
        standby         configured, healthy as far as we know, not in use
        stopped         this chain's socket has no live connection at all
        configured      an HTTP endpoint: there is no persistent connection to
                        check, so "set" is the most that can be claimed
        disabled        the chain's RPC toggle is off
        not configured  the slot is empty

    Nothing here invents latency or uptime.
    """
    enabled = await registry.enabled_map()

    # (card, chain, toggle, worker, kind, [slots])  — worker "" = no live socket
    groups: list[tuple[str, str, str, str, str, list[str]]] = [
        ("Ethereum", "eth", "rpc_eth", "eth", "WSS", list(scfg.ETH_WSS_ENDPOINTS)),
        ("Ethereum", "eth", "rpc_eth", "",    "HTTP", list(scfg.ETH_HTTP_ENDPOINTS)),
        ("ETH Gas Fees", "eth", "rpc_eth", "", "WSS", list(scfg.GAS_WSS_ENDPOINTS)),
        ("ETH Gas Fees", "eth", "rpc_eth", "", "HTTP", [scfg.GAS_RPC_HTTP]),
        ("Robinhood Chain", "rbh", "rpc_rbh", "rbh", "WSS", list(scfg.RBH_WSS_ENDPOINTS)),
        ("Robinhood Chain", "rbh", "rpc_rbh", "",    "HTTP", list(scfg.RBH_HTTP_ENDPOINTS)),
        # Its own three slots. While they are empty it borrows Robinhood's, and
        # the label says so rather than showing slots that look configured.
        ("Robinhood — X — Token Monitor" if scfg.RBHX_OWN_ENDPOINTS
         else "Robinhood — X — Token Monitor (borrowing Robinhood Chain)",
         "rbh", "rbhx_rpc", "rbhx", "WSS", list(scfg.RBHX_WSS_ENDPOINTS)),
        ("BNB Chain", "bnb", "", "", "HTTP", list(scfg.BNB_HTTP_ENDPOINTS)),
        ("Solana", "sol", "rpc_sol", "sol", "WSS", list(scfg.SOL_WSS_ENDPOINTS)),
        ("Solana", "sol", "rpc_sol", "",    "HTTP", list(scfg.SOL_HTTP_ENDPOINTS)),
        # Market Cap Alert: two HTTP slots per chain, its own switch each. No
        # worker column — these are one-off eth_call/getTokenSupply requests on
        # a timer, not a socket that can be "connected". While its own slots are
        # empty it borrows that chain's endpoints, and the name says so rather
        # than reporting "not configured" for something that is working.
        *(( f"Market Cap — {label}" if scfg.MCAP_ENDPOINTS.get(key)
            else f"Market Cap — {label} (borrowing {label})",
            chain, f"mcap_rpc_{key}", "", "HTTP",
            list(scfg.MCAP_ENDPOINTS.get(key) or []))
          for key, label, chain in (("rbh", "RBH", "rbh"), ("eth", "ETH", "eth"),
                                    ("bsc", "BSC", "bnb"), ("sol", "SOL", "sol"))),
    ]

    out: list[dict] = []
    for name, chain, toggle, worker, kind, slots in groups:
        on = bool(enabled.get(toggle, True)) if toggle else True
        # Solana's WSS slots exist for on-chain discovery and nothing else, so
        # with that switched off the socket is deliberately not dialled. That is
        # "disabled" — the same as any other toggled-off endpoint — and not the
        # "stopped" an endpoint that should be up but isn't would report.
        if (name == "Solana" and kind == "WSS"
                and not enabled.get("sol_onchain_discovery", True)):
            on = False
        live = supervisor.rpc_connected(worker) if worker else False
        active = supervisor.rpc_active_url(worker) if worker else ""
        # An empty group still gets one row, so a chain with nothing set is
        # visible as "not configured" rather than absent from the table.
        for i, url in enumerate(slots or [""], start=1):
            label = f"{name} {kind}" + (f" #{i}" if len(slots) > 1 else "")
            if not url:
                status = "not configured"
            elif not on:
                status = "disabled"
            elif kind != "WSS":
                status = "configured"
            elif not worker:
                status = "configured"
            elif live and url == active:
                status = "connected"
            elif live:
                status = "standby"
            else:
                status = "stopped"
            out.append({
                "name": label,
                "chain": chain,
                "kind": kind,
                "url": _mask(url),
                "enabled": on,
                "configured": bool(url),
                "active": bool(url) and url == active,
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
async def gas_recent(limit: int = 50, q: str | None = None, date: str | None = None,
                     dex: str | None = None):
    """Recent high-gas early buys — feeds the dashboard's ETH Gas Fees panel.

    `date` pins the panel to one IST day (History); without it the panel is the
    live view. Search applies to either, so a search on a past day filters that
    day rather than being ignored. `dex` is the V2/V3/V4 filter, applied before
    the count so the section header reports the filtered total rather than the
    whole day's.
    """
    gas_on = await registry.is_enabled("eth_gas_fees")
    docs = await db.get_collection("gas_alerts").find({}).to_list(5000)
    docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    if date:
        docs = [d for d in docs if ist_date_str(d.get("created_at") or 0) == date]
    if dex and dex != "all":
        want = dex.strip().lower()
        docs = [d for d in docs if str(d.get("dex") or "").lower() == want]
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
