"""Sending what the workers found to the people who asked for it.

One bot serves every account, so this is the piece that decides not what is
interesting — the workers already did that — but who wants it, and at what pace
Telegram will accept it.

    worker ──deliver(event)──▶ queue ──▶ fan-out ──▶ per-chat pacing ──▶ Telegram
                                          │
                                          └──▶ digest buffer ──▶ one summary

Three rules run the whole thing:

  never block a worker   `deliver` puts the event on a bounded queue and
                         returns. A launch is never held up by Telegram, and a
                         backlog is dropped oldest-first rather than grown —
                         an alert ten minutes late is not an alert.

  never flood            One send at a time, spaced globally and per chat, and
                         a 429 is honoured for exactly as long as Telegram
                         asked. Every account shares one bot token: a flood ban
                         earned on one account's behalf silences all of them.

  never surprise a bill  Every subscriber has a daily cap and can ask for a
                         digest instead of a message per event.

The operator's own groups are untouched by any of this. The workers still send
there directly, exactly as before — this runs after that and in addition.
"""

from __future__ import annotations

import asyncio
import html
import time
from typing import Optional

from . import alert_subs, db, notifier, telegram_link
from .alert_subs import Event
from .scanners.slog import get_logger
from .util import ist_date_str

log = get_logger(__name__)

# The queue between the workers and the sending. Bounded on purpose: if Telegram
# is refusing for minutes the backlog is capped rather than unbounded, and the
# oldest are the ones dropped.
_QUEUE_MAX = 500
_queue: "asyncio.Queue[Event]" = asyncio.Queue(maxsize=_QUEUE_MAX)

# Telegram takes roughly 30 messages a second across all chats, and about one a
# second sustained to any single chat. Both are held well under.
_GLOBAL_GAP = 0.05          # 20/sec across everybody
_PER_CHAT_GAP = 1.2         # slower to one chat than the limit allows

# How often the subscriber list is re-read. Matching happens in memory — a
# database query per subscriber per event would be thousands a minute.
_RELOAD_SECONDS = 30

_last_global = 0.0
_last_chat: dict[int, float] = {}
# One sender at a time. The pump is single, but a trial's delayed send runs in
# its own task — without this the two could compute the same gap and send
# together, which is exactly the burst the gaps exist to prevent.
_send_lock = asyncio.Lock()
_subs: list[dict] = []
# username -> the events waiting to go out in their next digest.
_digests: dict[str, list[tuple[float, Event, str]]] = {}
_digest_sent: dict[str, float] = {}

_dropped = 0


def deliver(event: Event) -> None:
    """Hand one event to the fan-out. Never blocks, never raises.

    Called from inside the workers, which are on the hot path of a launch — so
    this does exactly one thing: put it on the queue.
    """
    global _dropped
    try:
        _queue.put_nowait(event)
    except asyncio.QueueFull:
        # Drop the oldest, keep the newest: at capacity the queue is minutes
        # behind, and the front of it is the least worth sending.
        try:
            _queue.get_nowait()
            _queue.put_nowait(event)
        except Exception:  # noqa: BLE001
            pass
        _dropped += 1
        if _dropped % 50 == 1:
            log.warning(f"[FANOUT] queue full — {_dropped} event(s) dropped")


# ── counting what an account has been sent ───────────────────────────────────

def _usage():
    return db.get_collection("usage_daily")


async def sent_today(username: str) -> int:
    doc = await _usage().find_one({"user_id": username,
                                   "day": ist_date_str(time.time())}) or {}
    return int(doc.get("alerts_sent") or 0)


async def _note_sent(username: str, count: int = 1) -> int:
    res = await _usage().find_one_and_update(
        {"user_id": username, "day": ist_date_str(time.time())},
        {"$inc": {"alerts_sent": count}, "$set": {"updated_at": time.time()}},
        upsert=True, return_document=True)
    return int((res or {}).get("alerts_sent") or count)


# ── the loops ────────────────────────────────────────────────────────────────

async def run() -> None:
    """Started by the supervisor alongside the workers."""
    log.info("[FANOUT] alert fan-out started")
    await _reload()
    await asyncio.gather(_pump(), _refresher(), _digest_clock())


async def _refresher() -> None:
    while True:
        try:
            await asyncio.sleep(_RELOAD_SECONDS)
            await _reload()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[FANOUT] reload failed: {exc}")


async def _reload() -> None:
    global _subs
    try:
        _subs = await alert_subs.all_active()
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[FANOUT] subscriber list unreadable: {exc}")


async def _pump() -> None:
    while True:
        try:
            event = await _queue.get()
            await _fan_out(event)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            # One bad event must never take the pump down with it — every later
            # alert would be lost silently.
            log.warning(f"[FANOUT] event failed: {type(exc).__name__}: {exc}")


