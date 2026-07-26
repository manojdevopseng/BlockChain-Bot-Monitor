"""Bot command management routes.

Commands are implemented by scanners/commands.py — a long-poller on the
Telegram BOT token. This router exposes their definitions, their real usage
counters, and the per-command switch. Turning one off stops it replying and
removes it from Telegram's "/" menu straight away.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from .. import db, registry, supervisor
from ..scanners import scfg
from ..util import clean_list

router = APIRouter(prefix="/api/commands", tags=["commands"])


def _handler_running() -> bool:
    return bool(supervisor.diagnostics().get("workers", {}).get("cmd"))


@router.get("")
async def list_commands():
    docs = await db.get_collection("commands").find({}).to_list(200)
    docs.sort(key=lambda d: d.get("command", ""))
    running = _handler_running()
    for d in docs:
        uses = d.get("uses_total", 0) or 0
        errs = d.get("errors_total", 0) or 0
        # None until it has actually been used — better than showing 100%.
        d["success_rate"] = round((uses - errs) / uses * 100, 2) if uses else None
        # Enabled but dead if the handler itself isn't running.
        d["live"] = bool(running and d.get("enabled", True))
    return {"items": clean_list(docs), "handler_running": running}


@router.get("/stats")
async def stats():
    docs = await db.get_collection("commands").find({}).to_list(200)
    uses = sum(d.get("uses_total", 0) or 0 for d in docs)
    errs = sum(d.get("errors_total", 0) or 0 for d in docs)
    return {
        "total": len(docs),
        "enabled": sum(1 for d in docs if d.get("enabled", True)),
        "uses_total": uses,
        "errors_total": errs,
        "success_rate": round((uses - errs) / uses * 100, 2) if uses else None,
        "handler_running": _handler_running(),
        "handler_enabled": await registry.is_enabled("bot_commands"),
        # The one chat the bot answers in — blank means "every chat".
        "chat_id": scfg.COMMAND_CHAT_ID or None,
    }


@router.patch("/{command}")
async def toggle_command(command: str, payload: dict = Body(...)):
    if "enabled" not in payload:
        raise HTTPException(400, "body must include 'enabled'")
    cmd = command if command.startswith("/") else f"/{command}"
    enabled = bool(payload["enabled"])
    res = await db.get_collection("commands").update_one(
        {"command": cmd}, {"$set": {"enabled": enabled}}
    )
    if not res.matched_count:
        raise HTTPException(404, f"unknown command '{cmd}'")

    # Re-publish the "/" menu so Telegram reflects the change immediately.
    handler = supervisor.instance("cmd")
    if handler is not None:
        try:
            await handler.refresh_menu()
        except Exception as exc:  # noqa: BLE001
            print(f"[commands] menu not re-published after toggling {cmd}: {exc}")
    return {"command": cmd, "enabled": enabled}
