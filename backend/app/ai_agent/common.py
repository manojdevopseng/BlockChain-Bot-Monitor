"""Shared foundations: the narrative list, the tunables, and small helpers.

Everything else in this package imports from here and nothing here imports
back, which is what keeps the package acyclic.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from .. import db
from ..config import settings
from ..scanners.slog import get_logger

log = get_logger("app.ai_agent")

TELEGRAM_API = "https://api.telegram.org"


TELEGRAM_API = "https://api.telegram.org"

# The narratives a *post* is checked against — the product owner's list, in
# their order. Grok is asked to pick one or say none.
#
# These are the seed, not the source of truth. They are copied into Mongo on
# first start and edited from Settings after that, so adding one is a click
# rather than a deploy. `narratives()` is what the prompt reads.
DEFAULT_NARRATIVES = [
    "Related to Trump",
    "Related to Elon Musk or his Companies",
    "Any Tech Token",
    "Any Gaming Token",
    "New Product Launch",
    "New AI",
    "New Mascot",
    "New Pet adopted by anyone",
    'New "Token Launchpad"',
    "Related to Ethereum or Vitalik",
    "Viral Content",
    "Any Latest News of any Celebrity or VIP or Influencer",
    "Any Latest news of any animal",
    "Supply or Fees sent to someone or some wallet",
    "Any Big X account Launching Token",
    "Related to SOL Owner or its Employees",
]

# Held in memory because the prompt is built on the hot path and a database
# round trip per launch to read a list of sixteen strings would be silly. Kept
# honest by reloading whenever Settings changes it. Only the switched-on ones
# are in here — a narrative switched off stays on the page but leaves the
# prompt, which is the difference between pausing one and losing it.
_narratives: list[str] = list(DEFAULT_NARRATIVES)


def narratives() -> list[str]:
    """What the model is currently asked to choose between."""
    return _narratives


async def load_narratives(seed: bool = False) -> list[dict]:
    """Every narrative with its switch, newest last. Seeds on first start.

    Returns all of them, on and off, because that is what the page shows; the
    prompt cache it refreshes holds only the ones that are on.
    """
    global _narratives
    col = _col("ai_narratives")
    docs = await col.find({}).sort("order", 1).to_list(200)
    if not docs and seed:
        await col.insert_many([{"text": n, "order": i, "enabled": True,
                                "added_at": time.time()}
                               for i, n in enumerate(DEFAULT_NARRATIVES)])
        docs = await col.find({}).sort("order", 1).to_list(200)
    # `enabled` missing means on: rows written before the switch existed were
    # all in use, and defaulting them off would silently empty the prompt.
    items = [{"text": d["text"], "enabled": d.get("enabled", True)} for d in docs]
    if items:
        _narratives = [i["text"] for i in items if i["enabled"]]
    return items

# Whether the thing is REAL is deliberately not asked on this pass. The model
# does one job here — which narrative, if any — and nothing else. Reality is a
# separate question, asked per token by somebody pressing Fact check: it is the
# answer a person acts on, and it should be asked while they are looking rather
# than bought for the thousands of launches nobody ever opens.

# How many tokens get the full treatment in one pass. The feed returns 100 and
# most are already known; this caps the work when a burst arrives.
MAX_PER_CYCLE = 12
# Of that budget, how many go on re-asking about launches queued while the model
# was unreachable. Small on purpose: the queue is worth draining, but never at
# the price of the launches still coming in.
RETRY_PENDING_PER_CYCLE = 4
# A verdict of "error" means the model could not be reached, not that the token
# was judged — so it is retried. Capped, because a token nothing can classify
# should not be asked about forever.
MAX_ERROR_RETRIES = 5
# A name and ticker gets at most this many rows in one IST day — the first
# launch plus four repeats. The same pair relaunched over and over is the
# commonest spam here, and five is enough to see that it is happening without
# the list becoming a wall of one name. The day boundary is IST midnight, the
# same one the archives and per-day counters use, so "today" means one thing
# across the whole project.
MAX_PER_NAME_PER_DAY = 5

# The OG rule. A name and ticker launched this many times inside this window is
# somebody working at it, not a coincidence — and the one worth keeping is the
# first, before the copies. Only launches carrying an X link count: one without
# a link says nothing about who is behind it, and letting those make up the five
# turned anonymous name-squatting into a signal.
OG_BURST_COUNT = 5
OG_BURST_WINDOW = 300

# X can simply not answer — a timeout, a 429, a bad minute at the mirror. That
# is not the same as an account being unverified, and dropping the launch for it
# loses a real token silently. Retried this many times, this far apart.
X_RETRIES = 3
X_RETRY_DELAY = 20

# Mints kept per drop bucket — per hour, per reason. Enough to answer "why did
# this one not appear" for anything recent without the collection growing with
# the feed: a busy hour drops ~1,600 launches across five reasons.
DROP_MINTS_KEPT = 250

# Verdicts that mean a link's question has already been asked. `error` and
# `skipped` are absent on purpose: neither ever put the post to the model, and
# a link that failed once must not be shut out for good.
SETTLED = ("matched", "launching", "rejected", "pending")

_LINKED_KINDS = ("tweet", "profile")

# name_key -> launches seen inside the window, oldest first. Held in memory: it
# is a minute of traffic, and it must not cost a database round trip per launch.
_recent_launches: dict[str, list[dict]] = {}
_og_promoted: dict[str, float] = {}


# Tasks the event loop is running for us, held so they survive to finish.
#
# asyncio keeps only a weak reference to a task, so one nothing else holds can
# be collected mid-execution — silently, with no exception and no log line.
# `_handle_launch` waits on a semaphore, which is exactly the suspended state
# that gives the collector its chance, and the launches lost that way left no
# trace anywhere: not a row, not a drop, not a log. Measured on one burst, 21 of
# 41 launches on a single link went missing this way.
_inflight: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    """Run a coroutine in the background, and keep hold of it until it is done."""
    task = asyncio.create_task(coro)
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)
    return task


def _col(name: str):
    return db.get_collection(name)


def _utc_now() -> datetime:
    """The BSON date every TTL index in this project keys off."""
    return datetime.now(timezone.utc)


def chat_id() -> str:
    return (settings.ai_chat_id or settings.robinhood_chat_id or "").strip()


def allowed_verification() -> set[str]:
    return {t.strip().lower() for t in settings.ai_verified_types.split(",") if t.strip()}
