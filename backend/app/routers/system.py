"""System info routes — host, resources, services (from supervisor)."""

from __future__ import annotations

import platform
import sys
import time

from fastapi import APIRouter

from .. import db, heartbeat, registry, supervisor
from ..scanners import scfg

router = APIRouter(prefix="/api/system", tags=["system"])

_BOOT = time.time()


# Resolved once, at import: a deploy restarts the process, so this changing is
# exactly the signal that the browser is holding a previous build.
def _build_id() -> str:
    import subprocess
    from pathlib import Path
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[3], capture_output=True,
            text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


_BUILD = _build_id()
_STARTED = time.time()


@router.get("/version")
async def version():
    """What is deployed right now.

    A dashboard tab left open across a deploy keeps running the previous
    build's JavaScript — its polling intervals, its columns, its bugs — and
    nothing on screen says so. The page compares this against what it saw when
    it loaded and offers a reload when they differ.
    """
    return {"build": _BUILD, "started_at": _STARTED}


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


@router.get("/metrics")
async def metrics():
    """Live host metrics for the status bar.

    Read from the machine via psutil; if it isn't available the fields come
    back null so the UI can show a dash rather than a made-up number.
    """
    out: dict = {"cpu_percent": None, "ram_used_gb": None, "ram_total_gb": None,
                 "ram_percent": None, "disk_percent": None, "disk_free_gb": None,
                 "net_sent_mb": None, "net_recv_mb": None}
    try:
        import psutil
    except ImportError:
        return out
    try:
        # interval=None → since the previous call, so it never blocks the loop.
        out["cpu_percent"] = round(psutil.cpu_percent(interval=None), 1)
        vm = psutil.virtual_memory()
        out["ram_used_gb"] = round((vm.total - vm.available) / 1024**3, 2)
        out["ram_total_gb"] = round(vm.total / 1024**3, 2)
        out["ram_percent"] = round(vm.percent, 1)
        du = psutil.disk_usage("/")
        out["disk_percent"] = round(du.percent, 1)
        out["disk_free_gb"] = round(du.free / 1024**3, 1)
        net = psutil.net_io_counters()
        out["net_sent_mb"] = round(net.bytes_sent / 1024**2, 1)
        out["net_recv_mb"] = round(net.bytes_recv / 1024**2, 1)
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:120]
    return out


@router.get("/retention")
async def retention():
    """Data-retention policy + live counts.

    Enforced by MongoDB TTL indexes — mongod expires old documents on its own
    background sweep, so this costs the app (and the EC2 box) nothing.
    """
    return {"backend": db.backend_name(), "collections": await db.retention_policy()}


@router.get("/activity")
async def activity():
    """When each part of the bot last actually did something.

    A worker can be `running` and idle — the task is alive and the socket says
    connected while no work happens. This is the difference between "the
    process is up" and "the bot is working".
    """
    return {"items": heartbeat.snapshot(),
            "quiet_after_ws_seconds": scfg.HEALTH_DOWN_SECONDS,
            "watchdog_enabled": scfg.HEALTH_ALERT_ENABLED}


@router.get("/services")
async def services():
    """Every registered service with its real run state.

    Resolved through supervisor.service_states so this page and the dashboard
    always agree — an enabled toggle whose worker is dead reports "stopped"
    with the reason, never "running".
    """
    svcs = await registry.list_services()
    states = supervisor.service_states({s["id"]: bool(s["enabled"]) for s in svcs})
    out = []
    for s in svcs:
        st = states.get(s["id"], {"status": "unknown", "reason": "", "depends_on": None})
        out.append({
            "id": s["id"],
            # This list is flat, where Settings draws a heading per group — so
            # the label carries the group here, or two panels both showing
            # "Telegram Alerts" would be indistinguishable.
            "label": (f"{s['group']} — {s['label']}" if s.get("group") else s["label"]),
            "category": s["category"],
            "enabled": s["enabled"],
            "status": st["status"],
            "reason": st["reason"],
        })
    return {"items": out}
