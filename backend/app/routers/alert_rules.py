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
    return min(plan.alerts_per_day, alert_subs.HARD_DAILY_CAP)


@router.get("")
async def get_rules(owner: dict = Depends(security.require_customer)):
    """The rules, plus everything the page needs to draw itself."""
    sub = await alert_subs.get(owner["username"])
    plan = accounts.plan_of(owner)
    # Shown already capped by the plan, the same way the dispatcher applies it.
    # A number set under a bigger plan would otherwise sit on the page looking
    # like it was in force long after the plan that allowed it ended.
    cap = _plan_cap(owner)
    if cap:
        sub["daily_cap"] = min(int(sub.get("daily_cap") or cap), cap)
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


@router.get("/why")
async def why(owner: dict = Depends(security.require_customer)):
    """"Why am I not getting alerts?", answered against real launches.

    The most common support ticket there is, and it has a real answer sitting
    in the database: run this account's own rules over the launches that
    actually happened and report what each one was rejected for. A list of
    filters tells somebody what they set; this tells them what it did.

    Everything else here is the short list of things that stop alerts before a
    filter is ever reached — no Telegram, plan without alerts, master switch
    off, quiet hours, the day's cap already spent.
    """
    from .. import alert_dispatch, db, registry, supervisor
    from ..alert_subs import Event, in_quiet_hours, matches

    sub = await alert_subs.get(owner["username"])
    plan = accounts.plan_of(owner)
    enabled = await registry.enabled_map()
    sent = await alert_dispatch.sent_today(owner["username"])

    blockers = []
    if not plan.telegram_alerts:
        blockers.append(f"The {plan.label} plan does not include Telegram alerts.")
    if not owner.get("telegram_chat_id"):
        blockers.append("No Telegram chat is connected — connect one from Profile.")
    if not sub.get("enabled"):
        blockers.append("Your rules are switched off at the top of this page.")
    if not any((sub.get("feeds") or {}).values()):
        blockers.append("No feed is switched on, so nothing can match.")
    if in_quiet_hours(sub):
        blockers.append(f"It is inside your quiet hours "
                        f"({sub.get('quiet_from')}–{sub.get('quiet_to')} IST).")
    cap = int(sub.get("daily_cap") or 0)
    if cap and sent >= cap:
        blockers.append(f"Today's cap is spent — {sent} of {cap} sent.")
    if not enabled.get("alert_fanout", True):
        blockers.append("Alert delivery is switched off on the server. "
                        "This one is ours, not yours — please raise a ticket.")

    # The launches themselves, run through this account's own rules.
    rows = await db.get_collection("launchpad_tokens").find({}).sort(
        "open_timestamp", -1).limit(200).to_list(200)
    passed, reasons = [], {}
    for row in rows:
        dev = float(row.get("dev_buy_eth") or 0)
        ok, no = matches(sub, Event(
            feed="launchpad", text="", chain="rbh",
            address=str(row.get("address") or ""),
            symbol=str(row.get("symbol") or ""),
            handle=str(row.get("handle") or ""),
            followers=int(row.get("followers") or 0),
            launchpad=str(row.get("launchpad") or ""),
            dev_buy_eth=dev, strong=dev > _strong_floor(),
            watched=bool(row.get("watched")),
            excerpt=str(row.get("excerpt") or ""),
            matched_keywords=str(row.get("matched_keywords") or "")))
        if ok:
            passed.append({"symbol": row.get("symbol"), "handle": row.get("handle"),
                           "followers": row.get("followers"),
                           "launchpad": row.get("launchpad"),
                           "dev_buy_eth": row.get("dev_buy_eth"),
                           "at": row.get("open_timestamp")})
        else:
            reasons[no] = reasons.get(no, 0) + 1

    return {
        "blockers": blockers,
        "sample": len(rows),
        "would_send": len(passed),
        "recent_matches": passed[:8],
        # Biggest first: the filter at the top of this list is the one to
        # loosen, and it is nearly always one of them doing all the work.
        "rejected_for": [{"reason": r, "count": n}
                         for r, n in sorted(reasons.items(), key=lambda kv: -kv[1])],
        "sent_today": sent,
        "daily_cap": cap,
        "delay_seconds": plan.alert_delay_seconds,
        "delivery_running": bool(supervisor.diagnostics()
                                 .get("workers", {}).get("fan")),
    }


def _strong_floor() -> float:
    from ..scanners import scfg
    return float(getattr(scfg, "RBHX_DEV_BUY_STRONG_ETH", 0.199) or 0.199)


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
    from .. import tgstyle
    text = tgstyle.screen("SightLine test alert", "🔔", [
        "This chat is connected and your rules are live.",
        tgstyle.SPACER,
        ("<b>Feeds on:</b> " + ", ".join(on) if on else
         "<b>No feed is switched on yet</b> — nothing will arrive until one is."),
        "<b>Chains:</b> " + (", ".join(CHAINS.get(c, c)
                                       for c in (sub.get("chains") or [])) or "none"),
        (f"<b>Quiet:</b> {sub['quiet_from']} – {sub['quiet_to']} IST"
         if sub.get("quiet_from") and sub.get("quiet_to") else ""),
        f"<b>Daily cap:</b> {sub.get('daily_cap')}",
    ])
    if not await notifier.send_to(chat, text):
        raise HTTPException(502, "Telegram would not take the message — "
                                 "try again in a minute.")
    return {"sent": True, "to": why}
