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

# A *profile* is a coarser judgement — what kind of account is this.
PROFILE_NARRATIVES = ["A tech token", "A gaming token", "Other"]

# How many tokens get the full treatment in one pass. The feed returns 100 and
# most are already known; this caps the work when a burst arrives.
MAX_PER_CYCLE = 12
# Launching profiles re-checked per pass, oldest check first.
MAX_RECHECK_PER_CYCLE = 6
# A verdict of "error" means the model could not be reached, not that the token
# was judged — so it is retried. Capped, because a token nothing can classify
# should not be asked about forever.
MAX_ERROR_RETRIES = 5
# The same name and ticker relaunched over and over is the commonest form of
# spam on these chains, and the reference bot blocks it twice over: once for a
# day, and once per process run. Both are kept — a restart should not reopen a
# name that was already judged noise.
MAX_NAME_OCCURRENCES = 3
_name_counts: dict[str, int] = {}

_DEDUP_WINDOW = 24 * 3600

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

def _prompt(kind: str, token: dict, content: str, profile) -> str:
    narratives = TWEET_NARRATIVES if kind == "tweet" else PROFILE_NARRATIVES
    listed = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(narratives))
    what = "the post below" if kind == "tweet" else "the account below"
    return (
        f"Token name: {token.get('name') or '?'}\n"
        f"Token ticker: {token.get('symbol') or '?'}\n"
        f"Account: @{profile.handle} ({profile.followers:,} followers, "
        f"verified: {profile.verified_type or 'no'})\n"
        f"Account bio: {profile.bio[:400]}\n"
        f"Content:\n{content[:1500]}\n\n"
        f"Decide whether {what} matches one of these narratives:\n{listed}\n\n"
        "Reply with JSON only:\n"
        '{"match": true|false, "narrative": "the matched narrative or none", '
        '"confidence": 1-10, "summary": "one short line on what it is about", '
        '"red_flags": ["..."]}\n\n'
        "Rules:\n"
        "- match false if none of the narratives fit.\n"
        "- match false for pure hype with no substance (moon, 1000x, buy now, "
        "emoji-only, giveaway spam).\n"
        "- The token name does NOT have to match the narrative. Anyone can "
        "launch a token about any real event.\n"
        "- confidence 8-10 only for a clear, verifiable, current event.\n"
        "- confidence 1-5 for a weak or guessed match."
    )


