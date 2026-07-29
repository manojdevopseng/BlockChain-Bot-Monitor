"""Judge a new pump.fun launch by the X account and post behind it.

pump.fun produces thousands of tokens a day and almost all of them are noise.
What separates the handful worth looking at is not on-chain: it is whether a
real, verified account is behind the launch, and what the post it points at is
actually about. So each launch's X link is read and put to Grok, and a match
becomes an alert.

Two questions, asked in two different places on purpose:

  which narrative — every launch that clears the gates, automatically. One
                    question, one call, sixteen narratives to choose from.
  is it real      — Fact check, per token, when somebody presses the button.
                    It is the answer a person acts on, so it is asked while
                    they are looking rather than bought for the thousands of
                    rows nobody opens.

Separately from either, a launch is watched for its first minute; one from a
link that carried five launches inside five minutes and that crossed the market
cap bar in that minute is what reaches Telegram.

What this deliberately does not do:

  • It never calls gmgn.ai at all. The launches come from PumpPortal's socket
    and the socials from each token's own metadata URI, so the scanners' GMGN
    budget and its hard-won Cloudflare pacing are untouched by anything here.
  • It never makes an AI call it can avoid. One X link gets reused across a
    stream of copycat tokens, so links and names are deduplicated first; the
    reference bot learned that the hard way.
  • It sends nothing while AI_DRY_RUN is on. Decisions are recorded either way,
    so a day of them can be read before it is allowed to post.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from . import db, pump_mcap, x_client
from .scanners.bounded_set import BoundedSet
from .config import settings
from .scanners.slog import get_logger
from .util import esc, ist_date_str

log = get_logger(__name__)

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
# Launching profiles re-checked per pass, oldest check first.
MAX_RECHECK_PER_CYCLE = 6
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


# ── Grok ───────────────────────────────────────────────────────────────────────

def _prompt(token: dict, content: str, profile) -> str:
    listed = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(narratives()))
    return (
        f"Token name: {token.get('name') or '?'}\n"
        f"Token ticker: {token.get('symbol') or '?'}\n"
        f"Posted by: @{profile.handle} ({profile.followers:,} followers, "
        f"verified: {profile.verified_type or 'no'})\n"
        f"Post:\n{content[:1500]}\n\n"
        f"Does this post match one of these narratives?\n{listed}\n\n"
        "Reply with JSON only:\n"
        '{"match": true|false, "narrative": "the matched narrative or none", '
        '"confidence": 1-10, '
        '"summary": "one short line on what it is about", '
        '"red_flags": ["..."]}\n\n'
        "Rules:\n"
        "- match false if none of the narratives fit.\n"
        "- match false for pure hype with no substance (moon, 1000x, buy now, "
        "emoji-only, giveaway spam).\n"
        "- The token name does NOT have to match the narrative. Anyone can "
        "launch a token about any real event.\n"
        "- confidence 8-10 for a clear, current, specific match.\n"
        "- confidence 1-5 for a weak or guessed match."
    )


# ── Fact check ────────────────────────────────────────────────────────────────
# Asked about one token, when somebody presses the button. Kept apart from the
# narrative pass on purpose: it is the answer a person acts on, it costs a call,
# and running it on every launch would spend that call on the thousands nobody
# ever opens.

def _fact_prompt(token: dict, content: str, profile: dict) -> str:
    return (
        f"A token was launched pointing at this X post.\n\n"
        f"Token: {token.get('name') or '?'} (${token.get('symbol') or '?'})\n"
        f"Posted by: @{profile.get('handle') or '?'} "
        f"({int(profile.get('followers') or 0):,} followers, "
        f"verified: {profile.get('verified_type') or 'no'})\n"
        f"Post:\n{content[:1500]}\n\n"
        "Is what this post describes REAL — something that actually happened "
        "and could be checked against the outside world — rather than a claim "
        "the post makes about itself, a rumour, or marketing?\n\n"
        "Reply with JSON only:\n"
        '{"real": "Yes" or "No", '
        '"brief": "2-3 plain sentences a non-expert can read: what the post '
        'says, and why it is or is not real", '
        '"reason": "one short line — the single deciding fact"}\n\n'
        "Rules:\n"
        '- "Yes" only when the thing is real and checkable.\n'
        '- A post that merely announces itself, or announces the token, is "No".\n'
        "- Do not describe the token. Judge the post.\n"
        "- No jargon in `brief`. Write it for somebody who does not follow "
        "crypto."
    )


async def _ask_grok_json(session: aiohttp.ClientSession, prompt: str,
                         system: str, model: str = "") -> Optional[dict]:
    """One call, one JSON object back. None when it fails — never a made-up answer.

    Shared by the narrative pass and by Fact check: two different questions and
    two different models, but one way of asking, so a change to the parsing or
    the error handling lands on both.
    """
    if not settings.xai_api_key:
        return None
    payload = {
        "model": model or settings.xai_model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    url = f"{settings.xai_base_url.rstrip('/')}/chat/completions"
    try:
        async with session.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {settings.xai_api_key}"},
            timeout=aiohttp.ClientTimeout(total=45),
        ) as r:
            body = await r.json(content_type=None)
            if r.status != 200:
                log.warning(f"[AI] Grok {r.status}: {str(body)[:200]}")
                return None
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[AI] Grok call failed: {exc}")
        return None

    try:
        text = body["choices"][0]["message"]["content"]
        return json.loads(text.replace("```json", "").replace("```", "").strip())
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[AI] could not parse Grok reply: {exc}")
        return None


async def ask_grok(session: aiohttp.ClientSession, token: dict,
                   content: str, profile) -> Optional[dict]:
    """Which narrative, if any. None when the call fails."""
    return await _ask_grok_json(
        session, _prompt(token, content, profile),
        "You classify crypto token launches by the narrative behind them. "
        "Reply with JSON only — no markdown, no commentary.")


async def fact_check(address: str, force: bool = False) -> dict:
    """Is the post behind this launch about something real? Asked on a click.

    The answer is stored on the decision, so opening the same token again is
    free and the judgement survives a reload. `force` asks again, which is worth
    having when the post has changed or the first answer was plainly wrong.
    """
    dec = await _col("ai_decisions").find_one(
        {"address": address},
        {"address": 1, "symbol": 1, "name": 1, "handle": 1, "followers": 1,
         "verified_type": 1, "excerpt": 1, "fact": 1})
    if not dec:
        return {"ok": False, "error": "no decision for that address"}
    if dec.get("fact") and not force:
        return {"ok": True, "cached": True, **dec["fact"]}

    text = (dec.get("excerpt") or "").strip()
    if not text:
        return {"ok": False, "error": "there is no post text to check"}

    token = {"name": dec.get("name"), "symbol": dec.get("symbol")}
    profile = {"handle": dec.get("handle"), "followers": dec.get("followers"),
               "verified_type": dec.get("verified_type")}
    async with aiohttp.ClientSession() as session:
        answer = await _ask_grok_json(
            session, _fact_prompt(token, text, profile),
            "You fact-check claims made in social media posts. You are "
            "careful, you answer No when something cannot be verified, and you "
            "write for somebody who does not follow crypto.",
            # A reasoning model here, a fast one on the narrative pass. This
            # runs on a click a few times a day, so its extra tokens cost
            # nothing worth counting, and "is this real" is the judgement that
            # benefits from the model thinking before it answers.
            model=settings.xai_fact_model or settings.xai_model)
    if not answer:
        return {"ok": False,
                "error": "the model could not be reached — check the xAI key "
                         "and its credits"}

    real = str(answer.get("real") or "").strip().lower().startswith("y")
    fact = {"real": "Yes" if real else "No",
            "brief": str(answer.get("brief") or "")[:600],
            "reason": str(answer.get("reason") or "")[:200],
            "at": time.time()}
    await _col("ai_decisions").update_one({"address": address},
                                          {"$set": {"fact": fact}})
    log.info(f"[AI] FACT {dec.get('symbol')} — {fact['real']}: "
             f"{fact['reason'][:60]}")
    return {"ok": True, "cached": False, **fact}


# ── Decisions ──────────────────────────────────────────────────────────────────

async def _record(token: dict, row, profile, verdict: str, detail: dict) -> None:
    """Every judgement is written, including the skips.

    A dashboard that only shows what fired cannot answer "why did it ignore
    that one", which is the question actually worth asking of a filter.
    """
    try:
        await _col("ai_decisions").update_one(
            {"address": token["address"]},
            {"$set": {
                "address": token["address"], "symbol": token.get("symbol"),
                "name": token.get("name"), "handle": getattr(profile, "handle", ""),
                "name_key": (f"{(token.get('name') or '').lower().strip()}|"
                             f"{(token.get('symbol') or '').lower().strip()}"),
                "verified": getattr(profile, "verified", False),
                "verified_type": getattr(profile, "verified_type", ""),
                "followers": getattr(profile, "followers", 0),
                "kind": (row or {}).get("kind", "none"),
                "link": (row or {}).get("link", ""),
                "excerpt": (row or {}).get("excerpt", ""),
                # When the token launched, so Age here means the same thing it
                # means in the live section: time since launch, not time since
                # we got round to judging it.
                "open_timestamp": (row or {}).get("open_timestamp"),
                "verdict": verdict, **detail,
                "at": time.time(), "dt": _utc_now(),
                # The IST day this was decided — the same boundary the archives
                # and the launch sections use, so "today" means one thing.
                "day": ist_date_str(time.time()),
            },
             "$inc": {"tries": 1}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[AI] could not record decision: {exc}")
        return
    # Asked here rather than at each of the gates: every launch that reaches
    # Decisions is eligible, and a check placed on one path is a check the other
    # paths silently do not get.
    await _check_telegram(None, token["address"])


# ── Telegram ───────────────────────────────────────────────────────────────────

def _gmgn(address: str) -> str:
    return f"https://gmgn.ai/sol/token/{address}"


def _message(heading: str, token: dict, address: str, lines: list[str]) -> str:
    body = "\n".join(lines)
    return (f"{heading}\n"
            f"<b>{esc(token.get('name') or '?')}</b> (${esc(token.get('symbol') or '?')})\n"
            f"CA: <code>{esc(address)}</code>\n"
            f"{body}")


async def _notify(session: aiohttp.ClientSession, text: str, address: str) -> bool:
    dest = chat_id()
    if settings.ai_dry_run:
        log.info(f"[AI] [DRY-RUN] would send:\n{text}")
        return False
    if not dest or not settings.telegram_bot_token:
        log.info("[AI] no destination chat or bot token — not sending")
        return False
    payload = {
        "chat_id": dest, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": [[
            {"text": "📊 GMGN", "url": _gmgn(address)},
        ]]},
    }
    try:
        url = f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/sendMessage"
        async with session.post(url, json=payload,
                                timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                log.warning(f"[AI] telegram {r.status}: {(await r.text())[:160]}")
                return False
            return True
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[AI] telegram send failed: {exc}")
        return False


# ── Judging ────────────────────────────────────────────────────────────────────

async def _judge(session, token: dict, row: dict, profile,
                 preview: bool = True) -> bool:
    """Read the post behind the launch and put it to the model.

    Returns False when the model could not be reached, so the caller can stop
    rather than spend a pass collecting the same failure.
    """
    text = row.get("excerpt") or ""
    if not text.strip():
        await _record(token, row, profile, "skipped", {"reason": "no post text to read"})
        return True

    verdict = await ask_grok(session, token, text, profile)
    if not verdict:
        if preview:
            await _record(token, row, profile, "pending",
                          {"reason": "passed every gate — waiting for the model"})
        return False

    narrative = str(verdict.get("narrative") or "none")
    confidence = int(verdict.get("confidence") or 0)
    detail = {"narrative": narrative, "confidence": confidence,
              "reason": verdict.get("summary") or ""}

    if not verdict.get("match") or confidence < settings.ai_min_confidence:
        detail["reason"] = detail["reason"] or "no narrative match"
        await _record(token, row, profile, "rejected", detail)
        return True

    # Matched, and that is the whole of this pass's job. Whether the post is
    # about something real is no longer decided here — it is Fact check, on the
    # row, when somebody wants to know.
    text_out = _message("🎯 <b>NARRATIVE MATCH</b>", token, token["address"], [
        f"Narrative: <b>{esc(narrative)}</b> ({confidence}/10)",
        f"{esc(str(verdict.get('summary') or ''))}",
        f"By @{esc(profile.handle)} · {profile.followers:,} followers "
        f"· {esc(profile.verified_type or 'verified')}",
    ])
    sent = await _notify(session, text_out, token["address"])
    detail["sent"] = sent
    await _record(token, row, profile, "matched", detail)
    log.info(f"[AI] MATCH {token.get('symbol')} — {narrative} ({confidence}/10) "
             f"via @{profile.handle}")
    return True


async def run_once(session: aiohttp.ClientSession) -> int:
    """Judge the launches the feed has collected. Returns how many reached the model.

    Reads from `x_links`, which the PumpPortal socket fills — nothing here goes
    near GMGN, and the X account was already resolved when the row was written.
    Everything before the model is a cheap, deterministic gate; the model is
    asked once, about the post, and only when a launch has earned it.
    """
    # Oldest first, deliberately. The rule is that the FIRST launch of a name
    # goes to the model and its copies do not, and judging newest-first gave
    # that backwards: the copy was judged and the original skipped as a repeat.
    rows = await _col("x_links").find(
        {"judged": {"$ne": True}, "kind": {"$in": list(_LINKED_KINDS)}}
    ).sort("found_at", 1).limit(200).to_list(200)

    from . import registry
    preview = await registry.is_enabled("ai_gate_preview")

    judged = 0
    today = ist_date_str(time.time())

    # Anything queued waiting for the model gets a few attempts each pass,
    # ahead of new work but on a budget of its own — a backlog of queued rows
    # must not be able to starve the launches still arriving.
    judged += await _retry_pending(session, preview)

    for row in rows:
        if judged >= MAX_PER_CYCLE:
            break
        address = row["address"]

        prior = await _col("ai_decisions").find_one(
            {"address": address}, {"verdict": 1, "tries": 1})
        # `pending` is not a verdict, it is a queue: the gates said yes and the
        # model was not reachable. Asked again, like an error.
        if prior and prior.get("verdict") not in ("error", "pending"):
            await _mark_judged(address)
            continue                      # one verdict per token
        if (prior and prior.get("verdict") == "error"
                and int(prior.get("tries") or 0) >= MAX_ERROR_RETRIES):
            await _mark_judged(address)
            continue                      # never could be judged; stop asking

        token = {"address": address, "symbol": row.get("symbol") or "",
                 "name": row.get("name") or ""}
        profile = x_client.XProfile(
            handle=row.get("handle") or "", verified=bool(row.get("verified")),
            verified_type=row.get("verified_type") or "",
            followers=int(row.get("followers") or 0),
            bio=row.get("excerpt") or "", found=bool(row.get("resolved")))

        name_key = (f"{(token['name'] or '').lower().strip()}|"
                    f"{(token['symbol'] or '').lower().strip()}")

        # 1. The link, before anything else, and it takes only two launches to
        #    matter — not a burst of five. The text the model reads is the same
        #    on every launch carrying a link, so the first one asked is the only
        #    one worth asking; the rest inherit its answer through their reason.
        link = (row.get("link") or "").strip()
        if link:
            prior_link = await _col("ai_decisions").find_one(
                {"link": link, "verdict": {"$in": list(SETTLED)}},
                {"verdict": 1, "symbol": 1})
            if prior_link:
                await _record(token, row, profile, "skipped",
                              {"reason": f"same link — already judged "
                                         f"({prior_link.get('verdict')}) as "
                                         f"{prior_link.get('symbol') or '?'}"})
                await _mark_judged(address)
                continue

        # 2. An account with no followers is nobody, whatever it posted.
        if profile.followers <= 0:
            await _record(token, row, profile, "skipped", {"reason": "account has no followers"})
            await _mark_judged(address)
            continue

        # 3. The same name and ticker already asked about today — a relaunch
        #    under a fresh link is still a relaunch. The day runs from midnight
        #    IST, the same boundary the rest of the project uses.
        today_same = await _col("ai_decisions").find_one(
            {"name_key": name_key, "day": today, "verdict": {"$in": list(SETTLED)}},
            {"_id": 1})
        if today_same:
            await _record(token, row, profile, "skipped",
                          {"reason": "same name and ticker already judged today"})
            await _mark_judged(address)
            continue

        judged += 1
        ok = await _judge(session, token, row, profile, preview)
        if not ok and not preview:
            # The model is down and we are not recording a queue. Leave the
            # launch for a later pass rather than burning through the rest of
            # them collecting the same failure.
            await _mark_judged(address, False)
            break
        await _mark_judged(address)

    await _settle_mcaps()
    return judged


# ── Telegram: what a launch was actually worth ───────────────────────────────

async def _check_telegram(session: Optional[aiohttp.ClientSession],
                          address: str) -> bool:
    """Promote a launch to Telegram if it is in a link's burst AND cleared the bar.

    Two conditions that become true in either order and minutes apart: a token
    can cross $8k eight seconds in, while the fifth launch on its link does not
    exist until three minutes later. So this is called from every side that can
    change either answer — the decision being written, the bar being crossed,
    and the burst completing — and each time it asks the same question.

    Note this is the Telegram rule only. The model's gate is separate and does
    not care how many launches a link carried; it asks about the first and
    skips the copies.
    """
    dec = await _col("ai_decisions").find_one(
        {"address": address},
        {"address": 1, "telegram": 1, "symbol": 1, "name": 1,
         "narrative": 1, "verdict": 1, "peak_mcap_usd": 1, "link": 1})
    if not dec or dec.get("telegram"):
        return False                       # unknown, or already sent

    row = await _col("x_links").find_one(
        {"address": address},
        {"peak_mcap_usd": 1, "link": 1, "day": 1, "open_timestamp": 1}) or {}
    peak = max(float(dec.get("peak_mcap_usd") or 0),
               float(row.get("peak_mcap_usd") or 0),
               pump_mcap.peak_usd(address))
    # The cheap half first: only about one launch in twenty gets past this, so
    # the burst query below runs on those rather than on all of them.
    if peak < pump_mcap.threshold_usd():
        return False

    members = await _link_burst(row.get("link") or dec.get("link") or "",
                                row.get("day"), row.get("open_timestamp"))
    if address not in members:
        return False

    await _col("ai_decisions").update_one(
        {"address": address},
        {"$set": {"telegram": True, "peak_mcap_usd": round(peak),
                  "burst_position": members.index(address) + 1,
                  "telegram_at": time.time()}})
    await _notify_telegram(session, dec, peak, members.index(address) + 1)
    return True


async def _link_burst(link: str, day: Optional[str],
                      ts: Optional[float]) -> list[str]:
    """The first five launches on one link, if they arrived inside the window.

    Empty when the link has not carried five yet, or when the five took longer
    than the window. Names and tickers are not looked at at all — the same link
    under five different names is exactly the case this is for.

    Read from the collection rather than from memory so a restart mid-window
    cannot turn a real burst into one that never happened.
    """
    link = (link or "").strip()
    if not link:
        return []
    need = int(settings.ai_link_burst_count)
    window = int(settings.ai_link_burst_window)
    day = day or ist_date_str(ts or time.time())

    members = await _col("x_links").find(
        {"link": link, "day": day},
        {"address": 1, "open_timestamp": 1}
    ).sort("open_timestamp", 1).limit(need).to_list(need)
    if len(members) < need:
        return []
    span = (float(members[-1].get("open_timestamp") or 0)
            - float(members[0].get("open_timestamp") or 0))
    if span > window:
        return []
    return [m["address"] for m in members]


async def _burst_formed(link: str, day: str) -> None:
    """A launch was just written. If its link has now carried five inside the
    window, every one of those five is re-checked — the earlier ones may have
    crossed the bar minutes ago with nothing to promote them at the time.
    """
    members = await _link_burst(link, day, None)
    if not members:
        return
    for address in members:
        await _check_telegram(None, address)


async def _notify_telegram(session: Optional[aiohttp.ClientSession],
                           dec: dict, peak: float, position: int = 0) -> None:
    address = dec.get("address") or ""
    text = _message(
        "🔥 <b>Burst + market cap</b>",
        {"name": dec.get("name"), "symbol": dec.get("symbol")}, address,
        [f"Peak market cap: <b>${round(peak):,}</b> in the first "
         f"{pump_mcap.watch_seconds()}s",
         f"Launch #{position} of {settings.ai_link_burst_count} on this link",
         f"Narrative: {esc(str(dec.get('narrative') or '—'))}",
         f"Verdict: {esc(str(dec.get('verdict') or '—'))}",
         f"X: {esc(str(dec.get('link') or '—'))}"])
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        await _notify(session, text, address)
    finally:
        if own:
            await session.close()
    log.info(f"[AI] TELEGRAM {dec.get('symbol')} — ${round(peak):,} peak")


async def _on_mcap_cross(mint: str, usd: float) -> None:
    """A watched launch just crossed the bar. Written down now, and sent if the
    launch has already been looked at — otherwise `_record` picks it up when it
    is.
    """
    await _col("x_links").update_one(
        {"address": mint}, {"$set": {"peak_mcap_usd": round(usd),
                                     "crossed_mcap": True}})
    await _check_telegram(None, mint)


async def _settle_mcaps() -> None:
    """Write down what the finished minutes reached, whether or not they crossed."""
    for mint, peak_sol, peak_usd in pump_mcap.expired():
        if peak_sol <= 0:
            continue
        await _col("x_links").update_one(
            {"address": mint},
            {"$set": {"peak_mcap_sol": round(peak_sol, 2),
                      "peak_mcap_usd": round(peak_usd)}})
        await _col("ai_decisions").update_one(
            {"address": mint}, {"$set": {"peak_mcap_usd": round(peak_usd)}})


async def _retry_pending(session: aiohttp.ClientSession,
                         preview: bool = True) -> int:
    """Ask the model again about launches that cleared the gates while it was down."""
    rows = await _col("ai_decisions").find({"verdict": "pending"}).sort(
        "at", 1).limit(RETRY_PENDING_PER_CYCLE).to_list(RETRY_PENDING_PER_CYCLE)
    done = 0
    for d in rows:
        token = {"address": d["address"], "symbol": d.get("symbol") or "",
                 "name": d.get("name") or ""}
        row = {"excerpt": d.get("excerpt") or "", "kind": d.get("kind") or "none",
               "link": d.get("link") or "",
               # Carried through, or the rewritten decision would lose the
               # launch time and its Age would restart from the retry.
               "open_timestamp": d.get("open_timestamp")}
        profile = x_client.XProfile(
            handle=d.get("handle") or "", verified=bool(d.get("verified")),
            verified_type=d.get("verified_type") or "",
            followers=int(d.get("followers") or 0), bio=d.get("excerpt") or "",
            found=True)
        if not await _judge(session, token, row, profile, preview):
            break                         # still no model; try again next pass
        done += 1
    return done


async def _mark_judged(address: str, judged: bool = True) -> None:
    await _col("x_links").update_one({"address": address},
                                     {"$set": {"judged": judged}})


async def watch() -> None:
    """Supervisor task. Off unless the Settings switch and an xAI key are set."""
    log.info(f"[AI] narrative agent started — model {settings.xai_model}, "
             f"dry-run {settings.ai_dry_run}")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await asyncio.sleep(settings.ai_scan_interval)
                # Run even without a key. The gates are most of the work and
                # they are worth seeing on their own — what they let through
                # lands in `pending`, and that is exactly the list the model
                # will be given.
                await run_once(session)
            except asyncio.CancelledError:
                log.info("[AI] narrative agent stopped")
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[AI] cycle failed: {exc}")


# ── Reporting (dashboard) ──────────────────────────────────────────────────────

async def x_feed_watch() -> None:
    """Hold PumpPortal's realtime socket and record every launch that has an X link.

    This replaced a GMGN polling loop. GMGN publishes pump.fun pairs in bursts,
    so a token was 20-60 seconds old before its link could even be read, and no
    polling interval could fix that — the data was not there yet. PumpPortal
    pushes the launch itself, and the token's own metadata URI carries the
    twitter field, so the link arrives with the token: measured, the metadata
    fetch takes 0.3 to 1.4 seconds and 11 of 12 launches had one.

    No API key. The key PumpPortal issues is for its trading endpoints, and
    nothing in this project trades.
    """
    seen: BoundedSet = BoundedSet(20000)
    backoff = 1.0
    # A burst of launches must not queue up behind each other's metadata fetch,
    # and must not open fifty sockets at once either.
    gate = asyncio.Semaphore(4)

    log.info(f"[PUMP] connecting to {settings.pumpportal_ws}")
    async with aiohttp.ClientSession() as session:
        # A crossing has to be recognised as it happens, so the dollar price
        # must already be in hand when the trade arrives — not fetched after.
        pump_mcap.on_cross = _on_mcap_cross
        _spawn(_price_watch(session))
        while True:
            try:
                import websockets
                async with websockets.connect(settings.pumpportal_ws,
                                              max_size=2 ** 22,
                                              ping_interval=30,
                                              ping_timeout=60) as ws:
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    backoff = 1.0
                    log.info("[PUMP] subscribed to new launches")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:  # noqa: BLE001
                            continue
                        mint = (msg.get("mint") or "").strip()
                        if not mint or mint in seen:
                            continue
                        # pump.fun only. The feed labels the pool, so this is
                        # read rather than inferred from the mint's suffix.
                        if (msg.get("pool") or "").lower() != "pump":
                            continue
                        seen.add(mint)
                        # Counting moved into the handler, which is where a link
                        # is known. Arrival is stamped here because metadata
                        # fetches finish out of order, and the burst has to be
                        # ordered by when the launch happened.
                        msg["_seen_at"] = time.time()
                        # Counted before anything can filter or lose it, so the
                        # arithmetic closes: what the socket delivered should
                        # equal what was stored plus what was dropped. A gap on
                        # this side means PumpPortal did not send it; a gap on
                        # the other means we lost it after receiving it. Those
                        # want opposite fixes and looked identical until now.
                        await _count_drop("_received", msg)
                        # The market cap clock starts here, not after the
                        # metadata fetch: that fetch costs up to 1.4s, and a
                        # minute measured from 1.4s in is not the first minute.
                        # Watching every launch is cheap — the window is 60s, so
                        # a few dozen are held at a time — and it is the only way
                        # to know what a launch did before we knew we cared.
                        pump_mcap.watch(mint, msg.get("marketCapSol") or 0)
                        _spawn(_handle_launch(session, gate, msg))
            except asyncio.CancelledError:
                log.info("[PUMP] stopped")
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[PUMP] socket error: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


async def _price_watch(session: aiohttp.ClientSession) -> None:
    """Keep SOL's dollar price warm so a crossing is never waiting on an HTTP call."""
    while True:
        try:
            price = await pump_mcap.sol_usd(session)
            if not price:
                log.warning("[MCAP] no SOL price from any source")
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[MCAP] price refresh failed: {exc}")
        await asyncio.sleep(pump_mcap.PRICE_TTL)


