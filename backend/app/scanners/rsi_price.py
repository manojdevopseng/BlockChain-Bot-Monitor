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

V4 is not covered yet. Its pools have no address to call, only an id inside
the PoolManager, so reading one means computing a storage slot and going
through extsload; a token whose only pool is V4 reports no price source rather
than a wrong number.
"""

from __future__ import annotations

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
        "decimals": "0x313ce567"}

# Uniswap V3's three standard tiers. 100 exists too but is stablecoin-only in
# practice and has never held a token this tracker would be pointed at.
_FEE_TIERS = (500, 3000, 10000)

_TIMEOUT = aiohttp.ClientTimeout(total=12)


@dataclass
class ChainSpec:
    key: str
    label: str
    http: str
    wnative: str
    v2_factory: str
    v3_factory: str


def chains() -> dict[str, ChainSpec]:
    """The chains the tracker can price on, from .env.

    Its own endpoints when they are set, otherwise the ones that chain already
    uses — so it runs before RSI_*_RPC_HTTP is filled in, which is how it gets
    tested at all.
    """
    return {
        "eth": ChainSpec("eth", "ETH",
                         config.RSI_ETH_RPC_HTTP or config.ETH_RPC_HTTP,
                         config.ETH_WETH, config.ETH_V2_FACTORY, config.ETH_V3_FACTORY),
        "bsc": ChainSpec("bsc", "BSC",
                         config.RSI_BSC_RPC_HTTP or config.BNB_RPC_HTTP,
                         config.BNB_WBNB, config.BNB_V2_FACTORY, config.BNB_V3_FACTORY),
        "rbh": ChainSpec("rbh", "RBH",
                         config.RSI_RBH_RPC_HTTP or config.RBH_RPC_HTTP,
                         config.RBH_WETH, config.RBH_V2_FACTORY, config.RBH_V3_FACTORY),
    }


@dataclass
class PoolRef:
    """Where a token's price is read from, resolved once."""
    chain: str
    token: str
    kind: str = ""            # "v3" | "v2" | "" when nothing was found
    address: str = ""
    token_is_0: bool = False
    decimals: int = 18
    fee: int = 0
    found_at: float = 0.0


def _word(value: str) -> str:
    return value.lower().replace("0x", "").rjust(64, "0")


class PriceReader:
    """One HTTP session, one cache of resolved pools."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._pools: dict[tuple[str, str], PoolRef] = {}

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
            log.debug(f"[RSI] {spec.label} eth_call failed: {exc}")
            return None
        # A revert is the chain answering "no such pool", not a failure.
        return None if "error" in body else body.get("result")

    async def resolve(self, chain: str, token: str) -> PoolRef:
        key = (chain, token.lower())
        cached = self._pools.get(key)
        if cached is not None:
            return cached

        spec = chains().get(chain)
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

        self._pools[key] = ref
        if ref.kind:
            log.info(f"[RSI] {spec.label} {token[:10]}… priced from {ref.kind}"
                     + (f" {ref.fee}" if ref.fee else "") + f" pool {ref.address[:10]}…")
        else:
            log.info(f"[RSI] {spec.label} {token[:10]}… has no V2/V3 pool against "
                     f"{spec.wnative[:8]}… — nothing to price it from yet")
        return ref

    async def find_chains(self, token: str) -> list[str]:
        """Which chains actually have a pool for this address.

        The same 0x… exists on ETH, BSC and Robinhood and means a different
        token on each, so "which chain" cannot be guessed from the string. It
        can be asked, though: the chain that has a pool for it is the chain it
        trades on. Usually exactly one answers.
        """
        found = []
        for key in chains():
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
        spec = chains().get(chain)
        if not ref.kind or spec is None:
            return None

        if ref.kind == "v3":
            raw = await self._call(spec, ref.address, _SEL["slot0"])
            if not raw or len(raw) < 66:
                return None
            sqrt_price = int(raw[2:66], 16)
            if not sqrt_price:
                return None
            ratio = (sqrt_price / (2 ** 96)) ** 2      # token1 per token0
            if ref.token_is_0:
                return ratio * (10 ** (ref.decimals - 18))
            return (1 / ratio) * (10 ** (18 - ref.decimals)) if ratio else None

        raw = await self._call(spec, ref.address, _SEL["reserves"])
        if not raw or len(raw) < 130:
            return None
        r0, r1 = int(raw[2:66], 16), int(raw[66:130], 16)
        base, quote = (r0, r1) if ref.token_is_0 else (r1, r0)
        if not base:
            return None
        return (quote / 1e18) / (base / 10 ** ref.decimals)
