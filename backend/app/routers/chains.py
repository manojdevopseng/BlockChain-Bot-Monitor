"""Chain status routes. Merges live registry on/off state into each chain."""

from __future__ import annotations

from fastapi import APIRouter

from .. import db, registry
from ..util import clean_list

router = APIRouter(prefix="/api/chains", tags=["chains"])


@router.get("")
async def list_chains():
    docs = await db.get_collection("chains").find({}).to_list(50)
    enabled = await registry.enabled_map()
    for c in docs:
        cid = c.get("id")
        c["enabled"] = enabled.get(f"chain_{cid}", True)
        if not c["enabled"]:
            c["status"] = "disabled"
    return {"items": clean_list(docs)}


@router.get("/stats")
async def chain_stats():
    docs = await db.get_collection("chains").find({}).to_list(50)
    healthy = sum(1 for c in docs if c.get("uptime", 0) >= 95)
    return {
        "total": len(docs),
        "healthy": healthy,
        "warning": sum(1 for c in docs if 90 <= c.get("uptime", 0) < 95),
        "critical": sum(1 for c in docs if c.get("uptime", 100) < 90),
    }
