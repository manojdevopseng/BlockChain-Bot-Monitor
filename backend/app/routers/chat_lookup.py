"""Chat ID lookup routes — a helper for filling in .env chat ids.

  GET    /api/chat-id/lookup?q=…   -> resolve a name / @username / link / id
  GET    /api/chat-id/seen         -> every chat the bot has been added to
  DELETE /api/chat-id/seen/{id}    -> forget one (list is only a convenience)

Read-only with respect to the bot's behaviour: looking a group up here does not
subscribe to it, forward from it, or add it to any list.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import chatid, db, supervisor
from ..scanners import scfg

router = APIRouter(prefix="/api/chat-id", tags=["chat-id"])


def _discovery_state() -> dict:
    """Why the 'seen' list may be empty — the handler owns getUpdates."""
    running = bool(supervisor.diagnostics().get("workers", {}).get("cmd"))
    if running:
        return {"discovery": True, "note": ""}
    if not scfg.TELEGRAM_BOT_TOKEN_SET:
        return {"discovery": False,
                "note": "TELEGRAM_BOT_TOKEN is not set — add one from @BotFather to .env"}
    return {"discovery": False,
            "note": 'Bot Commands is off, so the bot is not listening — '
                    "turn it on above to auto-discover groups"}


@router.get("/lookup")
async def lookup(q: str = Query(..., description="group name, @username, t.me link or chat id")):
    return {**await chatid.lookup(q), **_discovery_state()}


@router.get("/seen")
async def seen():
    return {"items": await chatid.seen_chats(), **_discovery_state()}


@router.delete("/seen/{chat_id}")
async def forget(chat_id: int):
    res = await db.get_collection("chats_seen").delete_many({"id": chat_id})
    if not res.deleted_count:
        raise HTTPException(404, f"chat {chat_id} is not in the list")
    return {"id": chat_id, "removed": True}
