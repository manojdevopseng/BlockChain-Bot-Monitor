"""Chat ID lookup — a read-only helper for filling in .env chat ids.

Nothing here subscribes to a group, forwards from it, or adds it anywhere: the
only job is "I typed a group name / @username / link — what is its numeric id?"

Three ways a chat gets resolved, tried in this order:

  1. numeric id      -> asked straight back from Telegram (confirms it exists
                        and gives you the title, so you know you copied the
                        right one)
  2. @username, t.me -> Bot API getChat. Works for any public group/channel,
                        member or not.
  3. plain title     -> Telegram has no "search by name" for bots, so this is
                        answered from `chats_seen`: every chat the bot has been
                        added to or spoken in, recorded by the command handler.
                        A brand-new private group has no username, so this is
                        the path for it — create the group, add the bot, and it
                        shows up here with its id.

If the Telethon userbot happens to be running, its dialog list is searched too
(that reaches groups the bot itself was never added to).
"""

from __future__ import annotations

import re
import time
from typing import Optional

import aiohttp

from . import db
from .scanners import scfg

_CHATID_RE = re.compile(r"^-?\d{5,}$")
_USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{5,32}$")
_TME_RE = re.compile(r"(?:https?://)?t\.me/(?:joinchat/)?(\+?[A-Za-z0-9_\-]+)")

TELEGRAM_API = "https://api.telegram.org"


def parse_ref(q: str) -> dict:
    """Classify what the user typed."""
    q = (q or "").strip()
    if not q:
        return {"kind": "empty", "value": ""}
    if _CHATID_RE.match(q):
        return {"kind": "chat_id", "value": q}
    m = _TME_RE.search(q)
    if m:
        val = m.group(1)
        # t.me/+AbCdEf is a private invite link — a bot cannot resolve those.
        return {"kind": "invite" if val.startswith("+") else "username",
                "value": val.lstrip("+")}
    if q.startswith("@"):
        return {"kind": "username", "value": q.lstrip("@")}
    # A bare word is treated as a group *name* first — that is what people type
    # — and only guessed at as a username if no known chat is called that.
    # Without this, typing your group's name silently resolves to whatever
    # unrelated account happens to own the same @handle.
    return {"kind": "title", "value": q,
            "maybe_username": bool(_USERNAME_RE.match(q))}


def _shape(chat: dict, source: str) -> dict:
    """Normalise a Telegram chat object into the row the UI shows."""
    return {
        "id": chat.get("id"),
        "title": chat.get("title") or chat.get("username")
                 or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
                 or str(chat.get("id")),
        "type": chat.get("type") or "unknown",
        "username": chat.get("username"),
        "source": source,
    }


async def _get_chat(ref: str) -> tuple[Optional[dict], str]:
    """Bot API getChat. Returns (chat, error_description)."""
    if not scfg.TELEGRAM_BOT_TOKEN_SET:
        return None, "TELEGRAM_BOT_TOKEN is not set — add one from @BotFather to .env"
    url = f"{TELEGRAM_API}/bot{scfg.TELEGRAM_BOT_TOKEN}/getChat"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, data={"chat_id": ref},
                              timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001
        return None, f"could not reach Telegram: {exc}"
    if data.get("ok"):
        return data.get("result") or {}, ""
    return None, str(data.get("description") or "chat not found")


async def record_chat(chat: dict, how: str = "seen") -> None:
    """Remember a chat the bot has encountered, so it can be looked up by name.

    Called by the command handler for every update it receives — including the
    `my_chat_member` update Telegram sends the moment the bot is added to a
    group, which is what makes a freshly-created private group findable.
    """
    cid = chat.get("id")
    if cid is None:
        return
    row = _shape(chat, how)
    source = row.pop("source")
    now = time.time()
    try:
        await db.get_collection("chats_seen").update_one(
            {"id": cid},
            # `source` records how we first learned of the chat, so a later
            # lookup doesn't overwrite "bot added" with "looked up".
            {"$set": {**row, "last_seen": now},
             "$setOnInsert": {"first_seen": now, "source": source}},
            upsert=True,
        )
    except Exception:
        # Discovery is a convenience — never let it break update handling.
        pass


async def seen_chats() -> list[dict]:
    docs = await db.get_collection("chats_seen").find({}).to_list(500)
    docs.sort(key=lambda d: d.get("last_seen", 0), reverse=True)
    for d in docs:
        d.pop("_id", None)
    return docs


async def _search_seen(title: str) -> list[dict]:
    needle = title.strip().lower()
    return [
        d for d in await seen_chats()
        if needle in str(d.get("title") or "").lower()
        or needle in str(d.get("username") or "").lower()
    ]


async def _search_dialogs(title: str) -> list[dict]:
    """Search the userbot's dialog list — only if the forwarder is connected."""
    from . import supervisor
    fwd = supervisor.instance("fwd")
    client = getattr(fwd, "_client", None) if fwd is not None else None
    if client is None or not client.is_connected():
        return []
    needle = title.strip().lower()
    out: list[dict] = []
    try:
        async for dialog in client.iter_dialogs(limit=300):
            name = str(dialog.name or "")
            if needle in name.lower():
                out.append({
                    "id": dialog.id,
                    "title": name,
                    "type": "channel" if getattr(dialog, "is_channel", False)
                            else "group" if getattr(dialog, "is_group", False) else "user",
                    "username": getattr(getattr(dialog, "entity", None), "username", None),
                    "source": "userbot dialogs",
                })
    except Exception:
        return out
    return out


async def lookup(q: str) -> dict:
    """Resolve whatever the user typed into candidate chats."""
    ref = parse_ref(q)
    kind = ref["kind"]

    if kind == "empty":
        return {"query": q, "kind": kind, "matches": [],
                "error": "type a group name, @username, t.me link or chat id"}

    if kind == "invite":
        return {
            "query": q, "kind": kind, "matches": [],
            "error": "private invite links (t.me/+…) cannot be resolved by a bot — "
                     "add the bot to that group instead, it will appear below",
        }

    if kind in ("chat_id", "username"):
        probe = ref["value"] if kind == "chat_id" else f"@{ref['value']}"
        chat, err = await _get_chat(probe)
        if chat:
            found = _shape(chat, "Telegram getChat")
            await record_chat(chat, "looked up")
            return {"query": q, "kind": kind, "matches": [found], "error": ""}
        # A username the bot cannot see may still be a group it was added to.
        fallback = await _search_seen(ref["value"])
        return {"query": q, "kind": kind, "matches": fallback,
                "error": "" if fallback else err}

    # Plain name. Telegram gives bots no search-by-name, so match against the
    # chats we already know, then fall back to trying it as a @username.
    matches = await _search_seen(ref["value"])
    matches += [m for m in await _search_dialogs(ref["value"])
                if m["id"] not in {x["id"] for x in matches}]
    if matches:
        return {"query": q, "kind": kind, "matches": matches, "error": ""}

    if ref.get("maybe_username"):
        chat, err = await _get_chat(f"@{ref['value']}")
        if chat:
            found = _shape(chat, "Telegram getChat (as @username)")
            await record_chat(chat, "looked up")
            return {"query": q, "kind": "username", "matches": [found], "error": ""}

    return {
        "query": q, "kind": kind, "matches": [],
        "error": "no chat with that name yet — add the bot to the group "
                 "(or send one message there) and look again",
    }