async def _handle_launch(session: aiohttp.ClientSession,
                         gate: asyncio.Semaphore, msg: dict) -> None:
    """One launch: read its metadata, resolve the X account, publish the row."""
    from .ws_hub import hub

    async with gate:
        try:
            meta = await _fetch_metadata(session, msg.get("uri") or "")
            link = str(meta.get("twitter") or "")
            if not link:
                await _count_drop("no metadata" if not meta else "no twitter link", msg)
                return

            mint = msg["mint"].strip()
            ref = x_client.parse_ref(link)
            if ref.kind == "none":
                await _count_drop("link is not an account", msg)
                return

            # It has a link, so it counts towards a burst. Before the verified
            # gate on purpose: the copies in a burst are often unverified, and
            # the rule is about the name being worked, not the account.
            await _note_for_og(msg, ref)

            day = ist_date_str(time.time())
            name_key = (f"{(msg.get('name') or '').lower().strip()}|"
                        f"{(msg.get('symbol') or '').lower().strip()}")
            already = await _col("x_links").count_documents(
                {"name_key": name_key, "day": day})
            if already >= MAX_PER_NAME_PER_DAY:
                await _count_drop("name already at the daily cap", msg)
                return

            prof = await x_client.fetch_profile(session, ref.handle)
            for attempt in range(X_RETRIES):
                if not prof.lookup_failed:
                    break
                # No answer from X. Wait and ask again rather than treat silence
                # as "unverified" — that is how a real token goes missing.
                await asyncio.sleep(X_RETRY_DELAY)
                prof = await x_client.fetch_profile(session, ref.handle)
            if prof.lookup_failed:
                await _count_drop("x lookup failed", msg, ref.handle)
                log.info(f"[PUMP] {msg.get('symbol')} dropped — X gave no answer "
                         f"for @{ref.handle} after {X_RETRIES} tries")
                return

            # Verified or nothing. Any kind counts — individual, business,
            # government — but an unverified account is not worth a row: on this
            # chain anyone can point a launch at any handle, and the tick is the
            # only cheap evidence that somebody stands behind it. Not stored
            # either, which keeps the collection to the rows actually shown.
            if not prof.verified:
                await _count_drop("not verified", msg, ref.handle)
                return
            post = await x_client.fetch_post(session, ref)

            row = {
                "address": mint,
                "symbol": msg.get("symbol") or "?",
                "name": msg.get("name") or "",
                "link": link,
                "kind": ref.kind,
                "handle": ref.handle,
                "resolved": prof.found,
                "verified": prof.verified,
                "verified_type": prof.verified_type,
                "followers": prof.followers,
                "post_found": post.found,
                "post_source": post.source,
                "post_age_minutes": (round(post.age_minutes)
                                     if post.age_minutes is not None else None),
                "excerpt": (post.text or prof.bio or "")[:160],
                "description": str(meta.get("description") or "")[:400],
                "website": str(meta.get("website") or "")[:200],
                "market_cap_sol": float(msg.get("marketCapSol") or 0),
                "creator": msg.get("traderPublicKey") or "",
                # Stamped when PumpPortal pushed the launch, not when this row
                # was finally written — the metadata fetch and the X lookups in
                # between take 0.3-1.4s, and stamping it here made every age and
                # every timestamp on the page that much later than the truth.
                "open_timestamp": float(msg.get("_seen_at") or time.time()),
                "found_at": time.time(),
                "source": "pumpportal",
                "judged": False,
                # Kept on the row so the per-day cap and the History dropdown
                # both read the same field rather than recomputing a boundary.
                "day": day,
                "name_key": name_key,
            }
            await _col("x_links").update_one({"address": mint},
                                             {"$set": {**row, "dt": _utc_now()}},
                                             upsert=True)
            await hub.broadcast("x_link", row)
            # This launch may be the fifth on its link. If so the four before it
            # are re-checked here, because one of them may have crossed the bar
            # minutes ago when there was no burst yet to qualify it.
            await _burst_formed(link, day)
        except Exception as exc:  # noqa: BLE001
            # A warning, and a counted drop. At debug this was written nowhere —
            # both log handlers sit at INFO — and counted nothing, so a launch
            # lost here was indistinguishable from one that never arrived. That
            # is the difference between "the feed missed it" and "we broke on
            # it", and the two want opposite fixes.
            log.warning(f"[PUMP] handler failed on {msg.get('symbol')} "
                        f"{(msg.get('mint') or '')[:12]}: "
                        f"{type(exc).__name__}: {exc}")
            await _count_drop("handler error", msg)


