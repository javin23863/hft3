"""Edge-case tests for crypto/onchain feature builders.

Covers minimum-input contracts (empty / single-row), PIT-safety invariants,
and zero-z behavior on empty series.
"""
from __future__ import annotations

import math

import polars as pl
import pytest

from crypto_lane.src.features.crypto.deribit_vol_features import (
    build_deribit_vol_features,
)
from crypto_lane.src.features.crypto.funding_features import (
    build_funding_features,
)
from crypto_lane.src.features.onchain.btc_blockspace_event_features import (
    build_blockspace_event_features,
)
from crypto_lane.src.features.onchain.btc_node_mempool_features import (
    build_mempool_features,
    build_mempool_features_from_join,
)
from crypto_lane.src.math.funding_carry import funding_zscore


# ---------- funding_features ----------------------------------------------


def test_funding_zscore_returns_zero_for_empty_series():
    assert funding_zscore([], window=24) == 0.0


def test_build_funding_features_empty_returns_empty():
    empty = pl.DataFrame({
        "funding_rate": pl.Series([], dtype=pl.Float64),
        "perp_mid": pl.Series([], dtype=pl.Float64),
        "exchange_timestamp": pl.Series([], dtype=pl.Int64),
        "bid_ask_spread": pl.Series([], dtype=pl.Float64),
    })
    out = build_funding_features(empty)
    assert out.height == 0


def test_build_funding_features_single_row_does_not_crash():
    one = pl.DataFrame({
        "funding_rate": [0.0001],
        "perp_mid": [50000.0],
        "exchange_timestamp": [1000000],
        "bid_ask_spread": [2.0],
    })
    out = build_funding_features(one)
    assert out.height == 1
    # On a single observation, slope/zscore must be zero.
    assert out["funding_slope"][0] == 0.0
    assert out["funding_zscore"][0] == 0.0


# ---------- deribit_vol_features ------------------------------------------


def _surface_row(ts):
    return {
        "exchange_timestamp": ts,
        "atm_iv": 0.5,
        "skew_25d": 0.01,
        "term_structure_slope": 0.02,
        "call_mid": 100.0,
        "put_mid": 99.0,
        "spot_mid": 50000.0,
        "strike": 50000.0,
        "rate": 0.05,
        "yield_q": 0.0,
        "tau_years": 0.1,
        "iv_rv_zscore": 0.0,
        "vol_surface_quality_flag": 1,
    }


def test_build_deribit_vol_features_empty_surface_no_align_returns_empty():
    """No align_timestamps + empty (typed) surface → empty frame."""
    cols = list(_surface_row(0).keys())
    empty = pl.DataFrame({c: pl.Series([], dtype=pl.Int64 if c == "exchange_timestamp" else pl.Float64) for c in cols})
    out = build_deribit_vol_features(empty, [])
    assert out.height == 0


def test_build_deribit_vol_features_single_row_does_not_crash():
    surf = pl.DataFrame([_surface_row(1_000_000)])
    out = build_deribit_vol_features(surf, [0.001])
    assert out.height == 1
    assert "atm_iv" in out.columns
    assert "spot_realized_volatility" in out.columns


def test_build_deribit_vol_features_align_timestamps_row_count_matches():
    """With align_timestamps and a populated surface, output row count = len(align)."""
    surf = pl.DataFrame([_surface_row(500_000), _surface_row(1_500_000)])
    align = pl.Series("exchange_timestamp", [800_000, 1_200_000, 1_800_000])
    out = build_deribit_vol_features(surf, [0.001, 0.002, 0.003], align_timestamps=align)
    assert out.height == 3


@pytest.mark.xfail(
    reason="Acceptance test: empty surface + align_timestamps should produce NaN rows, "
           "but current implementation IndexErrors. Document desired behavior.",
    strict=False,
)
def test_build_deribit_vol_features_empty_surface_with_align():
    """Empty surface + align timestamps → frame of len(align) with NaN vol cols."""
    cols = list(_surface_row(0).keys())
    empty = pl.DataFrame({c: pl.Series([], dtype=pl.Int64 if c == "exchange_timestamp" else pl.Float64) for c in cols})
    align = pl.Series("exchange_timestamp", [1, 2, 3])
    out = build_deribit_vol_features(empty, [], align_timestamps=align)
    assert out.height == 3
    for c in ("atm_iv", "spot_realized_volatility"):
        col = out[c].to_list()
        assert all(v is None or (isinstance(v, float) and math.isnan(v)) for v in col)


