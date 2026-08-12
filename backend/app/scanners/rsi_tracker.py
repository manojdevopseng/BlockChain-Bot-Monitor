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

CADENCES: dict[str, int] = {"10s": 10, "20s": 20, "30s": 30, "1m": 60}
DEFAULT_CADENCE = "30s"

# How often the token list and the switches are re-read. Fifteen rather than a
# minute because this is also how long a token you just added sits there doing
# nothing — the query is two small collections and the wait was the only thing
# anyone would notice.
_REFRESH_SECONDS = 15
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
        ts = bucket(now, interval)
        # One document per candle: sampling twice inside the same bucket
        # overwrites the close, which is what a close is.
        await _col("rsi_candles").update_one(
            {"chain": chain, "address": addr, "interval": interval, "ts": ts},
            {"$set": {"close": price, "dt": _utc_now()}},
            upsert=True,
        )
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
        period = int(token.get("period") or self._settings.get("period")
                     or rsi_math.DEFAULT_PERIOD)

        candles = await _col("rsi_candles").find(
            {"chain": chain, "address": addr, "interval": interval},
            {"_id": 0, "ts": 1, "close": 1},
        ).sort("ts", -1).limit(period + 1).to_list(period + 1)
        closes = [c["close"] for c in reversed(candles)]
        value = rsi_math.rsi(closes, period)

        state = await _col("rsi_state").find_one({"chain": chain, "address": addr}) or {}
        here = rsi_math.zone(value, self.low, self.high)
        announced = str(state.get("announced_zone") or "")
        turn = rsi_math.crossed(announced, value, self.low, self.high)
        now = time.time()
        cooling = now - float(state.get("last_alert_at") or 0) < _ALERT_COOLDOWN

        update = {"rsi": value, "zone": here, "samples": len(closes),
                  "period": period, "interval": interval, "checked_at": now,
                  "day": ist_date_str(now), "dt": _utc_now()}
        # Back to neutral is what re-arms the next alert — recorded even while
        # a cooldown is running, because it is not an announcement.
        if here == "neutral":
            update["announced_zone"] = "neutral"
        await _col("rsi_state").update_one({"chain": chain, "address": addr},
                                           {"$set": update}, upsert=True)
        if not turn or cooling:
            return          # still due: `announced_zone` is deliberately unchanged
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
