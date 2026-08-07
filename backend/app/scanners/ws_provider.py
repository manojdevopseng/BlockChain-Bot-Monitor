"""WebSocket JSON-RPC provider with auto-reconnect.

Ported from the reference repo (api/ws_provider.py). One WSProvider = one
persistent WS connection to a chain's RPC, over a pool of up to three endpoints.

Endpoint selection lives in `wss_pool.EndpointPool`, shared with SOL discovery:
rotation on failure, wrapping back to the first, an alert when every endpoint is
refusing, and a follow-up when one answers again.
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
from app.scanners.wss_pool import EndpointPool, host_of

log = get_logger(__name__)


@dataclass
class SubscriptionSpec:
    params: list
    callback: Callable
    label: str = ""
    sub_id: Optional[str] = field(default=None, repr=False)


class WSProvider:
    def __init__(self, wss_url: str | list[str] | Callable[[], list[str]],
                 name: str = "", chain_label: str = "", alert_chat_id=None) -> None:
        # One endpoint, several, or a callable returning the current list. The
        # callable form is what lets an endpoint added in Settings be dialled on
        # the next reconnect instead of at the next process restart.
        self.name = name or "ws"
        if callable(wss_url):
            source = wss_url
        else:
            fixed = [wss_url] if isinstance(wss_url, str) else list(wss_url)
            fixed = [u for u in fixed if u]
            source = lambda: fixed  # noqa: E731
        self._pool = EndpointPool(self.name, source, chain_label or name)
        # Where this pool's outage is reported. None = the general alert group,
        # which is every existing caller.
        self._alert_chat_id = alert_chat_id
        self.wss_url = self._pool.current()
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
        fails = 0                # consecutive failures on the current endpoint

        while self._running:
            attempt += 1
            url = self._pool.current()
            if not url:
                log.error(f"[{self.name}] no WebSocket endpoint configured — "
                          f"add one in Settings → RPC Endpoints")
                await asyncio.sleep(30)
                continue
            self.wss_url = url
            # `last_error` carries the reason out of the try block so the
            # rotation decision below can act on what actually happened rather
            # than only on how many times it has happened.
            last_error: Optional[BaseException] = None
            try:
                log.info(f"[{self.name}] WebSocket connecting (attempt {attempt}, "
                         f"endpoint {self._pool.position()} — {host_of(url)})…")
                async with websockets.connect(
                    url,
                    ping_interval=30,
                    ping_timeout=60,
                    close_timeout=10,
                    max_size=2 ** 23,
                ) as ws:
                    self._ws = ws
                    backoff = 1.0
                    fails = 0
                    self.connected = True
                    self._down_since = None
                    log.info(f"[{self.name}] WebSocket connected ✓ ({host_of(url)})")
                    await self._note_success(url)

                    listen_task = asyncio.create_task(self._listen(ws))
                    await asyncio.sleep(0)
                    ok = await self._replay_persistent_specs()

                    for hook in self._on_connect:
                        try:
                            await hook()
                        except Exception as exc:
                            # Was log.debug, i.e. invisible. A hook that fails
                            # here leaves the socket connected but not doing
                            # whatever the hook set up.
                            log.error(f"[{self.name}] on_connect hook failed: "
                                      f"{type(exc).__name__}: {exc}")

                    if not ok:
                        # Connected, but a subscription we need was refused —
                        # commonly a per-subscription rate limit. Left alone the
                        # socket sits here forever looking healthy and receiving
                        # nothing, which is the worst possible failure: silent.
                        # Drop it so the loop below rotates and retries.
                        listen_task.cancel()
                        raise RuntimeError("subscription replay failed — "
                                           "reconnecting on another endpoint")

                    await listen_task

            except asyncio.CancelledError:
                self._running = False
                if self._ws is not None:
                    await self._ws.close()
                log.info(f"[{self.name}] WebSocket stopped")
                raise
            except ConnectionClosed as exc:
                last_error = exc
                log.warning(f"[{self.name}] WebSocket closed ({host_of(url)}): {exc}")
            except OSError as exc:
                last_error = exc
                log.warning(f"[{self.name}] WebSocket OS error ({host_of(url)}): {exc}")
            except Exception as exc:
                last_error = exc
                # WARNING, not ERROR: this and the two branches above were
                # inconsistent (this one alone reached Telegram) for exactly
                # the same kind of event — a single connect attempt failing,
                # which rotation below already handles. It used to double up
                # with the "endpoint rejected us" log a few lines down, so a
                # single 429 that rotated and recovered in ~1s sent two
                # separate Telegram messages for one non-event.
                log.warning(f"[{self.name}] WebSocket error ({host_of(url)}): "
                           f"{type(exc).__name__}: {exc}")
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

            fails += 1
            kind = "network"
            if last_error is not None:
                kind, detail = self._pool.note_failure(url, last_error)
                if kind in ("limit", "auth"):
                    # WARNING, not ERROR: this endpoint is down, but as long as
                    # rotation finds a working one the chain never actually
                    # stops — that is what _maybe_alert()/notify_rpc_exhausted
                    # below is for, and it is the one that should page anyone.
                    # A single 429 that self-heals in ~1s used to alert on its
                    # own here, every time, forever.
                    log.warning(f"[{self.name}] endpoint {host_of(url)} rejected us "
                               f"({detail}) — "
                               f"{'quota/rate limit' if kind == 'limit' else 'bad API key'}")
                await self._maybe_alert()

            # A quota rejection rotates immediately: the same endpoint answering
            # 429 will answer 429 again, so a second attempt only delays the
            # switch. Network errors keep the old two-strike rule, because there
            # the endpoint is probably fine.
            if kind in ("limit", "auth") or fails >= 2:
                if self._pool.rotate(f"{kind} on {host_of(url)}"):
                    fails = 0
                    backoff = 1.0

            log.info(f"[{self.name}] Reconnecting in {backoff:.1f}s…")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _note_success(self, url: str) -> None:
        """Clear this endpoint's failure mark; announce the end of an outage."""
        if not self._pool.note_success(url):
            return
        log.warning(f"[{self.name}] recovered via {host_of(url)}")
        try:
            from .. import notifier
            await notifier.notify_rpc_recovered(self._pool.label,
                                                self._pool.recovery_text(url),
                                                self._alert_chat_id)
        except Exception as exc:  # noqa: BLE001
            log.error(f"[{self.name}] could not send recovery alert: {exc}")

    async def _maybe_alert(self) -> None:
        """Alert when the whole pool is refusing. Throttled by the pool."""
        if not self._pool.should_alert():
            return
        body = self._pool.alert_text()
        # WARNING here, not ERROR: notify_rpc_exhausted() below is the actual
        # alert, with the full per-endpoint breakdown and a fix suggestion. An
        # ERROR-level line here would ALSO reach Telegram via the generic
        # log-bridge, sending the same outage as two separate messages.
        log.warning(f"[{self.name}] ALL RPC ENDPOINTS EXHAUSTED — {body.splitlines()[0]}")
        try:
            from .. import notifier
            await notifier.notify_rpc_exhausted(self._pool.label, body,
                                                self._alert_chat_id)
        except Exception as exc:  # noqa: BLE001
            log.error(f"[{self.name}] could not send exhaustion alert: {exc}")

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

    async def _replay_persistent_specs(self) -> bool:
        """Re-subscribe everything on a fresh socket.

        Returns False if any subscription was refused. The caller drops the
        connection on that: a socket with a missing subscription reports
        `connected` and delivers nothing, so the chain goes blind while every
        health check says it is fine.
        """
        ok = True
        for spec in self._persistent_specs:
            try:
                spec.sub_id = await self.subscribe(
                    spec.params, spec.callback, label=spec.label
                )
            except Exception as exc:
                ok = False
                log.error(f"[{self.name}] subscription [{spec.label}] refused: "
                          f"{type(exc).__name__}: {exc}")
        return ok

    async def _safe_call(self, cb: Callable, arg: Any) -> None:
        try:
            await cb(arg)
        except Exception as exc:
            # Named, and with the type: "Subscription callback error: 'symbol'"
            # with no chain and no traceback type was not enough to act on.
            log.error(f"[{self.name}] subscription callback error: "
                      f"{type(exc).__name__}: {exc}")
