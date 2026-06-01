"""Historical MBO market data adapter."""
from __future__ import annotations

from typing import Iterator, List, Optional

import numpy as np

from features_engine.src.features.mbo_features import MBOEvent
from features_engine.src.features.npz_feed import iter_mbo_events, load_npz_events
from features_engine.src.hypotheses.modules import MarketState
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline


class HistoricalReplayMarketDataAdapter:
    def __init__(
        self,
        raw_events: np.ndarray,
        *,
        tick_size: float = 0.25,
        latency_ms: float = 1.0,
    ) -> None:
        self._iter: Iterator[MBOEvent] = iter(iter_mbo_events(raw_events))
        self._pipeline = MarketStatePipeline(tick_size=tick_size, latency_ms=latency_ms)
        self._current_time_ns = 0
        self._last_state: Optional[MarketState] = None
        self._finished = False

    @classmethod
    def from_npz(cls, path: str = "", events: Optional[np.ndarray] = None, **kwargs) -> HistoricalReplayMarketDataAdapter:
        if events is not None:
            return cls(events, **kwargs)
        return cls(load_npz_events(path), **kwargs)

    def next_event(self) -> Optional[MBOEvent]:
        if self._finished:
            return None
        try:
            ev = next(self._iter)
        except StopIteration:
            self._finished = True
            return None
        if ev.timestamp_ns > self._current_time_ns:
            self._current_time_ns = ev.timestamp_ns
        if ev.timestamp_ns <= self._current_time_ns:
            self._last_state = self._pipeline.process_event(ev)
        return ev

    def current_time_ns(self) -> int:
        return self._current_time_ns

    def current_market_state(self, symbol: str) -> Optional[MarketState]:
        del symbol
        return self._last_state

    def is_finished(self) -> bool:
        return self._finished

    def sync_to_timestamp(self, timestamp_ns: int) -> Optional[MarketState]:
        """Feed all MBO events with ts <= timestamp_ns (filtration-safe)."""
        while True:
            ev = self.next_event()
            if ev is None:
                break
            if ev.timestamp_ns > timestamp_ns:
                return self._last_state
        return self._last_state
