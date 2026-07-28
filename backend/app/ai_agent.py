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

  • It never touches the GMGN client's pacing. One call per cycle to the same
    shared, rate-limited client — the Cloudflare fix stays exactly as it is.
  • It never makes an AI call it can avoid. One X link gets reused across a
    stream of copycat tokens, so links and names are deduplicated first; the
    reference bot learned that the hard way.
  • It sends nothing while AI_DRY_RUN is on. Decisions are recorded either way,
    so a day of them can be read before it is allowed to post.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from . import db, x_client
from .config import settings
from .scanners.slog import get_logger
from .util import esc

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


async def _handle_tweet(session, token: dict, ref, profile) -> None:
    post = await x_client.fetch_post(session, ref)
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
    if profile.followers >= settings.ai_big_account_followers:
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


# ── Pass ───────────────────────────────────────────────────────────────────────

def _is_pump(pair: dict) -> bool:
    """pump.fun and nothing else.

    Read from the pair, not guessed from the mint's "pump" suffix — a token can
    carry that suffix and be listed under another launchpad.
    """
    info = pair.get("base_token_info") or {}
    label = (pair.get("launchpad") or info.get("launchpad") or "").lower()
    return "pump" in label


def _social_link(pair: dict) -> str:
    info = pair.get("base_token_info") or {}
    links = info.get("social_links") or {}
    for key in ("twitter_username", "twitter", "x"):
        val = links.get(key) or info.get(key)
        if val:
            return str(val)
    return ""


async def run_once(client, session: aiohttp.ClientSession) -> int:
    """One pass over the Robinhood feed. Returns how many tokens were judged."""
    pairs = await client.get_chain_new_pairs("robinhood", 100)
    judged = 0

    for pair in pairs:
        if judged >= MAX_PER_CYCLE:
            break
        info = pair.get("base_token_info") or {}
        address = (pair.get("base_address") or info.get("address") or "").lower()
        if not address:
            continue
        prior = await _col("ai_decisions").find_one(
            {"address": address}, {"verdict": 1, "tries": 1})
        if prior and prior.get("verdict") != "error":
            continue                      # judged already, one verdict per token
        if prior and int(prior.get("tries") or 0) >= MAX_ERROR_RETRIES:
            continue                      # never could be judged; stop asking

        token = {"address": address,
                 "symbol": info.get("symbol") or pair.get("symbol") or "",
                 "name": info.get("name") or info.get("symbol") or "",
                 "description": info.get("description") or pair.get("description") or ""}

        link = _social_link(pair)
        ref = x_client.parse_ref(link)
        if ref.kind == "none":
            continue                      # no X link at all — silently ignored

        # One viral link gets attached to a run of copycat tokens. Reading it
        # more than a couple of times a day is money spent on the same answer.
        link_key = f"link:{ref.handle.lower()}:{ref.status_id}"
        if await _seen_count(link_key) >= settings.ai_max_link_reads:
            await _record(token, ref, x_client.XProfile(handle=ref.handle),
                          "skipped", {"reason": "link already analysed today"})
            continue

        # A name and ticker seen three times in this run, or at all in the last
        # day, is a relaunch. Checked before the account lookup so a spam run
        # costs nothing at all.
        name_key = (f"name:{(token['name'] or '').lower().strip()}|"
                    f"{(token['symbol'] or '').lower().strip()}")
        if _name_counts.get(name_key, 0) >= MAX_NAME_OCCURRENCES:
            continue
        if await _seen_count(name_key):
            await _record(token, ref, x_client.XProfile(handle=ref.handle),
                          "skipped", {"reason": "same name and ticker seen today"})
            continue

        profile = await x_client.fetch_profile(session, ref.handle)
        if not profile.found:
            await _record(token, ref, profile, "skipped",
                          {"reason": "account not found"})
            continue
        if not profile.verified or profile.verified_type.lower() not in allowed_verification():
            await _record(token, ref, profile, "skipped",
                          {"reason": f"not verified ({profile.verified_type or 'none'})"})
            continue

        # Registered before the model is asked, not after: two copies of the
        # same token arriving in one pass would otherwise both go through.
        await _mark_seen(link_key)
        await _mark_seen(name_key)
        _name_counts[name_key] = _name_counts.get(name_key, 0) + 1
        judged += 1
        if ref.is_tweet:
            await _handle_tweet(session, token, ref, profile)
        else:
            await _handle_profile(session, token, ref, profile)

    await _recheck_launching(session)
    return judged