# ---------- btc_node_mempool_features -------------------------------------


def test_build_mempool_features_from_join_empty_returns_empty():
    out = build_mempool_features_from_join(pl.DataFrame())
    assert out.is_empty()


def test_build_mempool_features_from_join_single_row_does_not_crash():
    one = pl.DataFrame({
        "exchange_timestamp": [1000000],
        "node_observation_time": [999900],
        "min_fee_sat": [50.0],
        "mempool_bytes": [12345.0],
        "mempool_max_bytes": [300_000_000.0],
        "mempool_tx_count": [42],
        "estimated_latency_ms": [7.0],
    })
    out = build_mempool_features_from_join(one)
    assert out.height == 1
    assert "btc_mempool_min_fee" in out.columns
    assert out["btc_mempool_min_fee"][0] == 50.0


def test_build_mempool_features_legacy_single_row_does_not_crash():
    one = pl.DataFrame({
        "node_observation_time": [999900],
        "exchange_timestamp": [1_000_000],
        "mempool_bytes": [12345.0],
        "mempool_max_bytes": [300_000_000.0],
        "mempool_tx_count": [42],
        "min_fee_sat": [50.0],
        "btc_blockspace_stress_score": [0.5],
        "node_clock_drift_ms": [1.0],
        "network_latency_ms": [5.0],
        "processing_latency_ms": [2.0],
        "exchange_clock_drift_ms": [0.5],
        "estimated_latency_ms": [7.0],
    })
    out = build_mempool_features(one)
    assert out.height == 1
    assert "btc_mempool_usage_bytes" in out.columns


# ---------- btc_blockspace_event_features ---------------------------------


def _empty_mempool_features():
    return pl.DataFrame({
        "btc_mempool_min_fee": pl.Series([], dtype=pl.Float64),
        "btc_blockspace_stress_score": pl.Series([], dtype=pl.Float64),
        "exchange_timestamp": pl.Series([], dtype=pl.Int64),
        "btc_mempool_bytes": pl.Series([], dtype=pl.Float64),
        "node_observation_time": pl.Series([], dtype=pl.Int64),
    })


def test_build_blockspace_event_features_empty_returns_empty():
    out = build_blockspace_event_features(_empty_mempool_features())
    assert out.height == 0


def test_build_blockspace_event_features_single_row_does_not_crash():
    mf = pl.DataFrame({
        "btc_mempool_min_fee": [100.0],
        "btc_blockspace_stress_score": [0.5],
        "exchange_timestamp": [1_000_000],
        "btc_mempool_bytes": [12345.0],
        "node_observation_time": [999_900],
    })
    out = build_blockspace_event_features(mf)
    assert out.height == 1


def test_build_blockspace_event_features_event_time_equals_node_observation_time():
    """PIT-safety: event_time MUST be node_observation_time, never exchange_timestamp."""
    mf = pl.DataFrame({
        "btc_mempool_min_fee": [100.0, 200.0, 50.0],
        "btc_blockspace_stress_score": [0.5, 0.6, 0.3],
        "exchange_timestamp": [1_000_000, 1_001_000, 1_002_000],
        "btc_mempool_bytes": [12345.0, 23456.0, 8000.0],
        "node_observation_time": [999_900, 1_000_900, 1_001_900],
    })
    out = build_blockspace_event_features(mf)
    for i in range(out.height):
        assert out["event_time"][i] == mf["node_observation_time"][i], (
            f"row {i}: event_time must equal node_observation_time for PIT safety"
        )
        assert out["event_time"][i] != mf["exchange_timestamp"][i], (
            f"row {i}: event_time must NOT be exchange_timestamp (would leak)"
        )
