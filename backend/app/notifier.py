"""Operational alerts to the BlockChainBot Telegram group.

Sends three kinds of notice to ALERT_CHAT_ID (set in .env):
  • startup / shutdown / restart  — so you always know the bot's state
  • errors                        — any ERROR-level log from a scanner
  • health                        — RPC/WebSocket down, GMGN auth expired, etc.

Uses the plain Bot API over HTTP (no Telethon), so it works even when the
forwarder/userbot is disabled — that matters, because the errors most worth
knowing about are the ones that stop the userbot from running.

Errors are de-duplicated: the same error signature is re-sent at most once per
ERROR_ALERT_COOLDOWN seconds, so a scanner failing every 5s cannot flood the
group (this is exactly what happens with a Cloudflare 403 loop).
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from typing import Optional

import aiohttp

from .config import settings

TELEGRAM_API = "https://api.telegram.org"

# error signature -> last time it was sent
_last_sent: dict[str, float] = {}
_session: Optional[aiohttp.ClientSession] = None
_lock = asyncio.Lock()

# Digits/hex/addresses vary between otherwise-identical errors; strip them so
# "RPC timeout for 0xabc…" and "RPC timeout for 0xdef…" share one signature.
_NOISE = re.compile(r"(0x[a-fA-F0-9]+|\b\d+(\.\d+)?\b)")


def enabled() -> bool:
    return bool(settings.telegram_bot_token and settings.alert_chat_id)


def _safe_print(msg: str) -> None:
    """Console-safe print: Windows consoles are cp1252 and raise on emoji."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))


def _signature(text: str) -> str:
    return _NOISE.sub("#", text)[:160]


async def _session_get() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None


async def send(text: str, *, silent: bool = False) -> bool:
    """Send an HTML message to the alert group. Never raises."""
    if not enabled():
        _safe_print(f"[notifier][DRY-RUN] {text.splitlines()[0] if text else ''}")
        return False
    try:
        session = await _session_get()
        async with session.post(
            f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/sendMessage",
            json={
                "chat_id": settings.alert_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": silent,
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                return True
            body = await resp.text()
            _safe_print(f"[notifier] Telegram error {resp.status}: {body[:200]}")
            return False
    except Exception as exc:  # noqa: BLE001
        _safe_print(f"[notifier] send failed: {exc}")
        return False


# ── Lifecycle ──────────────────────────────────────────────────────────────────

async def notify_startup(details: str = "") -> None:
    await send(
        "🟢 <b>BlockChain-Bot STARTED</b>\n"
        f"<i>{html.escape(_now())}</i>"
        + (f"\n\n{details}" if details else "")
    )


async def notify_shutdown(reason: str = "graceful stop") -> None:
    await send(
        "🔴 <b>BlockChain-Bot STOPPED</b>\n"
        f"<i>{html.escape(_now())}</i>\n"
        f"Reason: {html.escape(reason)}"
    )


async def notify_restart(uptime_seconds: int) -> None:
    """Sent on startup when a previous run ended without a clean shutdown."""
    await send(
        "🔁 <b>BlockChain-Bot RESTARTED</b>\n"
        f"<i>{html.escape(_now())}</i>\n"
        f"Previous run lasted {uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m"
    )


# ── Errors ─────────────────────────────────────────────────────────────────────

async def notify_error(service: str, message: str) -> None:
    """Report an ERROR-level event, throttled per unique error signature."""
    sig = f"{service}:{_signature(message)}"
    now = time.time()
    async with _lock:
        last = _last_sent.get(sig, 0)
        if now - last < settings.error_alert_cooldown:
            return  # already reported recently — stay quiet
        _last_sent[sig] = now
        # Bound the memory: drop signatures older than 2x the cooldown.
        if len(_last_sent) > 500:
            cutoff = now - settings.error_alert_cooldown * 2
            for k in [k for k, v in _last_sent.items() if v < cutoff]:
                _last_sent.pop(k, None)

    await send(
        "⚠️ <b>ERROR</b> — " + html.escape(service) + "\n"
        f"<i>{html.escape(_now())}</i>\n\n"
        f"<code>{html.escape(message[:600])}</code>\n\n"
        f"<i>Repeats suppressed for {settings.error_alert_cooldown // 60} min</i>"
    )


# ── RPC endpoint pool ──────────────────────────────────────────────────────────
#
# Its own message rather than notify_error, for two reasons: the throttling has
# to be per chain (EndpointPool owns it, so no cooldown is applied here), and
# this is the one alert that means a chain has stopped seeing anything. An
# ERROR-shaped message would sit in the same stream as the 429s that led up to
# it, which is exactly what happened on 29-07-2026 — three sockets logged
# individual 429s for hours and nothing said "all of them are down".

async def notify_rpc_exhausted(chain: str, body: str) -> None:
    """Every endpoint for a chain is refusing. Detection there is down."""
    await send(
        "🛑 <b>ALL RPC ENDPOINTS EXHAUSTED</b> — " + html.escape(chain) + "\n"
        f"<i>{_now()}</i>\n\n"
        + html.escape(body)
    )


async def notify_rpc_recovered(chain: str, body: str) -> None:
    """A chain that was fully down has a working endpoint again."""
    await send(
        "✅ <b>RPC RECOVERED</b> — " + html.escape(chain) + "\n"
        f"<i>{_now()}</i>\n\n"
        + html.escape(body)
    )


def _now() -> str:
    return time.strftime("%d-%m-%Y %H:%M:%S")
