"""TelegramForwarder — Telethon userbot (ported from core/forwarder.py).

Faithful port of the reference forwarder. What changed:
  • storage: JSON files → MongoDB (premium_groups, premium_detections,
    premium_archive collections). Premium groups, keywords and Otto hash rules
    are seeded from app/data/seed_data.json (nothing hardcoded) and stay
    user-editable. The dashboard "Forwarder Groups" add flow (settings router →
    forwarder_sources 'pending' docs) is resolved here and saved to premium_groups.
  • keyword detection: now uses the whole-word rule (app.keywords) reading the
    shared Mongo `keywords` collection — "ai" matches "new ai agent", not "main".
  • toggle gating: each handler is gated by its registry service so the Bots
    switches (BBCAnalyser2 / DexSignalCall / ETH otto Group / Premium Callers /
    Forwarder master) enable/disable forwarding live.

Handler logic (filters, dedup, forward rules) is unchanged from the reference.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

from app.scanners import scfg as config
from app.scanners.bounded_set import BoundedSet
from app.scanners.slog import get_logger
from app.keywords import match_any
from app import fwd_counters

log = get_logger(__name__)

_ETH_RPCS = [
    "https://ethereum.publicnode.com",
    "https://1rpc.io/eth",
    "https://eth.llamarpc.com",
    "https://rpc.flashbots.net",
]

DEST_OTTO               = config.DEST_OTTO
DEST_SIGNALS            = config.DEST_SIGNALS
DEST_DEXS               = config.DEST_DEXS
DEST_PREMIUM_ETH_CALLER = config.DEST_PREMIUM_ETH_CALLER
DEST_PREMIUM_ALL        = config.DEST_PREMIUM_ALL

SOURCE_OTTO   = config.SOURCE_OTTO
SOURCE_DEXS   = config.SOURCE_DEXS
SOURCE_CALL   = config.SOURCE_CALL
SOURCE_BUYBOT = config.SOURCE_BUYBOT

# How often the live premium-group set is re-read from Mongo.
PREMIUM_RELOAD_SECONDS = 20

# Cap on the per-message dedup guards. ~50k ids is days of traffic.
_DEDUP_MAX = 50000

# Registry service id that gates each handler.
GATE_CALL    = "bbcanalyser2"            # CallAnalyser2
GATE_DEXS    = "dexsignalcall"           # dexssignal
GATE_OTTO    = "eth_otto_group"          # OttoEthDeployments
GATE_PREMIUM = "premium_callers_signal"  # premium groups → premium ETH caller

# Premium groups, trigger keywords and Otto hash rules are NOT hardcoded here.
# They are seeded once from app/data/seed_data.json into MongoDB and loaded at
# start() (see _load_premium_ids / _load_otto_rules / _load_filter_keywords).
# Groups/keywords the user adds via the dashboard persist in the same Mongo
# collections, so this file stays free of environment/user data.

_HASH_RE = re.compile(r"#([a-fA-F0-9]{8})")
_ETH_RE  = re.compile(r"0x[a-fA-F0-9]{40}", re.IGNORECASE)
# Solana base58 mint/address (32-44 chars, base58 alphabet — no 0 O I l).
# Word-bounded so it doesn't slice a substring out of a longer token.
_SOL_RE  = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

IST = timezone(timedelta(hours=5, minutes=30))
_DETECTED_MAX = 300


class _ChatRateLimiter:
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


async def _safe_send(chat_id, coro_factory, limiter: "_ChatRateLimiter", tag: str):
    """Run a Telethon send/forward with pacing + FloodWait handling.

    `coro_factory` is a zero-arg callable returning a fresh coroutine, so the
    call can be retried after a FloodWait without reusing an awaited coroutine.
    """
    if chat_id is None:
        return None   # destination not configured in .env — skip silently
    key = _bare_id(chat_id)
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


def _bare_id(cid) -> int:
    s = str(cid).strip()
    if s.startswith("-100"):
        return int(s[4:])
    return abs(int(s))


def _ist_day(ts: float):
    return datetime.fromtimestamp(ts, IST).date()


# ── Mongo helpers (replace the reference's JSON files) ──────────────────────────

def _col(name: str):
    from .. import db
    return db.get_collection(name)


async def _load_premium_ids() -> set:
    """Live premium group filter — built-in (seeded) + user-added, all in the
    `premium_groups` collection. Nothing hardcoded."""
    docs = await _col("premium_groups").find({"enabled": {"$ne": False}}).to_list(5000)
    return {_bare_id(d["id"]) for d in docs if d.get("id") is not None}


async def _load_otto_rules() -> tuple[set, set, set]:
    doc = await _col("otto_rules").find_one({}) or {}
    return (set(doc.get("method_ids", [])),
            set(doc.get("function_texts", [])),
            set(doc.get("rugger_hashes", [])))


async def _load_filter_keywords() -> tuple[list, list]:
    doc = await _col("filter_keywords").find_one({}) or {}
    return (list(doc.get("call", [])), list(doc.get("buybot", [])))


async def _load_detections(chain: str) -> list:
    docs = await _col("premium_detections").find({"chain": chain}).to_list(_DETECTED_MAX)
    docs.sort(key=lambda d: d.get("ts", 0), reverse=True)
    return docs


class TelegramForwarder:
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
        self._processed:         BoundedSet = BoundedSet(_DEDUP_MAX)
        self._group_eth_tracker: BoundedSet = BoundedSet(_DEDUP_MAX)
        self._eth_global_counter: dict = {}
        self._http: Optional[aiohttp.ClientSession] = None

        # Loaded from Mongo in start() (seeded from seed_data.json, user-editable).
        self._premium_ids: set = set()
        # Groups whose title we have already written back.
        self._named: set = set()
        self._call_keywords: list = []
        self._buybot_keywords: list = []
        self._method_ids: set = set()
        self._function_texts: set = set()
        self._rugger_hashes: set = set()

        self._watcher_task: Optional[asyncio.Task] = None
        self._rollover_task: Optional[asyncio.Task] = None
        self._last_rollover_day = _ist_day(time.time())

        self._capture_seen: BoundedSet = BoundedSet(20000)

        # Outbound pacing so one account mirroring many groups never trips
        # Telegram's per-chat flood limits.
        from ..config import settings as _s
        self._limiter = _ChatRateLimiter(_s.tg_max_per_minute)

        # Live registry toggle map (updated by the supervisor on reconcile).
        self._enabled: dict[str, bool] = {}

        self.count_signals = self.count_dexs = self.count_premium = self.count_otto = 0

    def set_enabled_map(self, enabled: dict[str, bool]) -> None:
        self._enabled = dict(enabled)

    def _on(self, gate: str) -> bool:
        # Master Forwarder toggle must be on, plus the per-source gate.
        return self._enabled.get("forwarder", True) and self._enabled.get(gate, True)

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
        self._premium_ids = await _load_premium_ids()
        self._method_ids, self._function_texts, self._rugger_hashes = await _load_otto_rules()
        self._call_keywords, self._buybot_keywords = await _load_filter_keywords()
        # Warm capture dedup from existing detections so restart doesn't re-count.
        for chain in ("eth", "rbh"):
            for d in await _load_detections(chain):
                for gid in d.get("group_ids", [d.get("chat_id")]):
                    self._capture_seen.add(f"{gid}:{d.get('address')}")
                    if d.get("pair"):
                        self._capture_seen.add(f"{gid}:{d.get('pair')}")
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
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[PREMIUM] reload failed: {exc}")

    async def reload_premium_ids(self) -> int:
        """Re-read the enabled premium groups. Returns the live count.

        Called on a timer and directly by the dashboard when a group is added,
        so a new group is live immediately rather than up to a cycle later.
        """
        fresh = await _load_premium_ids()
        if fresh != self._premium_ids:
            added = len(fresh - self._premium_ids)
            removed = len(self._premium_ids - fresh)
            self._premium_ids = fresh
            if added or removed:
                log.info(f"[PREMIUM] Group list updated — {len(fresh)} live "
                         f"(+{added} / -{removed})")
        return len(self._premium_ids)

    async def _daily_rollover_watcher(self) -> None:
        """At IST midnight, archive both detection panels into premium_archive
        and clear premium_detections for the new day."""
        while True:
            try:
                await asyncio.sleep(60)
                today = _ist_day(time.time())
                if today == self._last_rollover_day:
                    continue
                for chain in ("eth", "rbh"):
                    items = await _load_detections(chain)
                    if items:
                        await _col("premium_archive").insert_one({
                            "chain": chain,
                            "date": self._last_rollover_day.strftime("%d-%m-%Y"),
                            "items": [{k: v for k, v in d.items() if k not in ("_id", "dt")} for d in items],
                            "count": len(items),
                            # TTL field — archives expire per ARCHIVE_RETENTION_DAYS.
                            "dt": datetime.now(timezone.utc),
                        })
                    await _col("premium_detections").delete_many({"chain": chain})
                log.info(f"[DAILY-ROLLOVER] New IST day — panels archived + cleared")
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
            await _col("premium_groups").update_one(
                {"id": {"$in": [bare, -bare, int(f"-100{bare}")]}},
                {"$set": {"name": title,
                          "username": getattr(chat, "username", None)}},
            )
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[FWD] could not read group title for {bare}: {exc}")

    # ── Keyword detection (whole-word, from Mongo) ─────────────────────────────

    async def _match_keywords(self, text: str) -> str:
        docs = await _col("keywords").find({}).to_list(500)
        words = [d.get("word", "") for d in docs]
        return match_any(words, text or "")

    # ── On-chain helpers (verbatim) ────────────────────────────────────────────

    async def _rpc_call(self, rpc_url: str, addr: str, data: Optional[str], method: str = "eth_call"):
        params = [{"to": addr, "data": data}, "latest"] if method == "eth_call" else [addr, "latest"]
        async with self._http.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=aiohttp.ClientTimeout(total=6),
        ) as resp:
            return (await resp.json(content_type=None)).get("result")

    async def _resolve_token(self, rpc_url: str, addr: str, bases: set):
        try:
            t0, t1 = await asyncio.gather(
                self._rpc_call(rpc_url, addr, "0x0dfe1681"),
                self._rpc_call(rpc_url, addr, "0xd21220a7"),
            )
        except Exception:
            return addr, None
        a0, a1 = _addr_from_word(t0), _addr_from_word(t1)
        if not a0 or not a1:
            return addr, None
        if a0 in bases and a1 not in bases:
            return a1, addr
        if a1 in bases and a0 not in bases:
            return a0, addr
        return (a1 if a0 in bases else a0), addr

    async def _capture_premium_eth(self, addr: str, chat_id: int, group: str, text: str,
                                   username: Optional[str] = None, msg_id: Optional[int] = None) -> None:
        if not self._http:
            return
        keyword = await self._match_keywords(text)
        native0 = "0x" + "0" * 40
        eth_bases = {
            config.ETH_WETH.lower(),
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "0xdac17f958d2ee523a2206206994597c13d831ec7",
            "0x6b175474e89094c44da98b954eedeac495271d0f",
            native0,
        }
        rbh_bases = {config.RBH_WETH.lower(), "0x5fc5360d0400a0fd4f2af552add042d716f1d168", native0}

        async def check_chain(label: str, chain: str, rpc_url: str, bases: set) -> None:
            try:
                if not rpc_url:
                    return
                code = await self._rpc_call(rpc_url, addr, None, "eth_getCode")
                if not code or code == "0x":
                    return
                token_addr, pair_addr = await self._resolve_token(rpc_url, addr, bases)
                token_l = token_addr.lower()
                existing = await _col("premium_detections").find_one(
                    {"chain": chain, "address": {"$regex": f"^{re.escape(token_l)}$", "$options": "i"}}
                )
                if existing:
                    entries = existing.get("group_entries") or []
                    if any(e.get("chat_id") == chat_id for e in entries):
                        return
                    entries = [{"chat_id": chat_id, "name": group, "username": username, "message_id": msg_id}] + entries
                    await _col("premium_detections").update_one(
                        {"_id": existing["_id"]},
                        {"$set": {
                            "group_entries": entries,
                            "groups": [e["name"] for e in entries],
                            "group_ids": [e["chat_id"] for e in entries],
                            "count": len(entries),
                            "ts": time.time(),
                            "keyword": existing.get("keyword") or keyword,
                        }},
                    )
                    log.info(f"[PREMIUM-{label}] {existing.get('symbol') or token_addr[:10]} shill count → {len(entries)} (from {group})")
                    return
                name_hex, sym_hex = await asyncio.gather(
                    self._rpc_call(rpc_url, token_addr, "0x06fdde03"),
                    self._rpc_call(rpc_url, token_addr, "0x95d89b41"),
                )
                record = {
                    "chain": chain,
                    "symbol": _decode_symbol(sym_hex or ""),
                    "name": _decode_symbol(name_hex or ""),
                    "address": token_addr,
                    "pair": pair_addr,
                    "group_entries": [{"chat_id": chat_id, "name": group, "username": username, "message_id": msg_id}],
                    "groups": [group],
                    "group_ids": [chat_id],
                    "count": 1,
                    "chat_id": chat_id,
                    "keyword": keyword,
                    "ts": time.time(),
                }
                await _col("premium_detections").insert_one(record)
                from ..ws_hub import hub
                await hub.broadcast("premium_detection", {k: v for k, v in record.items() if k != "_id"})
                log.info(f"[PREMIUM-{label}] Captured {record['symbol'] or token_addr[:10]} from {group} | "
                         + (f"{keyword} Matched" if keyword else "Not Matched"))
            except Exception as exc:
                log.debug(f"[PREMIUM-{label}] capture failed for {addr[:10]}: {exc}")

        await asyncio.gather(
            check_chain("ETH", "eth", config.ETH_RPC_HTTP, eth_bases),
            check_chain("RBH", "rbh", config.RBH_RPC_HTTP, rbh_bases),
        )

    # ── SOL premium capture (dashboard panel — new) ───────────────────────────

    async def _sol_rpc(self, method: str, params: list):
        async with self._http.post(
            config.SOL_RPC_HTTP,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=aiohttp.ClientTimeout(total=6),
        ) as resp:
            return (await resp.json(content_type=None)).get("result")

    async def _sol_token_info(self, address: str) -> dict:
        """Best-effort symbol/name via GMGN web quotation API (no key needed)."""
        try:
            url = f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{address}"
            async with self._http.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                data = await resp.json(content_type=None)
            d = (data.get("data") or {})
            tok = d.get("token") or d
            return {"symbol": tok.get("symbol") or "", "name": tok.get("name") or ""}
        except Exception:
            return {}

    async def _capture_premium_sol(self, addr: str, chat_id: int, group: str, text: str,
                                   username: Optional[str] = None, msg_id: Optional[int] = None) -> None:
        """Capture a Solana address seen in a premium group into the SOL panel.
        Dormant unless SOL_RPC_HTTP is set (mirrors the ETH/RBH behaviour); the
        RPC getAccountInfo check filters out random base58 noise."""
        if not self._http or not config.SOL_RPC_HTTP:
            return
        try:
            info = await self._sol_rpc("getAccountInfo", [addr, {"encoding": "base64"}])
            if not info or info.get("value") is None:
                return  # not a real on-chain account — skip
        except Exception:
            return

        keyword = await self._match_keywords(text)
        col = _col("premium_detections")
        existing = await col.find_one({"chain": "sol", "address": addr})
        if existing:
            entries = existing.get("group_entries") or []
            if any(e.get("chat_id") == chat_id for e in entries):
                return
            entries = [{"chat_id": chat_id, "name": group, "username": username, "message_id": msg_id}] + entries
            await col.update_one({"_id": existing["_id"]}, {"$set": {
                "group_entries": entries, "groups": [e["name"] for e in entries],
                "group_ids": [e["chat_id"] for e in entries], "count": len(entries),
                "ts": time.time(), "keyword": existing.get("keyword") or keyword}})
            log.info(f"[PREMIUM-SOL] {existing.get('symbol') or addr[:10]} shill count → {len(entries)} (from {group})")
            return

        meta = await self._sol_token_info(addr)
        record = {
            "chain": "sol", "symbol": meta.get("symbol", ""), "name": meta.get("name", ""),
            "address": addr, "pair": None,
            "group_entries": [{"chat_id": chat_id, "name": group, "username": username, "message_id": msg_id}],
            "groups": [group], "group_ids": [chat_id], "count": 1, "chat_id": chat_id,
            "keyword": keyword, "ts": time.time(),
        }
        await col.insert_one(record)
        from ..ws_hub import hub
        await hub.broadcast("premium_detection", {k: v for k, v in record.items() if k != "_id"})
        log.info(f"[PREMIUM-SOL] Captured {record['symbol'] or addr[:10]} from {group} | "
                 + (f"{keyword} Matched" if keyword else "Not Matched"))

    async def _fetch_token_symbol(self, address: str) -> str:
        if not self._http:
            return ""
        payload = {"jsonrpc": "2.0", "method": "eth_call",
                   "params": [{"to": address, "data": "0x95d89b41"}, "latest"], "id": 1}
        for rpc in _ETH_RPCS:
            try:
                async with self._http.post(rpc, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json(content_type=None)
                hex_result = data.get("result", "")
                if hex_result and hex_result != "0x":
                    return _decode_symbol(hex_result)
            except Exception:
                continue
        return ""

    # ── Handler registration ────────────────────────────────────────────────────

    def _register_handlers(self) -> None:
        self._client.add_event_handler(self._call_handler, events.NewMessage(chats=SOURCE_CALL))
        self._client.add_event_handler(self._buybot_handler, events.NewMessage(chats=SOURCE_BUYBOT))
        self._client.add_event_handler(self._dexs_handler, events.NewMessage(chats=SOURCE_DEXS))
        self._client.add_event_handler(self._premium_handler, events.NewMessage())
        self._client.add_event_handler(self._otto_handler, events.MessageEdited(chats=SOURCE_OTTO))

    # ── Handlers (logic verbatim; toggle gate added at the top of each) ────────

    async def _call_handler(self, event) -> None:
        fwd_counters.bump(fwd_counters.SOURCE, SOURCE_CALL)
        if not self._on(GATE_CALL):
            return
        unique_id = f"{event.chat_id}_{event.id}"
        if unique_id in self._processed:
            return
        message = event.raw_text.lower()
        if any(k.lower() in message for k in self._call_keywords) and _ETH_RE.search(message):
            self._processed.add(unique_id)
            try:
                await _safe_send(DEST_SIGNALS, lambda: event.forward_to(DEST_SIGNALS),
                                 self._limiter, "CALL")
                self.count_signals += 1
                log.info("[CALL] Forwarded -> Signals")
            except Exception as exc:
                log.error(f"[CALL] Forward error: {exc}")

    async def _buybot_handler(self, event) -> None:
        fwd_counters.bump(fwd_counters.SOURCE, SOURCE_BUYBOT)
        if not self._on("forwarder"):
            return
        unique_id = f"{event.chat_id}_{event.id}"
        if unique_id in self._processed:
            return
        message = event.raw_text.lower()
        if any(k.lower() in message for k in self._buybot_keywords) and re.search(r"chain:\s*eth", message):
            self._processed.add(unique_id)
            try:
                await _safe_send(DEST_SIGNALS, lambda: event.forward_to(DEST_SIGNALS),
                                 self._limiter, "BUYBOT")
                self.count_signals += 1
                log.info("[BUYBOT] Forwarded -> Signals")
            except Exception as exc:
                log.error(f"[BUYBOT] Forward error: {exc}")

    async def _dexs_handler(self, event) -> None:
        fwd_counters.bump(fwd_counters.SOURCE, SOURCE_DEXS)
        if not self._on(GATE_DEXS):
            return
        unique_id = f"{event.chat_id}_{event.id}"
        if unique_id in self._processed:
            return
        message = event.raw_text.lower()
        if _ETH_RE.search(message) and re.search(r"chain:.*(ethereum|base)", message):
            self._processed.add(unique_id)
            try:
                await _safe_send(DEST_DEXS, lambda: event.forward_to(DEST_DEXS),
                                 self._limiter, "DEXS")
                self.count_dexs += 1
                log.info("[DEXS] Forwarded -> Dexs Group")
            except Exception as exc:
                log.error(f"[DEXS] Forward error: {exc}")
                await _safe_send(
                    DEST_DEXS,
                    lambda: self._client.send_message(DEST_DEXS, "ETH/BASE DEXS SIGNAL\n\n" + event.raw_text),
                    self._limiter, "DEXS",
                )

    async def _premium_handler(self, event) -> None:
        if not self._on(GATE_PREMIUM):
            return
        bare = _bare_id(event.chat_id)
        if bare not in self._premium_ids:
            return
        fwd_counters.bump(fwd_counters.SOURCE, bare)
        await self._learn_group_name(event, bare)
        unique_id = f"{event.chat_id}_{event.id}"
        if unique_id in self._processed:
            return

        if DEST_PREMIUM_ALL:
            # Highest-volume path: every premium message is mirrored here, so it
            # is the most likely to hit Telegram's per-chat flood limit.
            try:
                await _safe_send(DEST_PREMIUM_ALL, lambda: event.forward_to(DEST_PREMIUM_ALL),
                                 self._limiter, "PREMIUM-ALL")
            except Exception as exc:
                err = str(exc)
                if "noforwards" in err or "can't do that operation" in err or "invalid" in err.lower():
                    text = event.raw_text or ""
                    if text:
                        try:
                            chat = await event.get_chat()
                            source = getattr(chat, "title", "Unknown")
                            await _safe_send(
                                DEST_PREMIUM_ALL,
                                lambda: self._client.send_message(DEST_PREMIUM_ALL, f"📢 {source}\n\n{text}"),
                                self._limiter, "PREMIUM-ALL",
                            )
                        except Exception as exc2:  # noqa: BLE001
                            # Last resort for a no-forwards group failed too, so
                            # this message is not mirrored anywhere. Identical
                            # repeats are collapsed by the log dedup filter.
                            log.warning(f"[PREMIUM-ALL] copy fallback failed for "
                                        f"chat {event.chat_id}: {exc2}")
                else:
                    log.error(f"[PREMIUM-ALL] Forward error: {exc}")

        raw = event.raw_text or ""
        message = raw.lower()

        # Resolve the source once — needed by both SOL and ETH capture.
        chat = await event.get_chat()
        source_name = getattr(chat, "title", "Unknown")
        source_uname = getattr(chat, "username", None)
        bare = _bare_id(event.chat_id)

        # ── SOL address capture (dashboard-only panel; independent of ETH) ──
        for sol_addr in set(_SOL_RE.findall(raw)):
            sol_key = f"{bare}:{sol_addr}"
            if sol_key not in self._capture_seen:
                self._capture_seen.add(sol_key)
                asyncio.create_task(self._capture_premium_sol(
                    sol_addr, bare, source_name, raw,
                    username=source_uname, msg_id=event.id,
                ))

        eth_match = _ETH_RE.search(message)
        if not eth_match:
            return
        eth_address = eth_match.group(0).lower()

        cap_key = f"{bare}:{eth_address}"
        if cap_key not in self._capture_seen:
            self._capture_seen.add(cap_key)
            asyncio.create_task(self._capture_premium_eth(
                eth_address, bare, source_name, raw,
                username=source_uname, msg_id=event.id,
            ))

        group_key = (event.chat_id, eth_address)
        if group_key in self._group_eth_tracker:
            return
        if self._eth_global_counter.get(eth_address, 0) >= 2:
            return
        self._group_eth_tracker.add(group_key)
        self._eth_global_counter[eth_address] = self._eth_global_counter.get(eth_address, 0) + 1
        self._processed.add(unique_id)

        symbol = await self._fetch_token_symbol(eth_address)
        token_line = f"Token: <b><code>{symbol}</code></b>\n" if symbol else ""
        try:
            forwarded_msg = await _safe_send(
                DEST_PREMIUM_ETH_CALLER,
                lambda: event.forward_to(DEST_PREMIUM_ETH_CALLER),
                self._limiter, "PREMIUM",
            )
            if forwarded_msg is None:
                return
            await _safe_send(
                DEST_PREMIUM_ETH_CALLER,
                lambda: self._client.send_message(
                    DEST_PREMIUM_ETH_CALLER,
                    (f"SOURCE: {source_name}\n{token_line}"
                     f"ETH: <code>{eth_address}</code>\n"
                     f"TOTAL CALLS: {self._eth_global_counter[eth_address]}/2"),
                    reply_to=forwarded_msg.id, parse_mode="html",
                ),
                self._limiter, "PREMIUM",
            )
            self.count_premium += 1
            log.info(f"[PREMIUM] [{source_name}] {symbol or '?'} -> PremiumETH "
                     f"({self._eth_global_counter[eth_address]}/2)")
        except Exception as exc:
            log.error(f"[PREMIUM] Forward error: {exc}")

    async def _otto_handler(self, event) -> None:
        if not self._on(GATE_OTTO):
            return
        unique_id = f"{event.chat_id}_{event.id}"
        if unique_id in self._processed:
            return
        message = event.raw_text.lower()
        if "method ids hash" not in message or "functions text" not in message:
            return
        hashes = {"#" + h for h in _HASH_RE.findall(message)}
        if any(h in self._method_ids for h in hashes) or any(h in self._function_texts for h in hashes):
            self._processed.add(unique_id)
            is_rugger = any(h in self._rugger_hashes for h in hashes)
            rugger_prefix = "🛑 🛑RUGGER 🛑🛑\n\n" if is_rugger else ""
            try:
                if is_rugger:
                    await _safe_send(
                        DEST_OTTO,
                        lambda: self._client.send_message(DEST_OTTO, rugger_prefix + event.raw_text),
                        self._limiter, "OTTO",
                    )
                else:
                    await _safe_send(DEST_OTTO, lambda: event.forward_to(DEST_OTTO),
                                     self._limiter, "OTTO")
                self.count_otto += 1
                log.info(f"[OTTO] {'🛑 RUGGER ' if is_rugger else ''}Forwarded -> Otto Group")
            except Exception as exc:
                log.error(f"[OTTO] Forward error: {exc}")
                await _safe_send(
                    DEST_OTTO,
                    lambda: self._client.send_message(
                        DEST_OTTO, rugger_prefix + "MATCHED TOKEN (COPY MODE)\n\n" + event.raw_text),
                    self._limiter, "OTTO",
                )


# ── Helpers (verbatim) ──────────────────────────────────────────────────────────

def _addr_from_word(hex_result: Optional[str]) -> Optional[str]:
    if not hex_result:
        return None
    h = hex_result[2:] if hex_result.startswith("0x") else hex_result
    if len(h) < 64:
        return None
    a = "0x" + h[-40:].lower()
    return None if a == "0x" + "0" * 40 else a


def _decode_symbol(hex_result: str) -> str:
    try:
        raw = hex_result[2:] if hex_result.startswith("0x") else hex_result
        if len(raw) < 64:
            return ""
        if len(raw) >= 128:
            str_length = int(raw[64:128], 16)
            if str_length == 0 or str_length > 100:
                raise ValueError("invalid length")
            str_hex = raw[128:128 + str_length * 2]
            return bytes.fromhex(str_hex).decode("utf-8", errors="replace").strip()
        return bytes.fromhex(raw[:64]).rstrip(b"\x00").decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
