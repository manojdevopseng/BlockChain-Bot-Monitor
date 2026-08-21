"""What each account wants told to it, and whether one event qualifies.

The panels are shared — one bot watches the chains and everybody reads the same
rows. What is not shared is which of those rows is worth a person's phone
buzzing, and that is what a subscription is: one document per account saying
which feeds, which chains, which launchpads, how many followers, whose accounts,
which words, and when not to be disturbed.

Kept apart from the sending on purpose. This module answers "does this event
belong to this account" and nothing else — no Telegram, no queues, no clock
beyond the quiet hours the subscriber set. `alert_dispatch` does the sending,
and the workers stay unaware of both.

The shape of an event is deliberately small (see `Event`): every feed already
builds its own message, so the fan-out decides *who*, never *what*.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import db
from .keywords import match_any
from .util import IST

# The feeds an account can subscribe to. The id is what the worker passes to
# `deliver`; the label is what the page shows.
# One event is only ever put on one of these — a launch that is both a
# launchpad mint and carries an X account is a launchpad event, or subscribing
# to two feeds would mean two messages about the same token.
FEEDS: dict[str, str] = {
    "launchpad": "Launchpad launches",
    "rbhx": "Robinhood × X (not from a launchpad)",
    "gas": "High gas early buys",
    "sol": "Cross-chain matches (SOL → ETH/RBH)",
    "calls": "Premium group calls",
}

# Chains an event can carry. Kept as ids the rest of the app already uses —
# which is exactly what this got wrong: BNB was "bsc" here and "bnb" in the
# events, so every BNB premium call was filtered out of every subscription with
# "bnb is not in your chains", a reason naming an id no subscriber could ever
# have chosen.
CHAINS: dict[str, str] = {"eth": "Ethereum", "rbh": "Robinhood",
                          "bnb": "BNB Chain", "sol": "Solana"}

# Subscriptions written while it was "bsc" still say so. Read as the same chain
# rather than rewritten: a stored list is somebody's setting, and quietly
# editing their settings to correct our own spelling is the worse fix.
_CHAIN_ALIASES: dict[str, str] = {"bsc": "bnb"}


def chain_id(chain: str) -> str:
    """The canonical id for a chain, whichever spelling arrived."""
    return _CHAIN_ALIASES.get(chain, chain)

MODES = ("instant", "digest")
DIGEST_CHOICES = (5, 15, 30, 60)

# A ceiling on the plans' ceilings: whatever an account sets and whatever plan
# it is on, it cannot be sent more than this many alerts a day. High-volume
# feeds are hundreds a day on their own, and an account that sets no filters at
# all should still not be sent all of them. Above every plan on purpose — the
# plan is the number that normally bites, this one is the backstop.
HARD_DAILY_CAP = 1000


@dataclass
class Event:
    """One thing that happened, in the only shape the fan-out cares about.

    `text` and `buttons` are the message the worker has already built — the
    subscriber gets the same words the operator's group gets. Everything else
    is here to be filtered on.
    """
    feed: str
    text: str
    chain: str = ""
    address: str = ""
    symbol: str = ""
    handle: str = ""
    followers: int = 0
    launchpad: str = ""
    dev_buy_eth: float = 0.0
    strong: bool = False
    watched: bool = False
    excerpt: str = ""
    matched_keywords: str = ""
    # Inline-keyboard rows, already built by tgstyle.keyboard — the subscriber
    # gets the same buttons the operator's group gets.
    keyboard: list = field(default_factory=list)


DEFAULTS: dict = {
    "enabled": True,
    # Off by default, every one of them. An account that has just connected
    # Telegram should get silence until it says what it wants — the opposite
    # costs it three hundred messages on its first day.
    "feeds": {key: False for key in FEEDS},
    "chains": list(CHAINS),
    "launchpads": [],          # empty = every launchpad
    "min_followers": 0,
    "with_x_only": False,
    "strong_dev_buy_only": False,
    "keywords": [],
    "keywords_only": False,
    "watch_handles": [],
    "watch_only": False,
    "skip_handles": [],
    "quiet_from": "",          # "23:00", IST, blank = never quiet
    "quiet_to": "",
    "mode": "instant",
    "digest_minutes": 15,
    "daily_cap": 150,
}


def _col():
    return db.get_collection("alert_subs")


def blank(username: str) -> dict:
    return {"user_id": username, **DEFAULTS}


async def get(username: str) -> dict:
    """This account's subscription, with every missing field defaulted.

    Defaulted on read rather than migrated on write: a field added to DEFAULTS
    is live for every existing subscriber the moment it is added, and an
    account that has never opened the page still has a usable one.
    """
    doc = await _col().find_one({"user_id": username}) or {}
    out = blank(username)
    for key, value in DEFAULTS.items():
        if key in doc and doc[key] is not None:
            out[key] = doc[key]
    # `feeds` is a dict and must be merged rather than replaced, or a feed
    # added later would be missing from every stored document.
    out["feeds"] = {**DEFAULTS["feeds"], **(doc.get("feeds") or {})}
    out["updated_at"] = doc.get("updated_at")
    return out


def clean(payload: dict, plan_cap: int) -> dict:
    """A subscription as it may be stored, from whatever the page sent.

    Everything is bounded here rather than trusted: this is the one document a
    customer writes that decides how much traffic the box sends on their
    behalf.
    """
    out: dict = {}
    if "enabled" in payload:
        out["enabled"] = bool(payload["enabled"])
    if "feeds" in payload:
        got = payload["feeds"] or {}
        out["feeds"] = {key: bool(got.get(key)) for key in FEEDS}
    if "chains" in payload:
        # Normalised on the way in, so a subscription saved once stops carrying
        # the old spelling forward.
        out["chains"] = [chain_id(c) for c in (payload["chains"] or [])
                         if chain_id(c) in CHAINS]
    if "launchpads" in payload:
        out["launchpads"] = [str(p)[:32] for p in (payload["launchpads"] or [])][:20]
    if "min_followers" in payload:
        out["min_followers"] = max(0, min(10_000_000,
                                          int(payload["min_followers"] or 0)))
    for flag in ("with_x_only", "strong_dev_buy_only", "keywords_only",
                 "watch_only"):
        if flag in payload:
            out[flag] = bool(payload[flag])
    for listy in ("keywords", "watch_handles", "skip_handles"):
        if listy in payload:
            seen: list[str] = []
            for raw in (payload[listy] or []):
                word = str(raw).strip().lstrip("@")[:64]
                if word and not any(word.lower() == s.lower() for s in seen):
                    seen.append(word)
            out[listy] = seen[:100]
    for when in ("quiet_from", "quiet_to"):
        if when in payload:
            out[when] = _clean_clock(payload[when])
    if "mode" in payload:
        out["mode"] = (str(payload["mode"]) if str(payload["mode"]) in MODES
                       else "instant")
    if "digest_minutes" in payload:
        want = int(payload["digest_minutes"] or 15)
        out["digest_minutes"] = min(DIGEST_CHOICES, key=lambda c: abs(c - want))
    if "daily_cap" in payload:
        out["daily_cap"] = max(1, min(plan_cap, int(payload["daily_cap"] or 1)))
    return out


def _clean_clock(value) -> str:
    """"23:00" or "". Anything unparseable becomes "", which means no quiet
    hours — a bad clock must not silence an account by accident."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        hour, minute = raw.split(":")
        h, m = int(hour), int(minute)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    except (ValueError, AttributeError):
        pass
    return ""


