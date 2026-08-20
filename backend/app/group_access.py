"""Letting a paying account into the Premium Callers group — and out again.

The alerts an account subscribes to arrive in its own chat with the bot; this
is the other half, the one shared room where every premium caller's message
lands. A group is a weaker thing to sell than a private feed: once somebody is
inside they can forward what they read, and no amount of code changes that.
What code can do is make sure only the people who paid ever get in, and that
they stop being in it the day they stop paying.

So the invite is never a link that lives anywhere. Each one is built for one
person, admits exactly one member, and dies fifteen minutes later — a link
pasted into a chat is a link that has already been used or has already
expired. Telegram enforces both; we only ask for them.

Getting somebody out is the same idea in reverse. `sweep` walks the accounts
recorded as being in the group, and any whose access has ended is removed and
unbanned in the same breath — removed so the room stays worth paying for,
unbanned so paying again just works.

Requires the bot to be an administrator of the group with the right to invite
users. Without that Telegram refuses, and the refusal is passed to the caller
rather than swallowed: "the operator has not finished setting this up" is a
true thing to tell somebody, and a silent failure is not.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import aiohttp

from . import accounts, db, notifier
from .config import settings
from .scanners.slog import get_logger

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# How long an invite is good for. Long enough to open Telegram and press the
# button, short enough that a link forwarded to a friend is already dead.
INVITE_TTL = 15 * 60

# How often the membership is reconciled with who has actually paid.
SWEEP_SECONDS = 15 * 60


def _token() -> str:
    return settings.telegram_bot_token or ""


def chat_id() -> str:
    """The group customers are let into.

    Its own setting so the operator can point customers somewhere other than
    their own raw mirror — but falling back to that mirror, because on most
    deployments they are the same room and asking twice for one answer is how
    a setup ends up half-done.
    """
    return (str(getattr(settings, "member_group_chat_id", "") or "").strip()
            or str(settings.dest_premium_all or "").strip())


def configured() -> bool:
    return bool(_token() and chat_id())


async def _api(method: str, **params) -> tuple[bool, dict | str]:
    """One Bot API call. (ok, result-or-reason)."""
    if not _token():
        return False, "no Telegram bot token is configured"
    try:
        session = await notifier._session_get()
        async with session.post(f"{TELEGRAM_API}/bot{_token()}/{method}",
                                json=params,
                                timeout=aiohttp.ClientTimeout(total=15)) as resp:
            body = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        return False, f"Telegram did not answer: {type(exc).__name__}"
    if body.get("ok"):
        return True, body.get("result") or {}
    return False, str(body.get("description") or "Telegram refused the request")


# ── who may be in the room ──────────────────────────────────────────────────

def eligible(doc: dict) -> tuple[bool, str]:
    """May this account be in the group, and why not when it may not.

    An admin is never held back — the operator is not a customer of their own
    product. Everybody else needs a live plan that is not the trial: the trial
    exists to show what the product does, and the shared room is the thing
    being sold rather than demonstrated.
    """
    if doc.get("role") == accounts.ADMIN:
        return True, ""
    state = accounts.access(doc)
    if not state.usable:
        return False, state.reason or "This account is not active"
    if accounts.plan_of(doc).id == "trial":
        return False, ("The Premium Callers group comes with a paid plan. "
                       "Your trial has everything else.")
    if not doc.get("telegram_chat_id"):
        return False, ("Connect Telegram first — the invite is issued to your "
                       "account, and it is how you are let out again when a "
                       "plan ends.")
    return True, ""


async def invite_for(doc: dict) -> dict:
    """A single-use invite for this account. Raises ValueError with the reason.

    The link is not stored. There is nothing to leak, nothing to re-issue by
    accident, and asking again simply builds another one — which costs a
    request and invalidates nothing, because the old one dies on its own.
    """
    if not configured():
        raise ValueError("The Premium Callers group is not set up yet — the "
                         "operator has to configure it first.")
    ok, why = eligible(doc)
    if not ok:
        raise ValueError(why)

    user = doc.get("username") or "account"
    ok, res = await _api(
        "createChatInviteLink",
        chat_id=chat_id(),
        name=f"{user} · single use"[:32],
        expire_date=int(time.time() + INVITE_TTL),
        member_limit=1,
    )
    if not ok:
        # The usual cause is the bot not being an administrator, or not having
        # "invite users" among its rights. Said plainly rather than as a 500.
        raise ValueError(f"Telegram would not issue an invite: {res}")

    await db.get_collection("users").update_one(
        {"username": user},
        {"$set": {"premium_group_invited_at": time.time()}})
    return {"url": (res or {}).get("invite_link", ""),
            "expires_in": INVITE_TTL,
            "single_use": True}


async def remove(doc: dict) -> tuple[bool, str]:
    """Take an account out of the group. Unbanned in the same breath.

    Banning is how Telegram removes somebody; leaving them banned would mean a
    renewal could not get back in, so the ban is lifted immediately. The person
    is out, and nothing about them is remembered by Telegram.
    """
    uid = doc.get("telegram_chat_id")
    if not uid or not configured():
        return False, "no Telegram account on file"
    ok, res = await _api("banChatMember", chat_id=chat_id(), user_id=uid)
    if not ok:
        return False, str(res)
    await _api("unbanChatMember", chat_id=chat_id(), user_id=uid,
               only_if_banned=True)
    await db.get_collection("users").update_one(
        {"username": doc.get("username")},
        {"$unset": {"premium_group_invited_at": ""}})
    return True, ""


async def status_for(doc: dict) -> dict:
    """What the Profile page shows: may they join, and are they in already."""
    ok, why = eligible(doc)
    out = {"configured": configured(), "eligible": ok, "reason": why,
           "member": False, "invited_at": doc.get("premium_group_invited_at")}
    uid = doc.get("telegram_chat_id")
    if configured() and uid:
        got, res = await _api("getChatMember", chat_id=chat_id(), user_id=uid)
        if got:
            out["member"] = str((res or {}).get("status") or "") in (
                "creator", "administrator", "member", "restricted")
    return out


# ── keeping the room to the people who paid ─────────────────────────────────

async def sweep() -> dict:
    """Remove anybody in the group whose access has ended."""
    if not configured():
        return {"checked": 0, "removed": []}
    rows = await db.get_collection("users").find(
        {"premium_group_invited_at": {"$exists": True}}).to_list(2000)
    removed = []
    for doc in rows:
        ok, _why = eligible(doc)
        if ok:
            continue
        gone, why = await remove(doc)
        if gone:
            removed.append(doc.get("username"))
            log.info(f"[GROUP] removed {doc.get('username')} — "
                     f"{accounts.access(doc).reason or 'no longer eligible'}")
        else:
            log.debug(f"[GROUP] could not remove {doc.get('username')}: {why}")
    return {"checked": len(rows), "removed": removed}


async def run() -> None:
    log.info(f"[GROUP] member sweep started — every {SWEEP_SECONDS // 60} min, "
             f"group {chat_id() or '(not configured)'}")
    while True:
        try:
            await sweep()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[GROUP] sweep failed: {type(exc).__name__}: {exc}")
        await asyncio.sleep(SWEEP_SECONDS)
