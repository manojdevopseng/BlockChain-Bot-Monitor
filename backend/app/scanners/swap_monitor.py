"""SwapMonitor — watches all swaps for a single token during its monitor window.

Ported from Uniswap_ETH_Monitor/src/monitor/swap_monitor.py. This is the real
"ETH Gas Fees" feature: someone paying an unusually high gas fee on an early buy
is sniping/front-running, which is the signal worth alerting on.

V2 pairs:  subscribes to Swap events on the pair contract.
V4 pools:  subscribes to Swap events on the PoolManager filtered by poolId
           (first indexed topic after the event sig).

Alert logic
───────────
On any BUY where fee_eth >= MIN_FEE_ETH → fire alert immediately → stop monitor.
Low-fee buys are ignored. No counter, no consecutive check.

Two timers run in parallel; whichever fires first stops the watch:
  • MONITOR_WINDOW_SECONDS   — hard cap from launch (default 4h)
  • FIRST_BUY_WINDOW_SECONDS — short window started by the first buy (default 240s)

Changes from the reference: logging/Telegram go through this project's slog +
notifier, and fired alerts are persisted to MongoDB for the dashboard.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from app.scanners import scfg as config
from app.scanners.gas_tracker import gas_tracker
from app.scanners.onchain_detector import DetectedToken
from app.scanners.slog import get_logger
from app.scanners.ws_provider import WSProvider

log = get_logger(__name__)

# ── Event signatures (keccak of the canonical signatures, hard-coded so this
# module needs no web3 dependency — same approach as onchain_detector) ────────

# V2: Swap(address indexed sender, uint256 amount0In, uint256 amount1In,
#          uint256 amount0Out, uint256 amount1Out, address indexed to)
V2_SWAP_SIG = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"

# V4: Swap(PoolId indexed id, address indexed sender, int128 amount0,
#          int128 amount1, uint160 sqrtPriceX96, uint128 liquidity,
#          int24 tick, uint24 fee)
V4_SWAP_SIG = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"


class SwapMonitor:
    """Monitors swaps for ONE token."""

    def __init__(self, token: DetectedToken, provider: WSProvider, on_alert) -> None:
        self.token = token
        self._provider = provider
        self._on_alert = on_alert          # async callback(token, fee_eth, age)

        self._sub_id: Optional[str] = None
        self._running = False
        self._first_buy_seen = False       # True after first buy arrives
        self._fired = False                # True once the alert has been claimed
        self._task: Optional[asyncio.Task] = None
        self._timers: list[asyncio.Task] = []
        self._buys = 0

        # Re-subscribe if the socket drops.
        provider.register_on_connect(self._on_reconnect)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        log.info(
            f"[GasMonitor] Watching {self.token.symbol} ({self.token.dex.upper()}) "
            f"for {config.MONITOR_WINDOW_SECONDS}s"
        )
        await self._subscribe()
        self._timers.append(asyncio.create_task(self._window_timer()))

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._sub_id:
            try:
                await self._provider.unsubscribe(self._sub_id)
            except Exception:
                pass
            self._sub_id = None
        for t in self._timers:
            if not t.done():
                t.cancel()
        self._timers.clear()
        log.info(f"[GasMonitor] Stopped  {self.token.symbol} ({self.token.dex.upper()})")

    @property
    def running(self) -> bool:
        return self._running

    # ── Subscription ──────────────────────────────────────────────────────────

    async def _subscribe(self) -> None:
        try:
            if self.token.dex == "v2":
                params = ["logs", {"address": self.token.pair, "topics": [V2_SWAP_SIG]}]
            else:
                # V4: PoolManager address + poolId as topic[1]
                params = ["logs", {"address": self.token.pair,
                                   "topics": [V4_SWAP_SIG, self.token.pool_id]}]
            self._sub_id = await self._provider.subscribe(
                params, self._handle_swap_log,
                label=f"Swap-{self.token.symbol}-{self.token.dex}",
            )
        except Exception as exc:  # noqa: BLE001
            log.error(f"[GasMonitor] Subscribe failed for {self.token.symbol}: {exc}")

    async def _on_reconnect(self) -> None:
        if self._running:
            self._sub_id = None
            await self._subscribe()

    # ── Timers ────────────────────────────────────────────────────────────────

    async def _window_timer(self) -> None:
        """Hard cap counted from launch."""
        try:
            await asyncio.sleep(config.MONITOR_WINDOW_SECONDS)
        except asyncio.CancelledError:
            return
        if self._running:
            log.info(
                f"[GasMonitor] Launch window ({config.MONITOR_WINDOW_SECONDS}s) expired for "
                f"{self.token.symbol} — {self._buys} buys recorded"
            )
            await self.stop()

    async def _first_buy_timer(self) -> None:
        """Started on the first buy; runs in parallel with the launch window."""
        try:
            await asyncio.sleep(config.FIRST_BUY_WINDOW_SECONDS)
        except asyncio.CancelledError:
            return
        if self._running:
            log.info(
                f"[GasMonitor] First-buy window ({config.FIRST_BUY_WINDOW_SECONDS}s) expired for "
                f"{self.token.symbol} — {self._buys} buys recorded"
            )
            await self.stop()

    # ── Swap handler ──────────────────────────────────────────────────────────

    async def _handle_swap_log(self, log_obj: dict) -> None:
        if not self._running:
            return

        tx_hash = log_obj.get("transactionHash")
        if not tx_hash:
            return

        # Only BUYs (WETH → Token); sells are ignored.
        if not _is_buy(log_obj, self.token.dex, self.token.weth_is_token0):
            return

        if not self._first_buy_seen:
            self._first_buy_seen = True
            self._timers.append(asyncio.create_task(self._first_buy_timer()))
            log.info(
                f"[GasMonitor] First buy received — {config.FIRST_BUY_WINDOW_SECONDS}s "
                f"window started  {self.token.symbol}"
            )

        # WS-first receipt (same node that delivered this log), HTTP fallback.
        fee_eth = await gas_tracker.get_fee_eth(tx_hash, provider=self._provider)
        if fee_eth is None:
            return

        self._buys += 1
        age = int(time.time() - self.token.detected_at)
        await self._check_alert(fee_eth, age, tx_hash)

    async def _check_alert(self, fee_eth: float, age_seconds: int, tx_hash: str) -> None:
        if fee_eth < config.MIN_FEE_ETH:
            return

        # Claim synchronously (no await before this) so two buys landing in the
        # same instant cannot both fire.
        if self._fired:
            return
        self._fired = True

        log.info(
            f"[GasAlert!] {self.token.symbol}  fee={fee_eth:.6f} ETH  age={age_seconds}s"
        )
        try:
            await self._on_alert(self.token, fee_eth, age_seconds, tx_hash)
        except Exception as exc:  # noqa: BLE001
            log.error(f"[GasMonitor] alert callback failed: {exc}")
        await self.stop()


# ── Buy/Sell direction helper (ported verbatim) ───────────────────────────────

def _is_buy(log_obj: dict, dex: str, weth_is_token0: bool) -> bool:
    """
    Returns True if this swap is a BUY (WETH → Token).

    V2 Swap data layout (4 x uint256):
        [0:32] amount0In  [32:64] amount1In  [64:96] amount0Out  [96:128] amount1Out
    BUY = WETH going INTO the pool:
        weth_is_token0=True  → amount0In > 0
        weth_is_token0=False → amount1In > 0

    V4 amounts are from the CALLER's perspective (int128, signed):
        positive = caller receives tokens (tokens leave the pool)
    BUY = caller receives the new token:
        weth_is_token0=True  → new token is token1 → amount1 > 0
        weth_is_token0=False → new token is token0 → amount0 > 0
    """
    try:
        raw = log_obj.get("data", "0x")
        data = bytes.fromhex(raw[2:] if raw.startswith("0x") else raw)

        if dex == "v2":
            if len(data) < 64:
                return True  # can't parse → allow through
            amount0In = int.from_bytes(data[0:32], "big")
            amount1In = int.from_bytes(data[32:64], "big")
            return amount0In > 0 if weth_is_token0 else amount1In > 0

        if len(data) < 64:
            return True

        def to_signed(b: bytes) -> int:
            val = int.from_bytes(b, "big")
            return val - (1 << 256) if val >= (1 << 255) else val

        amount0 = to_signed(data[0:32])
        amount1 = to_signed(data[32:64])
        return amount1 > 0 if weth_is_token0 else amount0 > 0

    except Exception:
        return True  # parse error → allow through
