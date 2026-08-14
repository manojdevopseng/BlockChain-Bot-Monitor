"""What a token's market cap is right now, in dollars.

    market cap = supply × price

and both halves have to be read, because nothing in this project holds either:

  supply   totalSupply() / 10**decimals on an EVM chain, getTokenSupply on
           Solana. Cached for a few minutes — a mint or a burn moves it, but
           not between two fifteen-second checks.
  price    the pool, through the RSI tracker's own PriceReader: three V3 fee
           tiers with the deepest winning, then V2. That gives ETH-per-token,
           so the dollar price of ETH itself (usd_price) is the last step.
           Solana has no such pool to read on an EVM-shaped RPC, so its price
           comes from Jupiter's public price API, which quotes in dollars
           already.

Reusing PriceReader rather than copying it is deliberate: pool-finding is the
part that took the measuring (empty tiers reporting 3.4e38, token0 ordering,
decimals), and it now serves two features from one implementation. What is not
shared is the endpoints — this reads through MCAP_*_RPC_HTTP, so a market cap
check cannot spend the RSI tracker's rate limit.

"Market cap" here is total supply × price, the honest on-chain answer. A token
whose team holds half the supply will read higher than a site quoting only the
circulating part, and no on-chain call can tell you which wallets to exclude.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

from app import usd_price
from app.scanners import scfg as config
from app.scanners.rsi_price import ChainSpec, PriceReader
from app.scanners.slog import get_logger

log = get_logger(__name__)

_SEL_TOTAL_SUPPLY = "0x18160ddd"      # totalSupply()
_SEL_DECIMALS = "0x313ce567"          # decimals()

# Jupiter's public price API — dollars per token for any Solana mint, no key.
# Solana is where our own RPC is thinnest (both Helius keys are rate-limited),
# so the price comes from here and the RPC is asked for one thing only: supply.
_JUPITER_PRICE = "https://lite-api.jup.ag/price/v3?ids="
_JUPITER_SEARCH = "https://lite-api.jup.ag/tokens/v2/search?query="

# How long a supply figure is trusted. Long enough that it is not re-read on
# every check, short enough that a burn shows up within a few minutes.
_SUPPLY_TTL = 300.0

_TIMEOUT = aiohttp.ClientTimeout(total=12)

# Which coin pays the gas, and therefore which dollar price turns a pool price
# into a market cap. Robinhood Chain is an ETH chain — its native token is ETH.
NATIVE = {"eth": "ETH", "bsc": "BNB", "rbh": "ETH", "sol": "SOL"}

CHAIN_LABELS = {"rbh": "RBH", "eth": "ETH", "bsc": "BSC", "sol": "SOL"}


def chains() -> dict[str, ChainSpec]:
    """The EVM chains market cap can be read on, on this feature's endpoints.

    Falls back to the RSI tracker's endpoints and then to the chain's own, so
    the tracker works before MCAP_*_RPC_HTTP is filled in — which is how it
    gets tested at all.
    """
    return {
        "rbh": ChainSpec("rbh", "RBH",
                         config.MCAP_RBH_RPC_HTTP or config.RSI_RBH_RPC_HTTP
                         or config.RBH_RPC_HTTP,
                         config.RBH_WETH, config.RBH_V2_FACTORY, config.RBH_V3_FACTORY),
        "eth": ChainSpec("eth", "ETH",
                         config.MCAP_ETH_RPC_HTTP or config.RSI_ETH_RPC_HTTP
                         or config.ETH_RPC_HTTP,
                         config.ETH_WETH, config.ETH_V2_FACTORY, config.ETH_V3_FACTORY),
        "bsc": ChainSpec("bsc", "BSC",
                         config.MCAP_BSC_RPC_HTTP or config.RSI_BSC_RPC_HTTP
                         or config.BNB_RPC_HTTP,
                         config.BNB_WBNB, config.BNB_V2_FACTORY, config.BNB_V3_FACTORY),
    }


def all_chains() -> dict[str, str]:
    """Every chain the section offers, EVM and Solana, in tab order."""
    return dict(CHAIN_LABELS)


def sol_http() -> str:
    return (config.MCAP_SOL_RPC_HTTP or config.RSI_SOL_RPC_HTTP
            or config.SOL_RPC_HTTP or "")


@dataclass
class Reading:
    """One market cap check. `mcap` is dollars; the rest is how it got there."""
    mcap: float = 0.0
    price_usd: float = 0.0
    price_native: float = 0.0
    supply: float = 0.0
    source: str = ""            # "v3" | "v2" | "jupiter"


class MarketCapReader:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._pools = PriceReader(session, specs=chains(), tag="MCAP")
        # (chain, address) -> (supply, read at)
        self._supply: dict[tuple[str, str], tuple[float, float]] = {}

    def forget(self, chain: str, address: str) -> None:
        self._pools.forget(chain, address)
        self._supply.pop((chain, address.lower()), None)

    # ── the answer ───────────────────────────────────────────────────────────

    async def read(self, chain: str, address: str) -> Optional[Reading]:
        """Market cap in dollars, or None when it cannot be read right now.

        None is not zero: a missed read must leave the last figure standing,
        not report a collapse to nothing and fire an alert on the way down.
        """
        chain, address = chain.lower(), address.strip()
        if chain == "sol":
            return await self._read_sol(address)
        return await self._read_evm(chain, address.lower())

    async def _read_evm(self, chain: str, address: str) -> Optional[Reading]:
        native_per_token = await self._pools.price(chain, address)
        if not native_per_token:
            return None
        supply = await self._evm_supply(chain, address)
        if not supply:
            return None
        native_usd = await usd_price.usd(NATIVE.get(chain, "ETH"), self._session)
        if not native_usd:
            return None
        ref = await self._pools.resolve(chain, address)
        price_usd = native_per_token * native_usd
        return Reading(mcap=price_usd * supply, price_usd=price_usd,
                       price_native=native_per_token, supply=supply,
                       source=ref.kind)

    async def _read_sol(self, mint: str) -> Optional[Reading]:
        price_usd = await self._jupiter_price(mint)
        if not price_usd:
            return None
        supply = await self._sol_supply(mint)
        if not supply:
            return None
        return Reading(mcap=price_usd * supply, price_usd=price_usd,
                       supply=supply, source="jupiter")

    # ── the two halves ───────────────────────────────────────────────────────

    async def _evm_supply(self, chain: str, address: str) -> float:
        cached = self._supply.get((chain, address))
        if cached and time.time() - cached[1] < _SUPPLY_TTL:
            return cached[0]
        spec = chains().get(chain)
        if spec is None or not spec.http:
            return 0.0
        raw = await self._call(spec.http, address, _SEL_TOTAL_SUPPLY)
        if not raw or raw == "0x":
            return 0.0
        dec_raw = await self._call(spec.http, address, _SEL_DECIMALS)
        decimals = int(dec_raw, 16) if dec_raw and dec_raw != "0x" else 18
        supply = int(raw, 16) / (10 ** decimals)
        self._supply[(chain, address)] = (supply, time.time())
        return supply

    async def _sol_supply(self, mint: str) -> float:
        cached = self._supply.get(("sol", mint))
        if cached and time.time() - cached[1] < _SUPPLY_TTL:
            return cached[0]
        http = sol_http()
        if not http:
            return 0.0
        try:
            async with self._session.post(
                http,
                json={"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply",
                      "params": [mint]},
                timeout=_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[MCAP] SOL getTokenSupply failed: {exc}")
            return 0.0
        value = ((body or {}).get("result") or {}).get("value") or {}
        supply = float(value.get("uiAmount") or 0.0)
        if supply:
            self._supply[("sol", mint)] = (supply, time.time())
        return supply

    async def _jupiter_price(self, mint: str) -> float:
        try:
            async with self._session.get(_JUPITER_PRICE + mint,
                                         timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    log.debug(f"[MCAP] Jupiter said {resp.status} for {mint[:8]}…")
                    return 0.0
                body = await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[MCAP] Jupiter failed for {mint[:8]}…: {exc}")
            return 0.0
        entry = (body or {}).get(mint) or {}
        return float(entry.get("usdPrice") or 0.0)

    async def _call(self, http: str, to: str, data: str) -> Optional[str]:
        try:
            async with self._session.post(
                http,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                      "params": [{"to": to, "data": data}, "latest"]},
                timeout=_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[MCAP] eth_call failed: {exc}")
            return None
        return None if "error" in (body or {}) else (body or {}).get("result")

    # ── naming, so nothing has to be typed ───────────────────────────────────

    async def name_symbol(self, chain: str, address: str) -> tuple[str, str]:
        if chain != "sol":
            return await self._pools.name_symbol(chain, address.lower())
        try:
            async with self._session.get(_JUPITER_SEARCH + address,
                                         timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    return "", ""
                body = await resp.json(content_type=None)
        except Exception:  # noqa: BLE001
            return "", ""
        rows = body if isinstance(body, list) else (body or {}).get("tokens") or []
        for row in rows:
            if str(row.get("id") or row.get("address") or "").strip() == address:
                return (str(row.get("symbol") or "")[:32].upper(),
                        str(row.get("name") or "")[:64])
        return "", ""

    async def find_chains(self, address: str) -> list[str]:
        """Which chains have a pool for this address — asked, not guessed."""
        return await self._pools.find_chains(address.lower())
