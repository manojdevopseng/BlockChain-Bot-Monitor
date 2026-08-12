"""EthTrendingScanner (SOL→ETH cross-chain).

Ported from the reference repo (core/eth_trending_scanner.py); imports only.
On-chain WS detection of Uniswap V2/V3/V4 new pairs on Ethereum mainnet; fires
an immediate lean alert when a new token's symbol matches an active SOL-watched
ticker. No smart-wallet layer.
"""

import asyncio
import contextlib
from typing import Optional

import aiohttp

from app.scanners.onchain_detector import ChainSpec, DetectedToken, OnChainDetector, NATIVE_ZERO
from app.scanners.cross_chain_common import send_telegram, format_immediate_lean_alert, record_alert
from app.scanners import storage_repo as storage
from app.scanners import scfg as config
from app.scanners.bounded_set import BoundedSet
from app.scanners.slog import get_logger
from app import tgbuttons

log = get_logger(__name__)


class EthTrendingScanner:
    def __init__(self, sol_scanner, session_factory) -> None:
        self._sol_scanner     = sol_scanner
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None

        self._alerted: BoundedSet = BoundedSet(20000, storage.load_set("xchain_watching"))

        self._spec = ChainSpec(
            name           = "ETH",
            gmgn_slug      = "eth",
            wss_url        = config.ETH_RPC_WSS,
            wss_source     = lambda: list(config.ETH_WSS_ENDPOINTS),
            chain_label    = "Ethereum",
            http_rpc       = config.ETH_RPC_HTTP,
            base_addrs     = frozenset({config.ETH_WETH.lower(), NATIVE_ZERO}),
            v2_factory     = (config.ETH_V2_FACTORY or "").lower() or None,
            v3_factory     = (config.ETH_V3_FACTORY or "").lower() or None,
            v4_poolmanager = (config.ETH_V4_POOLMANAGER or "").lower() or None,
        )
        self._detector = OnChainDetector(self._spec, self._on_token)

        # ETH Gas Fees feature: every detected V2/V4 pair gets a swap monitor
        # that alerts on a high-gas early buy. Created lazily in run().
        self._gas: Optional[object] = None
        self._gas_provider = None      # own socket only if GAS_RPC_WSS differs
        self._gas_task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        """Proxies OnChainDetector.connected — supervisor.rpc_connected() reads
        this on the instance it holds, which is this wrapper, not the detector
        inside it. Without this property, `getattr(inst, "connected", False)`
        silently found nothing and always fell back to False, so RPC Monitor
        showed "stopped" for a socket that was live and delivering pairs the
        whole time (caught live on the box: Robinhood was streaming pair
        events non-stop while its own status row said stopped)."""
        return self._detector.connected

    @property
    def active_endpoint(self) -> str:
        """The endpoint the socket is on right now.

        A pool of three rotates on rejection, so "which URL is this chain
        actually using" is not answerable from config alone — RPC Monitor reads
        it here to mark the live one apart from the standby slots.
        """
        return getattr(self._detector.provider, "wss_url", "") or ""

    async def run(self) -> None:
        if not config.ETH_RPC_WSS:
            log.error("[ETH-XCHAIN] ETH_RPC_WSS not set — SOL→ETH on-chain detection disabled")
            return
        self._session = self._session_factory()
        from .gas_monitor_manager import GasMonitorManager

        # A watched pair costs one subscription and every buy costs a receipt.
        # On its own endpoint that load cannot starve new-pair detection, so
        # when GAS_RPC_WSS points somewhere else the gas feature gets its own
        # socket. Same URL (or blank) = share the detector's, as before.
        gas_wss = config.GAS_RPC_WSS
        if gas_wss and gas_wss != config.ETH_RPC_WSS:
            from .ws_provider import WSProvider
            # The callable form, so a fallback pasted into RPC Monitor is
            # dialled on the next reconnect rather than at the next restart.
            self._gas_provider = WSProvider(
                lambda: list(config.GAS_WSS_ENDPOINTS) or [config.GAS_RPC_WSS],
                name="ETH-GAS")
            self._gas_task = asyncio.create_task(self._gas_provider.run(), name="eth-gas-ws")
            gas_provider = self._gas_provider
            where = "own RPC endpoint"
        else:
            gas_provider = self._detector.provider
            where = "shared ETH RPC endpoint"
        self._gas = GasMonitorManager(gas_provider)

        log.info(
            f"[ETH-XCHAIN] On-chain SOL→ETH scanner started — "
            f"immediate alerts → {config.CROSS_CHAIN_CHAT_ID} (ticker match only, no smart-wallet layer)"
        )
        log.info(
            f"[GasMonitor] ETH Gas Fees armed — alert when an early buy pays "
            f">= {config.MIN_FEE_ETH} ETH gas (V2+V4, window {config.MONITOR_WINDOW_SECONDS}s) "
            f"on its {where}"
        )
        try:
            await self._detector.run()
        except asyncio.CancelledError:
            log.info("[ETH-XCHAIN] stopped")
            raise
        finally:
            if self._gas is not None:
                await self._gas.close()
            if self._gas_provider is not None:
                self._gas_provider.stop()
            if self._gas_task is not None and not self._gas_task.done():
                self._gas_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._gas_task
            if self._session and not self._session.closed:
                await self._session.close()

    async def _on_token(self, tok: DetectedToken) -> None:
        symbol = tok.symbol.upper().strip()
        if not symbol:
            return

        # ── ETH Gas Fees: watch this pair's swaps for a high-gas early buy ──
        # Independent of the cross-chain match below — every new V2/V4 pair is
        # watched, gated by the dashboard's "ETH Gas Fees" toggle.
        if self._gas is not None:
            try:
                from .. import registry
                if await registry.is_enabled("eth_gas_fees"):
                    await self._gas.on_token(tok)
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[GasMonitor] could not start for {symbol}: {exc}")

        sol_data = self._sol_scanner.active_watched_tickers.get(symbol)
        if sol_data is None:
            return

        addr = tok.address.lower()
        if addr in self._alerted:
            return
        self._alerted.add(addr)
        storage.save_set("xchain_watching", self._alerted)

        log.info(
            f"[CROSS-CHAIN] Ticker match: {symbol} | "
            f"SOL {sol_data['address'][:8]}… ETH {addr[:8]}… ({tok.dex}) | immediate alert"
        )

        if await tgbuttons.is_muted("token", addr):
            log.info(f"[CROSS-CHAIN] {symbol} muted — alert suppressed")
            return

        text = format_immediate_lean_alert(sol_data, tok, self._spec)
        msg_id = await send_telegram(
            self._session, config.CROSS_CHAIN_CHAT_ID, text, tag="ETH-XCHAIN",
            buttons=tgbuttons.keyboard(chain=self._spec.gmgn_slug, address=tok.address,
                                       symbol=tok.symbol),
        )
        record_alert(sol_data, tok, self._spec,
                     tg_chat_id=config.CROSS_CHAIN_CHAT_ID, tg_message_id=msg_id)
