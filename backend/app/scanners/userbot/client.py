"""TelegramForwarder — the Telethon userbot's lifecycle and shared state.

Everything that is genuinely about *being* the userbot lives here: connecting,
reconnecting, the background watchers, and the state the handlers read
(premium group set, keyword lists, Otto rules, dedup guards, RPC pools).

The work each watched feed does is in `handlers.py`; the on-chain reads are in
`onchain.py`; the premium panel detections are in `premium.py`. They are mixed in
rather than split off as collaborators because they all operate on this
object's state — mixins keep `self.…` meaning exactly what it did when this
was one 946-line file.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from telethon import TelegramClient

from app.keywords import match_any
from app.scanners import scfg as config
from app.scanners.bounded_set import BoundedSet
from app.scanners.wss_pool import EndpointPool
from app.util import ist_day

from .common import DEDUP_MAX, DETECTION_CHAINS, PREMIUM_RELOAD_SECONDS, log
from .handlers import HandlersMixin
from .onchain import OnChainMixin
from .premium import PremiumCaptureMixin
from .sending import ChatRateLimiter
from .store import (col, load_detections, load_filter_keywords, load_group_names,
                    load_otto_rules,
                    load_ic_ids, load_premium_ids)


class TelegramForwarder(OnChainMixin, PremiumCaptureMixin, HandlersMixin):
    def __init__(self) -> None:
        self._client = TelegramClient(
            config.TELETHON_SESSION,
            config.TELETHON_API_ID,
            config.TELETHON_API_HASH,
        )
        # Bounded, not plain sets: one entry goes in per message seen and none
        # ever came out, so on a 24/7 userbot mirroring 100+ groups these grew
        # without limit. FIFO eviction — an id old enough to be evicted is far
        # past any chance of arriving again. (The reference has the same leak.)
        self._processed:         BoundedSet = BoundedSet(DEDUP_MAX)
        self._http: Optional[aiohttp.ClientSession] = None
        # One pool per premium-check job, each its own provider doing its own
        # thing — see scfg.{SOL,ETH,RBH}_HTTP_ENDPOINTS. Distinct chain_labels
        # from the on-chain discovery WSS pools ("Ethereum"/"Robinhood Chain")
        # so an exhaustion alert here doesn't read as if discovery were down.
        self._sol_http_pool = EndpointPool(
            "SOL-HTTP", lambda: list(config.SOL_HTTP_ENDPOINTS),
            chain_label="SOL premium check")
        self._eth_http_pool = EndpointPool(
            "ETH-PREMIUM-HTTP", lambda: list(config.ETH_HTTP_ENDPOINTS),
            chain_label="Ethereum premium check")
        self._rbh_http_pool = EndpointPool(
            "RBH-PREMIUM-HTTP", lambda: list(config.RBH_HTTP_ENDPOINTS),
            chain_label="Robinhood premium check")
        self._bnb_http_pool = EndpointPool(
            "BNB-PREMIUM-HTTP", lambda: list(config.BNB_HTTP_ENDPOINTS),
            chain_label="BNB premium check")
        self._base_http_pool = EndpointPool(
            "BASE-PREMIUM-HTTP", config.base_endpoints,
            chain_label="Base premium check")

        # Loaded from Mongo in start() (seeded from seed_data.json, user-editable).
        self._premium_ids: set = set()
        self._ic_ids: set = set()
        # Groups whose title we have already written back.
        self._named: set = set()
        # bare id -> (title, username). The hot path reads the name from here
        # instead of asking Telethon for the chat on every single message.
        self._group_meta: dict = {}
        self._call_keywords: list = []
        self._buybot_keywords: list = []
        self._method_ids: set = set()
        self._function_texts: set = set()
        self._rugger_hashes: set = set()

        self._watcher_task: Optional[asyncio.Task] = None
        self._rollover_task: Optional[asyncio.Task] = None
        self._last_rollover_day = ist_day(time.time())

        self._detection_seen: BoundedSet = BoundedSet(20000)

        # Outbound pacing so one account mirroring many groups never trips
        # Telegram's per-chat flood limits.
        from ...config import settings as _s
        self._limiter = ChatRateLimiter(_s.tg_max_per_minute)

        # Live registry toggle map (updated by the supervisor on reconcile).
        self._enabled: dict[str, bool] = {}

        self.count_signals = self.count_dexs = self.count_premium = self.count_otto = 0

    def set_enabled_map(self, enabled: dict[str, bool]) -> None:
        self._enabled = dict(enabled)

    def _on(self, gate: str) -> bool:
        # Master Forwarder toggle must be on, plus the per-source gate.
        return self._enabled.get("forwarder", True) and self._enabled.get(gate, True)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        # NOTE: never call client.start() — on a missing/expired session it
        # prompts for a phone number on stdin, which raises EOFError under
        # systemd/uvicorn and would take the whole app down. Connect explicitly
        # and fail with a clear message instead; a userbot login cannot be done
        # headlessly, the .session file must be copied to the server.
        await self._client.connect()
        if not await self._client.is_user_authorized():
            session = f"{config.TELETHON_SESSION}.session"
            raise RuntimeError(
                f"Telethon session not authorized — copy '{session}' to the backend "
                f"folder (log in once locally with your account, then upload the file)."
            )
        self._http = aiohttp.ClientSession()
        # Load all runtime data from Mongo (seeded from seed_data.json, editable
        # via the dashboard — nothing hardcoded).
        self._premium_ids = await load_premium_ids()
        self._group_meta = await load_group_names()
        self._ic_ids = await load_ic_ids()
        self._method_ids, self._function_texts, self._rugger_hashes = await load_otto_rules()
        self._call_keywords, self._buybot_keywords = await load_filter_keywords()
        # Warm the dedup guard from existing detections so a restart doesn't re-count.
        for chain in ("eth", "rbh"):
            for d in await load_detections(chain):
                for gid in d.get("group_ids", [d.get("chat_id")]):
                    self._detection_seen.add(f"{gid}:{d.get('address')}")
                    if d.get("pair"):
                        self._detection_seen.add(f"{gid}:{d.get('pair')}")
        self._register_handlers()
        self._watcher_task = asyncio.create_task(self._premium_watcher(), name="premium-watcher")
        self._rollover_task = asyncio.create_task(self._daily_rollover_watcher(), name="premium-rollover")
        log.info(
            "TelegramForwarder started — CallAnalyser2 | BuyBotTracker | DexsSignal | "
            f"{len(self._premium_ids)} premium groups | OttoEthDeployments"
        )

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                await self._client.run_until_disconnected()
                return
            except asyncio.CancelledError:
                return
            except Exception as exc:
                attempt += 1
                if attempt > 5:
                    log.error(f"TelegramForwarder disconnected: {exc} — giving up after 5 attempts")
                    return
                wait = min(30, 5 * attempt)
                log.error(f"TelegramForwarder disconnected: {exc} — reconnecting in {wait}s ({attempt}/5)")
                await asyncio.sleep(wait)
                try:
                    if not self._client.is_connected():
                        await self._client.connect()
                except Exception as ce:
                    log.error(f"TelegramForwarder reconnect failed: {ce}")

    async def stop(self) -> None:
        for t in (self._watcher_task, self._rollover_task):
            if t:
                t.cancel()
        try:
            await asyncio.wait_for(self._client.disconnect(), timeout=8)
        except (asyncio.TimeoutError, Exception) as exc:
            log.warning(f"TelegramForwarder disconnect timed out/failed: {exc}")
        if self._http and not self._http.closed:
            await self._http.close()
        log.info("TelegramForwarder stopped")

    # ── Watchers ───────────────────────────────────────────────────────────────

    async def _premium_watcher(self) -> None:
        """Keep the live premium set in step with the `premium_groups` collection.

        It used to only resolve dashboard-added 'pending' rows, which left a
        gap: the set was built once at start, so switching a group off in the
        dashboard did nothing until the next restart. Re-reading the collection
        covers adds, removals and toggles the same way.

        One find over ~100 tiny documents every RELOAD_SECONDS — cheap enough
        to be worth not having to invalidate anything.
        """
        while True:
            try:
                await asyncio.sleep(PREMIUM_RELOAD_SECONDS)
                await self.reload_premium_ids()
                self._ic_ids = await load_ic_ids()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[PREMIUM] reload failed: {exc}")

    async def reload_premium_ids(self) -> int:
        """Re-read the enabled premium groups. Returns the live count.

        Called on a timer and directly by the dashboard when a group is added,
        so a new group is live immediately rather than up to a cycle later.
        """
        fresh = await load_premium_ids()
        if fresh != self._premium_ids:
            added = len(fresh - self._premium_ids)
            removed = len(self._premium_ids - fresh)
            self._premium_ids = fresh
            if added or removed:
                log.info(f"[PREMIUM] Group list updated — {len(fresh)} live "
                         f"(+{added} / -{removed})")
        return len(self._premium_ids)

    async def reload_ic_ids(self) -> int:
        """Re-read the starred groups. Called by the dashboard on a star click,
        so the mirror starts with the next message rather than up to a reload
        cycle later."""
        fresh = await load_ic_ids()
        if fresh != self._ic_ids:
            log.info(f"[IC] Starred callers updated — {len(fresh)} live "
                     f"(+{len(fresh - self._ic_ids)} / -{len(self._ic_ids - fresh)})")
            self._ic_ids = fresh
        return len(self._ic_ids)

    async def _daily_rollover_watcher(self) -> None:
        """At IST midnight, archive every detection panel into premium_archive
        and clear premium_detections for the new day."""
        while True:
            try:
                await asyncio.sleep(60)
                today = ist_day(time.time())
                if today == self._last_rollover_day:
                    continue
                for chain in DETECTION_CHAINS:
                    items = await load_detections(chain)
                    if items:
                        await col("premium_archive").insert_one({
                            "chain": chain,
                            "date": self._last_rollover_day.strftime("%d-%m-%Y"),
                            "items": [{k: v for k, v in d.items() if k not in ("_id", "dt")} for d in items],
                            "count": len(items),
                            # TTL field — archives expire per ARCHIVE_RETENTION_DAYS.
                            "dt": datetime.now(timezone.utc),
                        })
                    await col("premium_detections").delete_many({"chain": chain})
                log.info("[DAILY-ROLLOVER] New IST day — panels archived + cleared")
                self._last_rollover_day = today
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.debug(f"[DAILY-ROLLOVER] watcher error: {exc}")

    async def _learn_group_name(self, event, bare: int) -> None:
        """Fill in a premium group's title the first time it speaks.

        The seeded rows carry ids only, so the dashboard would list 111
        anonymous numbers. The userbot already has the chat object here.
        """
        if bare in self._named:
            return
        self._named.add(bare)
        try:
            chat = await event.get_chat()
            title = getattr(chat, "title", None) or getattr(chat, "username", None)
            if not title:
                return
            self._group_meta[bare] = (title, getattr(chat, "username", None))
            await col("premium_groups").update_one(
                {"id": {"$in": [bare, -bare, int(f"-100{bare}")]}},
                {"$set": {"name": title,
                          "username": getattr(chat, "username", None)}},
            )
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[FWD] could not read group title for {bare}: {exc}")

    # ── Keyword detection (whole-word, from Mongo) ─────────────────────────────

    async def _match_keywords(self, text: str) -> str:
        docs = await col("keywords").find({}).to_list(500)
        words = [d.get("word", "") for d in docs]
        return match_any(words, text or "")
