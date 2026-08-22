"""What a token is worth, read off the chain, for the RSI tracker.

RSI needs a price series and nothing in this project had one — detections are
"an event happened", not "this is what it trades at". So this reads the pool
itself, in native terms (ETH per token, BNB per token), which is all RSI needs:
the indicator is scale-free, so a price in ETH gives the same RSI as a price in
dollars without a second data source to be rate-limited out of.

Two routes, tried in this order and then kept:

  V3  factory.getPool(token, wnative, fee) for each fee tier -> slot0()
      sqrtPriceX96. Every tier is checked and the deepest one wins: PEPE's
      500 tier exists, holds nothing, and reports a price of 3.4e38.
  V2  factory.getPair(token, wnative) -> getReserves().

Resolved once per token and cached — the pool does not move, and re-resolving
on every sample would cost four calls instead of one.

  V4  no pool address exists at all — every pool lives inside the PoolManager
      under a bytes32 id — so the id is worked out and the price read through
      the StateView periphery contract. Two ways to get the id, in order:

        computed  keccak of the PoolKey (currency0, currency1, fee, tickSpacing,
                  hooks). A standard pool has no hook, so the key is guessable:
                  the fee tiers against native and against wrapped native, and
                  the deepest one that answers wins. A launchpad pool has one,
                  but a launchpad runs the same hook for every token it mints —
                  so its address (RBH_V4_HOOKS) turns that back into a
                  computation too. No indexer, no key, no rate limit.
        recorded  the Robinhood detector already watches Initialize on the
                  PoolManager for its own reasons, so every pool opened while
                  we are running is written down as it happens (v4_pools) —
                  including under a hook nobody told us about.
        looked up last, and only then: the chain's explorer API. Blockscout's
                  free tier starts answering 429 under any real use, which is
                  exactly why it is not the thing this depends on.

Checked against a live launch: our reading of SPX4663 and letscash.fun's own
API agree to the last digit (1.3556577608171037 ETH).
"""

from __future__ import annotations

import asyncio

import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

from app.scanners import scfg as config
from app.scanners.slog import get_logger

log = get_logger(__name__)

# getPair(address,address) / getPool(address,address,uint24) / getReserves() /
# token0() / slot0() / liquidity() / decimals()
_SEL = {"pair": "0xe6a43905", "pool": "0x1698ee82", "reserves": "0x0902f1ac",
        "token0": "0x0dfe1681", "slot0": "0x3850c7bd", "liquidity": "0x1a686502",
        "decimals": "0x313ce567",
        # StateView, V4's read-only window onto the PoolManager.
        "v4_slot0": "0xc815641c", "v4_liquidity": "0xfa6793d5"}

# V4's own (fee, tickSpacing) pairs. A pool with a hook can use anything —
# LetsCash runs fee 0 with spacing 200 — which is why those are looked up
# instead of guessed.
_V4_TIERS = ((100, 1), (500, 10), (3000, 60), (10000, 200))

# What a hooked launchpad pool uses instead of a standard tier. LetsCash mints
# every token at fee 0 with tick spacing 200, and a launchpad has one shape for
# all of its launches — so knowing the hook is enough to compute the id.
_V4_HOOK_TIERS = ((0, 200), (0, 60), (0, 1))


def _hooks_for(chain: str) -> list[str]:
    """The hook contracts we know run pools on a chain, from .env."""
    raw = {"rbh": config.RBH_V4_HOOKS, "eth": config.ETH_V4_HOOKS,
           "bsc": config.BNB_V4_HOOKS}.get(chain, "")
    return [h.strip() for h in str(raw or "").split(",") if h.strip()]


# In V4 the native coin is a first-class currency at the zero address, so a
# token is usually paired with ETH itself rather than WETH.
_NATIVE = "0x0000000000000000000000000000000000000000"

# Initialize(id, currency0, currency1, fee, tickSpacing, hooks, sqrtPriceX96, tick)
_TOPIC_V4_INITIALIZE = ("0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307"
                        "b95f85e6110838d6438")

