"""Live market cap for a pump.fun launch, off the stream that is already open.

pump.fun prices a token from its bonding curve, and the program logs every
trade on that curve as an anchor `TradeEvent` carrying the curve's virtual
reserves. We already hold a `logsSubscribe` on that program for discovery, so
these numbers arrive with the trade itself — no aggregator, no polling, no
second provider, and nothing that can rate-limit or Cloudflare us.

    price_sol = (virtualSolReserves / 1e9) / (virtualTokenReserves / 1e6)
    mcap_sol  = price_sol * 1_000_000_000          # supply is always 1B

A fresh curve is 30 SOL against 1.073e15 base units, which gives 27.96 — the
same figure PumpPortal prints as `marketCapSol` on a launch. That agreement is
the check that this is the right formula and not a plausible-looking one.

A launch is watched for its first minute only. Measured live, the first trade
lands in the same transaction as the create (+0.0s), and a token that is going
to run does it immediately — a longer watch would only add tokens that crawled
there hours later, which is not the thing being looked for.
"""

from __future__ import annotations

import asyncio
import base64
import struct
import time
from typing import Awaitable, Callable, Optional

import aiohttp

from .config import settings
from .scanners.slog import get_logger

log = get_logger(__name__)

# The first 8 bytes of the `Program data:` blob a trade emits.
TRADE_EVENT = "bddb7fd34ee661ee"
_TRADE_DISC = bytes.fromhex(TRADE_EVENT)
# base64 is 4 characters per 3 bytes, so the first 8 characters of the encoded
# blob are fixed by the first 6 bytes of the discriminator. Checking those as a
# string keeps the hot path off base64 entirely for the lines that are not
# trades — and on this subscription most lines are not.
_TRADE_PREFIX = base64.b64encode(_TRADE_DISC).decode()[:8]

# Everything after the discriminator, up to the reserves we want:
#   mint 32 | solAmount 8 | tokenAmount 8 | isBuy 1 | user 32 | timestamp 8
#   | virtualSolReserves 8 | virtualTokenReserves 8
_RESERVES_AT = 8 + 32 + 8 + 8 + 1 + 32 + 8
_MIN_TRADE_LEN = _RESERVES_AT + 16

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

TOTAL_SUPPLY = 1_000_000_000          # every pump.fun mint, without exception

# ── Price ─────────────────────────────────────────────────────────────────────
# The threshold is in dollars, so a dollar price is needed. Three sources, tried
# in order, all measured answering in ~25ms from the server. Binance is absent
# on purpose: it returns 451 to this region. GMGN is absent on purpose too.
PRICE_SOURCES: tuple[tuple[str, str], ...] = (
    ("coinbase", "https://api.coinbase.com/v2/prices/SOL-USD/spot"),
    ("kraken", "https://api.kraken.com/0/public/Ticker?pair=SOLUSD"),
    ("jupiter", "https://lite-api.jup.ag/price/v3?ids="
                "So11111111111111111111111111111111111111112"),
)
PRICE_TTL = 30.0

_price: float = 0.0
_price_at: float = 0.0

# mint -> {until, peak_sol, crossed}. A minute of launches, so it stays small
# on its own; the sweep only exists for the case where trading stops early.
_watched: dict[str, dict] = {}

# Called the moment a watched launch crosses the threshold, with
# (mint, peak_usd). Set by whoever cares — nothing here knows about decisions.
on_cross: Optional[Callable[[str, float], Awaitable[None]]] = None

# Crossing notifications in flight, held so they are not collected mid-send.
_firing: set = set()


