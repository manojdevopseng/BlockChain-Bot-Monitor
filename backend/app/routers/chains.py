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
]

_RPC = {
    "sol": lambda: (scfg.SOL_RPC_HTTP, scfg.SOL_RPC_WSS),
    "eth": lambda: (scfg.ETH_RPC_HTTP, scfg.ETH_RPC_WSS),
    "rbh": lambda: (scfg.RBH_RPC_HTTP, scfg.RBH_RPC_WSS),
}


async def _build() -> list[dict]:
    enabled = await registry.enabled_map()
    live = supervisor.diagnostics().get("workers", {})
    tokens = db.get_collection("tokens")

    out = []
    for c in _CHAINS:
        http, wss = _RPC[c["id"]]()
        on = bool(enabled.get(c["toggle"], True))
        running = bool(live.get(c["worker"]))
        if not on:
            status = "disabled"
        elif running:
            status = "connected"
        elif not (wss or http):
            status = "not configured"
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
