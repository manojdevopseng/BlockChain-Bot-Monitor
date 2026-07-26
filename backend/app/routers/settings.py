"""Settings routes — the on/off control plane, keywords and group management.

  GET  /api/settings/services              -> toggles grouped Bots/Chains/RPCs
  PATCH /api/settings/services/{id}        -> flip a toggle (live via supervisor)
  GET  /api/settings/keywords              -> forwarder detection keywords
  POST /api/settings/keywords              -> add/remove (whole-word match only)
  GET  /api/settings/groups                -> forwarder source groups
  POST /api/settings/groups                -> add by @username/t.me link OR chat id
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException

from .. import db, envfile, registry, supervisor
from ..keywords import compile_keyword, keyword_matches
from ..util import clean_list
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

@router.get("/credentials")
async def get_credentials():
    """Editable .env credentials (secrets masked)."""
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


@router.post("/keywords/test")
async def test_keyword(payload: dict = Body(...)):
    """Convenience: does `word` match `text` under whole-word rules?"""
    word = str(payload.get("word") or "")
    text = str(payload.get("text") or "")
    return {"word": word, "text": text, "match": keyword_matches(word, text)}


# ── Forwarder source groups (add by username/link or chat id) ───────────────────

_CHATID_RE = re.compile(r"^-?\d{5,}$")
_USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{4,}$")
_TME_RE = re.compile(r"(?:https?://)?t\.me/(?:\+)?([A-Za-z0-9_+\-]+)")


def parse_group_ref(value: str) -> dict:
    """Classify a group reference the user typed: chat id vs username/invite link."""
    value = value.strip()
    if _CHATID_RE.match(value):
        return {"kind": "chat_id", "value": int(value)}
    m = _TME_RE.search(value)
    if m:
        return {"kind": "invite" if value.find("+") != -1 else "username", "value": m.group(1)}
    if _USERNAME_RE.match(value):
        return {"kind": "username", "value": value.lstrip("@")}
    raise HTTPException(400, "value must be a @username, a t.me link, or a numeric chat id")


@router.get("/groups")
async def get_groups():
    docs = await db.get_collection("forwarder_sources").find({}).to_list(500)
    return {"items": clean_list(docs)}


@router.post("/groups")
async def manage_group(payload: dict = Body(...)):
    action = payload.get("action")
    value = str(payload.get("value") or "").strip()
    if action not in ("add", "remove") or not value:
        raise HTTPException(400, "action must be add/remove with a value")

    col = db.get_collection("forwarder_sources")
    if action == "add":
        ref = parse_group_ref(value)
        # Phase 3: Telethon resolves username -> chat_id. For now we store the ref.
        await col.insert_one({
            "name": ref["value"] if ref["kind"] != "chat_id" else str(ref["value"]),
            "subtitle": f"added via {ref['kind']}",
            "type": "Channel", "status": "pending", "today": 0, "enabled": True,
            "ref_kind": ref["kind"], "ref_value": ref["value"], "added_at": time.time(),
        })
    else:
        await col.delete_many({"name": value})

    docs = await col.find({}).to_list(500)
    return {"items": clean_list(docs)}
