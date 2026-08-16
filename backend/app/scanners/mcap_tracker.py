"""Market Cap Alert — watches the tokens you add and says when one gets there.

Two loops, and deliberately fewer than the RSI tracker has: a market cap is a
number read now, not a series built over time, so there are no candles to draw
and nothing to warm up.

  checker    ticks every few seconds and reads the rows whose own cadence has
             elapsed. Two things make that cheap: the cadence is per row (a
             trial plan sits on five minutes, a paid one on fifteen seconds),
             and rows are grouped by token before reading — twenty accounts
             watching the same token cost ONE request, not twenty, because a
             market cap is a fact about the token rather than about who asked.
  refresher  every fifteen seconds, re-reads the token list, the settings and
             the switches, so the panel, Telegram and Settings agree without a
             restart.

Alerting is one message per target, not one per check: `armed` records which
direction the token has to travel to count, taken when the target is set. Add a
token at $40k with a $100k target and it fires on the way up; give a token
already at $2M a $1M target and it fires on the way down, rather than firing
instantly because the number is already past it.

A token that has fired stays on the list with its target intact — nothing is
removed here. Removing is yours, from the panel, the page or Telegram.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import aiohttp

from app import notifier
from app.scanners import scfg as config
from app.scanners.mcap_price import CHAIN_LABELS, MarketCapReader
from app.scanners.slog import get_logger
from app.util import gmgn_url, ist_date_str

log = get_logger(__name__)

# How often every tracked token is re-read. 15s is the default the section was
# asked for; the others are here for when the list gets long enough that the
# request count matters more than the second or two of delay.
CADENCES: dict[str, int] = {"15s": 15, "30s": 30, "1m": 60, "5m": 300}
DEFAULT_CADENCE = "15s"

# How often the list and the switches are re-read — the same fifteen seconds
# the RSI tracker uses, and the same reason: it is how long a token you just
# added sits there doing nothing.
_REFRESH_SECONDS = 15
# The heartbeat of the checker. Not the cadence: each row carries its own, and
# this is only how often the loop looks for rows that are due.
_TICK_SECONDS = 5
# Concurrent reads. The endpoints are shared with the rest of the app, and a
# hundred tokens firing at once is how a provider starts answering 429.
_READ_GATE = asyncio.Semaphore(6)


def _col(name: str):
    from app import db
    return db.get_collection(name)


def _token_key(row: dict) -> str:
    """One watcher's row. Two accounts watching the same token are two rows —
    they may be on different cadences — but they share the read."""
    return f"{row.get('user_id', '')}:{row.get('chain')}:{row.get('address')}"


def _cadence_of(row: dict) -> int:
    """Seconds between reads of this row, floored so a bad value cannot turn
    into a hot loop."""
    return max(_TICK_SECONDS, int(row.get("cadence") or CADENCES[DEFAULT_CADENCE]))


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def fmt_usd(value: float) -> str:
    """$1.2M / $940K / $12,345 — the way a market cap is normally said."""
    value = float(value or 0)
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:,.0f}"


def parse_usd(text: str) -> float:
    """"1.5m", "$250k", "40000" -> a number. Raises ValueError on anything else.

    Typing 1500000 on a phone is how the wrong target gets set, so the suffixes
    are the point rather than a convenience.
    """
    raw = str(text or "").strip().lower().replace("$", "").replace(",", "").replace("_", "")
    if not raw:
        raise ValueError("no market cap given")
    mult = 1.0
    if raw[-1] in "kmb":
        mult = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}[raw[-1]]
        raw = raw[:-1]
    try:
        value = float(raw) * mult
    except ValueError:
        raise ValueError(f"'{text}' is not a market cap — try 250k, 1.5m or 40000")
    if value <= 0:
        raise ValueError("the target has to be more than zero")
    return value


async def first_look(chain: str, address: str, symbol: str = "",
                     name: str = "") -> tuple[str, str, float]:
    """Ticker, name and market cap right now — one look, when a token is added.

    Lives here rather than in the route or the command because both of them
    need it and it must answer the same way for either: the reading is written
    to `mcap_state`, so the row shows a figure immediately instead of an empty
    column until the next pass, and the target is armed against a real number.

    Without it a token nobody has checked yet has no market cap on file, every
    target reads as "on the way up", and a target set below where the token
    already trades fires on the very first pass.

    Best-effort throughout: a token whose price cannot be read yet is still
    added, and the worker fills it in when it can.
    """
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            reader = MarketCapReader(session)
            if not symbol:
                symbol, got_name = await reader.name_symbol(chain, address)
                name = name or got_name
            reading = await reader.read(chain, address)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[MCAP] first look at {address[:10]}… failed: {exc}")
        return symbol, name, 0.0
    if reading is None:
        return symbol, name, 0.0
    now = time.time()
    await _col("mcap_state").update_one(
        {"chain": chain, "address": address},
        {"$set": {"chain": chain, "address": address, "mcap": reading.mcap,
                  "price_usd": reading.price_usd, "supply": reading.supply,
                  "source": reading.source, "checked_at": now,
                  "day": ist_date_str(now), "dt": _utc_now()}},
        upsert=True)
    return symbol, name, reading.mcap


def armed_for(target: float, current: float) -> str:
    """Which way a market cap has to travel for a target to mean anything.

    Above where it is now fires on the way up, below fires on the way down.
    "up" when the current figure is unknown: the common case is a target above
    where it is now, and an alert that waits is better than one that fires for
    nothing.
    """
    return "down" if current and target < current else "up"


class McapTracker:
    def __init__(self, session_factory=aiohttp.ClientSession) -> None:
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        self._reader: Optional[MarketCapReader] = None
        self._enabled: dict[str, bool] = {}
        self._tokens: list[dict] = []
        self._settings: dict = {}
        # token key -> when the next read of it is due, so a tick is a
        # dictionary lookup rather than a query.
        self._due: dict[str, float] = {}

    # ── switches and settings ────────────────────────────────────────────────

    def apply_toggles(self, enabled: dict[str, bool]) -> None:
        self._enabled = dict(enabled)

    def _on(self, service: str, default: bool = True) -> bool:
        return bool(self._enabled.get(service, default))

    def _chain_on(self, chain: str) -> bool:
        return self._on(f"mcap_chain_{chain}") and self._on(f"mcap_rpc_{chain}")

    async def _reload(self) -> None:
        try:
            self._tokens = await _col("mcap_tokens").find(
                {"enabled": {"$ne": False}}).to_list(2000)
            self._settings = await _col("mcap_settings").find_one({"_id": "mcap"}) or {}
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[MCAP] reload failed: {exc}")

    @property
    def cadence(self) -> int:
        return CADENCES.get(str(self._settings.get("cadence") or DEFAULT_CADENCE),
                            CADENCES[DEFAULT_CADENCE])

    # ── the loops ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        self._session = self._session_factory()
        self._reader = MarketCapReader(self._session)
        await self._reload()
        log.info(f"[MCAP] Market Cap Alert started — {len(self._tokens)} token(s), "
                 f"checking every {self.cadence}s, alerts → "
                 f"{config.MCAP_ALERT_CHAT_ID or 'not set'}")
        try:
            await asyncio.gather(self._checker(), self._refresher())
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
                log.debug(f"[MCAP] refresh failed: {exc}")

    async def _checker(self) -> None:
        while True:
            try:
                await asyncio.sleep(_TICK_SECONDS)
                if not self._on("mcap_tracker"):
                    continue
                now = time.time()
                due = [t for t in self._tokens
                       if self._chain_on(t.get("chain", ""))
                       and now >= self._due.get(_token_key(t), 0.0)]
                if not due:
                    continue
                # Grouped by the token, not by the row: the reading is the same
                # answer for everyone watching it, and it is the reading that
                # costs a request.
                by_token: dict[tuple[str, str], list[dict]] = {}
                for row in due:
                    self._due[_token_key(row)] = now + _cadence_of(row)
                    by_token.setdefault((row.get("chain", ""),
                                         row.get("address", "")), []).append(row)
                await asyncio.gather(*(self._check_token(chain, address, rows)
                                       for (chain, address), rows
                                       in by_token.items()))
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[MCAP] checker: {exc}")

    async def _check_token(self, chain: str, address: str,
                           rows: list[dict]) -> None:
        """One read of one token, then every watcher's own target against it."""
        key = {"chain": chain, "address": address}
        async with _READ_GATE:
            reading = await self._reader.read(chain, address)
        now = time.time()
        if reading is None:
            # Nothing readable this pass — record the attempt, keep the figure.
            await _col("mcap_state").update_one(
                key, {"$set": {**key, "checked_at": now, "dt": _utc_now()}},
                upsert=True)
            return

        # A row added before its ticker could be read fills itself in on the
        # first good check rather than staying "?" forever. Read once, written
        # to every row that is missing it.
        if any(not r.get("symbol") for r in rows):
            symbol, name = await self._reader.name_symbol(chain, address)
            if symbol:
                for row in rows:
                    row["symbol"], row["name"] = symbol, name
                await _col("mcap_tokens").update_many(
                    key, {"$set": {"symbol": symbol, "name": name}})
                log.info(f"[MCAP] {address[:10]}… is {symbol}")

        await _col("mcap_state").update_one(
            key,
            {"$set": {**key, "mcap": reading.mcap, "price_usd": reading.price_usd,
                      "supply": reading.supply, "source": reading.source,
                      "checked_at": now, "day": ist_date_str(now), "dt": _utc_now()}},
            upsert=True,
        )

        # One reading, every watcher's own target.
        for row in rows:
            await self._settle(row, reading, now)

    async def _settle(self, token: dict, reading, now: float) -> None:
        """Has this row's own target been reached? At most one alert, ever."""
        target = float(token.get("target") or 0)
        if not target or token.get("hit_at"):
            return
        armed = str(token.get("armed") or "up")
        hit = reading.mcap >= target if armed == "up" else reading.mcap <= target
        if not hit:
            return
        owner = token.get("user_id") or ""
        # Kept in the app as well as sent: an alert that exists only in
        # Telegram is lost to anyone who has not connected it.
        from app import notifications
        await notifications.notify(
            owner, notifications.ALERT,
            f"{token.get('symbol') or 'A token'} reached "
            f"{fmt_usd(reading.mcap)}",
            f"Your target was {fmt_usd(target)} on "
            f"{CHAIN_LABELS.get(token.get('chain'), '')}.",
            "/rsi")
        # Marked before the message is sent: a Telegram failure must not leave
        # it armed to fire again on the very next pass.
        await _col("mcap_tokens").update_one(
            {"user_id": owner, "chain": token.get("chain", ""),
             "address": token.get("address", "")},
            {"$set": {"hit_at": now, "hit_mcap": reading.mcap}})
        token["hit_at"] = now
        log.info(f"[MCAP] {token.get('symbol') or token.get('address', '')[:10]} "
                 f"({str(token.get('chain')).upper()}) reached "
                 f"{fmt_usd(reading.mcap)} — target {fmt_usd(target)}"
                 + (f" · {owner}" if owner else ""))
        await self._alert(token, reading, target, armed)

    async def _alert(self, token: dict, reading, target: float, armed: str) -> None:
        if not self._on("mcap_telegram"):
            return
        # Whose alert this is decides where it goes: an account that connected
        # its own chat gets it there, the operator falls back to the group, and
        # an account on a plan without Telegram gets it on the dashboard only.
        # Never the group as a fallback for a customer — that is a leak, not a
        # fallback.
        from app import telegram_link
        chat_id, why = await telegram_link.alert_target(
            token.get("user_id") or "", config.MCAP_ALERT_CHAT_ID)
        if not chat_id:
            log.debug(f"[MCAP] alert not sent for {token.get('symbol')}: {why}")
            return
        from app import tgstyle
        chain = token.get("chain", "")
        address = token.get("address") or ""
        icon, kind = (("🎯", "MARKET CAP HIT") if armed == "up"
                      else ("🔻", "MARKET CAP FELL TO TARGET"))
        text = tgstyle.card(
            icon=icon, kind=kind, chain=chain,
            symbol=token.get("symbol") or "?", name=token.get("name") or "",
            lines=[f"🎯 target {fmt_usd(target)} · now <b>{fmt_usd(reading.mcap)}</b>",
                   (f"💵 ${reading.price_usd:.10g} per token"
                    if reading.price_usd else ""),
                   (f"🧮 supply {reading.supply:,.0f}" if reading.supply else "")],
            address=address)
        # Same road as the RSI tracker and the fan-out: quiet hours applied,
        # one rate limiter for everything this bot sends a customer.
        from app import alert_dispatch
        sent, why = await alert_dispatch.send_personal(
            token.get("user_id") or "", chat_id, text,
            tgstyle.keyboard(chain=chain, address=address, mute=False))
        if not sent:
            log.info(f"[MCAP] alert not delivered for {token.get('symbol')}: {why}")
