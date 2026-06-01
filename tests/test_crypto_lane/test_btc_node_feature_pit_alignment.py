"""PIT alignment tests for BTC node features."""
from __future__ import annotations

import polars as pl
import pytest

from crypto_lane.src.align.pit_join import StructuralDataLeakageError, apply_pit_alignment, backward_join_node_to_exchange
from crypto_lane.src.types import PitConfig


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


def test_positive_theta_node_delays_availability():
    df = pl.DataFrame({
        "node_observation_time": [5000.0],
        "node_clock_drift_ms": [10.0],
        "network_latency_ms": [5.0],
        "processing_latency_ms": [2.0],
        "exchange_timestamp": [5000.0],
        "exchange_clock_drift_ms": [3.0],
    })
    out = apply_pit_alignment(df, max_staleness_ms=15000)
    t_avail = float(out["T_avail"][0])
    t_exch_true = float(out["T_exch_true"][0])
    assert t_avail == 5000.0 - 10.0 + 5.0 + 2.0, f"T_avail should be 4997.0, got {t_avail}"
    assert t_exch_true == 5000.0 - 3.0, f"T_exch_true should be 4997.0, got {t_exch_true}"


def test_negative_theta_advances_availability():
    df = pl.DataFrame({
        "node_observation_time": [5000.0],
        "node_clock_drift_ms": [-10.0],
        "network_latency_ms": [5.0],
        "processing_latency_ms": [2.0],
        "exchange_timestamp": [5000.0],
        "exchange_clock_drift_ms": [-3.0],
    })
    out = apply_pit_alignment(df, max_staleness_ms=15000)
    t_avail = float(out["T_avail"][0])
    t_exch_true = float(out["T_exch_true"][0])
    assert t_avail == 5000.0 - (-10.0) + 5.0 + 2.0, f"negative θ_node pushes T_avail later; got {t_avail}"
    assert t_exch_true == 5000.0 - (-3.0), f"negative θ_exch pushes T_exch_true later; got {t_exch_true}"


def test_negative_theta_exch_prevents_false_leakage():
    df = pl.DataFrame({
        "node_observation_time": [5000.0],
        "node_clock_drift_ms": [0.0],
        "network_latency_ms": [5.0],
        "processing_latency_ms": [2.0],
        "exchange_timestamp": [5000.0],
        "exchange_clock_drift_ms": [-100.0],
    })
    out = apply_pit_alignment(df, max_staleness_ms=15000)
    t_avail = float(out["T_avail"][0])
    t_exch_true = float(out["T_exch_true"][0])
    assert t_exch_true == 5100.0, "exchange behind local → T_exch_true > T_exch"
    assert t_avail < t_exch_true, "should be PIT-safe"
    assert out["is_pit_safe"][0] is True


def test_positive_theta_exch_causes_leakage_when_node_late():
    df = pl.DataFrame({
        "node_observation_time": [5000.0],
        "node_clock_drift_ms": [0.0],
        "network_latency_ms": [5.0],
        "processing_latency_ms": [2.0],
        "exchange_timestamp": [5000.0],
        "exchange_clock_drift_ms": [100.0],
    })
    out = apply_pit_alignment(df, max_staleness_ms=15000)
    t_exch_true = float(out["T_exch_true"][0])
    assert t_exch_true == 4900.0, "exchange ahead → T_exch_true < T_exch"
    assert out["is_pit_safe"][0] is False, "T_avail=5007 > T_exch_true=4900 → unsafe"


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


def test_availability_join_rejects_nominal_leakage():
    """Nominal node_ts <= exch_ts but T_avail > T_exch_true must not select leaking row."""
    exch = pl.DataFrame({"exchange_timestamp": [4000]})
    node = pl.DataFrame({
        "node_observation_time": [2500, 3500],
        "node_clock_drift_ms": [0.0, 0.0],
        "network_latency_ms": [5.0, 2000.0],
        "processing_latency_ms": [2.0, 2000.0],
        "mempool_bytes": [1.0, 99.0],
    })
    cfg = PitConfig(max_staleness_ms=15000, network_latency_ms=5.0, processing_latency_ms=2.0)
    out = backward_join_node_to_exchange(exch, node, config=cfg)
    assert out.height == 1
    assert out["node_observation_time"][0] == 2500
    assert out["mempool_bytes"][0] == 1.0
    assert out["is_pit_safe"][0] is True


def test_strict_join_raises_when_no_safe_node_row():
    exch = pl.DataFrame({"exchange_timestamp": [1000]})
    node = pl.DataFrame({
        "node_observation_time": [900],
        "node_clock_drift_ms": [0.0],
        "network_latency_ms": [500.0],
        "processing_latency_ms": [500.0],
        "mempool_bytes": [1.0],
    })
    cfg = PitConfig(strict=True, max_staleness_ms=15000)
    with pytest.raises(StructuralDataLeakageError):
        backward_join_node_to_exchange(exch, node, config=cfg)
