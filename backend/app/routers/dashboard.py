"""Dashboard summary routes — top stat cards, system overview, live activity."""

from __future__ import annotations

import time

from fastapi import APIRouter

from .. import db, registry, supervisor
from ..util import clean_list

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def stats():
    tokens = db.get_collection("tokens")
    alerts = db.get_collection("alerts")
    total_alerts = await alerts.count_documents({})
    total_tokens = await tokens.count_documents({})

    # ETH Gas Fees: high-gas early buys caught by the swap monitors.
    gas_docs = await db.get_collection("gas_alerts").find({}).to_list(500)
    fees = [g.get("fee_eth", 0) for g in gas_docs if g.get("fee_eth")]
    avg_gas = round(sum(fees) / len(fees), 5) if fees else 0.0
    gas_hits = len(fees)

    return {
        "total_alerts": total_alerts,
        "total_tokens": total_tokens,
        "eth_gas_avg_eth": avg_gas,
        "eth_gas_hits": gas_hits,
        "active_watchlist": await tokens.count_documents({"type": "watching"}),
        "cards": [
            {"key": "total_alerts", "label": "Total Alerts", "value": total_alerts},
            {"key": "total_tokens", "label": "Total Tokens", "value": total_tokens},
            {"key": "eth_gas",      "label": "High-Gas Buys", "value": gas_hits},
            {"key": "watchlist",    "label": "Active Watchlist",
             "value": await tokens.count_documents({"type": "watching"})},
        ],
    }


@router.get("/overview")
async def overview():
    """Real component state — the toggle AND whether its worker is alive.

    Reporting a service as connected just because its switch is on hides
    exactly the failure you need to see (the forwarder toggle stays on while
    the userbot is logged out, and then no message is ever sent). State is
    resolved by supervisor.service_states, shared with /api/system/services.
    """
    svcs = await registry.list_services()
    states = supervisor.service_states({s["id"]: bool(s["enabled"]) for s in svcs})

    components = []
    for s in svcs:
        if s["category"] not in ("bot", "chain"):
            continue
        st = states.get(s["id"], {"status": "unknown", "reason": "", "depends_on": None})
        components.append({
            "name": s["label"], "id": s["id"],
            "status": st["status"], "reason": st["reason"],
            "depends_on": st["depends_on"],
        })

    # Health = of the services you asked for, how many are actually working.
    wanted = [c for c in components if c["status"] != "disabled"]
    running = sum(1 for c in wanted if c["status"] == "running")
    health = round(running / len(wanted) * 100) if wanted else 100

    return {
        "overall_health": health,
        "running": running,
        "expected": len(wanted),
        "components": components,
        "uptime_seconds": supervisor.uptime_seconds(),
        "db_backend": db.backend_name(),
    }


@router.get("/activity")
async def activity(limit: int = 8):
    docs = await db.get_collection("alerts").find({}).sort("created_at", -1).limit(limit).to_list(limit)
    return clean_list(docs)
