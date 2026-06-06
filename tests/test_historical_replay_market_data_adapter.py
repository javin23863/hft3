from __future__ import annotations

import numpy as np
from hftbacktest.types import ADD_ORDER_EVENT, BUY_EVENT, EXCH_EVENT, LOCAL_EVENT, event_dtype

from replay.market_data_adapter import HistoricalReplayMarketDataAdapter


def _raw_add_events() -> np.ndarray:
    flags = ADD_ORDER_EVENT | BUY_EVENT | EXCH_EVENT | LOCAL_EVENT
    return np.array(
        [
            (flags, 100, 100, 100.0, 10, 1, 0, 0.0),
            (flags, 200, 200, 101.0, 10, 2, 0, 0.0),
        ],
        dtype=event_dtype,
    )


def _adapter_with_recorder() -> tuple[HistoricalReplayMarketDataAdapter, list[int]]:
    adapter = HistoricalReplayMarketDataAdapter.from_npz(events=_raw_add_events())
    processed_timestamps: list[int] = []

    class _RecorderPipeline:
        def process_event(self, ev):
            processed_timestamps.append(ev.timestamp_ns)
            return ev.timestamp_ns

    adapter._pipeline = _RecorderPipeline()
    return adapter, processed_timestamps


def test_sync_to_timestamp_buffers_first_future_event_once():
    adapter = HistoricalReplayMarketDataAdapter.from_npz(events=_raw_add_events())
    processed_timestamps: list[int] = []

    class _RecorderPipeline:
        def process_event(self, ev):
            processed_timestamps.append(ev.timestamp_ns)
            return ev.timestamp_ns

    adapter._pipeline = _RecorderPipeline()

    assert adapter.sync_to_timestamp(150) == 100
    assert processed_timestamps == [100]
    assert adapter.current_time_ns() == 100

    assert adapter.sync_to_timestamp(200) == 200
    assert processed_timestamps == [100, 200]
    assert adapter.current_time_ns() == 200

    assert adapter.sync_to_timestamp(200) == 200
    assert processed_timestamps == [100, 200]


def test_repeated_sync_before_pending_future_event_does_not_drain_buffer():
    adapter, processed_timestamps = _adapter_with_recorder()

    assert adapter.sync_to_timestamp(150) == 100
    assert adapter.sync_to_timestamp(150) == 100
    assert adapter.sync_to_timestamp(199) == 100

    assert processed_timestamps == [100]
    assert adapter.current_time_ns() == 100
    assert not adapter.is_finished()


def test_next_event_after_early_sync_drains_buffered_event_once():
    adapter, processed_timestamps = _adapter_with_recorder()

    assert adapter.sync_to_timestamp(150) == 100
    event = adapter.next_event()

    assert event is not None
    assert event.timestamp_ns == 200
    assert processed_timestamps == [100, 200]
    assert adapter.current_time_ns() == 200


def test_finished_after_buffered_event_is_consumed_and_iterator_exhausts():
    adapter, processed_timestamps = _adapter_with_recorder()

    assert adapter.sync_to_timestamp(150) == 100
    assert adapter.next_event() is not None
    assert not adapter.is_finished()

    assert adapter.next_event() is None
    assert adapter.is_finished()
    assert adapter.next_event() is None
    assert processed_timestamps == [100, 200]