async def _fan_out(event: Event) -> None:
    for sub in list(_subs):
        username = str(sub.get("user_id") or "")
        if not username:
            continue
        wanted, _why = alert_subs.matches(sub, event)
        if not wanted:
            continue
        if alert_subs.in_quiet_hours(sub):
            continue
        if str(sub.get("mode")) == "digest":
            _digests.setdefault(username, []).append(
                (time.time(), event, alert_subs.hit_keywords(sub, event)))
            continue
        delay = float((sub.get("plan") or {}).get("delay_seconds") or 0)
        if delay > 0:
            # The trial's handicap. In its own task rather than a sleep here:
            # the pump serves every other subscriber, and one plan's delay must
            # not become everybody's.
            asyncio.create_task(_send_later(sub, event, delay),
                                name=f"fanout-late-{username}")
            continue
        await _send_one(sub, event)


async def _send_later(sub: dict, event: Event, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        await _send_one(sub, event)
    except asyncio.CancelledError:
        return
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[FANOUT] delayed send failed: {exc}")


async def _send_one(sub: dict, event: Event) -> None:
    username = str(sub.get("user_id") or "")
    used = await sent_today(username)
    cap = int(sub.get("daily_cap") or 0)
    if cap and used >= cap:
        # Said once a day, not once an event: the point is that they know, not
        # that they are told two hundred times.
        if used == cap:
            await _note_sent(username)      # ticks past the cap so this is once
            chat, _ = await telegram_link.alert_target(username, None)
            if chat:
                await _paced(chat, f"🔕 That is {cap} alerts today — your daily "
                                   f"cap. More are waiting on the dashboard; "
                                   f"raise the cap in Alert Rules.")
        return
    chat, why = await telegram_link.alert_target(username, None)
    if not chat:
        return
    mine = alert_subs.hit_keywords(sub, event)
    text = event.text
    if mine:
        text = f"🟢 <b>Your keyword:</b> {html.escape(mine)}\n" + text
    if await _paced(chat, text, event.buttons):
        await _note_sent(username)


async def _paced(chat, text: str, buttons: Optional[list] = None) -> bool:
    """One send, spaced globally and per chat, honouring a 429 exactly once.

    Serialised under one lock, so the gaps below are the whole rate limiter
    however many tasks are sending — the pump, the digests and the delayed
    trial sends all queue here.
    """
    global _last_global
    async with _send_lock:
        now = time.time()
        wait = max(_GLOBAL_GAP - (now - _last_global),
                   _PER_CHAT_GAP - (now - _last_chat.get(chat, 0.0)))
        if wait > 0:
            await asyncio.sleep(wait)

        ok, retry_after = await notifier.send_result(chat, text,
                                                     buttons=buttons or None)
        _last_global = time.time()
        _last_chat[chat] = _last_global
        if not ok and retry_after:
            log.warning(f"[FANOUT] Telegram asked for {retry_after:.0f}s on {chat}")
            await asyncio.sleep(retry_after)
            ok, _ = await notifier.send_result(chat, text, buttons=buttons or None)
            _last_global = time.time()
            _last_chat[chat] = _last_global
    return ok


# ── digests ──────────────────────────────────────────────────────────────────

async def _digest_clock() -> None:
    """Flush each digest subscriber on their own interval."""
    while True:
        try:
            await asyncio.sleep(30)
            for username in list(_digests):
                sub = next((s for s in _subs
                            if str(s.get("user_id")) == username), None)
                if sub is None:
                    _digests.pop(username, None)
                    continue
                every = int(sub.get("digest_minutes") or 15) * 60
                if time.time() - _digest_sent.get(username, 0.0) < every:
                    continue
                await _flush_digest(username, sub)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[FANOUT] digest: {exc}")


async def _flush_digest(username: str, sub: dict) -> None:
    items = _digests.pop(username, [])
    _digest_sent[username] = time.time()
    if not items:
        return
    if alert_subs.in_quiet_hours(sub):
        return              # held back rather than queued: it would be stale
    used = await sent_today(username)
    cap = int(sub.get("daily_cap") or 0)
    if cap and used >= cap:
        return
    chat, _ = await telegram_link.alert_target(username, None)
    if not chat:
        return

    # One line per launch, most recent first, capped — a digest that needs
    # scrolling is not a digest.
    lines = []
    for _when, event, mine in reversed(items[-25:]):
        bits = [f"<b>{html.escape(event.symbol or '?')}</b>"]
        if event.launchpad:
            bits.append(event.launchpad)
        if event.handle:
            bits.append(f"@{html.escape(event.handle)}"
                        + (f" · {event.followers:,}" if event.followers else ""))
        if event.strong:
            bits.append("🟢 strong dev buy")
        if mine:
            bits.append(f"kw: {html.escape(mine)}")
        lines.append("• " + " · ".join(bits) + f"\n  <code>{event.address}</code>")

    minutes = int(sub.get("digest_minutes") or 15)
    head = (f"📬 <b>Last {minutes} minutes</b> — {len(items)} match"
            f"{'' if len(items) == 1 else 'es'}")
    if len(items) > 25:
        head += f" (newest 25 shown)"
    if await _paced(chat, head + "\n➖➖➖➖➖➖➖➖➖➖\n" + "\n".join(lines)):
        await _note_sent(username)


def queue_depth() -> int:
    return _queue.qsize()


def dropped() -> int:
    return _dropped
