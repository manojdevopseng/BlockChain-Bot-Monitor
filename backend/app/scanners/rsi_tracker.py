"""RSI Tracker — watches the tokens you add and says when they turn.

Three loops, because they answer three different questions:

  sampler    every second, but only touches a token whose own interval has
             elapsed. A token on 5 Min costs twelve reads an hour; one you
             deliberately put on 1 Sec costs 3,600. That is the whole cost
             model, and it is yours to choose per token.
  evaluator  every check cadence (10s / 20s / 30s / 1m), recomputes RSI from
             the stored candles and decides whether to say anything. Separate
             from the sampler because "how often is a candle drawn" and "how
             often do I want to be told" are not the same question.
  refresher  every minute, re-reads the token list, the bounds and the
             switches, so the panel, Telegram and Settings all mean the same
             thing without a restart.

Alerts fire on a crossing, not a level — see rsi_math.crossed. RSI sits under
30 for minutes at a time and alerting on the level would send one message per
check for as long as it stayed there.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import aiohttp

from app import notifier, rsi_math
from app.scanners import scfg as config
from app.scanners import rsi_klines
from app.scanners.rsi_price import PriceReader, chains
from app.scanners.slog import get_logger
from app.util import gmgn_url, ist_date_str

log = get_logger(__name__)

# The intervals the panel offers, and what each is worth in seconds.
INTERVALS: dict[str, int] = {
    "1s": 1, "5s": 5, "1m": 60, "5m": 300, "10m": 600,
    "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400,
}
INTERVAL_LABELS = {"1s": "1 Sec", "5s": "5 Sec", "1m": "1 Min", "5m": "5 Min",
                   "10m": "10 Min", "15m": "15 Min", "30m": "30 Min",
                   "1h": "1 Hour", "1d": "1 Day"}
DEFAULT_INTERVAL = "5m"

# How many candles one reading is made of. RSI(14) is the chart default and
# needs fifteen — fourteen changes between them — so that is what "15 candles"
# means here and everywhere in the UI. Fewer reacts sooner and jumps about;
# more is steadier and slower to turn.
CANDLE_CHOICES: tuple[int, ...] = (8, 10, 15, 21, 31, 51)
DEFAULT_CANDLES = 15


def period_of(candles: int) -> int:
    """RSI length from a candle count — one change per pair of candles."""
    return max(2, int(candles) - 1)


def candles_of(period: int) -> int:
    return int(period) + 1


CADENCES: dict[str, int] = {"10s": 10, "20s": 20, "30s": 30, "1m": 60}
DEFAULT_CADENCE = "30s"

# How often the token list and the switches are re-read. Fifteen rather than a
# minute because this is also how long a token you just added sits there doing
# nothing — the query is two small collections and the wait was the only thing
# anyone would notice.
_REFRESH_SECONDS = 15
# How much history one reading is built from. Long enough that the smoothing
# has settled — past a few hundred candles the oldest ones no longer move the
# answer — and bounded so a token on 1 Sec cannot drag a day of rows into every
# check.
_HISTORY_CANDLES = 500
# One token's alert cannot repeat inside this window even if it leaves the zone
# and comes back — a price bouncing on the 30 line would otherwise ring twice a
# minute.
_ALERT_COOLDOWN = 900.0
# How alive a series has to be before its RSI is worth alerting on.
#
# Both, because either alone is wrong. A count alone lets padding through: AIB
# moved 16 times in 499 five-minute candles — over any sane count, and still
# 3.2% of the series, which read 3.60 "oversold" for a pool nobody was trading.
# A percentage alone is noise on a young token with fifteen candles.
#
# Real tokens are nowhere near these lines. Measured the same day: LOCK 94%,
# DATBOI 100%, the BSC token 99.8%, ETH V4 96.4%.
_MIN_MOVES = 5
_MIN_MOVED_PCT = 10.0
# And measured over the END of the series, not all of it.
#
# This is the bug STONKBANKERS found. Its whole series read 8.9% moved, which
# scraped past the line — but the last fifty candles held THREE distinct prices,
# 4.1% moved, and it was those that produced the 6.2 the alert went out with.
# Wilder's RSI is dominated by its recent candles, so the liveness test has to
# look at the same ones. A token that traded early and then went quiet passes a
# whole-series check for hours after it stopped being worth reading.
_MOVE_WINDOW = 60
# Concurrent price reads. The endpoint is shared with the rest of the app.
_READ_GATE = asyncio.Semaphore(6)


def _col(name: str):
    from app import db
    return db.get_collection(name)


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _moves(closes: list[float], window: int = 0) -> tuple[int, int]:
    """(steps where the price changed, steps looked at) over the last `window`."""
    seq = closes[-window:] if window else closes
    if len(seq) < 2:
        return 0, 0
    return (sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1]),
            len(seq) - 1)


def _thin(moved: int, steps: int) -> bool:
    """Is this series too flat for its RSI to mean anything?

    A padded run of identical closes produces a real number — 0 or 100 — that
    is indistinguishable from a token in freefall unless you also know how
    often the price actually changed.
    """
    if steps <= 0:
        return True
    return moved < _MIN_MOVES or (moved / steps * 100.0) < _MIN_MOVED_PCT


def bucket(ts: float, interval: str) -> int:
    """The candle a timestamp belongs to: floor to the interval."""
    step = INTERVALS.get(interval, INTERVALS[DEFAULT_INTERVAL])
    return int(ts // step * step)


class RsiTracker:
    def __init__(self, session_factory=aiohttp.ClientSession) -> None:
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        self._reader: Optional[PriceReader] = None
        self._enabled: dict[str, bool] = {}
        self._tokens: list[dict] = []
        self._settings: dict = {}
        # token key -> when its next sample is due, so the one-second tick is a
        # dictionary lookup rather than a database query.
        self._due: dict[str, float] = {}
        # The shared GMGN client, handed over by the supervisor. None means the
        # candle source is simply unavailable and every token builds its own,
        # which is exactly the behaviour that existed before it.
        self._gmgn = None

    # ── switches and settings ────────────────────────────────────────────────

    def apply_toggles(self, enabled: dict[str, bool]) -> None:
        self._enabled = dict(enabled)

    def use_gmgn(self, client) -> None:
        """The shared GMGN client, so candles can come from there."""
        self._gmgn = client

    def _from_gmgn(self, token: dict) -> bool:
        """Does this token's series come from GMGN rather than from our own
        readings? One switch turns the whole thing off, and the chain and the
        interval both have to be ones GMGN actually serves."""
        return (self._on("rsi_gmgn", True) and self._gmgn is not None
                and rsi_klines.serves(token.get("chain", ""),
                                      token.get("interval") or DEFAULT_INTERVAL))

    def _on(self, service: str, default: bool = True) -> bool:
        return bool(self._enabled.get(service, default))

    def _chain_on(self, chain: str) -> bool:
        return self._on(f"rsi_chain_{chain}") and self._on(f"rsi_rpc_{chain}")

    async def _reload(self) -> None:
        try:
            self._tokens = await _col("rsi_tokens").find(
                {"enabled": {"$ne": False}}).to_list(2000)
            self._settings = await _col("rsi_settings").find_one({"_id": "rsi"}) or {}
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[RSI] reload failed: {exc}")

    @property
    def low(self) -> float:
        return float(self._settings.get("low", rsi_math.DEFAULT_LOW))

    @property
    def high(self) -> float:
        return float(self._settings.get("high", rsi_math.DEFAULT_HIGH))

    @property
    def cadence(self) -> int:
        return CADENCES.get(str(self._settings.get("cadence") or DEFAULT_CADENCE),
                            CADENCES[DEFAULT_CADENCE])

    # ── the loops ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        self._session = self._session_factory()
        self._reader = PriceReader(self._session)
        await self._reload()
        log.info(f"[RSI] Tracker started — {len(self._tokens)} token(s), "
                 f"bounds {self.low:g}/{self.high:g}, checking every "
                 f"{self.cadence}s, alerts → {config.RSI_ALERT_CHAT_ID or 'not set'}")
        try:
            await asyncio.gather(self._sampler(), self._evaluator(), self._refresher())
        finally:
            if self._session:
                await self._session.close()

    async def _refresher(self) -> None:
        while True:
            try:
                await asyncio.sleep(_REFRESH_SECONDS)
                await self._reload()
                from app import registry
                self.apply_toggles(await registry.enabled_map())
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[RSI] refresh failed: {exc}")

    async def _sampler(self) -> None:
        """One tick a second; only tokens whose interval has elapsed are read."""
        while True:
            try:
                await asyncio.sleep(1.0)
                if not self._on("rsi_tracker"):
                    continue
                now = time.time()
                # A token served by GMGN is not sampled at all: its candles
                # arrive whole, so reading the pool once an interval would cost
                # requests to produce a second, worse copy of the same series.
                due = [t for t in self._tokens
                       if self._chain_on(t.get("chain", ""))
                       and not self._from_gmgn(t)
                       and now >= self._due.get(_key(t), 0.0)]
                if due:
                    await asyncio.gather(*(self._sample(t, now) for t in due))
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[RSI] sampler: {exc}")

    async def _sample(self, token: dict, now: float) -> None:
        chain, addr = token.get("chain", ""), (token.get("address") or "").lower()
        interval = token.get("interval") or DEFAULT_INTERVAL
        step = INTERVALS.get(interval, INTERVALS[DEFAULT_INTERVAL])
        self._due[_key(token)] = now + step
        async with _READ_GATE:
            price = await self._reader.price(chain, addr)
        if price is None or price <= 0:
            return
        # A row added before the ticker was read off the chain — or one whose
        # contract was unreachable at the time — fills itself in on its first
        # good sample rather than staying "?" forever.
        if not token.get("symbol"):
            symbol, name = await self._reader.name_symbol(chain, addr)
            if symbol:
                token["symbol"], token["name"] = symbol, name
                await _col("rsi_tokens").update_one(
                    {"chain": chain, "address": addr},
                    {"$set": {"symbol": symbol, "name": name}})
                log.info(f"[RSI] {addr[:10]}… is {symbol}")

        ts = bucket(now, interval)
        # One document per candle: sampling twice inside the same bucket
        # overwrites the close, which is what a close is.
        await _col("rsi_candles").update_one(
            {"chain": chain, "address": addr, "interval": interval, "ts": ts},
            {"$set": {"close": price, "dt": _utc_now()}},
            upsert=True,
        )
        # A token that was tracked on another chain first leaves a reading
        # behind under that chain — the panel then shows an empty RSI while the
        # worker happily computes one, because state is keyed on chain and
        # address. BUY did exactly that when it moved from ETH to RBH.
        await _col("rsi_state").delete_many({"address": addr,
                                             "chain": {"$ne": chain}})
        await _col("rsi_state").update_one(
            {"chain": chain, "address": addr},
            {"$set": {"price": price, "interval": interval,
                      "updated_at": now, "day": ist_date_str(now), "dt": _utc_now()}},
            upsert=True,
        )

    async def _evaluator(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.cadence)
                if not self._on("rsi_tracker"):
                    continue
                for token in list(self._tokens):
                    if self._chain_on(token.get("chain", "")):
                        await self._evaluate(token)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[RSI] evaluator: {exc}")

    async def _evaluate(self, token: dict) -> None:
        chain, addr = token.get("chain", ""), (token.get("address") or "").lower()
        interval = token.get("interval") or DEFAULT_INTERVAL
        # This token's own candle count, else the default for new ones.
        period = int(token.get("period") or self._settings.get("period")
                     or period_of(int(self._settings.get("default_candles")
                                      or DEFAULT_CANDLES)))

        # Where the series comes from. GMGN on every chain and interval it
        # serves — one request, the whole history with real OHLC, so a token
        # added a minute ago has an RSI immediately and it is made of trades
        # rather than of the same pool price read over and over. Our own
        # sampling is the fallback for what it does not serve (1s, 5s, 10m) and
        # for any pass where it cannot answer.
        #
        # The ADDRESS AS STORED, not the lower-cased key: a Solana mint is
        # base58 and case is part of it, so `addr` would ask GMGN about a token
        # that does not exist and get an empty answer back.
        source = "chain"
        moved = 0
        closes: list[float] = []
        if self._from_gmgn(token):
            got, moved = await rsi_klines.closes(
                self._gmgn, chain, token.get("address") or addr, interval)
            if got:
                closes, source = got, "gmgn"

        if not closes:
            # Every candle we have, not just the last period+1. Wilder's RSI
            # smooths the previous average forward, so a fresh 15-candle window
            # and a long series give different numbers for the same prices —
            # measured on BUY, which read 82.1 over the last 15 candles and
            # 49.8 over all 91. The chart said 49.55. The window was the whole
            # disagreement.
            candles = await _col("rsi_candles").find(
                {"chain": chain, "address": addr, "interval": interval},
                {"_id": 0, "ts": 1, "close": 1},
            ).sort("ts", -1).limit(_HISTORY_CANDLES).to_list(_HISTORY_CANDLES)
            closes = [c["close"] for c in reversed(candles)]
            moved, _ = _moves(closes)
        # The liveness of the part that actually decides the reading.
        live, live_steps = _moves(closes, _MOVE_WINDOW)
        value = rsi_math.rsi(closes, period)

        state = await _col("rsi_state").find_one({"chain": chain, "address": addr}) or {}
        here = rsi_math.zone(value, self.low, self.high)
        announced = str(state.get("announced_zone") or "")
        turn = rsi_math.crossed(announced, value, self.low, self.high)
        now = time.time()
        cooling = now - float(state.get("last_alert_at") or 0) < _ALERT_COOLDOWN

        # `moved` is the number of steps in the series where the price
        # actually changed. It is recorded because the RSI alone cannot tell a
        # real extreme from a dead pool: GMGN pads a quiet candle with the
        # previous close, and a run of identical closes turns into RSI 0 or
        # 100 — which looks exactly like a token in freefall.
        steps = max(1, len(closes) - 1)
        update = {"rsi": value, "zone": here, "samples": len(closes),
                  "period": period, "interval": interval, "checked_at": now,
                  "source": source,
                  # Whole series, for context…
                  "moved": moved, "moved_pct": round(moved / steps * 100, 1),
                  # …and the last _MOVE_WINDOW candles, which is what decides.
                  "moved_recent": live, "moved_window": live_steps,
                  "moved_recent_pct": round(live / max(1, live_steps) * 100, 1),
                  "thin": _thin(live, live_steps),
                  "day": ist_date_str(now), "dt": _utc_now()}
        # Back to neutral is what re-arms the next alert — recorded even while
        # a cooldown is running, because it is not an announcement.
        if here == "neutral":
            update["announced_zone"] = "neutral"
        await _col("rsi_state").update_one({"chain": chain, "address": addr},
                                           {"$set": update}, upsert=True)
        if not turn or cooling:
            return          # still due: `announced_zone` is deliberately unchanged
        # A series that barely moved has no RSI worth sending. Padding turns
        # into 0 or 100, and those are the two values most likely to look like
        # the alert of the day. The panel still shows the number, marked thin.
        if _thin(live, live_steps):
            log.info(f"[RSI] {token.get('symbol') or addr[:10]} {turn} not sent — "
                     f"only {live} of the last {live_steps} steps moved "
                     f"({live / max(1, live_steps) * 100:.1f}%, {source})")
            return
        await _col("rsi_state").update_one(
            {"chain": chain, "address": addr},
            {"$set": {"last_alert_at": now, "announced_zone": turn}})
        log.info(f"[RSI] {token.get('symbol') or addr[:10]} ({chain.upper()}) "
                 f"{turn} — RSI {value:.1f} on {INTERVAL_LABELS.get(interval, interval)}")
        await self._alert(token, value, turn, state.get("price"))

    async def _alert(self, token: dict, value: float, turn: str,
                     price: Optional[float]) -> None:
        if not self._on("rsi_telegram") or not config.RSI_ALERT_CHAT_ID:
            return
        import html
        chain = token.get("chain", "")
        addr = (token.get("address") or "").lower()
        head = "📉 <b>RSI OVERSOLD</b>" if turn == "oversold" else "📈 <b>RSI OVERBOUGHT</b>"
        bound = self.low if turn == "oversold" else self.high
        side = "below" if turn == "oversold" else "above"
        text = (
            f"{head} — {value:.1f}\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"🪙 <b>{html.escape(token.get('symbol') or '?')}</b>"
            + (f" — {html.escape(token['name'])}" if token.get("name") else "") + "\n"
            f"⛓ {chain.upper()} · {INTERVAL_LABELS.get(token.get('interval'), '')}\n"
            f"📊 crossed {side} {bound:g}\n"
            + (f"💵 {price:.12f} native\n" if price else "")
            + f"\n<code>{addr}</code>"
        )
        buttons = [b for b in (("📊 GMGN", gmgn_url(chain, addr)),) if b[1]]
        if not await notifier.send_to(config.RSI_ALERT_CHAT_ID, text, buttons=buttons):
            log.warning(f"[RSI] alert not delivered for {token.get('symbol')}")


def _key(token: dict) -> str:
    return f"{token.get('chain')}:{(token.get('address') or '').lower()}"
