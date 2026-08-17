"""Candles from GMGN, for the chains it serves.

The tracker can build its own candles by reading a pool once per interval and
writing down the close, and for a while that was the only way on Robinhood
Chain. It is a poor way: a token that trades a few times an hour gets the same
mid-price written twenty times over, and no chart will ever agree with the RSI
that comes out of it. Measured on STONKBANKERS — 293 one-minute candles, 27
distinct prices.

GMGN answers the same question properly, in one request: the newest N candles
with real OHLC, at the resolution asked for, on all four chains including
Robinhood. So the series comes from here and the pool is not read at all.

Measured before this was written, on all three chains:

    1m 5m 15m 30m 1h 1d   served on eth, bsc, sol AND robinhood, and each
                          bucket is the size it claims
    10m                   not served anywhere
    1s 5s                 answered, but the buckets are not the size asked for
                          — on a thin token they come back trade-spaced (5s
                          returning 225s buckets on Robinhood, 130s on BSC), so
                          they are unusable whatever the request said

So a token on 10 Min keeps building its own candles, and so does one on 1s or 5s
— for those two the measured bucket sizes are the reason, not just the cost
under FINE_INTERVALS.
`serves()` is the only thing that decides, and everything downstream falls back
on its own.

Two things this deliberately does not do:

  it does not write to `rsi_candles`. Those are our readings; GMGN's are
  somebody else's, and mixing the two in one collection would make "where did
  this number come from" unanswerable later.

  it does not trust a flat run. GMGN pads a quiet candle with the previous
  close, so a token nobody traded for an hour comes back as sixty identical
  closes — which Wilder's RSI turns into 0 or 100, an extreme that reads
  exactly like a real one. `moved` counts the steps that actually changed, and
  the tracker refuses to alert on a series that barely moved.
"""

from __future__ import annotations

import time
from typing import Optional

from app.scanners.slog import get_logger

log = get_logger(__name__)

# Our interval -> GMGN resolution. 10m is absent on purpose: GMGN returns
# nothing for it, and a silent fallback to the nearest size would quietly
# compute a different indicator than the one the user chose.
RESOLUTIONS: dict[str, str] = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "30m": "30m", "1h": "1h", "1d": "1d",
}

# 1s and 5s are served by GMGN and deliberately not used.
#
# Two reasons, and the second is the one that matters. A one-second series has
# to be re-fetched every few seconds to be worth anything, which is twelve
# requests a minute for a single token — half of the whole GMGN budget. And our
# own sampler already reads the pool every second at those settings, which is
# not a worse copy of the same series but a better one: no padding, and no
# dependency on how quickly somebody else's cache updates.
FINE_INTERVALS = ("1s", "5s")

# The most requests a minute the RSI tracker may make of GMGN.
#
# The client is shared with the SOL scanner, which is the primary detection
# feed and polls every 5s — twelve requests a minute of a budget of twenty-four
# (GMGN_SCAN_RATE 0.4). Without a cap of its own, twenty tokens on 1 Min would
# ask for sixty a minute, and the limiter would serve them by making SOL wait.
# Over this line the answer is whatever is in the cache, however stale, and
# failing that the token builds its own candles for that pass.
MAX_PER_MINUTE = 8
_recent: list[float] = []

# Our chain key -> the slug GMGN uses. Robinhood is in here, and finding that
# out took two goes: the older /defi/quotation/v1/tokens/kline/ endpoint answers
# "network not supported" for it, which looks exactly like a chain GMGN does not
# carry. It does carry it — on /api/v1/token_candles/, which serves all four.
CHAINS = {"eth": "eth", "bsc": "bsc", "sol": "sol", "rbh": "robinhood"}

# One request is up to 3000 candles; this is what the tracker actually needs.
# Wilder's RSI keeps smoothing forward, so a longer series is a different (and
# steadier) number — 500 is where it stops moving in the third decimal.
WANT = 500

# How long an answer is reused. The evaluator runs every cadence (10s at the
# fastest) and would otherwise ask GMGN once per token per pass. A third of the
# candle is the most that can be stale without the reading being wrong: inside
# one candle the close is still moving anyway.
def _ttl(interval: str) -> float:
    step = _STEPS.get(interval, 60)
    return max(15.0, min(step / 3.0, 120.0))


_STEPS = {"1m": 60, "5m": 300, "15m": 900,
          "30m": 1800, "1h": 3600, "1d": 86400}


def _budget_left() -> bool:
    """Is there room for another request this minute?"""
    cutoff = time.time() - 60.0
    while _recent and _recent[0] < cutoff:
        _recent.pop(0)
    return len(_recent) < MAX_PER_MINUTE


_cache: dict[tuple[str, str, str], tuple[float, list[float], int]] = {}


def serves(chain: str, interval: str) -> bool:
    """Can GMGN answer for this chain at this interval?"""
    return chain in CHAINS and interval in RESOLUTIONS


def forget(chain: str, token: str) -> None:
    for key in [k for k in _cache if k[0] == chain and k[1] == token.lower()]:
        _cache.pop(key, None)


async def closes(client, chain: str, token: str, interval: str,
                 want: int = WANT) -> tuple[Optional[list[float]], int]:
    """(closes oldest-first, how many steps actually moved).

    (None, 0) when GMGN cannot answer — the caller falls back to its own
    candles rather than treating a failed request as a flat market.
    """
    if not serves(chain, interval) or client is None:
        return None, 0
    token = token.lower()
    key = (chain, token, interval)
    hit = _cache.get(key)
    now = time.time()
    if hit and now - hit[0] < _ttl(interval):
        return hit[1], hit[2]

    if not _budget_left():
        # Stale beats starving the SOL scanner. An RSI a minute old is still
        # the right shape; a detection feed that stopped polling is not.
        if hit:
            return hit[1], hit[2]
        log.debug(f"[RSI] GMGN budget spent — {token[:10]}… falls back to "
                  f"its own candles this pass")
        return None, 0

    _recent.append(now)
    try:
        # `limit`, not from/to: this endpoint ignores a time range and answers
        # with the newest `limit` candles. Asked for 500 on 1m it returns 500,
        # spanning about 27 hours.
        got = await client._web_get(
            f"/api/v1/token_candles/{CHAINS[chain]}/{token}",
            {"resolution": RESOLUTIONS[interval], "limit": want})
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[RSI] GMGN candles for {token[:10]}… ({chain} {interval}): {exc}")
        return None, 0

    # This surface reports failure in the body rather than the status: a chain
    # it does not know answers 200 with a non-zero code and an empty list.
    if isinstance(got, dict) and got.get("code") not in (0, None):
        log.debug(f"[RSI] GMGN candles for {token[:10]}… ({chain} {interval}): "
                  f"{got.get('message') or got.get('msg')}")
        return None, 0
    data = (got or {}).get("data") if isinstance(got, dict) else None
    rows = data.get("list") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        return None, 0
    try:
        rows.sort(key=lambda r: int(r.get("time") or 0))
        series = [float(r["close"]) for r in rows
                  if r.get("close") not in (None, "")]
    except (TypeError, ValueError) as exc:
        log.debug(f"[RSI] GMGN candles for {token[:10]}… unreadable: {exc}")
        return None, 0
    series = [p for p in series if p > 0]
    if len(series) < 2:
        return None, 0

    moved = sum(1 for i in range(1, len(series)) if series[i] != series[i - 1])
    _cache[key] = (now, series, moved)
    return series, moved
