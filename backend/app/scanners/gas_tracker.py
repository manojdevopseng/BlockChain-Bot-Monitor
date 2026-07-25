"""Per-transaction ETH gas fee — ported from the Uniswap-Token-Monitor
`src/services/fee_calculator.py`.

    fee_wei = gasUsed × effectiveGasPrice
    fee_eth = fee_wei / 1e18

The receipt is fetched WS-first (same node that delivered the pair-creation log,
avoiding cross-node replication lag) via the detector's WSProvider, falling back
to the HTTP RPC with fast retries. Receipts are cached; concurrent lookups of
the same tx share one request via an in-flight Future. Logic matches the
reference; only the transport (our WSProvider.rpc + scfg.ETH_RPC_HTTP) changed.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

import aiohttp

from app.scanners import scfg as config
from app.scanners.slog import get_logger

log = get_logger(__name__)

_WEI_PER_ETH = 10 ** 18


class GasTracker:
    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._req_id = 0
        self._cache: Dict[str, dict] = {}
        self._inflight: Dict[str, asyncio.Future] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Content-Type": "application/json"},
                connector=aiohttp.TCPConnector(limit=50),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Public ──────────────────────────────────────────────────────────────────

    async def get_fee_eth(self, tx_hash: str, provider=None) -> Optional[float]:
        """Fetch the receipt for tx_hash and return the fee in ETH (None if
        unavailable). `provider` (a WSProvider) is tried first, then HTTP."""
        if not tx_hash:
            return None
        receipt = await self._get_receipt(tx_hash, provider=provider)
        if receipt is None:
            return None
        try:
            gas_used = int(receipt["gasUsed"], 16)
            effective_gas_price = int(receipt["effectiveGasPrice"], 16)
        except (KeyError, ValueError, TypeError) as exc:
            log.warning(f"Receipt parse error for {tx_hash[:10]}…: {exc}")
            return None
        return (gas_used * effective_gas_price) / _WEI_PER_ETH

    # ── Internal ────────────────────────────────────────────────────────────────

    async def _get_receipt(self, tx_hash: str, provider=None,
                           retries: int = 5, retry_delay: float = 0.15) -> Optional[dict]:
        key = tx_hash.lower()
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        existing = self._inflight.get(key)
        if existing is not None:
            return await asyncio.shield(existing)

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[key] = fut

        try:
            # WS-first (same node that delivered the log — no replication lag).
            if provider is not None:
                try:
                    ws_receipt = await provider.rpc(
                        "eth_getTransactionReceipt", [tx_hash], timeout=6.0
                    )
                    if ws_receipt:
                        self._cache[key] = ws_receipt
                        if not fut.done():
                            fut.set_result(ws_receipt)
                        return ws_receipt
                except asyncio.CancelledError:
                    log.debug(f"WS receipt fetch cancelled (reconnect) for {tx_hash[:10]}… — HTTP fallback")
                except Exception as exc:
                    log.debug(f"WS receipt fetch failed for {tx_hash[:10]}…: {exc}")

            if not config.ETH_RPC_HTTP:
                if not fut.done():
                    fut.set_result(None)
                return None

            session = await self._get_session()
            result: Optional[dict] = None
            for attempt in range(1, retries + 1):
                self._req_id += 1
                payload = {
                    "jsonrpc": "2.0", "id": self._req_id,
                    "method": "eth_getTransactionReceipt", "params": [tx_hash],
                }
                try:
                    async with session.post(
                        config.ETH_RPC_HTTP, json=payload,
                        timeout=aiohttp.ClientTimeout(total=1),
                    ) as resp:
                        data = await resp.json(content_type=None)
                        receipt = data.get("result")
                        if receipt:
                            self._cache[key] = receipt
                            result = receipt
                            break
                        if attempt < retries:
                            await asyncio.sleep(retry_delay)
                except asyncio.TimeoutError:
                    log.warning(f"Receipt timeout (attempt {attempt}) for {tx_hash[:10]}…")
                except Exception as exc:
                    log.warning(f"Receipt error (attempt {attempt}) for {tx_hash[:10]}…: {exc}")
                    if attempt < retries:
                        await asyncio.sleep(retry_delay)

            if not fut.done():
                fut.set_result(result)
            return result
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)


gas_tracker = GasTracker()
