"""File-watcher → recompute → WebSocket push.

Watches the artifact directories; on any change (debounced) it recomputes every
zone rollup and broadcasts each over the hub. Recompute is cheap (large files
are mtime-cached in loaders), so a full recompute per change keeps the mapping
trivial and correct. Runs the watchdog observer on its own thread and marshals
the async broadcast back onto the FastAPI event loop.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from . import paths, push
from .aggregate import ZONES
from .hub import Hub

_DEBOUNCE_S = 0.5
# Ignore our own writes (control audit / alert state) to avoid feedback loops.
_IGNORE_SUBSTR = ("cockpit", ".tmp", "~")


def _build_all() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, builder in ZONES.items():
        try:
            out[name] = builder()
        except Exception as exc:  # a broken zone must not kill the push
            out[name] = {"zone": name, "error": str(exc), "generated_utc": paths.now_iso()}
    return out


async def _broadcast_all(hub: Hub) -> None:
    zones = _build_all()
    # Problem-only outbound notify on NEW problems (best-effort, never blocks push).
    try:
        push.process_alerts(zones.get("alerts", {}))
    except Exception:
        pass
    for name, payload in zones.items():
        await hub.broadcast(name, payload)


class _Handler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop, hub: Hub) -> None:
        self._loop = loop
        self._hub = hub
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = str(getattr(event, "src_path", "")).lower()
        if any(s in src for s in _IGNORE_SUBSTR):
            return
        self._schedule()

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(_DEBOUNCE_S, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        asyncio.run_coroutine_threadsafe(_broadcast_all(self._hub), self._loop)


class Watcher:
    def __init__(self, hub: Hub) -> None:
        self._hub = hub
        self._observer: Optional[Observer] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        handler = _Handler(loop, self._hub)
        obs = Observer()
        for d in paths.WATCH_DIRS:
            if d.is_dir():
                obs.schedule(handler, str(d), recursive=True)
        obs.daemon = True
        obs.start()
        self._observer = obs

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            try:
                self._observer.join(timeout=2)
            except RuntimeError:
                pass
            self._observer = None