async def _note_for_og(msg: dict, ref) -> None:
    """Count a linked launch towards the OG rule, promoting the original on the fifth.

    Only launches with an X link count. One without a link says nothing about
    who is behind it, and counting those let a name relaunched anonymously look
    like somebody working at it.
    """
    now = float(msg.get("_seen_at") or time.time())
    name_key = (f"{(msg.get('name') or '').lower().strip()}|"
                f"{(msg.get('symbol') or '').lower().strip()}")
    if name_key == "|":
        return

    launches = [l for l in _recent_launches.get(name_key, [])
                if now - l["ts"] <= OG_BURST_WINDOW]
    launches.append({"ts": now, "mint": (msg.get("mint") or "").strip(),
                     "symbol": msg.get("symbol") or "?",
                     "name": msg.get("name") or "",
                     "link": ref.raw, "handle": ref.handle, "kind": ref.kind})
    launches.sort(key=lambda l: l["ts"])
    _recent_launches[name_key] = launches

    # Names that went quiet are dropped, so this holds a minute of traffic
    # rather than a day of it.
    if len(_recent_launches) > 4000:
        for key, seen in list(_recent_launches.items()):
            if not seen or now - seen[-1]["ts"] > OG_BURST_WINDOW:
                _recent_launches.pop(key, None)
        for key, when in list(_og_promoted.items()):
            if now - when > 3600:
                _og_promoted.pop(key, None)

    if len(launches) < OG_BURST_COUNT:
        return
    if now - _og_promoted.get(name_key, 0) < OG_BURST_WINDOW:
        return                            # this burst already has its original
    _og_promoted[name_key] = now
    await _promote_og(name_key, launches[0])