# Uniswap V3's three standard tiers. 100 exists too but is stablecoin-only in
# practice and has never held a token this tracker would be pointed at.
_FEE_TIERS = (500, 3000, 10000)

_TIMEOUT = aiohttp.ClientTimeout(total=12)

# How long "this token has no pool" is believed before it is asked again. Ten
# minutes: long enough that a token that genuinely has none is not re-scanned on
# every check, short enough that one added at launch starts reporting a price
# the moment its pool exists.
_RETRY_UNRESOLVED = 600.0


@dataclass
class ChainSpec:
    key: str
    label: str
    http: str
    wnative: str
    v2_factory: str
    v3_factory: str
    # V4: where pools live, where their state is read, and where a hooked
    # pool's id can be looked up. All three may be blank — then V4 is simply
    # not tried and the V2/V3 answer stands, exactly as before.
    v4_stateview: str = ""
    v4_poolmanager: str = ""
    explorer_api: str = ""


def chains() -> dict[str, ChainSpec]:
    """The chains the tracker can price on, from .env.

    Its own endpoints when they are set, otherwise the ones that chain already
    uses — so it runs before RSI_*_RPC_HTTP is filled in, which is how it gets
    tested at all.
    """
    return {
        "eth": ChainSpec("eth", "ETH",
                         config.RSI_ETH_RPC_HTTP or config.ETH_RPC_HTTP,
                         config.ETH_WETH, config.ETH_V2_FACTORY, config.ETH_V3_FACTORY,
                         config.ETH_V4_STATEVIEW, config.ETH_V4_POOLMANAGER,
                         config.ETH_EXPLORER_API),
        "bsc": ChainSpec("bsc", "BSC",
                         config.RSI_BSC_RPC_HTTP or config.BNB_RPC_HTTP,
                         config.BNB_WBNB, config.BNB_V2_FACTORY, config.BNB_V3_FACTORY,
                         config.BNB_V4_STATEVIEW, config.BNB_V4_POOLMANAGER,
                         config.BNB_EXPLORER_API),
        "rbh": ChainSpec("rbh", "RBH",
                         config.RSI_RBH_RPC_HTTP or config.RBH_RPC_HTTP,
                         config.RBH_WETH, config.RBH_V2_FACTORY, config.RBH_V3_FACTORY,
                         config.RBH_V4_STATEVIEW, config.RBH_V4_POOLMANAGER,
                         config.RBH_EXPLORER_API),
    }


@dataclass
class PoolRef:
    """Where a token's price is read from, resolved once."""
    chain: str
    token: str
    kind: str = ""            # "v3" | "v2" | "v4" | "" when nothing was found
    address: str = ""
    # V4 only: the pool's bytes32 id. There is no address to hold instead.
    pool_id: str = ""
    token_is_0: bool = False
    decimals: int = 18
    fee: int = 0
    found_at: float = 0.0


def _word(value: str) -> str:
    return value.lower().replace("0x", "").rjust(64, "0")


def _first_word(raw: Optional[str]) -> int:
    """The first 32-byte word of a return value, as a number. 0 when there is
    nothing to read — which for slot0 means the pool was never initialised."""
    if not raw or len(raw) < 66:
        return 0
    return int(raw[2:66], 16)


def _from_sqrt(sqrt_price: int, token_is_0: bool, decimals: int) -> Optional[float]:
    """Native per token, out of a sqrtPriceX96.

    The ratio is in raw units, so it is scaled by the difference in decimals —
    `10 ** (decimals - 18)` either way round, because the quote side (ETH, WETH,
    WBNB) is 18 decimals in every case. The inverse branch used to raise that to
    `18 - decimals`, which is only harmless while the token has 18 decimals
    itself: RSI never noticed because a constant factor cannot change it, but a
    market cap is a number and would have been wrong by 10**24 on a 6-decimal
    token quoted second.
    """
    ratio = (sqrt_price / (2 ** 96)) ** 2      # token1 per token0, raw units
    if not ratio:
        return None
    base = ratio if token_is_0 else 1 / ratio
    return base * (10 ** (decimals - 18))


