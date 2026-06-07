from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..config import TrialConfig
from .base import ConnectorInterface

ALL_EVENT_TYPES = {
    "trade",
    "quote",
    "depth",
    "order_submit",
    "order_ack",
    "fill",
    "cancel",
    "reject",
    "position",
    "account",
}


class FixtureConnector(ConnectorInterface):
    """Synthetic trial events for CI and offline pipeline tests."""

    def __init__(self, cfg: TrialConfig, fixture_path: str | Path | None = None) -> None:
        self.cfg = cfg
        self.fixture_path = Path(fixture_path) if fixture_path else None
        self._events: list[dict[str, Any]] = []
        self._loaded = False

    def connect(self) -> None:
        if self._loaded:
            return
        if self.fixture_path and self.fixture_path.exists():
            for line in self.fixture_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    self._events.append(json.loads(line))
        else:
            base_ns = time.time_ns()
            sym = self.cfg.symbol or "MES"
            self._events = [
                {
                    "event_type": "quote",
                    "symbol": sym,
                    "exchange": self.cfg.exchange,
                    "exchange_timestamp_ns": base_ns,
                    "bid_price": 5000.0,
                    "ask_price": 5000.25,
                    "bid_size": 10,
                    "ask_size": 12,
                },
                {
                    "event_type": "trade",
                    "symbol": sym,
                    "exchange": self.cfg.exchange,
                    "exchange_timestamp_ns": base_ns + 1_000_000,
                    "price": 5000.25,
                    "size": 1,
                    "side": "B",
                },
                {
                    "event_type": "order_submit",
                    "symbol": sym,
                    "exchange": self.cfg.exchange,
                    "exchange_timestamp_ns": base_ns + 2_000_000,
                    "order_id": "FIX-001",
                    "price": 5000.0,
                    "size": 1,
                    "side": "B",
                },
                {
                    "event_type": "order_ack",
                    "symbol": sym,
                    "exchange": self.cfg.exchange,
                    "exchange_timestamp_ns": base_ns + 3_000_000,
                    "order_id": "FIX-001",
                },
            ]
        self._loaded = True

    def poll_events(self) -> list[dict[str, Any]]:
        if not self._events:
            return []
        batch, self._events = self._events, []
        now = time.time_ns()
        out = []
        for ev in batch:
            ev = dict(ev)
            ev.setdefault("local_receive_timestamp_ns", now)
            out.append(ev)
            now += 1000
        return out

    def detected_event_types(self) -> set[str]:
        return {"quote", "trade", "order_submit", "order_ack"}

    def limitations(self) -> dict[str, Any]:
        missing = sorted(ALL_EVENT_TYPES - self.detected_event_types())
        return {
            "connector": "fixture",
            "missing_event_types": missing,
            "note": "Synthetic fixture; no external Rithmic feed",
        }

    def close(self) -> None:
        self._events.clear()
        self._loaded = False