async def _promote_og(name_key: str, first: dict) -> None:
    """Flag the first launch of a burst, storing it if nothing stored it before."""
    from .ws_hub import hub
    try:
        mint = (first.get("mint") or "").strip()
        if not mint:
            return
        existing = await _col("x_links").find_one({"address": mint})
        if existing and existing.get("og"):
            return
        if existing:
            await _col("x_links").update_one(
                {"address": mint}, {"$set": {"og": True, "og_at": time.time()}})
            row = {**{k: v for k, v in existing.items() if k not in ("_id", "dt")},
                   "og": True}
        else:
            # Nothing stored it, so the original's account was unverified. It
            # still belongs here — the burst is what makes it worth seeing — and
            # its link is kept even though the account did not pass the gate.
            row = {
                "address": mint, "symbol": first.get("symbol") or "?",
                "name": first.get("name") or "",
                "link": first.get("link") or "", "kind": first.get("kind") or "none",
                "handle": first.get("handle") or "", "resolved": False,
                "verified": False,
                "verified_type": "", "followers": 0, "post_found": False,
                "post_source": "", "post_age_minutes": None, "excerpt": "",
                "open_timestamp": first.get("ts") or time.time(),
                "found_at": first.get("ts") or time.time(),
                "source": "pumpportal", "judged": True,
                "day": ist_date_str(first.get("ts") or time.time()),
                "name_key": name_key, "og": True, "og_at": time.time(),
            }
            await _col("x_links").update_one({"address": mint},
                                             {"$set": {**row, "dt": _utc_now()}},
                                             upsert=True)
        log.info(f"[PUMP] OG: {row.get('symbol')} — {OG_BURST_COUNT} linked "
                 f"launches under this name inside {OG_BURST_WINDOW}s")
        await hub.broadcast("x_link", row)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[PUMP] could not promote OG for {name_key}: {exc}")


