"""RSI, and the rule for when it is worth saying something.

Wilder's RSI, which is the one every chart shows: the first value is a plain
average of the first `period` changes, and every value after it smooths the
previous average rather than recomputing a window. A rolling-mean version
gives different numbers and would disagree with whatever chart the user checks
after an alert.

Pure functions, no database and no config — the worker owns those.
"""

from __future__ import annotations

from typing import Optional, Sequence

# 14 is what every chart defaults to; the user can change it per token.
DEFAULT_PERIOD = 14
DEFAULT_LOW = 30.0
DEFAULT_HIGH = 70.0


def rsi(closes: Sequence[float], period: int = DEFAULT_PERIOD) -> Optional[float]:
    """The latest RSI over `closes` (oldest first), or None if there is not
    enough history yet.

    None rather than a number: a made-up RSI off three candles is worse than
    admitting the token is still warming up, because it would fire alerts.
    """
    if period < 2 or len(closes) < period + 1:
        return None

    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period

    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period

    # A run with no losses is RSI 100 by definition — not a divide by zero.
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def zone(value: Optional[float], low: float = DEFAULT_LOW,
         high: float = DEFAULT_HIGH) -> str:
    """"oversold" / "overbought" / "neutral" — or "" while warming up."""
    if value is None:
        return ""
    if value <= low:
        return "oversold"
    if value >= high:
        return "overbought"
    return "neutral"


def crossed(previous: Optional[float], current: Optional[float],
            low: float = DEFAULT_LOW, high: float = DEFAULT_HIGH) -> str:
    """The alert rule: the zone this reading just entered, or "".

    A crossing, not a level. RSI sits under 30 for minutes at a time, and
    alerting on the level would send one message per check for as long as it
    stays there. Coming back to neutral is what re-arms the next alert.
    """
    now = zone(current, low, high)
    if now in ("", "neutral"):
        return ""
    before = zone(previous, low, high)
    # No previous reading means the token has just finished warming up. Say it
    # once — a token that is already oversold when we start watching is
    # exactly what someone adds a token to hear about.
    return now if before != now else ""
