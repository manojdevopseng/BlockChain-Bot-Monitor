"""GasMonitorManager — orchestrates SwapMonitor instances (ETH Gas Fees feature).

Ported from Uniswap_ETH_Monitor/src/monitor/token_monitor.py, plus this
project's persistence and Telegram wiring.

Receives every newly detected ETH pair from the on-chain detector and spins up a
SwapMonitor for it (V2 and V4 only — matching the reference, which does not
monitor V3 swaps). Duplicate monitors for the same token are prevented, and the
number of concurrent monitors is capped because each one holds a live WS
subscription.

When a monitor fires, this module:
  • sends the "High Gas Early Activity" alert to GAS_ALERT_CHAT_ID
  • stores the hit in MongoDB (`gas_alerts`) for the dashboard panel
"""

from __future__ import annotations

import time
from typing import Dict

import aiohttp

from app.scanners import scfg as config
from app.scanners.onchain_detector import DetectedToken
from app.scanners.slog import get_logger
from app.util import esc
from app import heartbeat, outcomes
from app.scanners import storage_repo as storage
from app.scanners.swap_monitor import SwapMonitor
from app.scanners.ws_provider import WSProvider

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# Only these DEX versions emit swaps we know how to parse (same as reference).
_MONITORED_DEX = ("v2", "v4")


class GasMonitorManager:
    def __init__(self, provider: WSProvider) -> None:
        self._provider = provider
        self._monitors: Dict[str, SwapMonitor] = {}   # token address -> monitor
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        for m in list(self._monitors.values()):
            try:
                await m.stop()
            except Exception:
                pass
        self._monitors.clear()
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Entry point from the ETH detector ─────────────────────────────────────

    async def on_token(self, token: DetectedToken) -> None:
        if token.dex not in _MONITORED_DEX:
            return                              # V3 not monitored (as reference)
        if not token.pair:
            return
        key = token.address.lower()

        self._reap()                            # drop finished monitors first
        if key in self._monitors:
            return                              # already watching
        if len(self._monitors) >= config.MAX_GAS_MONITORS:
            log.warning(
                f"[GasMonitor] cap reached ({config.MAX_GAS_MONITORS}) — "
                f"skipping {token.symbol}"
            )
            return

        monitor = SwapMonitor(token=token, provider=self._provider, on_alert=self._fire)
        self._monitors[key] = monitor
        await monitor.start()

    def _reap(self) -> None:
        for k in [k for k, m in self._monitors.items() if not m.running]:
            self._monitors.pop(k, None)

    @property
    def active(self) -> int:
        self._reap()
        return len(self._monitors)

    # ── Alert ─────────────────────────────────────────────────────────────────

    async def _fire(self, token: DetectedToken, fee_eth: float,
                    age_seconds: int, tx_hash: str) -> None:
        await self._store(token, fee_eth, age_seconds, tx_hash)
        await self._send_telegram(token, fee_eth, age_seconds)
        self._fan_out(token, fee_eth, age_seconds)

    def _fan_out(self, token: DetectedToken, fee_eth: float,
                 age_seconds: int) -> None:
        """The subscribers' copy of the same alert, after the operator's."""
        from .. import alert_dispatch
        from ..alert_subs import Event
        try:
            alert_dispatch.deliver(Event(
                feed="gas", chain="eth",
                text=_format_alert(token, fee_eth, age_seconds),
                keyboard=_alert_keyboard(token),
                address=token.address, symbol=token.symbol or ""))
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[GasMonitor] not queued for fan-out: {exc}")

    async def _store(self, token: DetectedToken, fee_eth: float,
                     age_seconds: int, tx_hash: str) -> None:
        from datetime import datetime, timezone
        from .. import db
        from ..ws_hub import hub
        now = time.time()
        doc = {
            "symbol": token.symbol,
            "name": token.name,
            "address": token.address,
            "dex": token.dex,
            "pool_id": token.pool_id,
            "fee_eth": fee_eth,
            "age_seconds": age_seconds,
            "tx_hash": tx_hash,
            "chain": "eth",
            "created_at": now,
            "dt": datetime.fromtimestamp(now, timezone.utc),   # TTL field
        }
        heartbeat.beat("gas_alert")
        storage._schedule(outcomes.track(
            source=outcomes.SRC_GAS, chain="eth", address=token.address,
            symbol=token.symbol, fee_eth=fee_eth,
        ))
        try:
            await db.get_collection("gas_alerts").insert_one(dict(doc))
            await hub.broadcast("gas_alert", {k: v for k, v in doc.items() if k != "dt"})
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[GasMonitor] could not store alert: {exc}")

    async def _send_telegram(self, token: DetectedToken, fee_eth: float,
                             age_seconds: int) -> None:
        chat_id = config.GAS_ALERT_CHAT_ID
        if not (config.TELEGRAM_BOT_TOKEN and chat_id):
            log.info(f"[GasAlert][DRY-RUN] {token.symbol} fee={fee_eth:.6f} ETH "
                     f"(GAS_ALERT_CHAT_ID not set)")
            return

        text = _format_alert(token, fee_eth, age_seconds)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.post(
                f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": True,
                      "reply_markup": {
                          "inline_keyboard": _alert_keyboard(token)}},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    log.info(f"[GasAlert] sent {token.symbol} → {chat_id}")
                else:
                    body = await resp.text()
                    log.error(f"[GasAlert] Telegram {resp.status}: {body[:180]}")
        except Exception as exc:  # noqa: BLE001
            log.error(f"[GasAlert] send failed: {exc}")


def _format_alert(token: DetectedToken, fee_eth: float, age_seconds: int) -> str:
    """The high-gas alert, in the house style (see app/tgstyle.py).

    Was nine paragraphs of "Token Name:" / value with a bare GMGN URL at the
    end. The facts are the same; the fee leads, because paying this much gas on
    a token this new IS the signal, and the URL is a button now.
    """
    from app import tgstyle
    lines = [f"⛽ <b>{fee_eth:.6f} ETH</b> gas on one buy",
             f"⏱ token is {age_seconds}s old",
             f"🔀 {token.dex.upper()}"
             + (f" · pool {token.pool_id[:10]}…"
                if token.dex == "v4" and token.pool_id else "")]
    return tgstyle.card(
        icon="🚨", kind="HIGH GAS EARLY BUY", chain="eth",
        symbol=token.symbol or "?", name=token.name or "",
        lines=lines, address=token.address)


def _alert_keyboard(token: DetectedToken) -> list[list[dict]]:
    from app import tgstyle
    return tgstyle.keyboard(chain="eth", address=token.address)