async def _count_drop(reason: str, msg: dict, handle: str = "") -> None:
    """Record that a launch did not become a row, and why.

    Counted by the hour, and the mint is kept alongside the count. It used to be
    kept only for "x lookup failed", which meant "why did this token never
    appear" was unanswerable for every other reason — the counts said 489
    launches had no X link but not which ones, so a token that vanished could
    not be told apart from one correctly filtered. Bounded per bucket, and the
    collection expires with the logs, so the cost is fixed.
    """
    try:
        hour = time.strftime("%d-%m-%Y %H:00", time.localtime())
        await _col("x_drops").update_one(
            {"_id": f"{hour}|{reason}"},
            {"$inc": {"count": 1},
             "$set": {"reason": reason, "hour": hour, "dt": _utc_now()},
             "$push": {"mints": {"$each": [{"mint": msg.get("mint"),
                                            "symbol": msg.get("symbol"),
                                            "handle": handle,
                                            "at": time.time()}],
                                 "$slice": -DROP_MINTS_KEPT}}},
            upsert=True)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[PUMP] could not count drop: {exc}")


async def why_dropped(mint: str) -> Optional[dict]:
    """The reason a launch never became a row, if it was recorded as dropped."""
    doc = await _col("x_drops").find_one({"mints.mint": mint},
                                         {"reason": 1, "hour": 1, "mints": 1})
    if not doc:
        return None
    entry = next((m for m in doc.get("mints", []) if m.get("mint") == mint), {})
    return {"reason": doc.get("reason"), "hour": doc.get("hour"), **entry}