async def ask_grok(session: aiohttp.ClientSession, kind: str, token: dict,
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
            {"role": "user", "content": _prompt(kind, token, content, profile)},
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


# ── Dedup ──────────────────────────────────────────────────────────────────────

async def _seen_count(key: str) -> int:
    doc = await _col("ai_seen").find_one({"_id": key})
    if not doc:
        return 0
    hits = [t for t in (doc.get("hits") or []) if time.time() - t < _DEDUP_WINDOW]
    return len(hits)


async def _mark_seen(key: str) -> None:
    now = time.time()
    doc = await _col("ai_seen").find_one({"_id": key})
    hits = [t for t in ((doc or {}).get("hits") or []) if now - t < _DEDUP_WINDOW]
    hits.append(now)
    await _col("ai_seen").update_one(
        {"_id": key}, {"$set": {"hits": hits, "dt": _utc_now()}}, upsert=True)


# ── Decisions ──────────────────────────────────────────────────────────────────

async def _record(token: dict, ref, profile, verdict: str, detail: dict) -> None:
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
                "verified": getattr(profile, "verified", False),
                "verified_type": getattr(profile, "verified_type", ""),
                "followers": getattr(profile, "followers", 0),
                "kind": getattr(ref, "kind", "none"), "link": getattr(ref, "raw", ""),
                "verdict": verdict, **detail,
                "at": time.time(), "dt": _utc_now(),
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


# ── The two branches ───────────────────────────────────────────────────────────

def _passes(verdict: dict, narrative_is_other: bool) -> bool:
    if not verdict.get("match"):
        return False
    floor = (settings.ai_other_min_confidence if narrative_is_other
             else settings.ai_min_confidence)
    return int(verdict.get("confidence") or 0) >= floor


async def _handle_tweet(session, token: dict, ref, profile, row: dict) -> None:
    # The post was read when the row was written; using it again costs nothing.
    post = x_client.XPost(text=row.get("excerpt") or "",
                          age_minutes=row.get("post_age_minutes"),
                          found=bool(row.get("post_found")))
    content = post.text if post.found else (token.get("description") or "")
    if not content:
        # Same order the reference bot uses: the post, else the token's own
        # description, else there is nothing to judge and it is dropped.
        await _record(token, ref, profile, "skipped", {"reason": "no content to read"})
        return
    if (post.age_minutes is not None
            and post.age_minutes > settings.ai_tweet_max_age_hours * 60):
        await _record(token, ref, profile, "skipped",
                      {"reason": f"post is {post.age_minutes / 60:.0f}h old"})
        return

    verdict = await ask_grok(session, "tweet", token, content, profile)
    if not verdict:
        await _record(token, ref, profile, "error", {"reason": "no verdict from Grok"})
        return
    narrative = str(verdict.get("narrative") or "none")
    if not _passes(verdict, narrative.strip().lower() == "other"):
        await _record(token, ref, profile, "rejected",
                      {"reason": verdict.get("summary") or "no narrative match",
                       "narrative": narrative,
                       "confidence": verdict.get("confidence")})
        return

    text = _message("🎯 <b>NARRATIVE MATCH</b>", token, token["address"], [
        f"Narrative: <b>{esc(narrative)}</b> ({verdict.get('confidence')}/10)",
        f"{esc(str(verdict.get('summary') or ''))}",
        f"By @{esc(profile.handle)} · {profile.followers:,} followers "
        f"· {esc(profile.verified_type or 'verified')}",
    ])
    sent = await _notify(session, text, token["address"])
    await _record(token, ref, profile, "matched",
                  {"narrative": narrative, "confidence": verdict.get("confidence"),
                   "reason": verdict.get("summary"), "sent": sent})
    log.info(f"[AI] MATCH {token.get('symbol')} — {narrative} "
             f"({verdict.get('confidence')}/10) via @{profile.handle}")


async def _handle_profile(session, token: dict, ref, profile) -> None:
    # Follower count is not a gate. It was one — accounts over a million were
    # skipped — and it is kept only as an opt-in knob, off by default: a large
    # account is as likely to be the real story as a small one.
    if (settings.ai_big_account_followers
            and profile.followers >= settings.ai_big_account_followers):
        await _record(token, ref, profile, "skipped",
                      {"reason": f"account too big ({profile.followers:,})"})
        return

    post = await x_client.fetch_post(session, ref)
    found_ca = x_client.find_contract(profile.bio, post.text if post.found else "")

    if found_ca:
        if found_ca == token["address"].lower():
            text = _message("✅ <b>MATCHED</b>", token, token["address"], [
                f"@{esc(profile.handle)} published this contract "
                f"· {profile.followers:,} followers",
            ])
            sent = await _notify(session, text, token["address"])
            await _record(token, ref, profile, "matched",
                          {"reason": "profile CA equals token CA", "sent": sent})
            log.info(f"[AI] MATCHED {token.get('symbol')} via @{profile.handle}")
        else:
            await _record(token, ref, profile, "skipped",
                          {"reason": "profile advertises a different contract",
                           "found_ca": found_ca})
        return

    # No contract anywhere yet. Seen this name before with the same silence?
    name_key = f"launching:{(token.get('name') or '').lower()}|" \
               f"{(token.get('symbol') or '').lower()}"
    if await _seen_count(name_key):
        await _record(token, ref, profile, "skipped",
                      {"reason": "same name already reported as launching"})
        return

    verdict = await ask_grok(session, "profile", token,
                             post.text if post.found else profile.bio, profile)
    if not verdict:
        await _record(token, ref, profile, "error", {"reason": "no verdict from Grok"})
        return
    narrative = str(verdict.get("narrative") or "none")
    if not _passes(verdict, narrative.strip().lower() == "other"):
        await _record(token, ref, profile, "rejected",
                      {"reason": verdict.get("summary") or "no narrative match",
                       "narrative": narrative,
                       "confidence": verdict.get("confidence")})
        return

    text = _message("🚀 <b>LAUNCHING</b>", token, token["address"], [
        f"Narrative: <b>{esc(narrative)}</b> ({verdict.get('confidence')}/10)",
        f"{esc(str(verdict.get('summary') or ''))}",
        f"@{esc(profile.handle)} · {profile.followers:,} followers "
        f"· no contract published yet",
    ])
    sent = await _notify(session, text, token["address"])
    await _mark_seen(name_key)
    await _col("ai_watch").update_one(
        {"_id": f"{profile.handle.lower()}:{token['address']}"},
        {"$set": {"handle": profile.handle, "address": token["address"],
                  "symbol": token.get("symbol"), "name": token.get("name"),
                  "status": "launching", "first_seen": time.time(),
                  "last_check": time.time(), "dt": _utc_now()}},
        upsert=True,
    )
    await _record(token, ref, profile, "launching",
                  {"narrative": narrative, "confidence": verdict.get("confidence"),
                   "reason": verdict.get("summary"), "sent": sent})
    log.info(f"[AI] LAUNCHING {token.get('symbol')} via @{profile.handle}")


async def _recheck_launching(session) -> None:
    """Ask each pending profile again whether it has published a contract yet."""
    cutoff = time.time() - settings.ai_launching_watch_hours * 3600
    rows = await _col("ai_watch").find({"status": "launching"}).to_list(500)
    rows = [r for r in rows if r.get("first_seen", 0) >= cutoff]
    stale = [r["_id"] for r in await _col("ai_watch").find(
        {"status": "launching"}).to_list(500) if r.get("first_seen", 0) < cutoff]
    if stale:
        await _col("ai_watch").update_many({"_id": {"$in": stale}},
                                           {"$set": {"status": "expired"}})

    rows.sort(key=lambda r: r.get("last_check", 0))
    for row in rows[:MAX_RECHECK_PER_CYCLE]:
        await _col("ai_watch").update_one({"_id": row["_id"]},
                                          {"$set": {"last_check": time.time()}})
        profile = await x_client.fetch_profile(session, row["handle"])
        ref = x_client.parse_ref(row["handle"])
        post = await x_client.fetch_post(session, ref)
        found = x_client.find_contract(profile.bio, post.text if post.found else "")
        if not found:
            continue
        if found != str(row["address"]).lower():
            await _col("ai_watch").update_one(
                {"_id": row["_id"]},
                {"$set": {"status": "mismatch", "found_ca": found}})
            continue
        token = {"name": row.get("name"), "symbol": row.get("symbol"),
                 "address": row["address"]}
        text = _message("✅ <b>MATCHED</b>", token, row["address"], [
            f"@{esc(row['handle'])} has now published this contract",
        ])
        sent = await _notify(session, text, row["address"])
        await _col("ai_watch").update_one(
            {"_id": row["_id"]},
            {"$set": {"status": "matched", "found_ca": found, "sent": sent}})
        log.info(f"[AI] MATCHED (late) {row.get('symbol')} via @{row['handle']}")


# ── Judging pass ───────────────────────────────────────────────────────────────

async def run_once(session: aiohttp.ClientSession) -> int:
    """Judge the launches the feed has collected. Returns how many were judged.

    Reads from `x_links`, which the PumpPortal socket fills — nothing here goes
    near GMGN. The X account was already resolved when the row was written, so
    this pass is the gates and the model, nothing else.
    """
    rows = await _col("x_links").find(
        {"judged": {"$ne": True}, "kind": {"$in": list(_LINKED_KINDS)}}
    ).to_list(500)
    rows.sort(key=lambda r: r.get("found_at") or 0, reverse=True)

    judged = 0
    for row in rows:
        if judged >= MAX_PER_CYCLE:
            break
        address = row["address"]

        prior = await _col("ai_decisions").find_one(
            {"address": address}, {"verdict": 1, "tries": 1})
        if prior and prior.get("verdict") != "error":
            await _mark_judged(address)
            continue                      # one verdict per token
        if prior and int(prior.get("tries") or 0) >= MAX_ERROR_RETRIES:
            await _mark_judged(address)
            continue                      # never could be judged; stop asking

        token = {"address": address, "symbol": row.get("symbol") or "",
                 "name": row.get("name") or "",
                 "description": row.get("description") or ""}
        ref = x_client.XRef(handle=row.get("handle") or "",
                            status_id=("1" if row.get("kind") == "tweet" else ""),
                            raw=row.get("link") or "", kind=row.get("kind") or "none")
        profile = x_client.XProfile(
            handle=row.get("handle") or "", verified=bool(row.get("verified")),
            verified_type=row.get("verified_type") or "",
            followers=int(row.get("followers") or 0),
            bio=row.get("excerpt") or "", found=bool(row.get("resolved")))

        # One viral link gets attached to a run of copycat tokens. Reading it
        # more than a couple of times a day is money spent on the same answer.
        link_key = f"link:{ref.handle.lower()}:{row.get('link', '')}"
        if await _seen_count(link_key) >= settings.ai_max_link_reads:
            await _record(token, ref, profile, "skipped",
                          {"reason": "link already analysed today"})
            await _mark_judged(address)
            continue

        # A name and ticker seen three times in this run, or at all in the last
        # day, is a relaunch — the commonest spam on this chain.
        name_key = (f"name:{(token['name'] or '').lower().strip()}|"
                    f"{(token['symbol'] or '').lower().strip()}")
        if _name_counts.get(name_key, 0) >= MAX_NAME_OCCURRENCES:
            await _mark_judged(address)
            continue
        if await _seen_count(name_key):
            await _record(token, ref, profile, "skipped",
                          {"reason": "same name and ticker seen today"})
            await _mark_judged(address)
            continue

        if not profile.found:
            await _record(token, ref, profile, "skipped",
                          {"reason": "account not found"})
            await _mark_judged(address)
            continue
        if not profile.verified or profile.verified_type.lower() not in allowed_verification():
            await _record(token, ref, profile, "skipped",
                          {"reason": f"not verified ({profile.verified_type or 'none'})"})
            await _mark_judged(address)
            continue

        # Registered before the model is asked, not after.
        await _mark_seen(link_key)
        await _mark_seen(name_key)
        _name_counts[name_key] = _name_counts.get(name_key, 0) + 1
        judged += 1

        if row.get("kind") == "tweet":
            await _handle_tweet(session, token, ref, profile, row)
        else:
            await _handle_profile(session, token, ref, profile)
        await _mark_judged(address)

    await _recheck_launching(session)
    return judged


async def _mark_judged(address: str) -> None:
    await _col("x_links").update_one({"address": address},
                                     {"$set": {"judged": True}})


async def watch() -> None:
    """Supervisor task. Off unless the Settings switch and an xAI key are set."""
    log.info(f"[AI] narrative agent started — model {settings.xai_model}, "
             f"dry-run {settings.ai_dry_run}")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await asyncio.sleep(settings.ai_scan_interval)
                if not settings.xai_api_key:
                    continue              # nothing to ask; stay idle, stay quiet
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


async def recent(limit: int = 100, verdict: Optional[str] = None) -> dict:
    """Decisions newest first, with the count of everything the filter matches."""
    flt: dict[str, Any] = {}
    if verdict:
        flt["verdict"] = verdict
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
              for v in ("matched", "launching", "rejected", "skipped", "error")}
    watching = await _col("ai_watch").count_documents({"status": "launching"})
    return {
        "enabled": bool(settings.xai_api_key),
        "dry_run": settings.ai_dry_run,
        "model": settings.xai_model,
        "total": await col.count_documents({}),
        "watching": watching,
        **counts,
    }


async def watching() -> list[dict]:
    rows = await _col("ai_watch").find({}).sort("first_seen", -1).limit(200).to_list(200)
    for r in rows:
        r.pop("_id", None)
        r["gmgn_url"] = _gmgn(r.get("address", ""))
    return rows
