"""Support requests: what went wrong, said in a way that can be acted on.

A free-text box gets "not working" and nothing else, and then two days go by
asking which page, which token, which chain. So the form is a list of the
things that actually go wrong with THIS product — written from the panels it
has — and the free text is the last option rather than the only one.

What makes a ticket useful is the part nobody types: which page they were on,
what plan they are on, whether the worker behind that panel was even running at
the time, and which endpoints were refusing. That is attached automatically, so
a report costs the person one tap and arrives with its own evidence.

Nothing here is a chat product. A ticket has a thread, a status and an owner,
and the whole of it fits on one screen.
"""

from __future__ import annotations

import secrets
import time
from typing import Optional

from . import db
from .scanners.slog import get_logger

log = get_logger(__name__)

OPEN = "open"
WORKING = "in_progress"
RESOLVED = "resolved"
CLOSED = "closed"

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# The catalogue. `needs` names the one extra thing worth asking for — a token
# address turns "market cap is wrong" from a conversation into a lookup.
PROBLEMS: list[dict] = [
    # ── alerts ──
    {"id": "no_telegram_alerts", "group": "Alerts",
     "label": "Alerts are not arriving on Telegram"},
    {"id": "telegram_connect", "group": "Alerts",
     "label": "Connect Telegram is not working"},
    {"id": "alerts_late", "group": "Alerts",
     "label": "Alerts arrive, but late"},

    # ── the numbers ──
    {"id": "launch_missing", "group": "Data",
     "label": "A launch is missing from Detections", "needs": "address"},
    {"id": "rsi_mismatch", "group": "Data",
     "label": "RSI does not match the chart", "needs": "address"},
    {"id": "mcap_wrong", "group": "Data",
     "label": "Market cap looks wrong, or shows nothing", "needs": "address"},
    {"id": "token_add_failed", "group": "Data",
     "label": "A token will not add", "needs": "address"},
    {"id": "stale_numbers", "group": "Data",
     "label": "Numbers are not updating"},

    # ── account and money ──
    {"id": "payment_not_activated", "group": "Account",
     "label": "I paid and my plan did not start", "needs": "order"},
    {"id": "trial_ended_early", "group": "Account",
     "label": "My trial ended sooner than it should have"},
    {"id": "login_issue", "group": "Account",
     "label": "Trouble signing in or resetting my password"},
    {"id": "plan_change", "group": "Account",
     "label": "I want to change or cancel my plan"},

    # ── the app itself ──
    {"id": "slow", "group": "App", "label": "Pages are slow or time out"},
    {"id": "mobile_layout", "group": "App", "label": "Something is broken on mobile"},
    {"id": "filters_broken", "group": "App", "label": "Search or filters are not working"},
    {"id": "other", "group": "Other", "label": "Something else"},
]

# How urgent each kind is, so the queue sorts itself. Money and access first:
# somebody who paid and cannot get in is a different problem from a wrong
# number on a chart.
PRIORITY = {
    "payment_not_activated": 1, "login_issue": 1, "trial_ended_early": 1,
    "no_telegram_alerts": 2, "telegram_connect": 2, "plan_change": 2,
    "launch_missing": 3, "mcap_wrong": 3, "rsi_mismatch": 3,
    "token_add_failed": 3, "stale_numbers": 3, "alerts_late": 3,
    "slow": 4, "filters_broken": 4, "mobile_layout": 4, "other": 4,
}


def _col():
    return db.get_collection("tickets")


def _id() -> str:
    return "SUP-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))


def catalogue() -> list[dict]:
    return PROBLEMS


async def diagnostics(account: dict, page: str = "") -> dict:
    """What was true when this was reported, gathered rather than asked.

    Half of support is establishing whether the thing was even running. This
    answers that before the question is asked — and it is best-effort
    throughout, because a diagnostic that fails must never stop a person from
    reporting a problem.
    """
    from . import accounts
    out: dict = {"page": page, "at": time.time(),
                 "plan": accounts.plan_of(account).id,
                 "status": accounts.access(account).status,
                 "telegram_linked": bool(account.get("telegram_chat_id"))}
    try:
        from . import supervisor
        diag = supervisor.diagnostics()
        out["workers"] = {k: bool(v) for k, v in (diag.get("workers") or {}).items()}
        out["rpc"] = {chain: supervisor.rpc_connected(chain)
                      for chain in ("eth", "rbh", "rbhx", "sol")}
        out["uptime_seconds"] = supervisor.uptime_seconds()
    except Exception as exc:  # noqa: BLE001
        out["workers_error"] = str(exc)[:120]
    try:
        counts = {}
        for name in ("rsi_tokens", "mcap_tokens"):
            counts[name] = await db.get_collection(name).count_documents(
                {"user_id": account.get("username", "")})
        out["owned"] = counts
    except Exception:  # noqa: BLE001
        pass
    return out