async def feed_audit(hours: int = 3) -> list[dict]:
    """Per hour: delivered by the socket, stored, dropped, and unexplained.

    `_received` is counted the moment a launch arrives, before any filter can
    touch it, so `received - stored - dropped` is the number of launches that
    reached this process and then went nowhere. If that is zero, anything
    missing was never delivered.
    """
    out: list[dict] = []
    rows = await _col("x_drops").find({}).sort("hour", -1).limit(hours * 10).to_list(200)
    by_hour: dict[str, dict] = {}
    for r in rows:
        h = by_hour.setdefault(r["hour"], {"hour": r["hour"], "received": 0,
                                           "dropped": 0, "reasons": {}})
        if r["reason"] == "_received":
            h["received"] = r["count"]
        else:
            h["dropped"] += r["count"]
            h["reasons"][r["reason"]] = r["count"]

    for hour, h in sorted(by_hour.items(), reverse=True)[:hours]:
        # The bucket label is local time; x_links is stamped in epoch seconds.
        start = time.mktime(time.strptime(hour, "%d-%m-%Y %H:00"))
        h["stored"] = await _col("x_links").count_documents(
            {"found_at": {"$gte": start, "$lt": start + 3600}})
        h["unexplained"] = h["received"] - h["stored"] - h["dropped"]
        out.append(h)
    return out


