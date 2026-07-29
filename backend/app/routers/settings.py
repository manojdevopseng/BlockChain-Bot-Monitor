"""Settings routes — the on/off control plane, keywords and group management.

  GET  /api/settings/services              -> toggles grouped Bots/Chains/RPCs
  PATCH /api/settings/services/{id}        -> flip a toggle (live via supervisor)
  GET  /api/settings/keywords              -> forwarder detection keywords
  POST /api/settings/keywords              -> add/remove (whole-word match only)

Adding a forwarder group lives on the Forwarder page now — it writes straight
into `premium_groups`, the collection the forwarder reads, instead of a pending
row here that a watcher had to pick up.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db, envfile, registry, security, supervisor
from ..keywords import compile_keyword, keyword_matches
from ..ws_hub import hub

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── Service toggles ────────────────────────────────────────────────────────────

@router.get("/services")
async def get_services():
    return await registry.grouped()


@router.patch("/services/{service_id}")
async def toggle_service(service_id: str, payload: dict = Body(...)):
    if "enabled" not in payload:
        raise HTTPException(400, "body must include 'enabled': true|false")
    svc = await registry.set_enabled(service_id, bool(payload["enabled"]))
    if svc is None:
        raise HTTPException(404, f"unknown service '{service_id}'")
    await hub.broadcast("service_changed", svc)
    return svc


# ── Credentials that expire (GMGN fingerprint) ─────────────────────────────────

@router.get("/credentials", dependencies=[Depends(security.require_admin)])
async def get_credentials():
    """Editable .env credentials (secrets masked).

    Admin only, and the one read that is. Everything else on this page is
    readable by anyone logged in; this hands out the GMGN fingerprint and the
    chat ids, so being masked is not the same as being safe to show.
    """
    return {"items": envfile.read_values()}


@router.put("/credentials/{key}")
async def set_credential(key: str, payload: dict = Body(...)):
    """Write a new value into .env (old one replaced) and apply it live.

    Covers the GMGN fingerprint that expires plus the detection thresholds, so
    they can be tuned without SSH-ing into the server. Most take effect on the
    next read; a field whose value is only consumed when a scanner is built
    (RBH_V3_ENABLED) restarts just that scanner.
    """
    key = key.upper()
    if "value" not in payload:
        raise HTTPException(400, "body must include 'value'")
    try:
        coerced = envfile.update(key, payload["value"])
        envfile.apply_runtime(key, coerced)
    except KeyError:
        raise HTTPException(404, f"'{key}' is not an editable setting")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"could not write .env: {exc}")

    note = "applied live"
    worker = envfile.worker_for(key)
    if worker:
        restarted = await supervisor.restart_worker(worker)
        note = f"{worker} scanner restarted" if restarted else \
               f"{worker} scanner is not running — will use the new value when it starts"

    await db.get_collection("logs").insert_one({
        "level": "INFO", "service": "Settings",
        "message": f"{key} set to {coerced} from dashboard (.env rewritten, {note})",
        "ts": time.time(), "dt": datetime.now(timezone.utc),
    })
    return {"key": key, "value": coerced, "updated": True,
            "note": note, "items": envfile.read_values()}


# ── Keywords (whole-word / exact match only — see app.keywords) ────────────────

@router.get("/keywords")
async def get_keywords():
    docs = await db.get_collection("keywords").find({}).to_list(500)
    return {"items": [d["word"] for d in docs]}


@router.post("/keywords")
async def set_keyword(payload: dict = Body(...)):
    action = payload.get("action")
    word = str(payload.get("value") or "").strip()
    if action not in ("add", "remove") or not word:
        raise HTTPException(400, "action must be add/remove with a non-empty value")

    col = db.get_collection("keywords")
    if action == "add":
        existing = await col.find_one({"word": {"$regex": f"^{re.escape(word)}$", "$options": "i"}})
        if existing:
            docs = await col.find({}).to_list(500)
            return {"items": [d["word"] for d in docs], "note": "already exists"}
        await col.insert_one({"word": word, "regex": compile_keyword(word), "added_at": time.time()})
    else:
        await col.delete_many({"word": {"$regex": f"^{re.escape(word)}$", "$options": "i"}})

    docs = await col.find({}).to_list(500)
    return {"items": [d["word"] for d in docs]}


# ── AI narratives ─────────────────────────────────────────────────────────────
# The list the model is asked to choose between. Seeded from code on first
# start, edited here after that — the agent reloads it on every change, so a
# narrative added on this page is in the next prompt without a restart.

@router.get("/narratives")
async def get_narratives():
    from .. import ai_agent
    return {"items": await ai_agent.load_narratives()}


@router.post("/narratives")
async def set_narrative(payload: dict = Body(...)):
    from .. import ai_agent

    action = payload.get("action")
    text = str(payload.get("value") or "").strip()
    if action not in ("add", "remove", "toggle") or not text:
        raise HTTPException(400, "action must be add/remove/toggle with a non-empty value")
    if len(text) > 120:
        raise HTTPException(400, "a narrative should be a short phrase, not a paragraph")

    col = db.get_collection("ai_narratives")
    same = {"text": {"$regex": f"^{re.escape(text)}$", "$options": "i"}}
    if action == "add":
        if await col.find_one(same):
            return {"items": await ai_agent.load_narratives(), "note": "already there"}
        last = await col.find({}).sort("order", -1).limit(1).to_list(1)
        order = int(last[0].get("order", 0)) + 1 if last else 0
        await col.insert_one({"text": text, "order": order, "enabled": True,
                              "added_at": time.time()})
    elif action == "toggle":
        # Off means out of the prompt but still on the page. Removing is for
        # narratives you are done with; this is for the ones you are pausing.
        await col.update_one(same, {"$set": {"enabled": bool(payload.get("enabled"))}})
    else:
        await col.delete_many(same)

    # Reloaded here rather than on a timer: the person who just pressed Add
    # should not have to wonder whether it took.
    return {"items": await ai_agent.load_narratives()}


@router.post("/keywords/test")
async def test_keyword(payload: dict = Body(...)):
    """Convenience: does `word` match `text` under whole-word rules?"""
    word = str(payload.get("word") or "")
    text = str(payload.get("text") or "")
    return {"word": word, "text": text, "match": keyword_matches(word, text)}