async def save(username: str, changes: dict) -> dict:
    await _col().update_one(
        {"user_id": username},
        {"$set": {**changes, "user_id": username, "updated_at": time.time()}},
        upsert=True)
    return await get(username)


async def all_active() -> list[dict]:
    """Every subscription that can actually be sent to right now.

    Read whole rather than queried per event: the dispatcher re-reads this on a
    timer and matches in memory, because an event arriving every few seconds
    must not become a database query per subscriber per event.

    "Can be sent to" is decided here and not at the send: the account has to be
    switched on, its subscription running, and its plan has to include Telegram
    at all. A subscription outliving the subscription that paid for it is the
    one bug in this whole path that costs money rather than noise.

    The plan travels on the subscription — cap, delay, label — so the dispatcher
    never has to look an account up in the middle of a send.
    """
    from . import accounts
    raw: list[dict] = []
    async for doc in _col().find({"enabled": {"$ne": False}}):
        sub = blank(str(doc.get("user_id") or ""))
        for key, value in DEFAULTS.items():
            if key in doc and doc[key] is not None:
                sub[key] = doc[key]
        sub["feeds"] = {**DEFAULTS["feeds"], **(doc.get("feeds") or {})}
        if sub["user_id"]:
            raw.append(sub)
    if not raw:
        return []

    names = [s["user_id"] for s in raw]
    people = {}
    async for user in db.get_collection("users").find({"username": {"$in": names}}):
        people[str(user.get("username"))] = user

    out = []
    for sub in raw:
        user = people.get(sub["user_id"])
        if user is None or not user.get("enabled", True):
            continue
        plan = accounts.plan_of(user)
        if not plan.telegram_alerts or not accounts.access(user).usable:
            continue
        sub["plan"] = {"id": plan.id, "label": plan.label,
                       "alerts_per_day": plan.alerts_per_day,
                       "delay_seconds": plan.alert_delay_seconds}
        # Whatever they set, the plan is the ceiling — a plan that changes down
        # takes the cap with it without anyone reopening the page.
        sub["daily_cap"] = min(int(sub.get("daily_cap") or 0) or plan.alerts_per_day,
                               plan.alerts_per_day)
        out.append(sub)
    return out