async def create(account: dict, problems: list[str], message: str = "",
                 page: str = "", token: str = "", order: str = "",
                 client: Optional[dict] = None) -> dict:
    """One report. At least one checkbox or some text — never neither."""
    picked = [p for p in problems if p in {x["id"] for x in PROBLEMS}]
    if not picked and not message.strip():
        raise ValueError("Pick what went wrong, or write it in your own words")
    row = {
        "id": _id(),
        "user_id": account.get("username", ""),
        "email": account.get("email", ""),
        "problems": picked,
        "labels": [x["label"] for x in PROBLEMS if x["id"] in picked],
        "message": message.strip()[:4000],
        "token": token.strip()[:64],
        "order": order.strip()[:32],
        "status": OPEN,
        "priority": min([PRIORITY.get(p, 4) for p in picked] or [4]),
        "created_at": time.time(),
        "updated_at": time.time(),
        "diagnostics": await diagnostics(account, page),
        # What the browser knew: the version, the screen, and any errors it had
        # already logged. Sent by the page, so it is claims rather than facts —
        # useful for reproducing, never for deciding.
        "client": client or {},
        "thread": [],
    }
    await _col().insert_one(dict(row))
    log.info(f"[SUPPORT] {row['id']} from {row['user_id']} — "
             f"{', '.join(picked) or 'free text'}")
    await _announce(row)
    return row


async def _announce(row: dict) -> None:
    """Tell the operator, twice, because one of the two will be seen."""
    from . import mailer, notifier
    from .config import settings
    lines = "\n".join(f"• {l}" for l in row["labels"]) or "• (described in words)"
    body = (f"{row['user_id']} <{row.get('email', '')}>\n\n{lines}\n\n"
            f"{row['message'] or ''}\n\n"
            f"page: {row['diagnostics'].get('page') or '—'}\n"
            f"plan: {row['diagnostics'].get('plan')} "
            f"({row['diagnostics'].get('status')})\n"
            f"token: {row.get('token') or '—'}   order: {row.get('order') or '—'}\n")
    await mailer.notify_admin(f"Support {row['id']} — {row['labels'][0] if row['labels'] else 'request'}",
                              body)
    chat = settings.support_chat_id or settings.alert_chat_id
    if chat:
        import html
        await notifier.send_to(
            chat,
            f"🆘 <b>Support {row['id']}</b>\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"👤 {html.escape(row['user_id'])} · plan "
            f"{row['diagnostics'].get('plan')}\n"
            + "".join(f"• {html.escape(l)}\n" for l in row["labels"])
            + (f"\n<i>{html.escape(row['message'][:300])}</i>\n" if row["message"] else "")
            + (f"\n🪙 <code>{html.escape(row['token'])}</code>" if row.get("token") else "")
            + (f"\n🧾 {html.escape(row['order'])}" if row.get("order") else ""))


async def mine(username: str, limit: int = 50) -> list[dict]:
    return await _col().find({"user_id": username}, {"_id": 0}) \
                       .sort("created_at", -1).to_list(limit)


async def get(ticket_id: str, username: Optional[str] = None) -> Optional[dict]:
    flt: dict = {"id": ticket_id}
    if username is not None:
        flt["user_id"] = username
    return await _col().find_one(flt, {"_id": 0})


async def queue(status: Optional[str] = None, limit: int = 200) -> list[dict]:
    """The operator's list: most urgent first, then oldest."""
    flt = {"status": status} if status else {"status": {"$in": [OPEN, WORKING]}}
    return await _col().find(flt, {"_id": 0}) \
                       .sort([("priority", 1), ("created_at", 1)]).to_list(limit)


async def reply(ticket_id: str, author: str, text: str, is_admin: bool,
                status: Optional[str] = None) -> Optional[dict]:
    """Add a message, and move the ticket if asked to."""
    row = await get(ticket_id, None if is_admin else author)
    if row is None:
        return None
    entry = {"at": time.time(), "author": author, "admin": is_admin,
             "text": text.strip()[:4000]}
    update: dict = {"updated_at": time.time()}
    if status in (OPEN, WORKING, RESOLVED, CLOSED):
        update["status"] = status
    elif is_admin and row.get("status") == OPEN:
        # An operator who answers has started: nobody should have to remember
        # to change a dropdown to say so.
        update["status"] = WORKING
    await _col().update_one({"id": ticket_id},
                            {"$push": {"thread": entry}, "$set": update})
    row = await get(ticket_id)
    if is_admin:
        await _tell_buyer(row, entry)
    return row


async def _tell_buyer(row: dict, entry: dict) -> None:
    """An answer is no use sitting on a page nobody has open."""
    from . import mailer, notifications, notifier, telegram_link
    await notifications.notify(
        row.get("user_id", ""), notifications.SUPPORT,
        f"Reply on {row['id']}", entry["text"][:200], f"/support/{row['id']}")
    from .config import settings
    await mailer.send(
        row.get("email", ""), f"Re: your support request {row['id']}",
        f"{entry['text']}\n\n"
        f"Reply on {settings.public_url.rstrip('/')}/support/{row['id']}\n")
    chat = await telegram_link.chat_for(row.get("user_id", ""))
    if chat:
        import html
        await notifier.send_to(
            chat,
            f"💬 <b>Support {row['id']}</b>\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"{html.escape(entry['text'][:600])}")
