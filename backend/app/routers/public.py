"""The pages anybody can see, and the two things they need from the server.

Everything here answers without a login, which makes it the one surface a
stranger can reach — so it hands out only what a price list and a contact form
need, and it counts what it is asked for.

Prices come from the same PLANS the product enforces. A marketing page with its
own copy of the numbers is a page that will one day promise something the app
refuses to give.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Body, HTTPException, Request

from .. import accounts, db, mailer, payments
from ..config import settings
from ..scanners.slog import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/public", tags=["public"])

# What one address may send. Deliberately mean: this is an unauthenticated form
# on a public IP, and the honest volume of it is a few a day.
_CONTACT_PER_HOUR = 3
_seen: dict[str, list[float]] = {}


@router.get("/plans")
async def plans():
    """The price list, from the plans the app actually enforces."""
    return {
        "trial_days": accounts.TRIAL_DAYS,
        "plans": [
            {"id": p.id, "label": p.label, "price_usd": p.price_usd,
             "days": p.days, "note": p.note,
             "rsi_tokens": p.rsi_tokens, "mcap_tokens": p.mcap_tokens,
             "mcap_checks_per_day": p.mcap_checks_per_day,
             "min_cadence": p.min_cadence, "min_interval": p.min_interval,
             "telegram_alerts": p.telegram_alerts,
             "support_hours": p.support_hours}
            for p in accounts.PLANS.values()
        ],
        "pay_with": [
            {"symbol": a.symbol, "label": a.label, "fee_note": a.fee_note}
            for a in payments.available()
        ],
    }


@router.get("/stats")
async def stats():
    """A few honest numbers for the front page.

    Counts of what the scanners have actually recorded — no invented figures,
    and nothing about any individual account.
    """
    from ..scanners.launchpads import all_launchpads
    out = {"launchpads": [p.label for p in all_launchpads()],
           "chains": ["Robinhood", "Ethereum", "BNB Chain", "Solana"]}
    try:
        day_ago = time.time() - 86400
        out["launches_24h"] = await db.get_collection(
            "launchpad_tokens").count_documents({"open_timestamp": {"$gte": day_ago}})
        out["accounts_named_24h"] = await db.get_collection(
            "launchpad_tokens").count_documents(
                {"open_timestamp": {"$gte": day_ago},
                 "handle": {"$nin": [None, ""]}})
    except Exception:  # noqa: BLE001
        pass
    return out


@router.post("/contact")
async def contact(request: Request, payload: dict = Body(...)):
    """A message from somebody who has no account yet.

    Support tickets are for people who are signed in and carry their own
    diagnostics; this is the other door, and it is kept narrow.
    """
    ip = (request.client.host if request.client else "") or "unknown"
    now = time.time()
    recent = [t for t in _seen.get(ip, []) if now - t < 3600]
    if len(recent) >= _CONTACT_PER_HOUR:
        raise HTTPException(429, "That is a few too many messages in an hour — "
                                 "try again later, or email us directly.")
    _seen[ip] = recent + [now]

    name = str(payload.get("name") or "").strip()[:80]
    email = str(payload.get("email") or "").strip()[:120]
    message = str(payload.get("message") or "").strip()[:4000]
    if not message or not accounts.EMAIL_RE.match(email):
        raise HTTPException(400, "A message and a working email address, please")

    row = {"name": name, "email": email, "message": message, "ip": ip,
           "at": now, "handled": False}
    await db.get_collection("contact_messages").insert_one(dict(row))
    log.info(f"[CONTACT] from {email}")

    await mailer.notify_admin(
        f"Contact form — {name or email}",
        f"{name or '(no name)'} <{email}>\n\n{message}\n")
    chat = settings.support_chat_id or settings.alert_chat_id
    if chat:
        import html
        from .. import notifier
        await notifier.send_to(
            chat,
            f"✉️ <b>Contact form</b>\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"{html.escape(name or '(no name)')} · {html.escape(email)}\n\n"
            f"<i>{html.escape(message[:500])}</i>")
    return {"sent": True,
            "message": "Thanks — we read these ourselves and answer by email."}
