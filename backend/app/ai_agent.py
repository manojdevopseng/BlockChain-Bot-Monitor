"""Judge a new Robinhood token by the X account behind it.

Robinhood Chain produces a few hundred tokens a day and almost all of them are
noise. What separates the handful worth looking at is not on-chain: it is
whether a real, verified account is behind the launch, and whether it is
attached to something actually happening. So each new token's X link is read
and put to Grok, and only a match becomes an alert.

Two branches, because a token's link points at one of two things:

  a post     — judge the post. Does it match one of the watched narratives?
  a profile  — judge the account. Is it a launch account, and has it published
               a contract address yet? If not, the token is reported as
               "Launching" and the profile is re-checked; when an address turns
               up and matches, it becomes "Matched".

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

from . import db, x_client
from .scanners.bounded_set import BoundedSet
from .config import settings
from .scanners.slog import get_logger
from .util import esc, ist_date_str

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# The narratives a *post* is checked against — the product owner's list, in
# their order. Grok is asked to pick one or say none.
TWEET_NARRATIVES = [
    "Trump",
    "Elon Musk or one of his companies",
    "A tech token",
    "A game",
    "A new product launch",
    "A new AI launch",
    "A new mascot launch",
    "A pet name",
    "A new animal introduced at any zoo",
    "A new token launchpad",
    "Ethereum or Vitalik",
    "Viral content",
    "Robinhood or a Robinhood employee",
]

# Rule 14 of the brief: the model must also say whether the thing is real.
# Without it, "New AI Launch" fires on anyone who types the words — the
# narrative is the easy half, the verification is the half that costs money.

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

# Verdicts that mean a link's question has already been asked. `error` and
# `skipped` are absent on purpose: neither ever put the post to the model, and
# a link that failed once must not be shut out for good.
SETTLED = ("matched", "launching", "rejected", "pending")

# name_key -> launches seen inside the window, oldest first. Held in memory: it
# is a minute of traffic, and it must not cost a database round trip per launch.
_recent_launches: dict[str, list[dict]] = {}
_og_promoted: dict[str, float] = {}


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
    listed = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(TWEET_NARRATIVES))
    return (
        f"Token name: {token.get('name') or '?'}\n"
        f"Token ticker: {token.get('symbol') or '?'}\n"
        f"Posted by: @{profile.handle} ({profile.followers:,} followers, "
        f"verified: {profile.verified_type or 'no'})\n"
        f"Post:\n{content[:1500]}\n\n"
        f"Two questions about the post.\n\n"
        f"First, does it match one of these narratives?\n{listed}\n\n"
        "Second, is the thing it describes real — an event, product, launch or "
        "post that actually happened and could be checked — rather than a claim "
        "the post makes about itself?\n\n"
        "Reply with JSON only:\n"
        '{"match": true|false, "narrative": "the matched narrative or none", '
        '"verified": true|false, "confidence": 1-10, '
        '"summary": "one short line on what it is about", '
        '"red_flags": ["..."]}\n\n'
        "Rules:\n"
        "- match false if none of the narratives fit.\n"
        "- match false for pure hype with no substance (moon, 1000x, buy now, "
        "emoji-only, giveaway spam).\n"
        "- verified true only when the thing is real and checkable. A post that "
        "merely announces itself is match true, verified false.\n"
        "- The token name does NOT have to match the narrative. Anyone can "
        "launch a token about any real event.\n"
        "- confidence 8-10 for a clear, current, checkable event.\n"
        "- confidence 1-5 for a weak or guessed match."
    )


async def ask_grok(session: aiohttp.ClientSession, token: dict,
                   content: str, profile) -> Optional[dict]:
    """One classification. None when the call fails — never a fabricated verdict."""
    if not settings.xai_api_key:
        return None
    payload = {
        "model": settings.xai_model,
        "messages": [
            {"role": "system",
             "content": ("You classify crypto token launches by the narrative "
                         "behind them. Reply with JSON only — no markdown, no "
                         "commentary.")},
            {"role": "user", "content": _prompt(token, content, profile)},
        ],
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
              "reason": verdict.get("summary") or "",
              "verified_claim": bool(verdict.get("verified"))}

    if not verdict.get("match") or confidence < settings.ai_min_confidence:
        detail["reason"] = detail["reason"] or "no narrative match"
        await _record(token, row, profile, "rejected", detail)
        return True

    # Matched the narrative but the model could not stand behind it being real.
    # Worth seeing, not worth acting on — that is what Launching is for.
    if not verdict.get("verified"):
        await _record(token, row, profile, "launching", detail)
        log.info(f"[AI] LAUNCHING {token.get('symbol')} — {narrative} "
                 f"({confidence}/10), unverified")
        return True

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

        # 1. The link, before anything else: it is the broadest gate and the
        #    cheapest. Measured on live data, one tweet carries up to 45 tokens
        #    under 15 different names, and the text the model reads is identical
        #    every time — so the first token on a link is the one that gets
        #    asked. The rest are skipped carrying that answer in their reason,
        #    which keeps the audit able to show what they were riding.
        link = row.get("link") or ""
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

    return judged


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
               "link": d.get("link") or ""}
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
                        asyncio.create_task(_handle_launch(session, gate, msg))
            except asyncio.CancelledError:
                log.info("[PUMP] stopped")
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[PUMP] socket error: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


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
                # The launch is now, so this is the launch time — not the time
                # some aggregator got round to listing it.
                "open_timestamp": time.time(),
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
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[PUMP] {msg.get('symbol')}: {exc}")


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

    Counted by the hour rather than kept per launch: most drops are the filter
    doing its job — thousands a day with no X link or an unverified account —
    and a document each would be a lot of noise to store. The exceptions are the
    ones worth chasing, so a launch dropped because X would not answer keeps its
    mint alongside the count.
    """
    try:
        hour = time.strftime("%d-%m-%Y %H:00", time.localtime())
        update: dict = {"$inc": {"count": 1},
                        "$set": {"reason": reason, "hour": hour, "dt": _utc_now()}}
        if reason == "x lookup failed":
            update["$push"] = {
                "mints": {"$each": [{"mint": msg.get("mint"),
                                     "symbol": msg.get("symbol"),
                                     "handle": handle, "at": time.time()}],
                          "$slice": -50},
            }
        await _col("x_drops").update_one({"_id": f"{hour}|{reason}"}, update, upsert=True)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[PUMP] could not count drop: {exc}")


async def drops(hours: int = 24) -> list[dict]:
    """Drop counts by reason, newest hour first — the audit for what was filtered."""
    rows = await _col("x_drops").find({}).sort("hour", -1).limit(hours * 8).to_list(200)
    for r in rows:
        r.pop("_id", None)
        r.pop("dt", None)
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
    if verdict:
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
        "total": await col.count_documents({}),
        **counts,
    }
