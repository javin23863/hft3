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
        self._peeked: Optional[MBOEvent] = None

    @classmethod
    def from_npz(cls, path: str = "", events: Optional[np.ndarray] = None, **kwargs) -> HistoricalReplayMarketDataAdapter:
        if events is not None:
            return cls(events, **kwargs)
        return cls(load_npz_events(path), **kwargs)

    def _peek(self) -> Optional[MBOEvent]:
        if self._peeked is None and not self._finished:
            try:
                self._peeked = next(self._iter)
            except StopIteration:
                self._finished = True
        return self._peeked

    def next_event(self) -> Optional[MBOEvent]:
        ev = self._peek()
        if ev is None:
            return None
        self._peeked = None
        # Use fast path: defer MarketState construction to current_market_state()
        self._pipeline.process_event_fast(ev)
        # Invalidate cached state (will be rebuilt lazily)
        self._last_state = None
        if ev.timestamp_ns > self._current_time_ns:
            self._current_time_ns = ev.timestamp_ns
        return ev

    def current_time_ns(self) -> int:
        return self._current_time_ns

    def current_market_state(self, symbol: str) -> Optional[MarketState]:
        del symbol
        if self._last_state is None:
            self._last_state = self._pipeline.finalize()
        return self._last_state

    def is_finished(self) -> bool:
        return self._finished and self._peeked is None

    def sync_to_timestamp(self, timestamp_ns: int) -> Optional[MarketState]:
        """Feed all MBO events with ts <= timestamp_ns (filtration-safe).

        Events with ts > timestamp_ns stay buffered and are not applied to the
        pipeline, so the returned state never contains future information.
        """
        while True:
            ev = self._peek()
            if ev is None or ev.timestamp_ns > timestamp_ns:
                break
            self.next_event()
        return self.current_market_state("")
