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
             "ai_checks_per_day": p.ai_checks_per_day,
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
    # One count per thing the product does, over the same 24 hours. The time
    # field differs by collection because they were written years apart; that
    # is a fact about the database, not something to paper over with a guess.
    windows = [
        ("launches_24h", "launchpad_tokens", "open_timestamp", {}),
        ("accounts_named_24h", "launchpad_tokens", "open_timestamp",
         {"handle": {"$nin": [None, ""]}}),
        ("gas_hits_24h", "gas_alerts", "created_at", {}),
        ("calls_24h", "premium_detections", "ts", {}),
        ("alerts_24h", "alerts", "created_at", {}),
    ]
    day_ago = time.time() - 86400
    for key, coll, field, extra in windows:
        try:
            out[key] = await db.get_collection(coll).count_documents(
                {field: {"$gte": day_ago}, **extra})
        except Exception:  # noqa: BLE001
            continue
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


# What changed, newest first. Kept in code so it ships with the change it
# describes — a changelog maintained somewhere else is a changelog that lags.
CHANGELOG: list[dict] = [
    {"date": "2026-08-17", "title": "Alert Rules — the alerts became yours",
     "items": ["Every feed a launch can come from now reaches your own "
               "Telegram, not just RSI and Market Cap",
               "Your own keywords, watch list and skip list, matched on the "
               "account's bio",
               "Filter by chain, launchpad, follower count, X account and "
               "Strong-Signal dev buys",
               "Quiet hours, a daily ceiling, and a digest instead of one "
               "message each",
               "The trial now gets real Telegram alerts — 25 a day, 45 seconds "
               "behind live",
               "\"Why am I not getting alerts?\" answers itself from the "
               "launches that actually happened",
               "AI fact-check works on any plan, with its own daily allowance"]},
    {"date": "2026-08-16", "title": "Strong Signal on a big dev buy",
     "items": ["A deployer buying more than 0.199 Ξ of their own launch is "
               "marked on the row and leads the Telegram alert",
               "Gas fees are now watched for 30 minutes after the first buy, "
               "not four"]},
    {"date": "2026-08-14", "title": "Accounts, plans and payment",
     "items": ["Sign up, 7-day trial, and per-account data throughout",
               "USDT and USDC on Solana, BNB Chain and Ethereum",
               "Alerts to your own Telegram chat rather than a shared group",
               "Support requests that carry their own diagnostics"]},
    {"date": "2026-08-14", "title": "LetsCash, and V4 pricing everywhere",
     "items": ["LetsCash added as the sixth Robinhood launchpad",
               "Uniswap V4 pools priced on RBH, ETH and BSC — including hooked "
               "launchpad pools, which most tools cannot read",
               "Market Cap Alert and on-demand Market Cap Check"]},
    {"date": "2026-08-13", "title": "Solana on-chain discovery is a switch",
     "items": ["Turn discovery off without touching the market-cap stream",
               "Gas endpoints rotate and say so when all of them are spent"]},
]


@router.get("/changelog")
async def changelog():
    return {"items": CHANGELOG}


@router.get("/status")
async def status():
    """Is it working — answered coarsely, on purpose.

    A public status page that names workers and endpoints is a map of what to
    attack. This says which parts of the PRODUCT are up, in the words the
    product uses, and nothing about how they are wired.
    """
    from .. import supervisor
    from ..scanners import scfg

    def state(ok: bool, degraded: bool = False) -> str:
        return "operational" if ok else ("degraded" if degraded else "down")

    try:
        diag = supervisor.diagnostics()
        workers = diag.get("workers") or {}
    except Exception:  # noqa: BLE001
        workers = {}

    components = [
        {"name": "Launch detection (Robinhood)",
         "status": state(bool(workers.get("rbhx")) and supervisor.rpc_connected("rbhx"),
                         bool(workers.get("rbhx")))},
        {"name": "Solana feed", "status": state(bool(workers.get("sol")))},
        {"name": "RSI tracker", "status": state(bool(workers.get("rsi")))},
        {"name": "Market cap alerts", "status": state(bool(workers.get("mcap")))},
        {"name": "Telegram alerts",
         "status": state(bool(scfg.TELEGRAM_BOT_TOKEN_SET))},
        {"name": "Payments", "status": state(bool(payments.available()))},
    ]
    worst = ("down" if any(c["status"] == "down" for c in components)
             else "degraded" if any(c["status"] == "degraded" for c in components)
             else "operational")
    return {"overall": worst, "components": components,
            "uptime_seconds": supervisor.uptime_seconds(),
            "checked_at": time.time()}