async def drops(hours: int = 24) -> list[dict]:
    """Drop counts by reason, newest hour first — the audit for what was filtered.

    `_received` shares this collection but is not a drop — it is the arrival
    count the audit measures everything else against, so it is left out here.
    """
    rows = await _col("x_drops").find(
        {"reason": {"$ne": "_received"}}
    ).sort("hour", -1).limit(hours * 8).to_list(200)
    for r in rows:
        r.pop("_id", None)
        r.pop("dt", None)
        r.pop("mints", None)      # the audit trail, not something to render
    return rows


async def _fetch_metadata(session: aiohttp.ClientSession, uri: str) -> dict:
    """The launch's own metadata JSON — where the socials live."""
    if not uri or not uri.startswith("http"):
        return {}
    try:
        async with session.get(uri, timeout=aiohttp.ClientTimeout(total=8),
                               headers={"User-Agent": "Mozilla/5.0 "
                                                      "(compatible; BlockChainBot/1.0)"}) as r:
            if r.status != 200:
                return {}
            return await r.json(content_type=None) or {}
    except Exception:  # noqa: BLE001
        return {}


# Link types that count as an account behind the token. A launch whose metadata
# names something else — an X community, a bare contract address — is not worth
# a row; one with no twitter field at all never becomes a row; and neither does
# one whose account is unverified.
_LINKED_KINDS = ("tweet", "profile")


