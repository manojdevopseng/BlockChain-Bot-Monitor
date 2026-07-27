"""Price a Robinhood-chain token by reading its pool directly.

Every other chain we track is on DexScreener, so a price is one HTTP call away.
Robinhood Chain is not listed anywhere, which meant SOL→RBH matches — the
largest source of alerts we have — were recorded as "unpriceable" and never
measured at all. Outcomes, the daily digest and the premium-group ranking were
all blind to two thirds of the alerts.

So the price is worked out from the pool itself:

    v2   pair.getReserves()      — the WETH/token ratio
    v3   pair.slot0()            — sqrtPriceX96
    v4   PoolManager.extsload()  — slot0 of the pool, keyed by its pool id
    noxa                         — no known curve to read, stays unpriced

That gives a price in WETH; multiplying by ETH's USD price (from DexScreener,
which does list ETH) gives USD. Robinhood's WETH is bridged ether, so the two
track each other.

This talks to RBH_RPC_HTTP only. It never touches the GMGN client or its rate
limiter — that pacing was hard-won against Cloudflare and stays untouched.
"""

from __future__ import annotations

import time
from typing import Optional

import aiohttp

from .scanners import scfg as config
from .scanners.slog import get_logger

log = get_logger(__name__)

# Function selectors — plain eth_call, no ABI/web3 dependency for four reads.
_SEL_GET_RESERVES = "0x0902f1ac"   # getReserves()            v2 pair
_SEL_SLOT0        = "0x3850c7bd"   # slot0()                  v3 pool
_SEL_TOKEN0       = "0x0dfe1681"   # token0()                 v2/v3 pair
_SEL_DECIMALS     = "0x313ce567"   # decimals()               erc20
_SEL_GET_POOL     = "0x1698ee82"   # getPool(a,b,fee)         v3 factory
_SEL_GET_PAIR     = "0xe6a43905"   # getPair(a,b)             v2 factory
_SEL_EXTSLOAD     = "0x1e2eaeaf"   # extsload(bytes32)        v4 PoolManager

# Uniswap V4 keeps every pool's state in one mapping on the singleton
# PoolManager; `pools` is storage slot 6, so a pool's slot0 lives at
# keccak256(poolId . 6). Verified against live pools on this chain.
_V4_POOLS_SLOT = 6

# The fee tiers a v3 pool can exist at, tried in turn when we have to find a
# pool for a token detected before the pair address was recorded.
_V3_FEES = (10000, 3000, 500, 100)

_ETH_USD_TTL = 300
_eth_usd: tuple[float, float] = (0.0, 0.0)     # (price, fetched_at)

# decimals() never changes, and the same tokens are re-priced at every
# checkpoint — so it is read once per token per process.
_decimals: dict[str, int] = {}


def _keccak(data: bytes) -> bytes:
    try:
        from Crypto.Hash import keccak
        h = keccak.new(digest_bits=256)
        h.update(data)
        return h.digest()
    except ImportError:
        from eth_hash.auto import keccak as _k     # type: ignore
        return _k(data)


def _addr_arg(addr: str) -> str:
    return addr.lower().replace("0x", "").rjust(64, "0")


def _uint_arg(value: int) -> str:
    return f"{value:064x}"


class _Rpc:
    """Minimal eth_call helper over one shared session."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._s = session

    async def call(self, to: str, data: str) -> Optional[int]:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                   "params": [{"to": to, "data": data}, "latest"]}
        try:
            async with self._s.post(config.RBH_RPC_HTTP, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=12)) as r:
                body = await r.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[RBH-PRICE] rpc failed: {exc}")
            return None
        result = body.get("result")
        if not result or result == "0x":
            return None
        return int(result, 16)

    async def word(self, to: str, data: str, index: int = 0) -> Optional[int]:
        """One 32-byte word of a multi-word return value."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                   "params": [{"to": to, "data": data}, "latest"]}
        try:
            async with self._s.post(config.RBH_RPC_HTTP, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=12)) as r:
                body = await r.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[RBH-PRICE] rpc failed: {exc}")
            return None
        raw = (body.get("result") or "")[2:]
        chunk = raw[index * 64:(index + 1) * 64]
        return int(chunk, 16) if len(chunk) == 64 else None


async def _eth_usd_price(session: aiohttp.ClientSession) -> Optional[float]:
    """ETH in USD, cached. DexScreener does list ether — just not this chain."""
    global _eth_usd
    price, at = _eth_usd
    if price and time.time() - at < _ETH_USD_TTL:
        return price
    url = ("https://api.dexscreener.com/latest/dex/tokens/"
           "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")        # mainnet WETH
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as r:
            data = await r.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[RBH-PRICE] ETH price lookup failed: {exc}")
        return price or None
    pairs = [p for p in (data.get("pairs") or [])
             if (p.get("chainId") or "").lower() == "ethereum"
             and (p.get("quoteToken") or {}).get("symbol") in ("USDC", "USDT")
             and p.get("priceUsd")]
    if not pairs:
        return price or None
    pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
               reverse=True)
    try:
        fresh = float(pairs[0]["priceUsd"])
    except (TypeError, ValueError):
        return price or None
    _eth_usd = (fresh, time.time())
    return fresh


