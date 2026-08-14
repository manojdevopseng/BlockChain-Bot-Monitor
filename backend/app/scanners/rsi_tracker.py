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

With more than one account watching, the work splits three ways by what it
actually depends on:

  the price      depends on the token only. One read per token per tick,
                 however many accounts asked for it (`rsi_state`).
  the candles    depend on the token and the timeframe (`rsi_candles`).
  the reading    depends on the token, the timeframe AND the candle count,
                 because Wilder's smoothing makes the window part of the
                 answer. So it is keyed on all three (`rsi_readings`), and two
                 accounts on identical settings share one computation.

What is never shared is the alert: which zone an account has already been told
about, and when, lives on that account's own row. Otherwise the first person to
be alerted would silence everybody else.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import aiohttp

from app import notifier, rsi_math
from app.scanners import scfg as config
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
# Concurrent price reads. The endpoint is shared with the rest of the app.
_READ_GATE = asyncio.Semaphore(6)


def _col(name: str):
    from app import db
    return db.get_collection(name)


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


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

    # ── switches and settings ────────────────────────────────────────────────

    def apply_toggles(self, enabled: dict[str, bool]) -> None:
        self._enabled = dict(enabled)

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
                due = [t for t in self._tokens
                       if self._chain_on(t.get("chain", ""))
                       and now >= self._due.get(_key(t), 0.0)]
                if not due:
                    continue
                by_token: dict[tuple[str, str], list[dict]] = {}
                for row in due:
                    by_token.setdefault(
                        (row.get("chain", ""),
                         (row.get("address") or "").lower()), []).append(row)
                await asyncio.gather(*(self._sample_token(chain, addr, rows, now)
                                       for (chain, addr), rows
                                       in by_token.items()))
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[RSI] sampler: {exc}")

    async def _sample_token(self, chain: str, addr: str, rows: list[dict],
                            now: float) -> None:
        """One price read, then a candle for each timeframe that wanted one.

        Grouped by token rather than by row: the price is a fact about the
        token, so ten accounts watching it cost one request. The candle is per
        timeframe, because that is what a candle is.
        """
        async with _READ_GATE:
            price = await self._reader.price(chain, addr)
        for row in rows:
            self._due[_key(row)] = now + INTERVALS.get(
                row.get("interval") or DEFAULT_INTERVAL,
                INTERVALS[DEFAULT_INTERVAL])
        if price is None or price <= 0:
            return
        # A row added before the ticker was read off the chain — or one whose
        # contract was unreachable at the time — fills itself in on its first
        # good sample rather than staying "?" forever.
        if any(not r.get("symbol") for r in rows):
            symbol, name = await self._reader.name_symbol(chain, addr)
            if symbol:
                for row in rows:
                    row["symbol"], row["name"] = symbol, name
                await _col("rsi_tokens").update_many(
                    {"chain": chain, "address": addr},
                    {"$set": {"symbol": symbol, "name": name}})
                log.info(f"[RSI] {addr[:10]}… is {symbol}")

        # One document per candle per timeframe. Sampling twice inside the same
        # bucket overwrites the close, which is what a close is — and two
        # accounts on the same timeframe write the same document, which is the
        # point.
        for interval in {r.get("interval") or DEFAULT_INTERVAL for r in rows}:
            await _col("rsi_candles").update_one(
                {"chain": chain, "address": addr, "interval": interval,
                 "ts": bucket(now, interval)},
                {"$set": {"close": price, "dt": _utc_now()}},
                upsert=True,
            )
        # A token that was tracked on another chain first leaves a price and a
        # reading behind under that chain — the panel then shows an empty RSI
        # while the worker happily computes one. BUY did exactly that when it
        # moved from ETH to RBH.
        for name in ("rsi_state", "rsi_readings"):
            await _col(name).delete_many({"address": addr,
                                          "chain": {"$ne": chain}})
        # The price, and only the price: what the number means depends on
        # settings, and that lives in rsi_readings keyed by them.
        await _col("rsi_state").update_one(
            {"chain": chain, "address": addr},
            {"$set": {"price": price, "updated_at": now,
                      "day": ist_date_str(now), "dt": _utc_now()}},
            upsert=True,
        )

    async def _evaluator(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.cadence)
                if not self._on("rsi_tracker"):
                    continue
                # Grouped by everything the number depends on, so identical
                # settings are computed once and shared.
                groups: dict[tuple, list[dict]] = {}
                for token in list(self._tokens):
                    if not self._chain_on(token.get("chain", "")):
                        continue
                    groups.setdefault(_reading_key(token, self._settings),
                                      []).append(token)
                for key, rows in groups.items():
                    await self._evaluate_group(key, rows)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[RSI] evaluator: {exc}")

    async def _evaluate_group(self, key: tuple, rows: list[dict]) -> None:
        """One reading for one set of settings, then each account's own alert."""
        chain, addr, interval, period = key

        # Every candle we have, not just the last period+1. Wilder's RSI smooths
        # the previous average forward, so a fresh 15-candle window and a long
        # series give different numbers for the same prices — measured on BUY,
        # which read 82.1 over the last 15 candles and 49.8 over all 91. The
        # chart said 49.55. The window was the whole disagreement.
        candles = await _col("rsi_candles").find(
            {"chain": chain, "address": addr, "interval": interval},
            {"_id": 0, "ts": 1, "close": 1},
        ).sort("ts", -1).limit(_HISTORY_CANDLES).to_list(_HISTORY_CANDLES)
        closes = [c["close"] for c in reversed(candles)]
        value = rsi_math.rsi(closes, period)
        here = rsi_math.zone(value, self.low, self.high)
        now = time.time()

        # The reading: shared by everyone on these settings.
        await _col("rsi_readings").update_one(
            {"chain": chain, "address": addr, "interval": interval,
             "period": period},
            {"$set": {"chain": chain, "address": addr, "interval": interval,
                      "period": period, "rsi": value, "zone": here,
                      "samples": len(closes), "checked_at": now,
                      "day": ist_date_str(now), "dt": _utc_now()}},
            upsert=True)

        price = ((await _col("rsi_state").find_one({"chain": chain,
                                                    "address": addr}) or {})
                 .get("price"))
        # The alert: each account's own, because each has been told a different
        # thing so far and will be told on its own chat.
        for row in rows:
            await self._announce(row, value, here, price, now)

    async def _announce(self, token: dict, value: float, here: str,
                        price, now: float) -> None:
        chain, addr = token.get("chain", ""), (token.get("address") or "").lower()
        owned = {"user_id": token.get("user_id"), "chain": chain, "address": addr}
        announced = str(token.get("announced_zone") or "")
        turn = rsi_math.crossed(announced, value, self.low, self.high)
        cooling = now - float(token.get("last_alert_at") or 0) < _ALERT_COOLDOWN

        # Back to neutral re-arms the next alert — recorded even while a
        # cooldown is running, because it is not an announcement.
        if here == "neutral" and announced != "neutral":
            token["announced_zone"] = "neutral"
            await _col("rsi_tokens").update_one(
                owned, {"$set": {"announced_zone": "neutral"}})
        if not turn or cooling:
            return          # still due: `announced_zone` is deliberately unchanged
        token["announced_zone"], token["last_alert_at"] = turn, now
        await _col("rsi_tokens").update_one(
            owned, {"$set": {"last_alert_at": now, "announced_zone": turn}})
        log.info(f"[RSI] {token.get('symbol') or addr[:10]} ({chain.upper()}) "
                 f"{turn} — RSI {value:.1f} on "
                 f"{INTERVAL_LABELS.get(token.get('interval'), '')}"
                 + (f" · {token.get('user_id')}" if token.get("user_id") else ""))
        await self._alert(token, value, turn, price)

    async def _alert(self, token: dict, value: float, turn: str,
                     price: Optional[float]) -> None:
        if not self._on("rsi_telegram"):
            return
        # Same rule as the Market Cap watcher: the owner's own chat, the
        # operator's group for the operator, and nothing at all for an account
        # whose plan has no Telegram alerts.
        from app import telegram_link
        chat_id, why = await telegram_link.alert_target(
            token.get("user_id") or "", config.RSI_ALERT_CHAT_ID)
        if not chat_id:
            log.debug(f"[RSI] alert not sent for {token.get('symbol')}: {why}")
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
        if not await notifier.send_to(chat_id, text, buttons=buttons):
            log.warning(f"[RSI] alert not delivered for {token.get('symbol')}")


def _key(token: dict) -> str:
    """One account's row. Two accounts on the same token are two rows — they may
    be on different timeframes — but they share the price read."""
    return (f"{token.get('user_id', '')}:{token.get('chain')}:"
            f"{(token.get('address') or '').lower()}:{token.get('interval')}")


def _reading_key(token: dict, settings: dict) -> tuple:
    """Everything an RSI number depends on: token, timeframe, candle count.

    The period belongs in the key and not just in the row — Wilder's smoothing
    makes the window part of the answer, so two accounts reading the same token
    on the same timeframe with different candle counts are asking two different
    questions.
    """
    period = int(token.get("period") or settings.get("period")
                 or period_of(int(settings.get("default_candles")
                                  or DEFAULT_CANDLES)))
    return (token.get("chain", ""), (token.get("address") or "").lower(),
            token.get("interval") or DEFAULT_INTERVAL, period)
