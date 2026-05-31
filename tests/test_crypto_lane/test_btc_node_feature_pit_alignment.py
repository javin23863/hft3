"""PIT alignment tests for BTC node features."""
from __future__ import annotations

import polars as pl
import pytest

from crypto_lane.src.align.pit_join import StructuralDataLeakageError, apply_pit_alignment, backward_join_node_to_exchange


def test_pit_safe_row_has_availability_flag():
    df = pl.DataFrame({
        "node_observation_time": [1000.0],
        "node_clock_drift_ms": [1.0],
        "network_latency_ms": [5.0],
        "processing_latency_ms": [2.0],
        "exchange_timestamp": [1010.0],
        "exchange_clock_drift_ms": [0.5],
    })
    out = apply_pit_alignment(df, max_staleness_ms=15000)
    assert out["btc_node_data_available_flag"][0] == 1
    assert out["is_pit_safe"][0] is True


def test_stale_node_sets_flag_zero():
    df = pl.DataFrame({
        "node_observation_time": [1000.0],
        "node_clock_drift_ms": [0.0],
        "network_latency_ms": [0.0],
        "processing_latency_ms": [0.0],
        "exchange_timestamp": [100000.0],
        "exchange_clock_drift_ms": [0.0],
    })
    out = apply_pit_alignment(df, max_staleness_ms=15000)
    assert out["btc_node_data_available_flag"][0] == 0


def test_strict_mode_raises_on_leakage():
    df = pl.DataFrame({
        "node_observation_time": [2000.0],
        "node_clock_drift_ms": [0.0],
        "network_latency_ms": [0.0],
        "processing_latency_ms": [0.0],
        "exchange_timestamp": [1000.0],
        "exchange_clock_drift_ms": [0.0],
    })
    with pytest.raises(StructuralDataLeakageError):
        apply_pit_alignment(df, strict=True)


def test_backward_join_uses_past_node_only():
    exch = pl.DataFrame({"exchange_timestamp": [3000, 4000]})
    node = pl.DataFrame({
        "node_observation_time": [1000, 2500, 3500],
        "node_clock_drift_ms": [0.0, 0.0, 0.0],
        "network_latency_ms": [5.0, 5.0, 5.0],
        "processing_latency_ms": [2.0, 2.0, 2.0],
        "mempool_bytes": [1.0, 2.0, 3.0],
    })
    out = backward_join_node_to_exchange(exch, node)
    assert out.height == 2
