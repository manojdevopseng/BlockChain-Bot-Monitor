"""Chain status routes.

The list is derived from what the app actually monitors — registry toggles plus
the RPC endpoints configured in .env — not from a seeded table. Every field is
something we genuinely know: whether the chain is enabled, whether an RPC is
configured, whether its worker is running. Numbers we don't measure (latency,
TPS, uptime) are not invented here.
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import db, registry, supervisor
from ..scanners import scfg
from ..util import clean_list

router = APIRouter(prefix="/api/chains", tags=["chains"])

# Chain -> which toggle governs it and which supervisor worker serves it.
_CHAINS = [
    {"id": "sol", "name": "Solana",          "symbol": "SOL",  "toggle": "chain_sol", "worker": "sol"},
    {"id": "eth", "name": "Ethereum",        "symbol": "ETH",  "toggle": "chain_eth", "worker": "eth"},
    {"id": "rbh", "name": "Robinhood Chain", "symbol": "RBH",  "toggle": "chain_rbh", "worker": "rbh"},
    # No discovery scanner and no socket: these two are read only when a
    # premium caller names an address, to answer "is this token live here".
    # They were missing from this page entirely, which made the dashboard
    # claim three supported chains while the detection panels showed five.
    {"id": "bnb",  "name": "BNB Chain", "symbol": "BNB",  "toggle": "premium_bnb_detection",
     "worker": "", "check_only": True},
    {"id": "base", "name": "Base",      "symbol": "BASE", "toggle": "premium_base_detection",
     "worker": "", "check_only": True},
]

_RPC = {
    "sol": lambda: (scfg.SOL_RPC_HTTP, scfg.SOL_RPC_WSS),
    "eth": lambda: (scfg.ETH_RPC_HTTP, scfg.ETH_RPC_WSS),
    "rbh": lambda: (scfg.RBH_RPC_HTTP, scfg.RBH_RPC_WSS),
    # HTTP only, on purpose: nothing watches these chains, so there is no
    # socket to report on and an empty WebSocket column would read as broken.
    "bnb": lambda: (scfg.BNB_RPC_HTTP, ""),
    "base": lambda: (next(iter(scfg.BASE_HTTP_ENDPOINTS), ""), ""),
}


async def _build() -> list[dict]:
    enabled = await registry.enabled_map()
    tokens = db.get_collection("tokens")

    out = []
    for c in _CHAINS:
        http, wss = _RPC[c["id"]]()
        on = bool(enabled.get(c["toggle"], True))
        # ETH/Robinhood: the real socket state, not just "the reconnect-loop
        # task hasn't crashed" — that task retries forever on a rejection, so
        # it stays "alive" through an hours-long 429 outage. SOL deliberately
        # keeps the worker-alive reading here: this row reflects the GMGN
        # rolling feed, which keeps working even while on-chain (Helius)
        # discovery is down — that split shows up as its own row on RPC
        # Monitor instead of collapsing into one misleading chain status.
        if c.get("check_only"):
            # There is no worker to be up or down. The honest state is whether
            # the question can be asked at all: an endpoint, or nothing.
            running = bool(http)
        else:
            running = (supervisor.rpc_connected(c["worker"]) if c["id"] in ("eth", "rbh")
                      else supervisor.diagnostics().get("workers", {}).get(c["worker"], False))
        if not on:
            status = "disabled"
        elif not (wss or http):
            status = "not configured"
        elif running:
            status = "ready" if c.get("check_only") else "connected"
        else:
            status = "stopped"
        out.append({
            "id": c["id"],
            "name": c["name"],
            "symbol": c["symbol"],
            "enabled": on,
            "status": status,
            "rpc_configured": bool(http or wss),
            "ws_configured": bool(wss),
            # Says why a chain has no socket, so the page can draw "not needed"
            # instead of a gap that reads as a fault.
            "check_only": bool(c.get("check_only")),
            "tokens": await tokens.count_documents({"chain": c["id"]}),
        })
    return out


@router.get("")
async def list_chains():
    return {"items": clean_list(await _build())}


@router.get("/stats")
async def chain_stats():
    items = await _build()
    return {
        "total": len(items),
        "connected": sum(1 for c in items if c["status"] == "connected"),
        "disabled": sum(1 for c in items if c["status"] == "disabled"),
        "unconfigured": sum(1 for c in items if not c["rpc_configured"]),
    }
