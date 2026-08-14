"""Binding an account to its own Telegram chat.

An operator's dashboard could post every alert into one group. A product cannot:
alerts belong to the person who set them, so each account gets its own private
chat with the bot and its own messages in it.

The join is a deep link. The Profile page asks for one, we hand back
`t.me/<bot>?start=<token>`, and the moment the person opens it Telegram sends
the bot `/start <token>` from their own chat — which is how the chat id arrives
without anybody typing a number they would have to look up first.

Two rules make that safe:

  the token is one-shot and short-lived   fifteen minutes, deleted on use, so a
      link pasted in a group cannot be redeemed by whoever reads it later.
  one chat, one account   a chat already bound elsewhere is refused rather than
      moved: silently re-pointing somebody's alerts is the worse surprise.
"""

from __future__ import annotations

import secrets
import time
from typing import Optional

from . import db
from .scanners.slog import get_logger

log = get_logger(__name__)

# Telegram allows 64 characters in a start payload; this is well inside it and
# still 128 bits of entropy.
_TOKEN_BYTES = 16
TOKEN_TTL = 900.0            # fifteen minutes


def _links():
    return db.get_collection("telegram_links")


def _users():
    return db.get_collection("users")


async def begin(username: str) -> str:
    """A fresh one-shot token for this account, replacing any it had."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    await _links().delete_many({"username": username})
    await _links().insert_one({"token": token, "username": username,
                               "created_at": time.time()})
    return token


async def finish(token: str, chat_id, tg_username: str = "") -> Optional[dict]:
    """Bind the chat that redeemed this token. None when it is no good.

    Raises ValueError when the chat belongs to somebody else — the caller says
    so out loud rather than moving it.
    """
    row = await _links().find_one({"token": (token or "").strip()})
    if not row:
        return None
    if time.time() - float(row.get("created_at") or 0) > TOKEN_TTL:
        await _links().delete_one({"token": row["token"]})
        return None

    taken = await _users().find_one({"telegram_chat_id": chat_id})
    if taken and taken.get("username") != row["username"]:
        raise ValueError("That Telegram account is already connected to "
                         "another login. Disconnect it there first.")

    await _users().update_one(
        {"username": row["username"]},
        {"$set": {"telegram_chat_id": chat_id,
                  "telegram_username": tg_username,
                  "telegram_linked_at": time.time()}})
    await _links().delete_one({"token": row["token"]})
    log.info(f"[TG] {row['username']} connected Telegram chat {chat_id}"
             + (f" (@{tg_username})" if tg_username else ""))
    return await _users().find_one({"username": row["username"]})


async def unlink(username: str) -> bool:
    res = await _users().update_one(
        {"username": username},
        {"$unset": {"telegram_chat_id": "", "telegram_username": "",
                    "telegram_linked_at": ""}})
    await _links().delete_many({"username": username})
    return bool(res.modified_count)


async def chat_for(username: str) -> Optional[int]:
    """Where this account's alerts go, or None if it never connected one."""
    if not username:
        return None
    doc = await _users().find_one({"username": username},
                                  {"_id": 0, "telegram_chat_id": 1}) or {}
    return doc.get("telegram_chat_id")


async def alert_target(username: str, fallback) -> tuple[Optional[int], str]:
    """(where to send, why) for one account's alert.

    The order is deliberate. An account that connected its own chat gets it —
    that is the whole point. An admin without one falls back to the operator's
    group, which is how the box behaved before accounts existed and how it must
    keep behaving. Anyone else without one gets nothing sent: their alerts are
    on the dashboard, and posting a customer's token into the operator's group
    would be a leak, not a fallback.
    """
    from . import accounts
    doc = await _users().find_one({"username": username}) if username else None
    if doc is None:
        # No owner recorded at all — the pre-accounts behaviour.
        return fallback, "operator group"
    if doc.get("role") == accounts.ADMIN:
        return doc.get("telegram_chat_id") or fallback, "admin"
    if not accounts.plan_of(doc).telegram_alerts:
        return None, "plan has no Telegram alerts"
    chat = doc.get("telegram_chat_id")
    if not chat:
        return None, "no Telegram connected"
    return chat, "own chat"
