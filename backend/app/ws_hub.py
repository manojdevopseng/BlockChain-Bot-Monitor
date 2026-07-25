"""WebSocket hub — one broadcast channel for realtime dashboard updates.

Any layer can call `await hub.broadcast({...})` to push an event to every
connected client. Events are envelope-shaped: {"type": "...", "data": {...}}.
Phase 1 wires the endpoint + a heartbeat; scanners publish real events later.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class WSHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    @property
    def count(self) -> int:
        return len(self._clients)

    async def broadcast(self, event_type: str, data: Any) -> None:
        payload = {"type": event_type, "data": data}
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


hub = WSHub()
