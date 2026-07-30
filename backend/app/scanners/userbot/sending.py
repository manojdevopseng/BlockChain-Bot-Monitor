"""Outbound Telegram sending: per-chat pacing and FloodWait handling."""

from __future__ import annotations

import asyncio

from telethon.errors import FloodWaitError

from app import fwd_counters
from app.util import bare_chat_id

from .common import log


class ChatRateLimiter:
    """Per-destination send pacing.

    Telegram allows roughly 20 messages/minute into a single group; exceeding it
    earns a FloodWait (and repeated offences risk a temporary ban on the
    account). One userbot mirroring 100+ premium groups into DEST_PREMIUM_ALL
    can easily cross that, so every outbound send waits its turn per chat.
    """

    def __init__(self, per_minute: int) -> None:
        self._min_gap = 60.0 / max(1, per_minute)
        self._next_at: dict[int, float] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def acquire(self, chat_id: int) -> None:
        async with self._lock(chat_id):
            now = asyncio.get_event_loop().time()
            wait = self._next_at.get(chat_id, 0) - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = asyncio.get_event_loop().time()
            self._next_at[chat_id] = now + self._min_gap

    def penalise(self, chat_id: int, seconds: float) -> None:
        """Telegram told us to back off — don't send to this chat until then."""
        self._next_at[chat_id] = asyncio.get_event_loop().time() + seconds


async def safe_send(chat_id, coro_factory, limiter: ChatRateLimiter, tag: str):
    """Run a Telethon send/forward with pacing + FloodWait handling.

    `coro_factory` is a zero-arg callable returning a fresh coroutine, so the
    call can be retried after a FloodWait without reusing an awaited coroutine.
    """
    if chat_id is None:
        return None   # destination not configured in .env — skip silently
    key = bare_chat_id(chat_id)
    for attempt in (1, 2):
        await limiter.acquire(key)
        try:
            sent = await coro_factory()
            fwd_counters.bump(fwd_counters.DEST, key)
            return sent
        except FloodWaitError as exc:
            wait = int(getattr(exc, "seconds", 30)) + 1
            limiter.penalise(key, wait)
            log.warning(f"[{tag}] FloodWait {wait}s on chat {chat_id} — pausing this destination")
            if attempt == 2:
                return None
            await asyncio.sleep(wait)
        except Exception:
            raise
    return None