async def _token_decimals(rpc: _Rpc, token: str) -> int:
    key = token.lower()
    if key not in _decimals:
        got = await rpc.call(token, _SEL_DECIMALS)
        _decimals[key] = 18 if got is None else int(got)
    return _decimals[key]


async def _find_pool(rpc: _Rpc, token: str, dex: str) -> Optional[str]:
    """Locate the pool for a token detected before pairs were recorded."""
    weth = config.RBH_WETH
    if not weth:
        return None
    if dex == "v2" and config.RBH_V2_FACTORY:
        got = await rpc.call(config.RBH_V2_FACTORY,
                             _SEL_GET_PAIR + _addr_arg(token) + _addr_arg(weth))
        return f"0x{got:040x}" if got else None
    if dex == "v3" and config.RBH_V3_FACTORY:
        for fee in _V3_FEES:
            got = await rpc.call(
                config.RBH_V3_FACTORY,
                _SEL_GET_POOL + _addr_arg(token) + _addr_arg(weth) + _uint_arg(fee))
            if got:
                return f"0x{got:040x}"
    return None


async def _price_in_weth(rpc: _Rpc, *, token: str, dex: str, pair: Optional[str],
                         pool_id: Optional[str],
                         weth_is_token0: Optional[bool]) -> Optional[float]:
    dex = (dex or "").lower()
    token_dec = await _token_decimals(rpc, token)

    if dex == "v4":
        if not pool_id or not config.RBH_V4_POOLMANAGER:
            return None
        raw = bytes.fromhex(pool_id[2:] if pool_id.startswith("0x") else pool_id)
        slot = _keccak(raw + _V4_POOLS_SLOT.to_bytes(32, "big"))
        word = await rpc.call(config.RBH_V4_POOLMANAGER,
                              _SEL_EXTSLOAD + slot.hex())
        if not word:
            return None
        sqrt_price = word & ((1 << 160) - 1)
        return _from_sqrt(sqrt_price, token_dec, bool(weth_is_token0))

    if not pair:
        pair = await _find_pool(rpc, token, dex)
        if not pair:
            return None

    if weth_is_token0 is None:
        token0 = await rpc.call(pair, _SEL_TOKEN0)
        if token0 is None:
            return None
        weth_is_token0 = f"0x{token0:040x}".lower() == (config.RBH_WETH or "").lower()

    if dex == "v3":
        word = await rpc.word(pair, _SEL_SLOT0, 0)
        if not word:
            return None
        return _from_sqrt(word & ((1 << 160) - 1), token_dec, bool(weth_is_token0))

    if dex == "v2":
        r0 = await rpc.word(pair, _SEL_GET_RESERVES, 0)
        r1 = await rpc.word(pair, _SEL_GET_RESERVES, 1)
        if not r0 or not r1:
            return None
        weth_res, token_res = (r0, r1) if weth_is_token0 else (r1, r0)
        if not token_res:
            return None
        return (weth_res / 1e18) / (token_res / 10 ** token_dec)

    return None            # noxa and anything new — no curve we can read


def _from_sqrt(sqrt_price_x96: int, token_dec: int, weth_is_token0: bool) -> Optional[float]:
    """sqrtPriceX96 -> price of one token in WETH."""
    if not sqrt_price_x96:
        return None
    ratio = (sqrt_price_x96 / 2 ** 96) ** 2        # token1 per token0, raw units
    if weth_is_token0:
        # ratio is tokens per WETH; invert, then correct for the decimal gap.
        tokens_per_weth = ratio * 10 ** (18 - token_dec)
        return 1 / tokens_per_weth if tokens_per_weth else None
    return ratio * 10 ** (token_dec - 18)


async def price_usd(*, token: str, dex: str, pair: Optional[str] = None,
                    pool_id: Optional[str] = None,
                    weth_is_token0: Optional[bool] = None) -> Optional[float]:
    """USD price of a Robinhood-chain token, or None if it cannot be read."""
    if not config.RBH_RPC_HTTP or not token:
        return None
    async with aiohttp.ClientSession() as session:
        rpc = _Rpc(session)
        in_weth = await _price_in_weth(rpc, token=token, dex=dex, pair=pair,
                                       pool_id=pool_id, weth_is_token0=weth_is_token0)
        if not in_weth:
            return None
        eth_usd = await _eth_usd_price(session)
        if not eth_usd:
            return None
        usd = in_weth * eth_usd
        # A pool read the wrong way round produces a wildly wrong number rather
        # than an error — a v4 pool quoting native ether against WETH priced
        # "one token" at $2.9e14 during testing. An outcome is a percentage
        # change, so one bad entry price poisons every later reading; refusing
        # the value costs nothing, since the next checkpoint tries again.
        if not (1e-18 < usd < 1e6):
            log.debug(f"[RBH-PRICE] implausible price for {token[:10]}…: {usd}")
            return None
        return usd
