"""Alert Rules — the account's own copy of "tell me about this, not that".

Everything here is one document per account (`alert_subs`), read and written
only by its owner. The shared panels next door are the operator's; this is the
one place a customer decides what their own phone does.

The daily cap is bounded by the plan rather than by the page: a trial that
could subscribe to every feed with no ceiling would cost the same to serve as a
yearly account.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import accounts, alert_subs, notifier, security, telegram_link
from ..alert_subs import CHAINS, DIGEST_CHOICES, FEEDS, MODES

router = APIRouter(prefix="/api/alert-rules", tags=["alert-rules"])


def _plan_cap(doc: dict) -> int:
    """How many alerts a day this plan may ask for.

    Derived from the plan rather than stored on it: the number that matters is
    "is this account paying", and the hard ceiling is the box's, not the
    plan's.
    """
    plan = accounts.plan_of(doc)
    if not plan.telegram_alerts:
        return 0
    return alert_subs.HARD_DAILY_CAP


@router.get("")
async def get_rules(owner: dict = Depends(security.require_customer)):
    """The rules, plus everything the page needs to draw itself."""
    sub = await alert_subs.get(owner["username"])
    plan = accounts.plan_of(owner)
    return {
        "rules": sub,
        "feeds": [{"id": key, "label": label} for key, label in FEEDS.items()],
        "chains": [{"id": key, "label": label} for key, label in CHAINS.items()],
        "launchpads": await _launchpads(),
        "modes": list(MODES),
        "digest_choices": list(DIGEST_CHOICES),
        "plan": {
            "label": plan.label,
            "telegram_alerts": plan.telegram_alerts,
            "daily_cap": _plan_cap(owner),
        },
        # Rules with nowhere to send are the single most common reason an
        # account hears nothing, so the page is told plainly rather than
        # leaving the person to find the Profile page and guess.
        "telegram_linked": bool(owner.get("telegram_chat_id")),
        "sent_today": await _sent_today(owner["username"]),
    }


async def _launchpads() -> list[dict]:
    """The filter list, from the launchpads actually configured — the same
    source the Detections tabs use, so the two can never disagree."""
    from ..scanners import launchpads
    return [{"id": pad.id, "label": pad.label}
            for pad in launchpads.all_launchpads()]


async def _sent_today(username: str) -> int:
    from .. import alert_dispatch
    return await alert_dispatch.sent_today(username)


@router.patch("")
async def set_rules(payload: dict = Body(...),
                    owner: dict = Depends(security.require_customer)):
    changes = alert_subs.clean(payload, _plan_cap(owner) or 1)
    if not changes:
        raise HTTPException(400, "nothing to change")
    return {"rules": await alert_subs.save(owner["username"], changes)}


@router.post("/test")
async def send_test(owner: dict = Depends(security.require_customer)):
    """One message to the connected chat, so "is this working" is answerable
    without waiting for a launch that fits the rules."""
    plan = accounts.plan_of(owner)
    if not plan.telegram_alerts:
        raise HTTPException(
            402, f"The {plan.label} plan has dashboard alerts only — "
                 f"Telegram alerts come with a paid plan.")
    chat, why = await telegram_link.alert_target(owner["username"], None)
    if not chat:
        raise HTTPException(409, f"Nothing to send to — {why}.")
    sub = await alert_subs.get(owner["username"])
    on = [FEEDS[f] for f, want in (sub.get("feeds") or {}).items()
          if want and f in FEEDS]
    text = (
        "🔔 <b>SightLine test alert</b>\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        "This chat is connected and your rules are live.\n\n"
        + ("<b>Feeds on:</b> " + ", ".join(on) + "\n" if on else
           "<b>No feed is switched on yet</b> — nothing will arrive until one is.\n")
        + f"<b>Chains:</b> {', '.join(CHAINS.get(c, c) for c in (sub.get('chains') or [])) or 'none'}\n"
        + (f"<b>Quiet:</b> {sub['quiet_from']} – {sub['quiet_to']} IST\n"
           if sub.get("quiet_from") and sub.get("quiet_to") else "")
        + f"<b>Daily cap:</b> {sub.get('daily_cap')}"
    )
    if not await notifier.send_to(chat, text):
        raise HTTPException(502, "Telegram would not take the message — "
                                 "try again in a minute.")
    return {"sent": True, "to": why}
