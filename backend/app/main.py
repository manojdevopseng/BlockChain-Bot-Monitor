"""SightLine — FastAPI application entry point.

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

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import db, migrations, notifier, registry, seed, security, supervisor
from .config import settings
from .routers import (
    account, admin as admin_router, ai_agent as ai_router, alert_rules,
    alerts, analytics,
    auth, billing, calls as calls_router, chains,
    chat_lookup, support,
    commands, dashboard, forwarder, launchpad, logs, mcap,
    notifications as notif_router,
    outcomes as outcomes_router, public, rbhx, rpc, rsi,
    settings as settings_router, system, tokens, trading, users as users_router,
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
    # Rows written before accounts existed belong to nobody; adopt them before
    # anything scoped by owner runs and finds an empty list.
    await migrations.run()

    # Count the launches already on file before anything reads the tally.
    # Without it every account reads as its first launch on the day this
    # ships, which is wrong for the thousands that already have more than one.
    # Fills in only accounts it has never seen, so it is safe on every boot.
    try:
        from . import x_accounts
        seeded = await x_accounts.backfill()
        if seeded:
            print(f"[startup] counted {seeded} X account(s) from the launches on file")
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] X account tally not seeded (continuing): {exc}")
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


app = FastAPI(title="SightLine API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Everything behind a login. `require_user` existed from the start but nothing
# depended on it, so /api/settings/credentials handed out the GMGN API key and
# the Cloudflare cookie to anyone who asked, and a PATCH could stop a scanner.
# The auth router itself stays open — the login has to be reachable.
# Three kinds of surface, three rules.
#
#   product   the customer's own lists. A live account reads and writes its own
#             rows; which rows are theirs is decided in the query, not here.
#   shared    what the scanners produce for everybody. A live account reads;
#             only an admin changes anything.
#   operator  the controls. Admin only, whatever the method — these are hidden
#             from a customer rather than greyed out, and the rule is here so
#             the hiding is a courtesy and not the control.
#           AI Narrative is here rather than in `shared` for one endpoint:
#           fact-check is a POST, and under the shared rule every non-GET is
#           admin-only — which left the main button of a paid feature answering
#           403 to the people paying for it. Its writes are its own allowance
#           instead, checked at the endpoint.
_PRODUCT = (rsi, mcap, ai_router, alert_rules, trading)
# Billing is its own rule: an account with an ended subscription must be able to
# buy one, so this needs a login and nothing more.
# Reporting a problem is not a product feature to be paywalled: an account
# whose subscription ended because a payment did not land is exactly the one
# that needs to say so.
_ACCOUNT = (billing, support, notif_router)
_SHARED = (dashboard, alerts, tokens, chains, commands, analytics,
           chat_lookup, outcomes_router, rbhx, launchpad, calls_router)
_OPERATOR = (forwarder, logs, rpc, system, settings_router, users_router,
             admin_router)

app.include_router(auth.router)
# Sign-up, email confirmation and password reset cannot sit behind a login,
# and the routes that do need one carry their own dependency.
app.include_router(account.router)
# The marketing pages: a price list and a contact form, and nothing else that
# a stranger could ask for.
app.include_router(public.router)
# `require_write` is the login check AND the read-only rule in one dependency:
# any request that is not a GET needs the admin role. Mounting it on the
# routers rather than listing endpoints means a new POST is covered the day it
# is written. What a `user` may not even read — the .env credentials — is
# guarded at its own endpoint, because it is a GET.
for r in _ACCOUNT:
    app.include_router(r.router, dependencies=[Depends(security.require_user)])
for r in _PRODUCT:
    app.include_router(r.router,
                       dependencies=[Depends(security.require_customer)])
for r in _SHARED:
    app.include_router(r.router,
                       dependencies=[Depends(security.require_customer_read)])
for r in _OPERATOR:
    app.include_router(r.router, dependencies=[Depends(security.require_admin)])

# CSV endpoints live in the outcomes module but mount under their own paths.
for extra in (outcomes_router.alerts_csv, outcomes_router.detections_csv):
    app.include_router(extra, dependencies=[Depends(security.require_write)])


@app.get("/api/health")
async def health():
    """Deliberately thin: this is the one unauthenticated endpoint, used by the
    deploy check and any uptime monitor. It reports that the service is up and
    whether the database answered — nothing about which scanners are running."""
    return {"ok": True, "db_ok": db.DB_OK}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str | None = None):
    """Live push channel. Same login as the REST API.

    A browser cannot set an Authorization header on a WebSocket, so the token
    comes as a query parameter — checked before the socket is accepted, so an
    unauthenticated client is closed rather than joined to the hub.
    """
    if not token or not security.decode_token(token):
        await ws.close(code=4401)   # 4401: application-level "unauthorized"
        return
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
