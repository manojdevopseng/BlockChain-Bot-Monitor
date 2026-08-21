"""Who the account belongs to, what they paid for, and what that buys.

The dashboard began with two accounts out of .env — one admin, one read-only —
because it was one person's control panel. Selling it changes the question from
"may this request write" to "whose data is this, and is their subscription
still running", so that is what this module answers. `users` (the collection an
admin already created accounts in) grows the fields to carry it; nothing that
worked before stops working, and the env admin still logs in when the database
is empty or unreachable.

Three ideas, kept apart on purpose:

  role     admin or user. What the account IS allowed to do.
  plan     which subscription was bought, or `trial`. What it PAID for.
  status   trialing / active / expired / blocked. Whether it may be used today,
           worked out from the dates rather than stored — a stored status is a
           thing that goes stale at midnight and needs a job to fix.

Quotas hang off the plan and are enforced where the work is started, not where
it is displayed: a request that would exceed one is refused, because the cost
of a tracked token is real RPC traffic every fifteen seconds.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

from . import db, users

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

# Roles — unchanged from security.py, repeated here so a caller needs one import.
ADMIN = "admin"
USER = "user"

TRIAL_DAYS = 7


@dataclass(frozen=True)
class Plan:
    """One purchasable plan, and everything the app decides from it.

    The limits are not marketing numbers: a watched token is four RPC requests
    a minute forever, and a tracked RSI token on a one-second interval is
    sixty. They are the shape of the bill.
    """
    id: str
    label: str
    price_usd: float
    days: int
    # What it may use.
    rsi_tokens: int
    mcap_tokens: int
    mcap_checks_per_day: int
    # The fastest cadence this plan may set, in seconds. A trial that could sit
    # on 15s would cost the same as a paid account.
    min_cadence: int
    # The shortest RSI timeframe, in seconds. On RSI this is the bill: a token
    # on 1 Sec is 3,600 reads an hour, one on 5 Min is twelve.
    min_interval: int
    telegram_alerts: bool
    # Alerts a day this plan may be sent, and how long each one waits first.
    #
    # The trial gets real alerts on purpose. It used to get none at all, which
    # meant a person could spend seven days with the product and never once see
    # the thing it is for — the upgrade was described to them rather than felt.
    # It gets few of them, and each arrives a beat late: a late call is still
    # worth reading and is exactly the reason to pay for the on-time one.
    alerts_per_day: int = 25
    alert_delay_seconds: int = 0
    # AI fact-checks a day. Its own allowance rather than sharing the market-cap
    # one, because it is a different bill: a market cap check is an RPC call,
    # a fact-check is a model call and costs real money per press.
    ai_checks_per_day: int = 10
    support_hours: int = 48
    note: str = ""


PLANS: dict[str, Plan] = {
    # Telegram is the paid half. The trial shows the whole dashboard and lets
    # somebody add tokens of their own, but nothing reaches their phone — that
    # is the line between looking and subscribing, and it is drawn here rather
    # than in a check beside every send: alert_target, the connect route and
    # the Profile card all read this one flag.
    "trial": Plan("trial", "7-day Trial", 0.0, TRIAL_DAYS,
                  rsi_tokens=3, mcap_tokens=3, mcap_checks_per_day=25,
                  min_cadence=300, min_interval=300,
                  telegram_alerts=False, alerts_per_day=0,
                  alert_delay_seconds=45,
                  ai_checks_per_day=10, support_hours=72,
                  note="Everything readable and a few tokens of your own. "
                       "Telegram alerts come with a paid plan."),
    "monthly": Plan("monthly", "Monthly", 29.99, 30,
                    rsi_tokens=25, mcap_tokens=25, mcap_checks_per_day=300,
                    min_cadence=15, min_interval=60,
                    telegram_alerts=True, alerts_per_day=300,
                    ai_checks_per_day=100, support_hours=24),
    "half": Plan("half", "6 Months", 149.99, 182,
                 rsi_tokens=50, mcap_tokens=50, mcap_checks_per_day=600,
                 min_cadence=15, min_interval=15,
                 telegram_alerts=True, alerts_per_day=500,
                 ai_checks_per_day=250, support_hours=24,
                 note="Five months' price for six."),
    "yearly": Plan("yearly", "Yearly", 299.99, 365,
                   rsi_tokens=100, mcap_tokens=100, mcap_checks_per_day=1500,
                   min_cadence=15, min_interval=5,
                   telegram_alerts=True, alerts_per_day=800,
                   ai_checks_per_day=600, support_hours=12,
                   note="Ten months' price for twelve."),
}

# What an admin gets: the same shape, no ceilings. Written as a plan so no code
# anywhere needs an "if admin" beside every limit.
ADMIN_PLAN = Plan("admin", "Admin", 0.0, 36500,
                  rsi_tokens=10_000, mcap_tokens=10_000,
                  mcap_checks_per_day=100_000, min_cadence=15, min_interval=1,
                  telegram_alerts=True, alerts_per_day=100_000,
                  ai_checks_per_day=100_000, support_hours=0)


def _col():
    return db.get_collection("users")


# ── the account itself ───────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def public(doc: dict) -> dict:
    """An account as its owner may see it. Never the hash, never a token."""
    plan = plan_of(doc)
    state = access(doc)
    return {
        "username": doc.get("username", ""),
        "email": doc.get("email", ""),
        "role": doc.get("role", USER),
        "email_verified": bool(doc.get("email_verified")),
        "unlimited": bool(doc.get("unlimited")),
        "plan": plan.id,
        "plan_label": plan.label,
        "status": state.status,
        "days_left": state.days_left,
        "expires_at": state.expires_at,
        "telegram_linked": bool(doc.get("telegram_chat_id")),
        # An account from before subscriptions existed, kept working on the
        # house. Its expiry is a placeholder decades out, so the UI has to be
        # told not to print it as if it were a real date.
        "comped": bool(doc.get("comped")),
        "created_at": doc.get("created_at"),
        "limits": {
            "rsi_tokens": plan.rsi_tokens,
            "mcap_tokens": plan.mcap_tokens,
            "mcap_checks_per_day": plan.mcap_checks_per_day,
            "ai_checks_per_day": plan.ai_checks_per_day,
            "min_cadence": plan.min_cadence,
            "min_interval": plan.min_interval,
            "telegram_alerts": plan.telegram_alerts,
            "alerts_per_day": plan.alerts_per_day,
            "alert_delay_seconds": plan.alert_delay_seconds,
        },
    }


def plan_of(doc: dict) -> Plan:
    """What this account may use. Not what it may operate.

    `unlimited` is the operator handing somebody the ceilings without the keys:
    the same limits an admin has — every token, every check, every alert — on
    an account whose role is still `user`. That distinction is the whole point.
    Roles decide what is reachable; plans decide how much of it. Raising the
    plan cannot expose Settings or the Forwarder, because nothing reads the
    plan to answer that question — see security.require_admin and the nav's
    hideFromUser, both of which read the role and only the role.

    So the person gets a yearly plan with no ceiling, and still cannot change
    an endpoint, a keyword or anybody else's account.
    """
    if doc.get("role") == ADMIN or doc.get("unlimited"):
        return ADMIN_PLAN
    return PLANS.get(str(doc.get("plan") or "trial"), PLANS["trial"])


@dataclass
class Access:
    """Whether this account may be used right now, and for how much longer."""
    status: str            # trialing | active | expired | blocked | unverified
    days_left: int = 0
    expires_at: float = 0.0
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.status in ("trialing", "active")


def access(doc: dict) -> Access:
    """Worked out from the dates every time it is asked.

    Deliberately not a stored field: a subscription that ended at 2am would
    otherwise stay `active` until something remembered to run.
    """
    if doc.get("role") == ADMIN:
        return Access("active", days_left=36500, expires_at=_now() + 36500 * 86400)
    if doc.get("blocked"):
        return Access("blocked", reason=str(doc.get("blocked_reason") or
                                            "This account has been suspended"))
    if not doc.get("email_verified", False):
        return Access("unverified", reason="Confirm your email address to start "
                                           "your trial")
    ends = float(doc.get("plan_ends_at") or 0)
    left = ends - _now()
    if left <= 0:
        return Access("expired", expires_at=ends,
                      reason=("Your trial has ended"
                              if str(doc.get("plan")) == "trial"
                              else "Your subscription has ended"))
    status = "trialing" if str(doc.get("plan")) == "trial" else "active"
    return Access(status, days_left=int(left // 86400) + 1, expires_at=ends)


# ── registration ─────────────────────────────────────────────────────────────

def validate_signup(username: str, email: str, password: str) -> Optional[str]:
    """Why this sign-up cannot be accepted, in words meant for the person."""
    problem = users.validate(username, password)
    if problem:
        return problem
    if not EMAIL_RE.match((email or "").strip().lower()):
        return "That does not look like an email address"
    return None


async def register(username: str, email: str, password: str) -> dict:
    """Create an account, unverified, with no trial running yet.

    The trial starts when the email is confirmed, not here: an unconfirmed
    address is how one person takes twenty trials.
    """
    username = username.strip()
    email = email.strip().lower()
    if await _col().find_one({"username": username}):
        raise ValueError("That username is taken")
    if await _col().find_one({"email": email}):
        raise ValueError("An account with that email already exists")
    doc = {
        "username": username,
        "email": email,
        "password": users.hash_password(password),
        "role": USER,
        "enabled": True,
        "plan": "trial",
        # Both zero until the email is confirmed — see start_trial.
        "plan_started_at": 0.0,
        "plan_ends_at": 0.0,
        "email_verified": False,
        "verify_token": secrets.token_urlsafe(32),
        "created_at": _now(),
        "trial_used": False,
    }
    await _col().insert_one(doc)
    return doc


async def verify_email(token: str) -> Optional[dict]:
    """Confirm an address and start the trial in the same step."""
    doc = await _col().find_one({"verify_token": (token or "").strip()})
    if not doc:
        return None
    await _col().update_one({"username": doc["username"]},
                            {"$set": {"email_verified": True},
                             "$unset": {"verify_token": ""}})
    return await start_trial(doc["username"])


async def start_trial(username: str) -> Optional[dict]:
    """Give this account its one trial. Idempotent — a second call does nothing.

    `trial_used` is what makes it one: re-registering the same email is blocked
    by the unique address, and re-confirming cannot extend anything.

    It also never shortens. An account can be put on a plan while its address is
    still unconfirmed — granted for a cash payment, say — and confirming it
    afterwards used to overwrite that plan with a seven-day trial, taking away
    something already paid for. So the trial is only started when there is
    nothing running, and only extends when it would end later than what is.
    """
    doc = await _col().find_one({"username": username})
    if not doc:
        return None
    if doc.get("trial_used"):
        return doc
    now = _now()
    trial_ends = now + TRIAL_DAYS * 86400
    running = float(doc.get("plan_ends_at") or 0)

    changed: dict = {"trial_used": True}
    if running <= now:
        # The ordinary case: a fresh sign-up confirming its address.
        changed.update({"plan": "trial", "plan_started_at": now,
                        "plan_ends_at": trial_ends})
    elif trial_ends > running:
        # Something is running but ends sooner than the trial would. Take the
        # longer of the two and leave the plan it is on alone.
        changed["plan_ends_at"] = trial_ends
    # Otherwise a longer plan is already there. Marking the trial used is all
    # this does — starting it would cost the account days it already has.

    await _col().update_one({"username": username}, {"$set": changed})
    return await _col().find_one({"username": username})


async def activate(username: str, plan_id: str,
                   source: str = "order") -> Optional[dict]:
    """Put an account on a paid plan, or extend the one it is on.

    Extends from whichever is later, now or the current expiry, so paying early
    never costs the days already bought.
    """
    plan = PLANS.get(plan_id)
    doc = await _col().find_one({"username": username})
    if plan is None or doc is None:
        return None
    # A comped account (see migrations._keep_existing_accounts) carries an
    # expiry decades out, so extending from it would bury the days just paid
    # for under a date nobody will live to see — one real payment came out as
    # "runs to 31/01/2101". Paying turns the comp into a real subscription
    # instead, starting now, and the comp is dropped so it cannot happen twice.
    comped = bool(doc.get("comped"))
    base = _now() if comped else max(_now(), float(doc.get("plan_ends_at") or 0))
    ops: dict = {"$set": {"plan": plan.id, "plan_started_at": _now(),
                          "plan_ends_at": base + plan.days * 86400,
                          "last_activation": {"plan": plan.id, "at": _now(),
                                              "source": source}}}
    if comped:
        ops["$unset"] = {"comped": "", "comped_reason": ""}
    await _col().update_one({"username": username}, ops)
    return await _col().find_one({"username": username})


async def by_username(username: str) -> Optional[dict]:
    return await _col().find_one({"username": username})


async def by_email(email: str) -> Optional[dict]:
    return await _col().find_one({"email": (email or "").strip().lower()})


# ── password reset ───────────────────────────────────────────────────────────

RESET_TTL = 3600.0


async def begin_reset(email: str) -> Optional[tuple[dict, str]]:
    """(account, token) for a real address, None otherwise.

    The caller answers the same way either way — telling a stranger which
    addresses have accounts is a favour to whoever is guessing.
    """
    doc = await by_email(email)
    if not doc:
        return None
    token = secrets.token_urlsafe(32)
    await _col().update_one({"username": doc["username"]},
                            {"$set": {"reset_token": token,
                                      "reset_at": _now()}})
    return doc, token


async def finish_reset(token: str, password: str) -> Optional[dict]:
    doc = await _col().find_one({"reset_token": (token or "").strip()})
    if not doc or _now() - float(doc.get("reset_at") or 0) > RESET_TTL:
        return None
    problem = users.validate(doc["username"], password)
    if problem:
        raise ValueError(problem)
    await _col().update_one({"username": doc["username"]},
                            {"$set": {"password": users.hash_password(password)},
                             "$unset": {"reset_token": "", "reset_at": ""}})
    return doc


# ── quotas ───────────────────────────────────────────────────────────────────

@dataclass
class Usage:
    """What this account is using of what it is allowed."""
    used: int = 0
    limit: int = 0
    field: str = ""

    @property
    def room(self) -> bool:
        return self.used < self.limit


async def count_owned(collection: str, username: str) -> int:
    return await db.get_collection(collection).count_documents(
        {"user_id": username})


# What a deleted account leaves behind that must go with it. Two different
# reasons, and both matter:
#
#   rsi_tokens / mcap_tokens   they cost money. The trackers read every watched
#                              token every cadence and never ask whose it is,
#                              so a deleted account's tokens would be polled
#                              for ever — a bill with nobody attached to it.
#   the rest                   they are that person's, and keeping them after
#                              the account is gone is keeping data we have no
#                              reason to hold.
_OWNED_COLLECTIONS = ("rsi_tokens", "mcap_tokens", "alert_subs",
                      "notifications", "usage_daily")
# Same idea, different key: the bind tokens are filed under `username`.
_OWNED_BY_USERNAME = ("telegram_links",)


async def purge_account_data(username: str) -> dict:
    """Delete everything an account owned. Returns what went, per collection.

    Orders and tickets are deliberately NOT here. An order is the record of
    money that moved and a ticket is correspondence about it; both have to
    outlive the login, and neither costs anything to keep.
    """
    gone: dict[str, int] = {}
    for name, key in ([(c, "user_id") for c in _OWNED_COLLECTIONS]
                      + [(c, "username") for c in _OWNED_BY_USERNAME]):
        try:
            res = await db.get_collection(name).delete_many({key: username})
            if res.deleted_count:
                gone[name] = res.deleted_count
        except Exception:  # noqa: BLE001
            # One unreadable collection must not leave the rest behind.
            continue
    return gone


async def check_room(doc: dict, what: str) -> Usage:
    """Whether this account may add one more of `what` (rsi | mcap)."""
    plan = plan_of(doc)
    collection, limit = ({"rsi": ("rsi_tokens", plan.rsi_tokens),
                          "mcap": ("mcap_tokens", plan.mcap_tokens)}[what])
    return Usage(used=await count_owned(collection, doc.get("username", "")),
                 limit=limit, field=what)


async def note_check(username: str, what: str = "mcap") -> int:
    """Record one on-demand check; returns today's total for that kind.

    Counted per IST day in one document per account, so the daily allowance is
    a lookup rather than a scan of anything. `what` picks the counter — market
    cap checks and AI fact-checks share the document and not the allowance,
    because they are two different bills.
    """
    from .util import ist_date_str
    field = _CHECK_FIELDS[what]
    day = ist_date_str(_now())
    res = await db.get_collection("usage_daily").find_one_and_update(
        {"user_id": username, "day": day},
        {"$inc": {field: 1}, "$set": {"updated_at": _now()}},
        upsert=True, return_document=True,
    )
    return int((res or {}).get(field) or 1)


async def checks_today(username: str, what: str = "mcap") -> int:
    from .util import ist_date_str
    doc = await db.get_collection("usage_daily").find_one(
        {"user_id": username, "day": ist_date_str(_now())}) or {}
    return int(doc.get(_CHECK_FIELDS[what]) or 0)


# The counter each kind of check increments. `mcap_checks` keeps its name from
# when it was the only one, so no existing row needs migrating.
_CHECK_FIELDS = {"mcap": "mcap_checks", "ai": "ai_checks"}
