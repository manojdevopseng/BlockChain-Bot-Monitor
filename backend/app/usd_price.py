"""What one ETH, BNB or SOL is worth in dollars, cached.

Market cap is a dollar figure and the chain cannot answer it: a pool gives ETH
per token, so the last step needs a dollar price for ETH itself. That is one
number per chain, shared by every token on it, which is why it is cached here
rather than fetched per token — a hundred tokens on a fifteen-second loop would
otherwise be four hundred exchange requests a minute for three numbers.

Two sources per symbol, tried in order, then the last good figure: a price
thirty seconds stale beats no market cap at all. Binance is deliberately absent
— it answers 451 from this region — and so is anything needing a key.

pump_mcap keeps its own SOL price on purpose. It sits on the pump.fun hot path
with its own TTL and its own fallbacks, and rewiring a working alert path to
share this one would risk that to save fifteen lines.
"""

from __future__ import annotations

import time
from typing import Optional

import aiohttp

from .scanners.slog import get_logger

log = get_logger(__name__)

# symbol -> the endpoints that quote it, in the order they are tried.
SOURCES: dict[str, tuple[tuple[str, str], ...]] = {
    "ETH": (("coinbase", "https://api.coinbase.com/v2/prices/ETH-USD/spot"),
            ("kraken", "https://api.kraken.com/0/public/Ticker?pair=ETHUSD")),
    "BNB": (("coinbase", "https://api.coinbase.com/v2/prices/BNB-USD/spot"),
            ("kraken", "https://api.kraken.com/0/public/Ticker?pair=BNBUSD")),
    "SOL": (("coinbase", "https://api.coinbase.com/v2/prices/SOL-USD/spot"),
            ("kraken", "https://api.kraken.com/0/public/Ticker?pair=SOLUSD")),
}

TTL = 30.0
_TIMEOUT = aiohttp.ClientTimeout(total=6)

# symbol -> (price, when it was read)
_cache: dict[str, tuple[float, float]] = {}


def cached(symbol: str) -> float:
    """The last price read for a symbol, however old. 0.0 if never."""
    return _cache.get(symbol.upper(), (0.0, 0.0))[0]


async def usd(symbol: str, session: Optional[aiohttp.ClientSession] = None) -> float:
    """Dollars per unit of `symbol`, or 0.0 when nothing could be reached and
    nothing was ever cached."""
    symbol = symbol.upper()
    price, at = _cache.get(symbol, (0.0, 0.0))
    if price and time.time() - at < TTL:
        return price

    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        for name, url in SOURCES.get(symbol, ()):
            try:
                async with session.get(url, timeout=_TIMEOUT) as resp:
                    if resp.status != 200:
                        continue
                    body = await resp.json(content_type=None)
                if name == "coinbase":
                    got = float(body["data"]["amount"])
                else:
                    got = float(list(body["result"].values())[0]["c"][0])
                if got > 0:
                    _cache[symbol] = (got, time.time())
                    return got
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[USD] {name} {symbol} failed: {exc}")
        if price:
            log.debug(f"[USD] {symbol} sources all failed — using the last price")
        return price
    finally:
        if own:
            await session.close()