async def x_link_dates(og_only: bool = False) -> list[str]:
    """IST days that have rows, newest first — the History dropdown."""
    days = await _col("x_links").distinct("day", {"og": True} if og_only else {})
    days = [d for d in days if d]
    return sorted(days, key=lambda x: datetime.strptime(x, "%d-%m-%Y"), reverse=True)


async def x_links(limit: int = 40, q: str | None = None,
                  min_followers: int = 0, day: str | None = None,
                  og_only: bool = False) -> dict:
    """Tokens with an X link, newest first. Read from Mongo — no upstream call."""
    # Sorted by Mongo, not in Python. Reading a fixed slice and sorting that
    # returns the newest of the OLDEST documents — which is what this did once
    # the collection outgrew the slice, so the section froze on rows two hours
    # old while fresh ones were being written the whole time.
    # The live view is the verified, linked launches. The OG view is a burst's
    # original, which may have had neither — so it filters on the flag alone.
    flt: dict[str, Any] = ({"og": True} if og_only
                           else {"kind": {"$in": list(_LINKED_KINDS)}, "verified": True})
    if day:
        flt["day"] = day
    if min_followers > 0:
        flt["followers"] = {"$gte": min_followers}
    if q:
        # Address, @handle, or any word in the post text / name / ticker.
        rx = {"$regex": re.escape(q.lstrip("@")), "$options": "i"}
        flt["$or"] = [{f: rx} for f in
                      ("address", "handle", "excerpt", "symbol", "name", "link")]

    # Counted with the same filter, before the limit. `total` was len(rows),
    # so a section holding two thousand launches reported forty — the page size,
    # dressed up as the total.
    total = await _col("x_links").count_documents(flt)
    rows = await _col("x_links").find(flt).sort(
        "found_at", -1).limit(limit).to_list(limit)
    for r in rows:
        r.pop("_id", None)
        r.pop("dt", None)
    return {
        "at": time.time(),
        "newest_age_minutes": (round((time.time() - rows[0]["open_timestamp"]) / 60, 1)
                               if rows and rows[0].get("open_timestamp") else None),
        "total": total,
        "shown": len(rows),
        "resolved": sum(1 for r in rows if r.get("resolved")),
        "verified": sum(1 for r in rows if r.get("verified")),
        "posts": sum(1 for r in rows if r.get("post_found")),
        "items": rows,
    }


async def decision_dates() -> list[str]:
    """IST days that have decisions, newest first — the History dropdown."""
    days = [d for d in await _col("ai_decisions").distinct("day") if d]
    return sorted(days, key=lambda x: datetime.strptime(x, "%d-%m-%Y"), reverse=True)


async def recent(limit: int = 200, verdict: Optional[str] = None,
                 q: Optional[str] = None, min_followers: int = 0,
                 day: Optional[str] = None) -> dict:
    """Decisions newest first, with the count of everything the filter matches.

    Every filter is applied by the query. Searching a page of results and
    calling that a search means the answer changes with the page size, which is
    not a search anyone can trust.
    """
    flt: dict[str, Any] = {}
    # Telegram is not a verdict — a launch can be pending or matched AND have
    # cleared the market cap bar — so it filters on its own flag.
    if verdict == "telegram":
        flt["telegram"] = True
    elif verdict:
        flt["verdict"] = verdict
    if day:
        flt["day"] = day
    if min_followers > 0:
        flt["followers"] = {"$gte": min_followers}
    if q:
        rx = {"$regex": re.escape(q.lstrip("@")), "$options": "i"}
        flt["$or"] = [{f: rx} for f in
                      ("address", "handle", "symbol", "name", "narrative", "reason")]
    total = await _col("ai_decisions").count_documents(flt)
    docs = await _col("ai_decisions").find(flt).sort("at", -1).limit(limit).to_list(limit)
    out: list[dict] = []
    for d in docs:
        d.pop("_id", None)
        d["gmgn_url"] = _gmgn(d.get("address", ""))
        out.append(d)
    return {"total": total, "shown": len(out), "items": out}


async def stats() -> dict:
    col = _col("ai_decisions")
    counts = {v: await col.count_documents({"verdict": v})
              for v in ("matched", "launching", "rejected", "skipped",
                        "pending", "error")}
    return {
        "enabled": bool(settings.xai_api_key),
        "dry_run": settings.ai_dry_run,
        "model": settings.xai_model,
        # Two models now: the fast one reads narratives on every launch, the
        # reasoning one answers Fact check on a click.
        "fact_model": settings.xai_fact_model or settings.xai_model,
        "telegram": await col.count_documents({"telegram": True}),
        "total": await col.count_documents({}),
        **counts,
    }
