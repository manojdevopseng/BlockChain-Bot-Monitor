"""OnChainDetector — real-time new-pair detection over WebSocket (eth_subscribe).

Ported verbatim from the reference repo (core/onchain_detector.py); only the
imports changed. Listens for Uniswap V2/V3/V4 (and noxa.fun) pool-creation
events, filters to WETH/native-paired tokens, fetches ERC-20 name/symbol via
eth_call over the same WS, and fires `on_token` with a DetectedToken.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from app.scanners.ws_provider import SubscriptionSpec, WSProvider
from app.scanners.bounded_set import BoundedSet
from app.scanners.slog import get_logger
from app import heartbeat

log = get_logger(__name__)

_SEEN_MAX = 50000

TOPIC_V2_PAIR_CREATED = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
TOPIC_V3_POOL_CREATED = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
TOPIC_V4_INITIALIZE   = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"

NATIVE_ZERO = "0x0000000000000000000000000000000000000000"

_SEL_NAME   = "0x06fdde03"
_SEL_SYMBOL = "0x95d89b41"


@dataclass
class ChainSpec:
    name: str
    gmgn_slug: str
    wss_url: str
    base_addrs: frozenset
    v2_factory: Optional[str] = None
    v3_factory: Optional[str] = None
    v4_poolmanager: Optional[str] = None
    explorer_token_url: str = ""
    http_rpc: str = ""
    noxa_factory: Optional[str] = None
    noxa_topic: Optional[str] = None
    # Ordered failover list. Empty = just wss_url, which is every existing
    # caller — wss_url stays the primary and is what gets logged.
    wss_endpoints: tuple = ()
    # Preferred over wss_endpoints: a callable returning the current list, so an
    # endpoint added in Settings is dialled on the next reconnect. A tuple is a
    # snapshot taken when the scanner was built and goes stale the moment the
    # list is edited.
    wss_source: Optional[Callable[[], list]] = None
    # Where this chain's "all endpoints refusing" alert goes. None = the
    # general alert group. A feature with its own endpoints wants its own.
    alert_chat_id: Optional[int] = None
    # Human name for alerts — "Ethereum", not "ETH-XCHAIN".
    chain_label: str = ""


@dataclass
class DetectedToken:
    address: str
    symbol: str
    name: str
    dex: str
    pair: str
    chain: str
    tx_hash: str = ""     # pair-creation tx
    # Which side of the pool the base currency (WETH) sits on. The swap monitor
    # needs this to tell a BUY (WETH into the pool) from a SELL.
    weth_is_token0: bool = False
    # V4 only: the bytes32 pool id — swaps are filtered by it on the PoolManager.
    pool_id: Optional[str] = None
    detected_at: float = field(default_factory=time.time)


OnTokenCallback = Callable[[DetectedToken], Awaitable[None]]


class OnChainDetector:
    def __init__(self, spec: ChainSpec, on_token: OnTokenCallback) -> None:
        self._spec = spec
        self._on_token = on_token
        self._provider = WSProvider(
            spec.wss_source or (list(spec.wss_endpoints) or spec.wss_url),
            name=spec.name,
            chain_label=spec.chain_label or spec.name,
            alert_chat_id=spec.alert_chat_id,
        )
        self._seen: BoundedSet = BoundedSet(_SEEN_MAX)

        self._filters: list = []
        subs: list[str] = []
        if spec.noxa_factory and spec.noxa_topic:
            self._add_sub(spec.noxa_factory, spec.noxa_topic, self._handle_noxa, f"{spec.name}-NOXA")
            subs.append("NOXA")
        if spec.v2_factory:
            self._add_sub(spec.v2_factory, TOPIC_V2_PAIR_CREATED, self._handle_v2, f"{spec.name}-V2")
            subs.append("V2")
        if spec.v3_factory:
            self._add_sub(spec.v3_factory, TOPIC_V3_POOL_CREATED, self._handle_v3, f"{spec.name}-V3")
            subs.append("V3")
        if spec.v4_poolmanager:
            self._add_sub(spec.v4_poolmanager, TOPIC_V4_INITIALIZE, self._handle_v4, f"{spec.name}-V4")
            subs.append("V4")
        self._enabled_versions = subs

        self._first_connect = True
        self._provider.register_on_connect(self._on_reconnect)

    def _add_sub(self, address: str, topic: str, handler, label: str) -> None:
        self._provider.add_persistent_spec(SubscriptionSpec(
            params=["logs", {"address": address, "topics": [topic]}],
            callback=handler, label=label,
        ))
        self._filters.append((address, topic, handler))

    @property
    def provider(self):
        # WS provider — gas_tracker fetches tx receipts WS-first over it.
        return self._provider

    @property
    def connected(self) -> bool:
        return self._provider.connected

    def down_seconds(self) -> float:
        return self._provider.down_seconds()

    async def run(self) -> None:
        log.info(
            f"[{self._spec.name}] On-chain detector starting — "
            f"versions: {'+'.join(self._enabled_versions) or 'none'} | "
            f"base: {len(self._spec.base_addrs)} addr(s) | eth_call over WSS"
        )
        await self._provider.run()

    async def _on_reconnect(self) -> None:
        if self._first_connect:
            self._first_connect = False
            return
        await self._catch_up()

    async def _catch_up(self) -> None:
        try:
            latest = int(await self._provider.rpc("eth_blockNumber", []), 16)
            frm    = hex(max(0, latest - 9))
            recovered = 0
            for address, topic, handler in self._filters:
                logs = await self._provider.rpc(
                    "eth_getLogs",
                    [{"fromBlock": frm, "toBlock": "latest",
                      "address": address, "topics": [topic]}],
                )
                for lg in logs or []:
                    recovered += 1
                    await handler(lg)
            if recovered:
                log.info(f"[{self._spec.name}] Reconnect catch-up scanned {recovered} recent log(s)")
        except Exception as exc:
            log.debug(f"[{self._spec.name}] catch-up skipped: {exc}")

    async def _handle_v2(self, log_obj: dict) -> None:
        topics = log_obj.get("topics", [])
        if len(topics) < 3:
            return
        token0 = _addr_from_topic(topics[1])
        token1 = _addr_from_topic(topics[2])
        new_token = self._pick_new_token(token0, token1)
        if new_token is None:
            return
        pair = _addr_from_data_word(log_obj.get("data", "0x"), 0)
        await self._process(new_token, pair or "", "v2", log_obj.get("transactionHash", ""),
                            weth_is_token0=(token0 in self._spec.base_addrs))

    async def _handle_v3(self, log_obj: dict) -> None:
        topics = log_obj.get("topics", [])
        if len(topics) < 4:
            return
        token0 = _addr_from_topic(topics[1])
        token1 = _addr_from_topic(topics[2])
        new_token = self._pick_new_token(token0, token1)
        if new_token is None:
            return
        pool = _addr_from_data_word(log_obj.get("data", "0x"), 1)
        await self._process(new_token, pool or "", "v3", log_obj.get("transactionHash", ""),
                            weth_is_token0=(token0 in self._spec.base_addrs))

    async def _handle_noxa(self, log_obj: dict) -> None:
        topics = log_obj.get("topics", [])
        if len(topics) < 2:
            return
        new_token = _addr_from_topic(topics[1])
        pool = _addr_from_data_word(log_obj.get("data", "0x"), 1)
        await self._process(new_token, pool or "", "noxa", log_obj.get("transactionHash", ""))

    async def _handle_v4(self, log_obj: dict) -> None:
        topics = log_obj.get("topics", [])
        if len(topics) < 4:
            return
        pool_id = topics[1]
        currency0 = _addr_from_topic(topics[2])
        currency1 = _addr_from_topic(topics[3])
        # Recorded before the "is this one of ours" check, and for every V4
        # pool: a pool id cannot be worked out later without knowing the hook,
        # and this subscription is the one place it arrives for free. The RSI
        # and Market Cap readers look here first, which is what lets them price
        # a hooked launchpad pool without asking an explorer that rate-limits.
        await self._remember_v4_pool(pool_id, currency0, currency1, log_obj)
        new_token = self._pick_new_token(currency0, currency1)
        if new_token is None:
            return
        # V4 swaps are emitted by the PoolManager, filtered by pool id — so the
        # "pair" for subscription purposes is the PoolManager address.
        await self._process(new_token, pool_id, "v4", log_obj.get("transactionHash", ""),
                            weth_is_token0=(currency0 in self._spec.base_addrs),
                            pool_id=pool_id,
                            pair_override=self._spec.v4_poolmanager or "")

    async def _remember_v4_pool(self, pool_id: str, currency0: str,
                                currency1: str, log_obj: dict) -> None:
        """Keep this pool's id against both its currencies.

        One small document per pool, written once. Never allowed to affect
        detection: a database that is down must not cost us a launch, so
        anything thrown here is logged at debug and dropped.
        """
        try:
            from app import db
            chain = self._spec.name.lower()
            data = (log_obj.get("data") or "0x")[2:]
            fee = int(data[0:64], 16) if len(data) >= 64 else 0
            hooks = "0x" + data[128:192][-40:] if len(data) >= 192 else ""
            await db.get_collection("v4_pools").update_one(
                {"chain": chain, "pool_id": pool_id},
                {"$set": {"chain": chain, "pool_id": pool_id,
                          "currency0": currency0.lower(),
                          "currency1": currency1.lower(),
                          "fee": fee, "hooks": hooks,
                          "block": log_obj.get("blockNumber", "")}},
                upsert=True)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[{self._spec.name}] could not record V4 pool: {exc}")

    def _pick_new_token(self, addr0: str, addr1: str) -> Optional[str]:
        base = self._spec.base_addrs
        a0_base = addr0 in base
        a1_base = addr1 in base
        if a0_base and not a1_base:
            return addr1
        if a1_base and not a0_base:
            return addr0
        return None

    async def _process(self, token_address: str, pair: str, dex: str, tx_hash: str = "",
                       weth_is_token0: bool = False, pool_id: Optional[str] = None,
                       pair_override: str = "") -> None:
        key = (pair or token_address).lower()
        if key in self._seen:
            return
        self._seen.add(key)

        log.info(f"[{self._spec.name}] New {dex.upper()} pair — token={token_address} pair={pair[:12]}…")

        meta = await self._fetch_metadata(token_address)
        if meta is None:
            log.debug(f"[{self._spec.name}] metadata fetch failed for {token_address} — skipping")
            return
        name, symbol = meta
        if not symbol:
            return

        tok = DetectedToken(
            address=token_address,
            symbol=symbol,
            name=name,
            dex=dex,
            # For V4 the swap subscription targets the PoolManager, not the pool id.
            pair=pair_override or pair,
            chain=self._spec.name,
            tx_hash=tx_hash,
            weth_is_token0=weth_is_token0,
            pool_id=pool_id,
        )
        try:
            heartbeat.beat("rbh_pair" if self._spec.name == "ROBINHOOD" else "eth_pair")
            await self._on_token(tok)
        except Exception as exc:
            log.error(f"[{self._spec.name}] on_token callback error: {exc}")

    async def _fetch_metadata(self, token_address: str) -> Optional[tuple[str, str]]:
        try:
            name_hex, symbol_hex = await asyncio.gather(
                self._eth_call(token_address, _SEL_NAME),
                self._eth_call(token_address, _SEL_SYMBOL),
            )
            name   = _decode_string(name_hex)   or "Unknown"
            symbol = (_decode_string(symbol_hex) or "").upper().strip()
            return name, symbol
        except Exception as exc:
            log.debug(f"[{self._spec.name}] eth_call failed for {token_address}: {exc}")
            return None

    async def _eth_call(self, to: str, data: str) -> Optional[str]:
        return await self._provider.rpc(
            "eth_call", [{"to": to, "data": data}, "latest"], timeout=6.0
        )


def _addr_from_topic(topic: str) -> str:
    t = topic[2:] if topic.startswith("0x") else topic
    return "0x" + t[-40:].lower()


def _addr_from_data_word(data_hex: str, word_index: int) -> Optional[str]:
    d = data_hex[2:] if data_hex.startswith("0x") else data_hex
    start = word_index * 64
    word = d[start:start + 64]
    if len(word) < 64:
        return None
    return "0x" + word[-40:].lower()


def _decode_string(hex_data: Optional[str]) -> Optional[str]:
    if not hex_data or hex_data in ("0x", "0x0"):
        return None
    try:
        raw = bytes.fromhex(hex_data[2:])
        if len(raw) < 96:
            return raw.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
        length = int.from_bytes(raw[32:64], "big")
        return raw[64:64 + length].decode("utf-8", errors="replace").strip()
    except Exception:
        return None