async def watch() -> None:
    """Supervisor task. Off unless the Settings switch and an xAI key are set."""
    from . import supervisor
    log.info(f"[AI] narrative agent started — model {settings.xai_model}, "
             f"dry-run {settings.ai_dry_run}")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await asyncio.sleep(settings.ai_scan_interval)
                if not settings.xai_api_key:
                    continue              # nothing to ask; stay idle, stay quiet
                client = getattr(supervisor, "_client", None)
                if client is None:
                    continue              # scanners not up — no GMGN client to borrow
                await run_once(client, session)
            except asyncio.CancelledError:
                log.info("[AI] narrative agent stopped")
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[AI] cycle failed: {exc}")


# ── Reporting (dashboard) ──────────────────────────────────────────────────────

# How often the X feed is read. This is the one GMGN call the loop makes, and it
# goes through the shared client at its existing pace, so gmgn.ai sees no change
# in request rate — only in which requests fill the same budget.
X_FEED_INTERVAL = 15
# How long a token is kept while it has no X link of its own.
_PENDING_MAX_AGE = 10 * 60


async def note_onchain_token(address: str, symbol: str, name: str,
                             dex: str = "") -> None:
    """Record a mint the instant on-chain discovery sees it, X link or not.

    The pump.fun program's own CreateEvent reaches us about a second after the
    mint exists, where GMGN's feed takes longer. Recording it here is what makes
    the Age column the real time since launch rather than the time GMGN got
    round to listing it.

    The X link is not knowable at this point — only GMGN carries it — so the row
    lands pending and is filled in when the feed catches up. A row that never
    gets one is dropped.
    """
    address = (address or "").strip()
    if not address:
        return
    from .ws_hub import hub
    try:
        if await _col("x_links").find_one({"address": address}, {"_id": 1}):
            return
        row = {
            "address": address, "symbol": symbol or "?", "name": name or "",
            "dex": dex, "link": "", "kind": "pending", "handle": "",
            "resolved": False, "verified": False, "verified_type": "",
            "followers": 0, "post_found": False, "post_source": "",
            "post_age_minutes": None, "excerpt": "",
            "open_timestamp": time.time(), "found_at": time.time(),
            "source": "onchain",
        }
        await _col("x_links").update_one({"address": address},
                                         {"$set": {**row, "dt": _utc_now()}},
                                         upsert=True)
        await hub.broadcast("x_link", row)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[X-FEED] could not note {symbol}: {exc}")


async def x_feed_watch() -> None:
    """Read the X feed continuously and push each new token as it appears.

    This used to be an on-demand endpoint the page polled, which meant tokens
    arrived in clumps a poll apart and a row could be a minute behind the chain
    before anyone saw it. Reading on a loop and broadcasting each new row
    instead makes them appear one at a time, the moment they are found — the
    dashboard stops asking and starts being told.

    Runs whether or not the model is enabled: knowing the X side is alive is
    useful in its own right, and it is one call per pass either way.
    """
    from . import supervisor

    log.info(f"[X-FEED] started — reading every {X_FEED_INTERVAL}s")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await asyncio.sleep(X_FEED_INTERVAL)
                client = getattr(supervisor, "_client", None)
                if client is None:
                    continue          # scanners down: no client to borrow
                await _read_x_feed(client, session, on_row=_publish)
                # GMGN publishes a pair's socials within a minute or so of it
                # appearing. A row still pending well past that has none, and
                # keeping it costs a document per token launched — which on this
                # chain is thousands a day.
                await _col("x_links").delete_many(
                    {"kind": "pending", "found_at": {"$lt": time.time() - _PENDING_MAX_AGE}})
            except asyncio.CancelledError:
                log.info("[X-FEED] stopped")
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[X-FEED] pass failed: {exc}")


