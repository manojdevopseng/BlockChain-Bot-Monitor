"""Talking to Grok: the narrative prompt, the fact-check prompt, and the one
JSON call both go through."""

from __future__ import annotations

import json
import time
from typing import Optional

import aiohttp

from ..config import settings
from .common import _col, log, narratives


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