def in_quiet_hours(sub: dict, now: Optional[float] = None) -> bool:
    """Is this subscriber asleep right now, by their own clock?

    Handles the normal case — 23:00 to 07:00 — which spans midnight and would
    read as "never" under a plain from <= now <= to.
    """
    start, end = sub.get("quiet_from") or "", sub.get("quiet_to") or ""
    if not start or not end or start == end:
        return False
    at = datetime.fromtimestamp(now if now is not None else time.time(), IST)
    minute = at.hour * 60 + at.minute

    def mins(clock: str) -> int:
        h, m = clock.split(":")
        return int(h) * 60 + int(m)

    a, b = mins(start), mins(end)
    return (a <= minute < b) if a < b else (minute >= a or minute < b)


def matches(sub: dict, event: Event) -> tuple[bool, str]:
    """(does this subscriber want this event, why not).

    The reason is returned rather than logged so the "why did I get no alert"
    page can say something true about a specific launch instead of listing the
    filters and leaving the person to work it out.
    """
    if not sub.get("feeds", {}).get(event.feed):
        return False, f"the {FEEDS.get(event.feed, event.feed)} feed is off"

    if event.chain:
        want = {chain_id(c) for c in (sub.get("chains") or [])}
        if chain_id(event.chain) not in want:
            return False, (f"{CHAINS.get(chain_id(event.chain), event.chain)} "
                           f"is not in your chains")

    pads = sub.get("launchpads") or []
    if event.launchpad and pads and event.launchpad not in pads:
        return False, f"{event.launchpad} is not one of your launchpads"

    handle = (event.handle or "").lower()
    if handle and any(handle == s.lower().lstrip("@")
                      for s in (sub.get("skip_handles") or [])):
        return False, f"@{event.handle} is on your skip list"

    watch = [w.lower().lstrip("@") for w in (sub.get("watch_handles") or [])]
    on_watch = bool(handle and handle in watch)
    if sub.get("watch_only") and not on_watch:
        return False, "you only want launches from your watch list"

    if sub.get("with_x_only") and not event.handle:
        return False, "it carries no X account"

    floor = int(sub.get("min_followers") or 0)
    if floor and event.followers < floor:
        return False, (f"{event.followers:,} followers is under your "
                       f"{floor:,} floor")

    if sub.get("strong_dev_buy_only") and not event.strong:
        return False, "the dev buy is not a Strong Signal"

    own = sub.get("keywords") or []
    if sub.get("keywords_only"):
        if not own:
            return False, "you asked for keyword matches but have no keywords"
        if not match_any(own, event.excerpt or ""):
            return False, "no keyword of yours is in the bio"

    return True, ""


def hit_keywords(sub: dict, event: Event) -> str:
    """Which of this subscriber's own words are in the bio, for the message."""
    return match_any(sub.get("keywords") or [], event.excerpt or "")
