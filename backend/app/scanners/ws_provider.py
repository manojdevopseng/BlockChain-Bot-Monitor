"""WebSocket JSON-RPC provider with auto-reconnect.

Ported verbatim from the reference repo (api/ws_provider.py); only the logger
import changed. One WSProvider = one persistent WS connection to a chain's RPC.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from app.scanners.slog import get_logger

log = get_logger(__name__)


@dataclass
class SubscriptionSpec:
    params: list
    callback: Callable
    label: str = ""
    sub_id: Optional[str] = field(default=None, repr=False)


class WSProvider:
    def __init__(self, wss_url: str, name: str = "") -> None:
        self.wss_url = wss_url
        self.name = name or "ws"
        self._ws: Optional[Any] = None
        self._running = False

        self._pending: Dict[int, asyncio.Future] = {}
        self._req_id = 0
        self._sub_callbacks: Dict[str, Callable] = {}
        self._persistent_specs: List[SubscriptionSpec] = []
        self._on_connect: List[Callable[[], Coroutine]] = []

        self.connected: bool = False
        self._down_since: Optional[float] = time.time()

    def add_persistent_spec(self, spec: SubscriptionSpec) -> None:
        self._persistent_specs.append(spec)

    def register_on_connect(self, coro_fn: Callable[[], Coroutine]) -> None:
        self._on_connect.append(coro_fn)

    def down_seconds(self) -> float:
        if self.connected or self._down_since is None:
            return 0.0
        return time.time() - self._down_since

    async def subscribe(self, params: list, callback: Callable, label: str = "") -> str:
        sub_id = await self._rpc("eth_subscribe", params)
        self._sub_callbacks[sub_id] = callback
        log.debug(f"[{self.name}] Subscribed [{label or sub_id[:10]}] → {sub_id}")
        return sub_id

    async def unsubscribe(self, sub_id: str) -> None:
        """Cancel a subscription by id (used when a SwapMonitor stops watching)."""
        try:
            await self._rpc("eth_unsubscribe", [sub_id])
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[{self.name}] Unsubscribe {sub_id[:10]}… failed: {exc}")
        self._sub_callbacks.pop(sub_id, None)
        log.debug(f"[{self.name}] Unsubscribed {sub_id[:10]}…")

    async def rpc(self, method: str, params: list, timeout: float = 6.0):
        return await self._rpc(method, params, timeout=timeout)

    async def run(self) -> None:
        self._running = True
        backoff = 1.0
        attempt = 0

        while self._running:
            attempt += 1
            try:
                log.info(f"[{self.name}] WebSocket connecting (attempt {attempt})…")
                async with websockets.connect(
                    self.wss_url,
                    ping_interval=30,
                    ping_timeout=60,
                    close_timeout=10,
                    max_size=2 ** 23,
                ) as ws:
                    self._ws = ws
                    backoff = 1.0
                    self.connected = True
                    self._down_since = None
                    log.info(f"[{self.name}] WebSocket connected ✓")

                    listen_task = asyncio.create_task(self._listen(ws))
                    await asyncio.sleep(0)
                    await self._replay_persistent_specs()

                    for hook in self._on_connect:
                        try:
                            await hook()
                        except Exception as exc:
                            log.debug(f"[{self.name}] on_connect hook error: {exc}")

                    await listen_task

            except asyncio.CancelledError:
                self._running = False
                if self._ws is not None:
                    await self._ws.close()
                log.info(f"[{self.name}] WebSocket stopped")
                raise
            except ConnectionClosed as exc:
                log.warning(f"[{self.name}] WebSocket closed: {exc}")
            except OSError as exc:
                log.warning(f"[{self.name}] WebSocket OS error: {exc}")
            except Exception as exc:
                log.error(f"[{self.name}] WebSocket unexpected error: {exc}")
            finally:
                self._ws = None
                if self.connected:
                    self.connected = False
                    self._down_since = time.time()
                for fut in self._pending.values():
                    if not fut.done():
                        fut.cancel()
                self._pending.clear()
                self._sub_callbacks.clear()

            if not self._running:
                break

            log.info(f"[{self.name}] Reconnecting in {backoff:.1f}s…")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    def stop(self) -> None:
        self._running = False

    async def _listen(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("method") == "eth_subscription":
                sub_id = msg["params"]["subscription"]
                result = msg["params"]["result"]
                cb = self._sub_callbacks.get(sub_id)
                if cb:
                    asyncio.create_task(self._safe_call(cb, result))
            elif "id" in msg:
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(
                            RuntimeError(msg["error"].get("message", "rpc error"))
                        )
                    else:
                        fut.set_result(msg.get("result"))

    async def _rpc(self, method: str, params: list, timeout: float = 10.0) -> Any:
        if self._ws is None:
            raise RuntimeError("WebSocket not connected")
        self._req_id += 1
        req_id = self._req_id
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        )
        await self._ws.send(payload)
        return await asyncio.wait_for(fut, timeout=timeout)

    async def _replay_persistent_specs(self) -> None:
        for spec in self._persistent_specs:
            try:
                spec.sub_id = await self.subscribe(
                    spec.params, spec.callback, label=spec.label
                )
            except Exception as exc:
                log.error(f"[{self.name}] Failed to replay subscription [{spec.label}]: {exc}")

    @staticmethod
    async def _safe_call(cb: Callable, arg: Any) -> None:
        try:
            await cb(arg)
        except Exception as exc:
            log.error(f"Subscription callback error: {exc}")
