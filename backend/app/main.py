"""BlockChain-Bot dashboard — FastAPI application entry point.

Run (from backend/):
    uvicorn app.main:app --reload --port 8000

Startup wires the DB (Mongo or in-memory fallback), seeds *configuration* only
(services, groups, keywords, command definitions — never fake activity), starts
the supervisor (owns background workers), and launches a heartbeat that pushes
live stats over the WebSocket hub.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import db, notifier, registry, seed, supervisor
from .config import settings
from .routers import (
    alerts, analytics, auth, chains, chat_lookup, commands, dashboard,
    forwarder, logs, rpc, settings as settings_router, system, tokens,
)
from .ws_hub import hub

_heartbeat_task: asyncio.Task | None = None


async def _heartbeat() -> None:
    """Push a light stats event to all WS clients every few seconds."""
    while True:
        try:
            await asyncio.sleep(5)
            payload = {
                "ts": time.time(),
                "uptime_seconds": supervisor.uptime_seconds(),
                "services": supervisor.status(),
                "clients": hub.count,
                "db_backend": db.backend_name(),
            }
            await hub.broadcast("heartbeat", payload)
            await _mark_alive()   # keeps the run marker fresh for restart detection
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001
            pass


_RUN_MARKER = "run_state"


async def _announce_start() -> None:
    """Tell the alert group we're up — and whether this was a clean restart.

    A marker doc is refreshed while running and cleared on graceful shutdown.
    Finding a stale marker at boot means the previous run died (crash, kill -9,
    EC2 reboot), so we report a RESTART instead of a plain start.
    """
    col = db.get_collection(_RUN_MARKER)
    prev = await col.find_one({"_id": "last_run"}) if db.DB_OK else None
    now = time.time()
    if prev and prev.get("alive"):
        uptime = int(float(prev.get("updated_at", now)) - float(prev.get("started_at", now)))
        await notifier.notify_restart(max(0, uptime))
    else:
        enabled = [s["label"] for s in await registry.list_services() if s.get("enabled")]
        await notifier.notify_startup(
            f"DB: <code>{db.backend_name()}</code> · Services on: {len(enabled)}"
        )
    try:
        await col.update_one(
            {"_id": "last_run"},
            {"$set": {"alive": True, "started_at": now, "updated_at": now}},
            upsert=True,
        )
    except Exception:
        pass


async def _mark_alive() -> None:
    try:
        await db.get_collection(_RUN_MARKER).update_one(
            {"_id": "last_run"}, {"$set": {"updated_at": time.time()}}
        )
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _heartbeat_task
    await db.connect()
    await db.ensure_indexes()
    await registry.seed()
    await seed.seed_all()
    await supervisor.start()
    _heartbeat_task = asyncio.create_task(_heartbeat())
    # Alerting must never be able to stop the bot from starting.
    try:
        await _announce_start()
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] start notification failed (continuing): {exc}")
    print(f"[startup] DB backend: {db.backend_name()} | ready on :{settings.api_port}")
    try:
        yield
    finally:
        if _heartbeat_task:
            _heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _heartbeat_task
        await supervisor.stop()
        # Clear the marker so the next boot knows this was a clean stop.
        try:
            await db.get_collection(_RUN_MARKER).update_one(
                {"_id": "last_run"}, {"$set": {"alive": False, "stopped_at": time.time()}}
            )
        except Exception:
            pass
        try:
            await notifier.notify_shutdown()
        except Exception as exc:  # noqa: BLE001
            print(f"[shutdown] stop notification failed (continuing): {exc}")
        await notifier.close()
        await db.close()


app = FastAPI(title="BlockChain-Bot Dashboard API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth, dashboard, alerts, tokens, chains, forwarder, commands,
          analytics, logs, rpc, system, settings_router, chat_lookup):
    app.include_router(r.router)


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "db_backend": db.backend_name(),
        "db_ok": db.DB_OK,
        "uptime_seconds": supervisor.uptime_seconds(),
        "scanners": supervisor.diagnostics(),
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await hub.connect(ws)
    try:
        await ws.send_json({"type": "hello", "data": {"backend": db.backend_name()}})
        while True:
            # We don't require client messages; keep the socket open.
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)
    except Exception:  # noqa: BLE001
        await hub.disconnect(ws)
