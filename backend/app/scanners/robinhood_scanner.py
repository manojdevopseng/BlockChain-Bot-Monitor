"""RobinhoodScanner (SOL→Robinhood cross-chain).

Ported from the reference repo (core/robinhood_scanner.py); imports only. On-chain
WS detection of noxa.fun / Uniswap V2/V3/V4 new pairs on Robinhood Chain (4663);
fires an immediate lean alert on ticker match, deduped to at most once per IST
calendar day per symbol (cycle resets 12:00 AM IST).
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional

import aiohttp

from app.scanners.onchain_detector import ChainSpec, DetectedToken, OnChainDetector, NATIVE_ZERO
from app.scanners.cross_chain_common import send_telegram, format_immediate_lean_alert, record_alert
from app.scanners import storage_repo as storage
from app.scanners import scfg as config
from app.scanners.bounded_set import BoundedSet
from app.scanners.slog import get_logger
from app.util import IST, ist_day
from app import tgbuttons

log = get_logger(__name__)



class RobinhoodScanner:
    def __init__(self, sol_scanner, session_factory) -> None:
        self._sol_scanner     = sol_scanner
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None

        self._alerted: BoundedSet = BoundedSet(20000, storage.load_set("robinhood_alerted"))
        self._symbol_alerted: dict = storage.load_dict("rbh_symbol_alerted")

        self._spec = ChainSpec(
            name               = "ROBINHOOD",
            gmgn_slug          = "robinhood",
            wss_url            = config.RBH_RPC_WSS,
            wss_source         = lambda: list(config.RBH_WSS_ENDPOINTS),
            chain_label        = "Robinhood Chain",
            http_rpc           = config.RBH_RPC_HTTP,
            base_addrs         = frozenset({config.RBH_WETH.lower(), NATIVE_ZERO}),
            v2_factory         = ((config.RBH_V2_FACTORY or "").lower() or None)     if config.RBH_V2_ENABLED else None,
            v3_factory         = ((config.RBH_V3_FACTORY or "").lower() or None)     if config.RBH_V3_ENABLED else None,
            v4_poolmanager     = ((config.RBH_V4_POOLMANAGER or "").lower() or None) if config.RBH_V4_ENABLED else None,
            explorer_token_url = config.RBH_EXPLORER_TOKEN_URL,
            noxa_factory       = ((config.NOXA_FACTORY_ADDRESS or "").lower() or None)      if config.RBH_NOXA_ENABLED else None,
            noxa_topic         = ((config.NOXA_TOKEN_CREATED_TOPIC0 or "").lower() or None) if config.RBH_NOXA_ENABLED else None,
        )
        self._detector = OnChainDetector(self._spec, self._on_token)

    async def run(self) -> None:
        if not config.RBH_RPC_WSS:
            log.error("[RBH-XCHAIN] RBH_RPC_WSS not set — SOL→Robinhood detection disabled")
            return
        self._session = self._session_factory()
        log.info(
            f"[RBH-XCHAIN] On-chain SOL→Robinhood scanner started — "
            f"immediate alerts → {config.ROBINHOOD_CHAT_ID} (ticker match only, no smart-wallet layer)"
        )
        try:
            await self._detector.run()
        except asyncio.CancelledError:
            log.info("[RBH-XCHAIN] stopped")
            raise
        finally:
            if self._session and not self._session.closed:
                await self._session.close()

    async def _on_token(self, tok: DetectedToken) -> None:
        symbol = tok.symbol.upper().strip()
        if not symbol:
            return

        sol_data = self._sol_scanner.active_watched_tickers.get(symbol)
        if sol_data is None:
            return

        addr = tok.address.lower()
        if addr in self._alerted:
            return
        self._alerted.add(addr)
        storage.save_set("robinhood_alerted", self._alerted)

        now   = time.time()
        today = ist_day(now)
        last  = float(self._symbol_alerted.get(symbol, 0))
        if last and ist_day(last) == today:
            midnight = (datetime.fromtimestamp(now, IST) + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            left_h = (midnight.timestamp() - now) / 3600
            log.info(
                f"[RBH-XCHAIN] {symbol} skipped — already alerted today (IST); "
                f"resets 12:00 AM IST ({left_h:.1f}h left) | new addr {addr[:8]}…"
            )
            return
        self._symbol_alerted = {
            s: t for s, t in self._symbol_alerted.items() if ist_day(float(t)) == today
        }
        self._symbol_alerted[symbol] = now
        storage.save_dict("rbh_symbol_alerted", self._symbol_alerted)

        log.info(
            f"[CROSS-CHAIN] Ticker match: {symbol} | "
            f"SOL {sol_data['address'][:8]}… RBH {addr[:8]}… ({tok.dex}) | immediate alert"
        )

        if await tgbuttons.is_muted("token", addr):
            log.info(f"[CROSS-CHAIN] {symbol} muted — alert suppressed")
            return

        text = format_immediate_lean_alert(sol_data, tok, self._spec)
        msg_id = await send_telegram(
            self._session, config.ROBINHOOD_CHAT_ID, text, tag="RBH-XCHAIN",
            buttons=tgbuttons.keyboard(chain=self._spec.gmgn_slug, address=tok.address,
                                       symbol=tok.symbol),
        )
        record_alert(sol_data, tok, self._spec,
                     tg_chat_id=config.ROBINHOOD_CHAT_ID, tg_message_id=msg_id)
