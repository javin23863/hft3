"""Unit tests for HybridExecutionStrategy (in-memory MBO steps, no NPZ file)."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from features_engine.src.features.mbo_features import MBOEvent
from backtest_pipeline.src.pdf_hybrid_strategy import (
    HybridExecutionStrategy,
    event_window_end_ns,
    event_window_start_ns,
)


def _event_meta() -> dict:
    return {
        "event_id": "TEST_EVENT",
        "end_utc": "2024-09-11T12:35:00+00:00",
        "window_utc": "2024-09-11T12:29:30+00:00 to 2024-09-11T12:35:00+00:00",
    }


def _mbo_sequence() -> list[MBOEvent]:
    base = 1_000_000_000_000
    return [
        MBOEvent(base, 1, "ADD", "B", 5500.0, 10),
        MBOEvent(base + 100, 2, "ADD", "A", 5500.25, 8),
        MBOEvent(base + 200, 3, "TRADE", "A", 5500.25, 5),
        MBOEvent(base + 300, 4, "TRADE", "B", 5500.0, 3),
    ]


def test_event_window_end_ns_parses_iso() -> None:
    ns = event_window_end_ns(_event_meta())
    assert ns > 1_000_000_000_000


def test_event_window_start_ns_parses_iso() -> None:
    meta = _event_meta()
    meta["start_utc"] = "2024-09-11T12:29:30+00:00"
    start = event_window_start_ns(meta)
    end = event_window_end_ns(meta)
    assert start < end


def test_time_remaining_before_window_uses_window_length() -> None:
    meta = _event_meta()
    meta["start_utc"] = "2024-09-11T12:29:30+00:00"
    events = _mbo_sequence()
    strategy = HybridExecutionStrategy(
        "unused.npz",
        event_meta=meta,
        mbo_events=events,
        quote_refresh_ticks=1,
    )
    depth = SimpleNamespace(best_bid=5500.0, best_ask=5500.25, best_bid_qty=10, best_ask_qty=8)
    hbt = MagicMock()
    hbt.depth.return_value = depth
    hbt.current_timestamp = 1_000_000_000_000
    hbt.position.return_value = 0.0
    hbt.cancel = MagicMock()
    hbt.submit_buy_order = MagicMock()
    hbt.submit_sell_order = MagicMock()

    remaining = strategy._time_remaining_sec(hbt)
    expected = (event_window_end_ns(meta) - event_window_start_ns(meta)) / 1e9
    assert abs(remaining - expected) < 1.0


def test_on_step_uses_book_bbo_when_depth_nan() -> None:
    events = _mbo_sequence()
    strategy = HybridExecutionStrategy(
        "unused.npz",
        tick_size=0.25,
        event_meta=_event_meta(),
        mbo_events=events,
        quote_refresh_ticks=1,
    )
    depth = SimpleNamespace(
        best_bid=float("nan"),
        best_ask=float("nan"),
        best_bid_qty=0,
        best_ask_qty=0,
    )
    hbt = MagicMock()
    hbt.depth.return_value = depth
    hbt.current_timestamp = events[-1].timestamp_ns
    hbt.position.return_value = 0.0
    hbt.submit_buy_order = MagicMock()
    hbt.submit_sell_order = MagicMock()
    hbt.cancel = MagicMock()

    strategy.on_step(hbt)

    assert strategy.last_hybrid_out is not None
    payload = strategy.last_hybrid_out.payload
    assert payload is not None
    assert math.isfinite(payload.optimal_bid)
    assert math.isfinite(payload.optimal_ask)
    assert payload.optimal_bid < payload.optimal_ask
    assert hbt.submit_buy_order.called or hbt.submit_sell_order.called


def test_on_step_runs_hybrid_after_trades() -> None:
    events = _mbo_sequence()
    strategy = HybridExecutionStrategy(
        "unused.npz",
        tick_size=0.25,
        event_meta=_event_meta(),
        mbo_events=events,
    )
    depth = SimpleNamespace(
        best_bid=5500.0,
        best_ask=5500.25,
        best_bid_qty=10,
        best_ask_qty=8,
    )
    hbt = MagicMock()
    hbt.depth.return_value = depth
    hbt.current_timestamp = events[-1].timestamp_ns
    hbt.position.return_value = 0.0
    hbt.submit_buy_order = MagicMock()
    hbt.submit_sell_order = MagicMock()
    hbt.cancel = MagicMock()

    strategy.on_step(hbt)

    assert strategy._mbo_idx == len(events)
    assert strategy.last_hybrid_out is not None
    assert strategy.last_hybrid_out.payload is not None
    assert strategy.last_hybrid_out.payload.optimal_bid < strategy.last_hybrid_out.payload.optimal_ask
    assert strategy._last_book_out is not None
    assert strategy._last_vpin_out is not None


def test_vpin_uses_trade_volume_not_depth_qty() -> None:
    events = _mbo_sequence()
    strategy = HybridExecutionStrategy(
        "unused.npz",
        event_meta=_event_meta(),
        mbo_events=events,
    )
    ingest_calls: list[float] = []
    original = strategy.vpin_model.ingest_bar

    def _wrapped(mid, volume, timestamp_ns=0, bucket_volume=None):
        ingest_calls.append(volume)
        return original(mid, volume, timestamp_ns, bucket_volume)

    strategy.vpin_model.ingest_bar = _wrapped

    depth = SimpleNamespace(best_bid=5500.0, best_ask=5500.25, best_bid_qty=999, best_ask_qty=999)
    hbt = MagicMock()
    hbt.depth.return_value = depth
    hbt.current_timestamp = events[-1].timestamp_ns
    hbt.position.return_value = 0.0

    strategy.on_step(hbt)

    assert ingest_calls == [5.0, 3.0]


def test_vpin_ingest_prefers_book_mid_over_depth_when_both_valid() -> None:
    """When internal MBO book has BBO, VPIN mid must not follow divergent hbt.depth."""
    events = _mbo_sequence()
    strategy = HybridExecutionStrategy(
        "unused.npz",
        event_meta=_event_meta(),
        mbo_events=events,
    )
    ingest_mids: list[float] = []
    original = strategy.vpin_model.ingest_bar

    def _wrapped(mid, volume, timestamp_ns=0, bucket_volume=None):
        ingest_mids.append(float(mid))
        return original(mid, volume, timestamp_ns, bucket_volume)

    strategy.vpin_model.ingest_bar = _wrapped

    # Depth mid = 5520.125; MBO book mid after adds/trades = 5500.125
    depth = SimpleNamespace(best_bid=5520.0, best_ask=5520.25, best_bid_qty=10, best_ask_qty=8)
    hbt = MagicMock()
    hbt.depth.return_value = depth
    hbt.current_timestamp = events[-1].timestamp_ns
    hbt.position.return_value = 0.0

    strategy.on_step(hbt)

    assert ingest_mids
    for mid in ingest_mids:
        assert abs(mid - 5500.125) < 1e-6
        assert abs(mid - 5520.125) > 0.1


def test_vpin_ingest_falls_back_to_depth_mid_when_book_empty() -> None:
    """TRADE before book BBO: VPIN uses hbt.depth mid fallback."""
    base = 1_000_000_000_000
    events = [MBOEvent(base, 1, "TRADE", "A", 5500.25, 7)]
    strategy = HybridExecutionStrategy(
        "unused.npz",
        event_meta=_event_meta(),
        mbo_events=events,
    )
    ingest_mids: list[float] = []
    original = strategy.vpin_model.ingest_bar

    def _wrapped(mid, volume, timestamp_ns=0, bucket_volume=None):
        ingest_mids.append(float(mid))
        return original(mid, volume, timestamp_ns, bucket_volume)

    strategy.vpin_model.ingest_bar = _wrapped

    depth = SimpleNamespace(best_bid=5510.0, best_ask=5510.25, best_bid_qty=5, best_ask_qty=5)
    hbt = MagicMock()
    hbt.depth.return_value = depth
    hbt.current_timestamp = base
    hbt.position.return_value = 0.0

    strategy.on_step(hbt)

    assert ingest_mids == [5510.125]


def test_cancel_quote_flag_cancels_resting_orders() -> None:
    events = _mbo_sequence()
    strategy = HybridExecutionStrategy(
        "unused.npz",
        event_meta=_event_meta(),
        mbo_events=events,
    )
    strategy._bid_oid = 100
    strategy._ask_oid = 101
    strategy.hybrid_model.evaluate = MagicMock(
        return_value=MagicMock(
            payload=MagicMock(
                cancel_quote_flag=True,
                passive_to_aggressive_flag=False,
                optimal_bid=5499.0,
                optimal_ask=5501.0,
            )
        )
    )
    depth = SimpleNamespace(best_bid=5500.0, best_ask=5500.25, best_bid_qty=10, best_ask_qty=8)
    hbt = MagicMock()
    hbt.depth.return_value = depth
    hbt.current_timestamp = events[-1].timestamp_ns
    hbt.position.return_value = 0.0
    hbt.cancel = MagicMock()

    strategy.on_step(hbt)

    assert hbt.cancel.call_count == 2
    hbt.submit_buy_order.assert_not_called()
    hbt.submit_sell_order.assert_not_called()