_SAVE_TASKS: set = set()


def _spawn_save(ref: "PoolRef") -> None:
    """Store a resolved pool without making the caller wait for Mongo."""
    async def go() -> None:
        try:
            from app import db
            await db.get_collection("pool_refs").update_one(
                {"chain": ref.chain, "token": ref.token},
                {"$set": {"chain": ref.chain, "token": ref.token,
                          "kind": ref.kind, "address": ref.address,
                          "pool_id": ref.pool_id, "fee": int(ref.fee or 0),
                          "decimals": int(ref.decimals or 18),
                          "token_is_0": bool(ref.token_is_0),
                          "found_at": time.time()}},
                upsert=True)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[POOLREF] could not store {ref.token[:10]}: {exc}")

    task = asyncio.ensure_future(go())
    _SAVE_TASKS.add(task)
    task.add_done_callback(_SAVE_TASKS.discard)


def _pool_id(currency0: str, currency1: str, fee: int, tick_spacing: int,
             hooks: str) -> str:
    """V4's pool id: keccak of the PoolKey, five words, in order.

    Verified against a live pool — the id computed here is byte-for-byte the one
    the PoolManager reported in its own Initialize log.
    """
    from eth_utils import keccak

    def addr(value: str) -> bytes:
        return bytes(12) + bytes.fromhex(value.lower().replace("0x", "").rjust(40, "0"))

    def num(value: int) -> bytes:
        return int(value).to_bytes(32, "big", signed=value < 0)

    return "0x" + keccak(addr(currency0) + addr(currency1) + num(fee)
                         + num(tick_spacing) + addr(hooks)).hex()


