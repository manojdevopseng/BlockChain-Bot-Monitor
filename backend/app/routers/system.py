"""System info routes — host, resources, services (from supervisor)."""

from __future__ import annotations

import platform
import sys
import time

from fastapi import APIRouter

from .. import db, registry, supervisor

router = APIRouter(prefix="/api/system", tags=["system"])

_BOOT = time.time()


@router.get("/overview")
async def overview():
    return {
        "status": "healthy",
        "uptime_seconds": int(time.time() - _BOOT),
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "python": sys.version.split()[0],
        "db_backend": db.backend_name(),
        "db_ok": db.DB_OK,
    }


@router.get("/retention")
async def retention():
    """Data-retention policy + live counts.

    Enforced by MongoDB TTL indexes — mongod expires old documents on its own
    background sweep, so this costs the app (and the EC2 box) nothing.
    """
    return {"backend": db.backend_name(), "collections": await db.retention_policy()}


@router.get("/services")
async def services():
    """Every registered service with its live run status."""
    svcs = await registry.list_services()
    st = supervisor.status()
    out = []
    for s in svcs:
        out.append({
            "id": s["id"],
            "label": s["label"],
            "category": s["category"],
            "enabled": s["enabled"],
            "status": st.get(s["id"], "stopped" if not s["enabled"] else "running"),
        })
    return {"items": out}
