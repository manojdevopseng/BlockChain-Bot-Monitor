"""Asking the model about a launch and recording what it said.

`watch()` is the loop the supervisor runs; `run_once` is one pass of it.
"""

from __future__ import annotations

import asyncio
import time

import aiohttp

from .. import x_client
from ..config import settings
from ..util import esc, ist_date_str
from .common import (MAX_ERROR_RETRIES, MAX_PER_CYCLE, RETRY_PENDING_PER_CYCLE,
                     SETTLED, _LINKED_KINDS, _col, _utc_now, log)
from .grok import ask_grok
from .notify import _message, _notify
from .tgfilter import _check_telegram, _settle_mcaps


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
# ── Judging ────────────────────────────────────────────────────────────────────

async def _judge_profile(session, token: dict, row: dict, profile,
                         preview: bool = True) -> bool:
    """A launch whose X link points at an account rather than at a post.

    A post says what it is about; an account does not, so the question becomes
    whether the account has published a contract address, and whether it is
    this token's. Three answers, and they are the whole of it:

        the account names THIS token   -> launching, with the address confirmed
        the account names another one  -> skipped, it is not about this launch
        the account names none yet     -> launching, still waiting

    These stay in Launching either way, and nothing happens to them after that:
    the account is not re-read, the bio is not polled, no timer runs. Launching
    is a resting place, not a queue. Judged once on the text the launch arrived
    with, and left there. The narrative is still read, because "which narrative"
    and "has it published an address" are separate questions and the first is
    worth having on the row whichever way the second goes.

    Only the bio is available to read. The account's latest tweet would be the
    better text, but every free mirror for it is unreachable from this server —
    measured across 353 profile launches, not one returned a post.
    """
    text = " ".join(filter(None, [row.get("excerpt"), row.get("description")]))
    if not text.strip():
        await _record(token, row, profile, "skipped",
                      {"reason": "nothing on the account to read"})
        return True

    address = token["address"]
    # The exact address, not "an address that looks like this one" — the
    # question is whether the account named THIS token, and near-misses are not
    # an answer to it.
    named = address in text
    other = None if named else x_client.find_contract(text)
    if other:
        await _record(token, row, profile, "skipped",
                      {"reason": f"the account publishes a different contract "
                                 f"({other[:12]}…)",
                       "ca_found": other, "ca_matched": False})
        return True

    # Same bio, same narrative — so the model is asked once per account and the
    # other launches on it read the answer off the first.
    detail: dict = {"ca_matched": named}
    prior = await _col("ai_decisions").find_one(
        {"link": row.get("link") or "", "narrative": {"$exists": True}},
        {"narrative": 1, "confidence": 1})
    if prior:
        detail["narrative"] = prior.get("narrative")
        detail["confidence"] = prior.get("confidence")
    else:
        verdict = await ask_grok(session, token, text, profile)
        if not verdict:
            if preview:
                await _record(token, row, profile, "pending",
                              {"reason": "passed every gate — waiting for the model",
                               "ca_matched": named})
            return False
        detail["narrative"] = str(verdict.get("narrative") or "none")
        detail["confidence"] = int(verdict.get("confidence") or 0)

    detail["reason"] = ("the account names this token's contract" if named
                        else "watching the account for a contract address")
    await _record(token, row, profile, "launching", detail)
    log.info(f"[AI] LAUNCHING {token.get('symbol')} — @{profile.handle}"
             f"{' · CA matched' if named else ''}")
    return True


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

    from .. import registry
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

        # A profile link is a different question from a post, so it takes a
        # different route — and it takes it before the link gate, because that
        # gate judges one launch per link and this check has to run on each of
        # them separately. An account whose bio names its contract names ONE
        # token; deduplicating by link would test the wrong one.
        if row.get("kind") == "profile":
            judged += 1
            if not await _judge_profile(session, token, row, profile, preview):
                await _mark_judged(address, False)
                break                     # model unreachable; try again later
            await _mark_judged(address)
            continue

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