class PriceReader:
    """One HTTP session, one cache of resolved pools."""

    def __init__(self, session: aiohttp.ClientSession,
                 specs: Optional[dict[str, ChainSpec]] = None,
                 tag: str = "RSI") -> None:
        self._session = session
        # What this reader calls itself in the log, so a Market Cap read is not
        # filed under [RSI] by a shared line.
        self._tag = tag
        self._pools: dict[tuple[str, str], PoolRef] = {}
        # Whose endpoints to read through. None means RSI's own, which is every
        # caller that existed before the Market Cap tracker: that one prices the
        # same pools on its own endpoints, and the pool-finding — three fee
        # tiers, deepest wins, V2 fallback — is the part worth having once.
        self._specs = specs

    def _chains(self) -> dict[str, ChainSpec]:
        return self._specs if self._specs is not None else chains()

    def forget(self, chain: str, token: str) -> None:
        self._pools.pop((chain, token.lower()), None)

    async def _call(self, spec: ChainSpec, to: str, data: str) -> Optional[str]:
        if not to or not spec.http:
            return None
        try:
            async with self._session.post(
                spec.http,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                      "params": [{"to": to, "data": data}, "latest"]},
                timeout=_TIMEOUT,
            ) as resp:
                body = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[{self._tag}] {spec.label} eth_call failed: {exc}")
            return None
        # A revert is the chain answering "no such pool", not a failure.
        return None if "error" in body else body.get("result")

    async def resolve(self, chain: str, token: str) -> PoolRef:
        key = (chain, token.lower())
        cached = self._pools.get(key)
        # A pool that was found never moves, so that answer is kept for the life
        # of the process. "No pool" is not the same kind of answer: a token
        # added the minute it launched, or one whose RPC was down at the time,
        # would otherwise stay unpriced until a restart. That one is retried.
        if cached is not None and (cached.kind
                                   or time.time() - cached.found_at < _RETRY_UNRESOLVED):
            return cached

        spec = self._chains().get(chain)
        ref = PoolRef(chain=chain, token=token.lower(), found_at=time.time())
        if spec is None or not spec.http or not spec.wnative:
            self._pools[key] = ref
            return ref

        raw = await self._call(spec, token, _SEL["decimals"])
        ref.decimals = int(raw, 16) if raw and raw != "0x" else 18

        best_liq = 0
        for fee in _FEE_TIERS:
            got = await self._call(spec, spec.v3_factory,
                                   _SEL["pool"] + _word(token) + _word(spec.wnative)
                                   + hex(fee)[2:].rjust(64, "0"))
            pool = "0x" + got[-40:] if got and len(got) >= 42 else ""
            if not pool or not int(pool, 16):
                continue
            liq_raw = await self._call(spec, pool, _SEL["liquidity"])
            liq = int(liq_raw, 16) if liq_raw and liq_raw != "0x" else 0
            # Deepest tier wins. An empty tier still answers slot0() and
            # reports a price off by twenty orders of magnitude.
            if liq > best_liq:
                t0 = await self._call(spec, pool, _SEL["token0"])
                best_liq = liq
                ref.kind, ref.address, ref.fee = "v3", pool, fee
                ref.token_is_0 = bool(t0) and ("0x" + t0[-40:]).lower() == token.lower()

        if not ref.kind:
            got = await self._call(spec, spec.v2_factory,
                                   _SEL["pair"] + _word(token) + _word(spec.wnative))
            pair = "0x" + got[-40:] if got and len(got) >= 42 else ""
            if pair and int(pair, 16):
                t0 = await self._call(spec, pair, _SEL["token0"])
                ref.kind, ref.address = "v2", pair
                ref.token_is_0 = bool(t0) and ("0x" + t0[-40:]).lower() == token.lower()

        # V4 last, because it costs the most calls — and it is what finds the
        # Robinhood launchpad tokens, which have no V2 or V3 pool at all.
        if not ref.kind:
            await self._resolve_v4(spec, token, ref)

        self._pools[key] = ref
        # Written down as well as remembered. This resolution costs a handful
        # of chain reads and produces exactly what a trade needs — which pool,
        # which version, which fee — and until now it lived in one object in
        # one process, so the trading side went and asked DexScreener for the
        # same answer at a cost of two seconds on a token nobody had indexed
        # yet. Persisted, that question stops being asked.
        if ref.kind:
            _spawn_save(ref)
        if ref.kind:
            log.info(f"[{self._tag}] {spec.label} {token[:10]}… priced from {ref.kind}"
                     + (f" {ref.fee}" if ref.fee else "") + f" pool {ref.address[:10]}…")
        else:
            log.info(f"[{self._tag}] {spec.label} {token[:10]}… has no V2/V3 pool against "
                     f"{spec.wnative[:8]}… — nothing to price it from yet")
        return ref

    # ── V4 ───────────────────────────────────────────────────────────────────

    async def _resolve_v4(self, spec: ChainSpec, token: str, ref: PoolRef) -> None:
        """Fill `ref` with the deepest V4 pool this token trades in, if any."""
        if not spec.v4_stateview:
            return
        best_liq = -1
        best: Optional[tuple[str, bool]] = None
        for pool_id, token_is_0 in await self._v4_candidates(spec, token):
            sqrt = await self._v4_sqrt_price(spec, pool_id)
            if not sqrt:
                continue                      # never initialised — not a pool
            liq = await self._v4_liquidity(spec, pool_id)
            # Deepest wins, the same rule as the V3 tiers and for the same
            # reason: an empty pool still answers, with a price off by twenty
            # orders of magnitude.
            if liq > best_liq:
                best_liq, best = liq, (pool_id, token_is_0)
        if best is None:
            return
        ref.kind, ref.pool_id, ref.token_is_0 = "v4", best[0], best[1]
        ref.address = spec.v4_poolmanager or spec.v4_stateview

    @staticmethod
    def _quotes_for(spec: ChainSpec) -> set:
        """What a price may be quoted in and still mean dollars downstream.

        The reader multiplies whatever a pool says by the native coin's dollar
        price. That is only true when the other side of the pool IS the native
        coin — a TOKEN/OTHERTOKEN pool priced that way is not wrong by a little,
        it is a different number entirely.
        """
        return {q for q in (_NATIVE, (spec.wnative or "").lower()) if q}

    def _quoted_in_native(self, spec: ChainSpec, token: str,
                          currency0: str, currency1: str) -> bool:
        """Is this pool the token against the native coin, either way round?"""
        t = token.lower()
        c0, c1 = (currency0 or "").lower(), (currency1 or "").lower()
        quotes = self._quotes_for(spec)
        return (c0 == t and c1 in quotes) or (c1 == t and c0 in quotes)

    async def _v4_candidates(self, spec: ChainSpec,
                             token: str) -> list[tuple[str, bool]]:
        """(pool id, is the token currency0) for every pool worth trying.

        The computed ones first — they cost nothing but keccak — and the
        explorer only when none of them answered, because that is a request to
        somebody else's server.
        """
        out: list[tuple[str, bool]] = []
        quotes = sorted(self._quotes_for(spec))
        # Hookless standard pools first, then the same tiers under each hook we
        # know about — a launchpad runs one hook for every token it mints, so
        # one address turns "cannot be guessed" back into "computed".
        hooks = [_NATIVE] + [h.lower() for h in _hooks_for(spec.key) if h]
        for quote in dict.fromkeys(quotes):
            c0, c1 = sorted([quote.lower(), token.lower()])
            for hook in dict.fromkeys(hooks):
                for fee, spacing in _V4_TIERS + _V4_HOOK_TIERS:
                    out.append((_pool_id(c0, c1, fee, spacing, hook),
                                c0 == token.lower()))
        found = []
        for pool_id, token_is_0 in dict.fromkeys(out):
            if await self._v4_sqrt_price(spec, pool_id):
                found.append((pool_id, token_is_0))
        if found:
            return found
        # Then the pools our own detector watched being created. Free, instant,
        # and it covers every hooked pool opened while we were running —
        # including ones whose hook nobody has told us about.
        found = await self._v4_from_our_own_records(spec, token)
        if found:
            return found
        return await self._v4_from_explorer(spec, token)

    async def _v4_from_our_own_records(self, spec: ChainSpec,
                                       token: str) -> list[tuple[str, bool]]:
        """Pool ids the Robinhood detector recorded from Initialize events."""
        try:
            from app import db
            rows = await db.get_collection("v4_pools").find(
                {"chain": spec.key,
                 "$or": [{"currency0": token.lower()},
                         {"currency1": token.lower()}]}).to_list(10)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[{self._tag}] v4_pools lookup failed: {exc}")
            return []
        # Only pools quoted in the native coin. Every token here has one of
        # each — a launchpad pool against the coin, and a pool against some
        # other token — and this used to return both, in whatever order the
        # database gave them. The first that answered won, so a token could be
        # priced in a currency the reader then treated as ETH: CLANKER came out
        # at $25.5bn against a real $159,701, off by a factor of 160,000.
        return [(r["pool_id"], str(r.get("currency0", "")).lower() == token.lower())
                for r in rows
                if r.get("pool_id")
                and self._quoted_in_native(spec, token,
                                           r.get("currency0", ""),
                                           r.get("currency1", ""))]

    async def _v4_from_explorer(self, spec: ChainSpec,
                                token: str) -> list[tuple[str, bool]]:
        """A hooked pool's id, out of its Initialize log.

        Its key holds the launchpad's own fee, tick spacing and hook address,
        so there is nothing to guess — but the log names the id outright. Asked
        once per token: a pool does not move, and the answer is cached with the
        rest of the PoolRef.
        """
        if not (spec.explorer_api and spec.v4_poolmanager):
            return []
        found: list[tuple[str, bool]] = []
        for slot in (2, 3):
            url = (f"{spec.explorer_api}?module=logs&action=getLogs"
                   f"&fromBlock=0&toBlock=latest&address={spec.v4_poolmanager}"
                   f"&topic0={_TOPIC_V4_INITIALIZE}&topic0_{slot}_opr=and"
                   f"&topic{slot}=0x{token.lower().replace('0x', '').rjust(64, '0')}")
            try:
                async with self._session.get(url, timeout=_TIMEOUT) as resp:
                    if resp.status != 200:
                        continue
                    body = await resp.json(content_type=None)
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[{self._tag}] {spec.label} explorer lookup failed: {exc}")
                continue
            rows = body.get("result") if isinstance(body.get("result"), list) else []
            for row in rows:
                topics = row.get("topics") or []
                if len(topics) < 4:
                    continue
                currency0 = "0x" + topics[2][-40:]
                currency1 = "0x" + topics[3][-40:]
                # Same rule as the records above: a pool against another token
                # cannot be turned into dollars by the native coin's price.
                if not self._quoted_in_native(spec, token, currency0, currency1):
                    continue
                found.append((topics[1], currency0.lower() == token.lower()))
        return found

    async def _v4_sqrt_price(self, spec: ChainSpec, pool_id: str) -> int:
        raw = await self._call(spec, spec.v4_stateview,
                               _SEL["v4_slot0"] + pool_id[2:])
        if not raw or len(raw) < 66:
            return 0
        return int(raw[2:66], 16)

    async def _v4_liquidity(self, spec: ChainSpec, pool_id: str) -> int:
        raw = await self._call(spec, spec.v4_stateview,
                               _SEL["v4_liquidity"] + pool_id[2:])
        return int(raw, 16) if raw and raw != "0x" else 0

    async def name_symbol(self, chain: str, token: str) -> tuple[str, str]:
        """The token's own ticker and name, off the contract.

        Asked so nobody has to type them. A row that says "?" because the ticker
        was left blank is a row you cannot recognise in a list or in an alert,
        and the two ERC-20 getters cost one call each, once, at add time.
        """
        from app.scanners.rbhx_monitor import decode_string_tuple
        spec = self._chains().get(chain)
        if spec is None:
            return "", ""

        async def one(selector: str) -> str:
            raw = await self._call(spec, token, selector)
            return (decode_string_tuple(raw or "") or [""])[0].strip()

        symbol, name = await one("0x95d89b41"), await one("0x06fdde03")
        return symbol[:32].upper(), name[:64]

    async def find_chains(self, token: str) -> list[str]:
        """Which chains actually have a pool for this address.

        The same 0x… exists on ETH, BSC and Robinhood and means a different
        token on each, so "which chain" cannot be guessed from the string. It
        can be asked, though: the chain that has a pool for it is the chain it
        trades on. Usually exactly one answers.
        """
        found = []
        for key in self._chains():
            ref = await self.resolve(key, token)
            if ref.kind:
                found.append(key)
        return found

    async def price(self, chain: str, token: str) -> Optional[float]:
        """Native per token, or None when it cannot be read right now.

        None is not zero: a missed read must leave a gap the candle builder
        fills with the last close, not a crash to zero that reads as -100%.
        """
        ref = await self.resolve(chain, token)
        spec = self._chains().get(chain)
        if not ref.kind or spec is None:
            return None

        if ref.kind in ("v3", "v4"):
            sqrt_price = (await self._v4_sqrt_price(spec, ref.pool_id)
                          if ref.kind == "v4" else
                          _first_word(await self._call(spec, ref.address,
                                                       _SEL["slot0"])))
            if not sqrt_price:
                return None
            return _from_sqrt(sqrt_price, ref.token_is_0, ref.decimals)

        raw = await self._call(spec, ref.address, _SEL["reserves"])
        if not raw or len(raw) < 130:
            return None
        r0, r1 = int(raw[2:66], 16), int(raw[66:130], 16)
        base, quote = (r0, r1) if ref.token_is_0 else (r1, r0)
        if not base:
            return None
        return (quote / 1e18) / (base / 10 ** ref.decimals)
