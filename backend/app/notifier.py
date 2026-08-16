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


# The bot's own @name, asked once and kept. Needed to build the connect link a
# customer taps — and asked rather than configured, because a name typed into
# .env by hand is a name that will one day be wrong.
_bot_username: str = ""


async def bot_username() -> str:
    """The bot's @name without the @, or "" if it cannot be asked."""
    global _bot_username
    if _bot_username or not settings.telegram_bot_token:
        return _bot_username
    try:
        session = await _session_get()
        async with session.get(
                f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/getMe",
                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            body = await resp.json(content_type=None)
        _bot_username = str((body.get("result") or {}).get("username") or "")
    except Exception as exc:  # noqa: BLE001
        _safe_print(f"[notifier] getMe failed: {exc}")
    return _bot_username


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


async def send_to(chat_id, text: str, *, silent: bool = False,
                  buttons: Optional[list[tuple[str, str]]] = None,
                  keyboard: Optional[list[list[dict]]] = None) -> bool:
    """Send an HTML message to a specific chat. Never raises.

    `send` is the operational channel (ALERT_CHAT_ID); this is for features that
    have a chat of their own — premium detections go to one chat per chain.

    `buttons` is [(label, url), …], laid out as one inline row. A link that is
    a button instead of text keeps it out of the message body, where it would
    otherwise sit as a bare URL under everything else.
    """
    ok, _ = await send_result(chat_id, text, silent=silent, buttons=buttons,
                              keyboard=keyboard)
    return ok


async def send_result(chat_id, text: str, *, silent: bool = False,
                      buttons: Optional[list[tuple[str, str]]] = None,
                      keyboard: Optional[list[list[dict]]] = None
                      ) -> tuple[bool, float]:
    """(sent, seconds to wait before trying this chat again).

    The second half is what `send_to` throws away and the alert dispatcher
    cannot: sending to many chats at once is how a bot meets 429, and Telegram
    says in the refusal exactly how long to wait. Guessing instead — a fixed
    sleep, or worse a retry — is how a bot earns a longer ban.
    """
    if not chat_id or not settings.telegram_bot_token:
        return False, 0.0
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": silent,
    }
    # `keyboard` is rows already built (tgstyle.keyboard); `buttons` is the
    # older one-row [(label, url)] shape, kept because plenty of callers still
    # only ever want one row of links.
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    elif buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": label, "url": url} for label, url in buttons]]
        }
    try:
        session = await _session_get()
        async with session.post(
            f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/sendMessage",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                return True, 0.0
            body = await resp.text()
            _safe_print(f"[notifier] Telegram error {resp.status} for {chat_id}: {body[:200]}")
            return False, _retry_after(body) if resp.status == 429 else 0.0
    except Exception as exc:  # noqa: BLE001
        _safe_print(f"[notifier] send to {chat_id} failed: {exc}")
        return False, 0.0


def _retry_after(body: str) -> float:
    """The wait Telegram asked for, off a 429 body. 5s when it did not say."""
    import json as _json
    try:
        got = _json.loads(body or "{}")
        wait = (got.get("parameters") or {}).get("retry_after")
        return float(wait) if wait else 5.0
    except Exception:  # noqa: BLE001
        return 5.0


async def send_panel(chat_id, text: str, keyboard: list[list[dict]]) -> Optional[int]:
    """Send a message carrying callback buttons, and return its message id.

    `send_to`'s buttons are links, which Telegram opens itself. These are
    buttons that come back to us — a settings screen rather than a shortcut —
    so the id matters: the panel is edited in place afterwards rather than
    posting a new copy on every press.
    """
    if not chat_id or not settings.telegram_bot_token:
        return None
    try:
        session = await _session_get()
        async with session.post(
            f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True,
                  "reply_markup": {"inline_keyboard": keyboard}},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            body = await resp.json()
    except Exception as exc:  # noqa: BLE001
        _safe_print(f"[notifier] panel to {chat_id} failed: {exc}")
        return None
    if not body.get("ok"):
        _safe_print(f"[notifier] panel rejected: {str(body)[:200]}")
        return None
    return (body.get("result") or {}).get("message_id")


async def edit_panel(chat_id, message_id, text: str,
                     keyboard: list[list[dict]]) -> bool:
    """Redraw a panel in place. A screen, not a stream of messages."""
    if not chat_id or not message_id or not settings.telegram_bot_token:
        return False
    try:
        session = await _session_get()
        async with session.post(
            f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True,
                  "reply_markup": {"inline_keyboard": keyboard}},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            body = await resp.json()
    except Exception as exc:  # noqa: BLE001
        _safe_print(f"[notifier] panel edit failed: {exc}")
        return False
    # "message is not modified" is Telegram objecting that nothing changed,
    # which happens when a button is pressed twice. Not a failure.
    if not body.get("ok") and "not modified" not in str(body).lower():
        _safe_print(f"[notifier] panel edit rejected: {str(body)[:200]}")
        return False
    return True


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
        "🟢 <b>SightLine STARTED</b>\n"
        f"<i>{html.escape(_now())}</i>"
        + (f"\n\n{details}" if details else "")
    )


async def notify_shutdown(reason: str = "graceful stop") -> None:
    await send(
        "🔴 <b>SightLine STOPPED</b>\n"
        f"<i>{html.escape(_now())}</i>\n"
        f"Reason: {html.escape(reason)}"
    )


async def notify_restart(uptime_seconds: int) -> None:
    """Sent on startup when a previous run ended without a clean shutdown."""
    await send(
        "🔁 <b>SightLine RESTARTED</b>\n"
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

async def notify_rpc_exhausted(chain: str, body: str, chat_id=None) -> None:
    """Every endpoint for a chain is refusing. Detection there is down.

    `chat_id` sends it somewhere other than the alert group: a pool that
    belongs to one feature reports where that feature is being watched.
    """
    text = (
        "🛑 <b>ALL RPC ENDPOINTS EXHAUSTED</b> — " + html.escape(chain) + "\n"
        f"<i>{_now()}</i>\n\n"
        + html.escape(body)
    )
    await (send_to(chat_id, text) if chat_id else send(text))


async def notify_rpc_recovered(chain: str, body: str, chat_id=None) -> None:
    """A chain that was fully down has a working endpoint again."""
    text = (
        "✅ <b>RPC RECOVERED</b> — " + html.escape(chain) + "\n"
        f"<i>{_now()}</i>\n\n"
        + html.escape(body)
    )
    await (send_to(chat_id, text) if chat_id else send(text))


def _now() -> str:
    return time.strftime("%d-%m-%Y %H:%M:%S")
