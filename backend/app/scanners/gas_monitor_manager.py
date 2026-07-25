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

import html
import time
from typing import Dict

import aiohttp

from app.scanners import scfg as config
from app.scanners.onchain_detector import DetectedToken
from app.scanners.slog import get_logger
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
                      "disable_web_page_preview": True},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    log.info(f"[GasAlert] sent {token.symbol} → {chat_id}")
                else:
                    body = await resp.text()
                    log.error(f"[GasAlert] Telegram {resp.status}: {body[:180]}")
        except Exception as exc:  # noqa: BLE001
            log.error(f"[GasAlert] send failed: {exc}")


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def _format_alert(token: DetectedToken, fee_eth: float, age_seconds: int) -> str:
    """The reference's alert body, unchanged."""
    addr = token.address
    pool_info = ""
    if token.dex == "v4" and token.pool_id:
        pool_info = f"\nPool ID: <code>{token.pool_id[:18]}…</code>"

    return (
        "🚨 <b>High Gas Early Activity</b>\n\n"
        f"Token Name:\n<b>{_esc(token.name)}</b>\n\n"
        f"Symbol:\n<b>{_esc(token.symbol)}</b>\n\n"
        f"CA:\n<code>{addr}</code>\n\n"
        f"DEX: <b>{token.dex.upper()}</b>{pool_info}\n\n"
        f"Age:\n<b>{age_seconds}s</b>\n\n"
        f"Latest Fee:\n<b>{fee_eth:.6f} ETH</b>\n\n"
        f"🔗 <b>GMGN:</b>\n"
        f"https://gmgn.ai/eth/token/{addr}"
    )