def _b58(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def threshold_usd() -> float:
    return float(settings.ai_telegram_mcap_usd)


def watch_seconds() -> int:
    return int(settings.ai_mcap_watch_seconds)


async def sol_usd(session: Optional[aiohttp.ClientSession] = None) -> float:
    """Dollars per SOL, cached. Falls back through the sources, then to the last
    good figure — a price that is thirty seconds stale beats no answer at all.
    """
    global _price, _price_at
    if _price and time.time() - _price_at < PRICE_TTL:
        return _price

    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        for name, url in PRICE_SOURCES:
            try:
                async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if r.status != 200:
                        continue
                    d = await r.json(content_type=None)
                if name == "coinbase":
                    price = float(d["data"]["amount"])
                elif name == "kraken":
                    price = float(list(d["result"].values())[0]["c"][0])
                else:
                    price = float(list(d.values())[0]["usdPrice"])
                if price > 0:
                    _price, _price_at = price, time.time()
                    return price
            except Exception:  # noqa: BLE001
                continue
    finally:
        if own:
            await session.close()
    return _price                          # last good, or 0 if we never had one


# ── Watching ──────────────────────────────────────────────────────────────────

def watch(mint: str, launch_mcap_sol: float = 0.0) -> None:
    """Start the clock on a launch. Called the instant it is seen, so that the
    minute being measured is the token's first minute and not ours.
    """
    if not mint or mint in _watched:
        return
    _watched[mint] = {"until": time.time() + watch_seconds(),
                      "peak_sol": float(launch_mcap_sol or 0.0),
                      "crossed": False}


def parse_trade(data: bytes) -> Optional[tuple[str, float]]:
    """(mint, market cap in SOL) from a TradeEvent blob, or None."""
    if data[:8] != _TRADE_DISC or len(data) < _MIN_TRADE_LEN:
        return None
    v_sol, v_tok = struct.unpack_from("<QQ", data, _RESERVES_AT)
    if not v_tok or not v_sol:
        return None                        # a closed curve; nothing to price
    mcap_sol = (v_sol / 1e9) / (v_tok / 1e6) * TOTAL_SUPPLY
    return _b58(data[8:40]), mcap_sol


def note_log_line(line: str) -> None:
    """Feed one `Program data:` log line in. Cheap enough for the hot path: a
    string compare rejects everything that is not a trade before any decoding.
    """
    if not _watched:
        return
    blob = line[14:]
    if blob[:8] != _TRADE_PREFIX:
        return
    try:
        parsed = parse_trade(base64.b64decode(blob))
    except Exception:  # noqa: BLE001
        return
    if not parsed:
        return
    mint, mcap_sol = parsed
    entry = _watched.get(mint)
    # Frozen at the crossing. What is wanted is the figure that made the launch
    # worth sending, and that figure has to stay put: it has already gone out in
    # a message, and a number that keeps moving afterwards means the chat and
    # the table disagree. Measured, 11 of 19 crossings went on climbing — one to
    # +353% — so this is not a hypothetical difference.
    if entry is None or entry["crossed"] or mcap_sol <= entry["peak_sol"]:
        return
    entry["peak_sol"] = mcap_sol
    if _price:
        usd = mcap_sol * _price
        if usd >= threshold_usd():
            entry["crossed"] = True
            if on_cross is not None:
                # Fired now, not on the next sweep: the whole point of reading
                # the crossing off the trade is that it is not delayed by us.
                # Held in a set until it finishes — asyncio keeps only a weak
                # reference, so a task nothing else holds can be collected
                # mid-flight, and this one is what sends the message.
                task = asyncio.get_event_loop().create_task(_fire(mint, usd))
                _firing.add(task)
                task.add_done_callback(_firing.discard)


async def _fire(mint: str, usd: float) -> None:
    try:
        await on_cross(mint, usd)          # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[MCAP] cross handler failed for {mint[:8]}: {exc}")


def watching() -> bool:
    """Whether any launch is inside its minute. The log tap asks this before it
    looks at anything, so the stream costs nothing while nothing is watched.
    """
    return bool(_watched)


def peak_sol(mint: str) -> float:
    entry = _watched.get(mint)
    return float(entry["peak_sol"]) if entry else 0.0


def peak_usd(mint: str) -> float:
    return peak_sol(mint) * _price


def settled(mint: str) -> bool:
    """True once the minute is up — or once the launch is no longer held."""
    entry = _watched.get(mint)
    return entry is None or time.time() >= entry["until"]


def expired() -> list[tuple[str, float, float]]:
    """Drop the launches whose minute is over, returning what they reached as
    (mint, peak SOL, peak USD) so the caller can write it down.
    """
    now = time.time()
    done = [m for m, e in _watched.items() if now >= e["until"]]
    out = []
    for mint in done:
        entry = _watched.pop(mint)
        out.append((mint, entry["peak_sol"], entry["peak_sol"] * _price))
    return out


# ── Manual lookup ─────────────────────────────────────────────────────────────
# The live watch freezes at the crossing on purpose, so it cannot answer "how
# far did it actually go". pump.fun's own API can: it publishes ath_market_cap
# per token. Checked against our own figures — CURGRE read $36,254 here and
# $36,256 there — so the two agree where they overlap, and this fills in the
# part we deliberately stopped measuring.
#
# Not GMGN, and not on any automatic path: this is asked for one token at a
# time, by someone clicking.
LOOKUP_URL = "https://frontend-api-v3.pump.fun/coins/{}"
LOOKUP_TTL = 15.0
_lookup_cache: dict[str, tuple[float, dict]] = {}


async def lookup(mint: str,
                 session: Optional[aiohttp.ClientSession] = None) -> dict:
    """Current and all-time-high market cap for one pump.fun token."""
    mint = (mint or "").strip()
    if not mint:
        return {"ok": False, "error": "no address given"}

    hit = _lookup_cache.get(mint)
    if hit and time.time() - hit[0] < LOOKUP_TTL:
        return hit[1]

    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.get(
                LOOKUP_URL.format(mint),
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=12)) as r:
            if r.status == 404:
                return {"ok": False, "error": "pump.fun does not know this token"}
            if r.status != 200:
                return {"ok": False, "error": f"pump.fun returned {r.status}"}
            d = await r.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"lookup failed: {exc}"}
    finally:
        if own:
            await session.close()

    ath_at = d.get("ath_market_cap_timestamp")
    out = {
        "ok": True,
        "address": mint,
        "symbol": d.get("symbol") or "?",
        "name": d.get("name") or "",
        "market_cap_usd": d.get("usd_market_cap"),
        "ath_market_cap_usd": d.get("ath_market_cap"),
        # pump.fun stamps these in milliseconds; everything here is seconds.
        "ath_at": (ath_at / 1000) if ath_at else None,
        "created_at": ((d.get("created_timestamp") or 0) / 1000) or None,
        "complete": bool(d.get("complete")),
        "image": d.get("image_uri") or "",
        "twitter": d.get("twitter") or "",
    }
    _lookup_cache[mint] = (time.time(), out)
    if len(_lookup_cache) > 500:
        cut = time.time() - LOOKUP_TTL
        for k, (at, _) in list(_lookup_cache.items()):
            if at < cut:
                _lookup_cache.pop(k, None)
    return out


def stats() -> dict:
    return {"watching": len(_watched), "sol_usd": round(_price, 2),
            "threshold_usd": threshold_usd(),
            "threshold_sol": round(threshold_usd() / _price, 1) if _price else None}
