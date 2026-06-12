"""WebSocket hub — fan-out zone deltas to subscribed clients.

A client connects, optionally subscribes to a subset of zones (default: all),
and receives ``{"zone": <name>, "payload": <rollup>}`` messages whenever the
watcher recomputes that zone. Broadcast is best-effort; a dead socket is
dropped, never allowed to block the others."""
from __future__ import annotations

import asyncio
from typing import Optional

from starlette.websockets import WebSocket, WebSocketState


class Hub:
    def __init__(self) -> None:
        self._conns: dict[WebSocket, set[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, zones: Optional[set[str]] = None) -> None:
        await ws.accept()
        async with self._lock:
            self._conns[ws] = zones or set()

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._conns.pop(ws, None)

    async def set_zones(self, ws: WebSocket, zones: set[str]) -> None:
        async with self._lock:
            if ws in self._conns:
                self._conns[ws] = zones

    async def broadcast(self, zone: str, payload: dict) -> None:
        message = {"zone": zone, "payload": payload}
        async with self._lock:
            targets = [
                ws for ws, zones in self._conns.items()
                if not zones or zone in zones
            ]
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                if ws.application_state == WebSocketState.CONNECTED:
                    await ws.send_json(message)
                else:
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._conns.pop(ws, None)

    @property
    def count(self) -> int:
        return len(self._conns)