async def _publish(row: dict) -> None:
    from .ws_hub import hub
    await _col("x_links").update_one({"address": row["address"]},
                                     {"$set": {**row, "dt": _utc_now()}},
                                     upsert=True)
    await hub.broadcast("x_link", row)
    log.debug(f"[X-FEED] {row['symbol']} @{row['handle']} ({row['kind']})")


async def _read_x_feed(client, session: aiohttp.ClientSession,
                       limit: int = 12, on_row=None) -> list[dict]:
    """Resolve the newest linked tokens, publishing each one as it is ready.

    Published per row, not per pass: the X lookups take a second or two each, so
    holding a batch until the end of a pass was what made four or five tokens
    land at the same moment.
    """
    pairs = await client.get_sol_new_pairs(limit=200)
    pairs = [p for p in pairs if _is_pump(p)]
    linked = [(p, _social_link(p)) for p in pairs]
    linked = [(p, l) for p, l in linked if l]

    rows: list[dict] = []
    for pair, link in linked[:limit]:
        info = pair.get("base_token_info") or {}
        address = (pair.get("base_address") or info.get("address") or "").strip()
        if not address:
            continue
        known = await _col("x_links").find_one({"address": address},
                                               {"_id": 1, "kind": 1})
        if known and known.get("kind") != "pending":
            continue                  # already resolved; not news any more
        ref = x_client.parse_ref(link)
        prof = (await x_client.fetch_profile(session, ref.handle)
                if ref.kind != "none" else x_client.XProfile(handle=""))
        post = (await x_client.fetch_post(session, ref)
                if prof.found else x_client.XPost())
        rows.append({
            "symbol": info.get("symbol") or "?",
            "address": (pair.get("base_address") or info.get("address") or ""),
            "open_timestamp": float(pair.get("open_timestamp")
                                    or info.get("creation_timestamp") or 0),
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
            "found_at": time.time(),
            "source": "gmgn",
        })
        if on_row is not None:
            await on_row(rows[-1])
    return rows


# Link types that count as having an account behind them. A row starts as
# `pending` — recorded from our own socket a second after the pair exists — and
# only becomes displayable once GMGN publishes its socials. `none` means GMGN
# published something that is not an account at all (a contract address in the
# field, an X community link), which is not worth a row either.
_LINKED_KINDS = ("tweet", "profile")


async def x_links(limit: int = 40) -> dict:
    """Tokens with an X link, newest first. Read from Mongo — no upstream call."""
    rows = await _col("x_links").find({"kind": {"$in": list(_LINKED_KINDS)}}).to_list(400)
    rows.sort(key=lambda r: r.get("open_timestamp") or r.get("found_at") or 0,
              reverse=True)
    rows = rows[:limit]
    for r in rows:
        r.pop("_id", None)
        r.pop("dt", None)
    return {
        "at": time.time(),
        "interval": X_FEED_INTERVAL,
        "newest_age_minutes": (round((time.time() - rows[0]["open_timestamp"]) / 60, 1)
                               if rows and rows[0].get("open_timestamp") else None),
        "total": len(rows),
        "resolved": sum(1 for r in rows if r.get("resolved")),
        "verified": sum(1 for r in rows if r.get("verified")),
        "posts": sum(1 for r in rows if r.get("post_found")),
        "items": rows,
    }


async def recent(limit: int = 100, verdict: Optional[str] = None) -> list[dict]:
    flt: dict[str, Any] = {}
    if verdict:
        flt["verdict"] = verdict
    docs = await _col("ai_decisions").find(flt).to_list(1000)
    docs.sort(key=lambda d: d.get("at", 0), reverse=True)
    out = []
    for d in docs[:limit]:
        d.pop("_id", None)
        d["gmgn_url"] = _gmgn(d.get("address", ""))
        out.append(d)
    return out


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
    rows = await _col("ai_watch").find({}).to_list(500)
    rows.sort(key=lambda r: r.get("first_seen", 0), reverse=True)
    for r in rows:
        r.pop("_id", None)
        r["gmgn_url"] = _gmgn(r.get("address", ""))
    return rows
