"""Sending to Telegram — the message bodies and the two senders."""

from __future__ import annotations

from typing import Optional

import aiohttp

from .. import pump_mcap
from ..config import settings
from ..util import esc
from .common import TELEGRAM_API, chat_id, log


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
