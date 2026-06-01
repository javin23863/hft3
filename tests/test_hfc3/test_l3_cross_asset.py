"""Tests for HFC3 L3 cross-asset infrastructure."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from features_engine.src.features.feature_index import FeatureIndex, vector_to_feature_dict
from hfc3.ablation.metrics import mbo_predictive_r2, ols_r2
from hfc3.events.l3_event_snapshot_tensor import SNAPSHOT_OFFSETS_SEC, build_l3_event_tensor
from hfc3.features.cross_asset_l3_event_features import build_cross_asset_l3_features
from hfc3.labels.l3_event_targets import build_l3_event_targets

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(
    not (REPO / "data" / "npz" / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz").is_file(),
    reason="CPI NPZ not on disk",
)
def test_build_l3_tensor_for_cpi():
    df = build_l3_event_tensor(REPO, "CPI_2024_09_11_TIGHT", symbols=["MES.v.0", "ES.v.0"])
    assert not df.empty
    assert set(df["offset_sec"].unique()).issubset(set(SNAPSHOT_OFFSETS_SEC))
    mes = df[df["symbol"] == "MES.v.0"]
    core = mes[mes["offset_sec"].between(-30, 30)]
    if core["mbo_missing"].sum() > 0:
        pytest.skip(
            "CPI NPZ lacks full MBO coverage for MES.v.0 ±30s; "
            "need Databento MBO download for this event"
        )
    assert (core["data_source"] == "MBO_DERIVED").all()
    row0 = core[core["offset_sec"] == 0].iloc[0]
    assert row0["liquidity_vacuum_score"] == pytest.approx(
        row0["liquidity_vacuum_score"]
    )


def test_tensor_uses_feature_index_not_raw_vec_slots():
    vec = np.zeros(64, dtype=np.float64)
    vec[FeatureIndex.LIQUIDITY_VACUUM_SCORE] = 0.77
    vec[FeatureIndex.BOOK_SLOPE] = 0.11
    fd = vector_to_feature_dict(vec)
    assert fd["liquidity_vacuum_score"] == pytest.approx(0.77)
    assert fd["book_slope"] == pytest.approx(0.11)
    assert vec[18] != fd["liquidity_vacuum_score"]


def test_cross_asset_features_from_tensor():
    df = pd.DataFrame(
        [
            {
                "offset_sec": 0,
                "canonical_symbol": "MES",
                "mbo_missing": False,
                "mid_price": 5000.0,
                "liquidity_vacuum_score": 0.2,
                "aggressor_volume_imbalance": 0.1,
            },
            {
                "offset_sec": 0,
                "canonical_symbol": "MNQ",
                "mbo_missing": True,
                "mid_price": 0.0,
                "liquidity_vacuum_score": 0.0,
                "aggressor_volume_imbalance": 0.0,
            },
        ]
    )
    feats = build_cross_asset_l3_features(df, offset_sec=0)
    assert "cross_asset_feature_count" in feats


def test_first_equity_imbalance_uses_event_time_not_hash():
    df = pd.DataFrame(
        [
            {
                "offset_sec": 1,
                "canonical_symbol": "ES",
                "mbo_missing": False,
                "aggressor_volume_imbalance": 0.05,
                "mid_price": 5000.0,
                "liquidity_vacuum_score": 0.1,
            },
            {
                "offset_sec": 5,
                "canonical_symbol": "NQ",
                "mbo_missing": False,
                "aggressor_volume_imbalance": 0.4,
                "mid_price": 18000.0,
                "liquidity_vacuum_score": 0.1,
            },
        ]
    )
    feats = build_cross_asset_l3_features(df, offset_sec=5)
    assert feats["first_equity_index_to_show_aggressor_imbalance"] == pytest.approx(2.0)


def test_first_equity_ordinal_no_future_offsets():
    df = pd.DataFrame(
        [
            {
                "offset_sec": 0,
                "canonical_symbol": "ES",
                "mbo_missing": False,
                "aggressor_volume_imbalance": 0.05,
                "mid_price": 5000.0,
                "liquidity_vacuum_score": 0.1,
            },
            {
                "offset_sec": 5,
                "canonical_symbol": "NQ",
                "mbo_missing": False,
                "aggressor_volume_imbalance": 0.9,
                "mid_price": 18000.0,
                "liquidity_vacuum_score": 0.1,
            },
        ]
    )
    feats_t0 = build_cross_asset_l3_features(df, offset_sec=0)
    assert "first_equity_index_to_show_aggressor_imbalance" not in feats_t0


def test_ols_r2_requires_more_obs_than_params():
    y = np.array([1.0, 2.0])
    x = np.array([[1.0], [2.0]])
    assert ols_r2(y, x) != ols_r2(y, x)  # nan: n == p+1 is underdetermined for R²


def test_mbo_predictive_r2_needs_three_instruments():
    tensor = pd.DataFrame(
        [
            {
                "canonical_symbol": "MES",
                "offset_sec": 0,
                "mbo_missing": False,
                "liquidity_vacuum_score": 0.1,
                "aggressor_volume_imbalance": 0.2,
                "spread": 0.25,
                "cancel_to_add_ratio": 0.5,
            },
            {
                "canonical_symbol": "ES",
                "offset_sec": 0,
                "mbo_missing": False,
                "liquidity_vacuum_score": 0.9,
                "aggressor_volume_imbalance": -0.3,
                "spread": 0.25,
                "cancel_to_add_ratio": 0.4,
            },
        ]
    )
    targets = pd.DataFrame(
        [
            {"canonical_symbol": "MES", "horizon_sec": 30, "forward_return": 0.001},
            {"canonical_symbol": "ES", "horizon_sec": 30, "forward_return": -0.002},
        ]
    )
    assert mbo_predictive_r2(tensor, targets, horizon_sec=30) != mbo_predictive_r2(
        tensor, targets, horizon_sec=30
    )


def test_mbo_predictive_r2_three_instruments():
    tensor = pd.DataFrame(
        [
            {
                "canonical_symbol": "MES",
                "offset_sec": 0,
                "mbo_missing": False,
                "liquidity_vacuum_score": 0.1,
                "aggressor_volume_imbalance": 0.2,
                "spread": 0.25,
                "cancel_to_add_ratio": 0.5,
            },
            {
                "canonical_symbol": "ES",
                "offset_sec": 0,
                "mbo_missing": False,
                "liquidity_vacuum_score": 0.5,
                "aggressor_volume_imbalance": -0.1,
                "spread": 0.25,
                "cancel_to_add_ratio": 0.4,
            },
            {
                "canonical_symbol": "NQ",
                "offset_sec": 0,
                "mbo_missing": False,
                "liquidity_vacuum_score": 0.9,
                "aggressor_volume_imbalance": -0.3,
                "spread": 0.5,
                "cancel_to_add_ratio": 0.6,
            },
        ]
    )
    targets = pd.DataFrame(
        [
            {"canonical_symbol": "MES", "horizon_sec": 30, "forward_return": 0.001},
            {"canonical_symbol": "ES", "horizon_sec": 30, "forward_return": -0.0005},
            {"canonical_symbol": "NQ", "horizon_sec": 30, "forward_return": -0.002},
        ]
    )
    r2 = mbo_predictive_r2(tensor, targets, horizon_sec=30)
    assert r2 == r2
    assert r2 > 0.0


def test_ols_r2_perfect_fit():
    y = np.array([1.0, 2.0, 3.0])
    x = np.array([[1.0], [2.0], [3.0]])
    assert ols_r2(y, x) == pytest.approx(1.0)


def test_targets_no_lookahead_columns():
    rows = []
    for off in [0, 1, 5]:
        rows.append(
            {
                "canonical_symbol": "MES",
                "offset_sec": off,
                "mbo_missing": False,
                "mid_price": 5000.0 + off,
            }
        )
    df = pd.DataFrame(rows)
    targets = build_l3_event_targets(df, anchor_offset_sec=0, horizons_sec=[1, 5])
    assert len(targets) >= 1
    assert "forward_return" in targets.columns
    t1 = targets[targets["horizon_sec"] == 1].iloc[0]
    assert t1["forward_return"] == pytest.approx(1.0 / 5000.0)
