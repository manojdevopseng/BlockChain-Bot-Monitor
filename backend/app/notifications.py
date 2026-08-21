"""In-app notices: the same things Telegram says, kept where they happened.

An alert that only exists in Telegram is lost to anybody who has not connected
it, and to everybody who scrolled past it. So every message worth keeping is
also written here, against the account it belongs to, and the bell in the
top bar counts what has not been read.

Deliberately not a second alerting system: nothing decides here what is worth
saying. The places that already decide — a market cap hit, an order activated,
a support reply — call `notify` on their way out, and this only stores it.
"""

from __future__ import annotations

import time
from typing import Optional

from . import db
from .scanners.slog import get_logger

log = get_logger(__name__)

# What a notice is about. The kind drives its icon and nothing else — the text
# is written where it happens, because that is where the facts are.
ALERT = "alert"          # a market cap hit, an RSI crossing
BILLING = "billing"      # an order paid, a plan running out
SUPPORT = "support"      # a reply on a request
SYSTEM = "system"        # anything the operator broadcasts


def _col():
    return db.get_collection("notifications")


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


async def notify(user_id: str, kind: str, title: str, body: str = "",
                 link: str = "", key: str = "") -> None:
    """One notice for one account. Never raises — a notice that cannot be
    written must not take down the thing it was about.

    `key` makes a notice at-most-once: "your plan ends in 3 days" should be one
    row however many times the sweep runs.
    """
    if not user_id:
        return
    try:
        row = {"user_id": user_id, "kind": kind, "title": title[:200],
               "body": body[:1000], "link": link[:200], "read": False,
               "at": time.time(), "dt": _utc_now()}
        if key:
            await _col().update_one({"user_id": user_id, "key": key},
                                    {"$setOnInsert": {**row, "key": key}},
                                    upsert=True)
        else:
            await _col().insert_one(row)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[NOTIFY] could not store a notice for {user_id}: {exc}")


async def recent(user_id: str, limit: int = 30) -> list[dict]:
    return await _col().find({"user_id": user_id}, {"_id": 0}) \
                       .sort("at", -1).to_list(limit)


async def unread(user_id: str) -> int:
    return await _col().count_documents({"user_id": user_id, "read": False})


async def mark_read(user_id: str, before: Optional[float] = None) -> int:
    """Everything, or everything up to a moment — never one at a time.

    A bell that needs each line ticked off is a bell people stop opening.
    """
    flt: dict = {"user_id": user_id, "read": False}
    if before:
        flt["at"] = {"$lte": before}
    res = await _col().update_many(flt, {"$set": {"read": True}})
    return int(res.modified_count)


# ── the one notice nobody else would send ────────────────────────────────────

async def warn_expiring() -> int:
    """Tell accounts their time is running out, once per threshold.

    Three days and one day: enough warning to pay without it becoming nagging.
    Written as a notice AND a Telegram message where one is connected, because
    somebody whose plan lapses while they are not looking at the dashboard is
    exactly the person this is for.
    """
    from . import accounts, telegram_link
    from .config import settings
    now = time.time()
    sent = 0
    rows = await db.get_collection("users").find(
        {"role": {"$ne": accounts.ADMIN},
         "plan_ends_at": {"$gt": now, "$lt": now + 3 * 86400}}).to_list(1000)
    for doc in rows:
        left = float(doc.get("plan_ends_at") or 0) - now
        days = 1 if left <= 86400 else 3
        state = accounts.access(doc)
        if not state.usable:
            continue
        what = "trial" if str(doc.get("plan")) == "trial" else "plan"
        title = (f"Your {what} ends tomorrow" if days == 1
                 else f"Your {what} ends in {state.days_left} days")
        body = ("Buy any plan before it does and the days are added to what you "
                "have — nothing is lost and nothing resets.")
        # The key is the account, the threshold and the expiry date, so moving
        # the expiry (by paying) starts a fresh set rather than staying silent.
        key = f"expiry:{days}:{int(float(doc.get('plan_ends_at') or 0))}"
        before = await _col().count_documents({"user_id": doc["username"],
                                               "key": key})
        await notify(doc["username"], BILLING, title, body, "/plan", key=key)
        if before:
            continue                       # already told, on this threshold
        sent += 1
        chat = await telegram_link.chat_for(doc["username"])
        if chat:
            from . import notifier
            await notifier.send_to(
                chat, f"⏳ <b>{title}</b>\n➖➖➖➖➖➖➖➖➖➖\n{body}")
    if sent:
        log.info(f"[NOTIFY] {sent} expiry warning(s) sent")
    return sent
