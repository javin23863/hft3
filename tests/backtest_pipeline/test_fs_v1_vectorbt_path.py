"""Phase 5: VectorBT fs_v1 row-loop path tests."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from backtest_pipeline.src.feature_plane import (
    FEATURE_PLANE_STATUS_BAR_STUB,
    FEATURE_PLANE_STATUS_SCHEDULED_EVENT_ONLY,
    build_feature_usage_manifest,
    build_manifest_from_feature_recipes,
)
from backtest_pipeline.src.fs_v1_screen_path import (
    FS_V1_BAR_CONSTRUCTION_ID,
    build_fs_v1_signal_computer,
    cross_asset_features_at_vis_ts,
    ohlcv_from_feature_store,
    resolve_fs_v1_screen_context,
)
from backtest_pipeline.src.vectorbt_adapter import filter_candidates
from data_system.src.feature_store import store_path
from research_pipeline.types import CandidateModel


def _feature_index_hash_value() -> str:
    from data_system.src.feature_store import feature_index_hash

    return feature_index_hash()


def _make_feature_store_npz(dest: Path) -> None:
    from research_pipeline.src.stage_a_screen import REGIME_LABELS_ORDERED

    dest.parent.mkdir(parents=True, exist_ok=True)
    n_rows = 40
    ts_start = 1_700_000_000_000_000_000
    tick_ns = 1_000_000
    ts = np.array([ts_start + i * tick_ns for i in range(n_rows)], dtype=np.int64)
    X = np.zeros((n_rows, 64), dtype=np.float64)
    X[:, 40] = 5000.0
    X[:, 41] = 1.0
    X[:, 0] = 0.6
    best_bid = np.full(n_rows, 4999.75, dtype=np.float64)
    best_ask = np.full(n_rows, 5000.00, dtype=np.float64)
    bbo_valid = np.ones(n_rows, dtype=np.bool_)
    np.savez_compressed(
        str(dest),
        ts=ts,
        X=X,
        best_bid=best_bid,
        best_ask=best_ask,
        bbo_valid=bbo_valid,
        regime_state_vocab=np.array(list(REGIME_LABELS_ORDERED)),
        regime_state_id=np.zeros(n_rows, dtype=np.int32),
        event_ctx_vocab=np.array(["NORMAL", "CPI_TIGHT"]),
        event_ctx_id=np.zeros(n_rows, dtype=np.int32),
        vol_state_vocab=np.array(["NORMAL"]),
        vol_state_id=np.zeros(n_rows, dtype=np.int32),
        liq_state_vocab=np.array(["NORMAL"]),
        liq_state_id=np.zeros(n_rows, dtype=np.int32),
        tick_size=np.float64(0.25),
        feature_index_hash=np.array(_feature_index_hash_value()),
    )


def test_ohlcv_from_feature_store_has_timestamp_column(tmp_path: Path) -> None:
    dest = tmp_path / "MES.v.0" / "MES.v.0_EVT_features_v1.npz"
    _make_feature_store_npz(dest)
    from data_system.src.feature_store import load_store

    ohlcv = ohlcv_from_feature_store(load_store(dest))
    assert ohlcv.shape[1] == 6
    assert ohlcv[0, 0] > 1e12


def test_resolve_fs_v1_context(tmp_path: Path) -> None:
    event_id = "EVT"
    sym = "MES.v.0"
    _make_feature_store_npz(store_path(tmp_path, sym, event_id))
    ctx = resolve_fs_v1_screen_context(
        repo_root=tmp_path,
        event_id=event_id,
        symbol=sym,
        feature_store_root_override=tmp_path,
    )
    assert ctx is not None
    assert len(ctx.store["ts"]) == 40


def test_build_manifest_marks_primary_fs_v1_row_loop() -> None:
    manifest = build_manifest_from_feature_recipes([], fs_v1_row_loop_active=True)
    assert manifest["primary_fs_v1"]["model_consumption"] == "not_measured"
    assert "fs_v1_row_loop" in manifest["primary_fs_v1"]["why_not_used_or_sidelined"]


def test_feature_plane_fs_v1_not_bar_stub() -> None:
    manifest = build_feature_usage_manifest(
        bar_construction_id=FS_V1_BAR_CONSTRUCTION_ID,
        feature_set_id="fs_v1",
        feature_set_hash=_feature_index_hash_value(),
        research_clock="scheduled_event",
        screening_scope="pilot",
    )
    assert manifest["primary_fs_v1"]["model_consumption"] == "not_measured"
    assert "fs_v1_row_loop" in manifest["primary_fs_v1"]["why_not_used_or_sidelined"]


def test_build_fs_v1_signal_computer_accepts_numpy_vocabs(tmp_path: Path) -> None:
    event_id = "EVT"
    sym = "MES.v.0"
    _make_feature_store_npz(store_path(tmp_path, sym, event_id))
    ctx = resolve_fs_v1_screen_context(
        repo_root=tmp_path,
        event_id=event_id,
        symbol=sym,
        feature_store_root_override=tmp_path,
    )
    assert ctx is not None
    ohlcv = ohlcv_from_feature_store(ctx.store)
    cand = CandidateModel(
        candidate_id="c1",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.01, "holding_period_bars": 5},
        thesis="test",
        metadata={"symbol": sym},
    )
    entry, exit_ = build_fs_v1_signal_computer(ctx)(cand, ohlcv, None, tmp_path)
    assert entry.shape == exit_.shape
    assert len(entry) == len(ctx.store["ts"])


def test_filter_candidates_selects_fs_v1_path(tmp_path: Path) -> None:
    event_id = "EVT"
    sym = "MES.v.0"
    _make_feature_store_npz(store_path(tmp_path, sym, event_id))
    cand = CandidateModel(
        candidate_id="c1",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.01, "holding_period_bars": 5},
        thesis="test",
        metadata={"symbol": sym},
    )
    result = filter_candidates(
        candidates=[cand],
        parsed=None,
        event_id=event_id,
        repo_root=tmp_path,
        feature_store_root=tmp_path,
        symbol=sym,
        prefer_fs_v1_path=True,
        data_loader=lambda *_: None,
        param_grid={
            "signal_threshold": [0.01],
            "holding_period_bars": [5],
            "stop_loss_pct": [None],
            "take_profit_pct": [None],
        },
    )
    artifact = result.to_dict()
    assert artifact["bar_construction_id"] == FS_V1_BAR_CONSTRUCTION_ID
    assert artifact["feature_plane_status"] != FEATURE_PLANE_STATUS_BAR_STUB
    assert artifact["model_feature_usage_status"] == "partial_observed"
    assert "fs_v1_row_loop" in artifact["feature_usage_manifest"]["primary_fs_v1"]["why_not_used_or_sidelined"]
    assert artifact["feature_plane_status"] in {
        FEATURE_PLANE_STATUS_SCHEDULED_EVENT_ONLY,
        "feature_complete_pit_declared",
    }


def test_resolve_fs_v1_context_loads_leader_leg(tmp_path: Path) -> None:
    event_id = "EVT"
    mes_sym = "MES.v.0"
    es_sym = "ES.v.0"
    _make_feature_store_npz(store_path(tmp_path, mes_sym, event_id))
    _make_feature_store_npz(store_path(tmp_path, es_sym, event_id))
    ctx = resolve_fs_v1_screen_context(
        repo_root=tmp_path,
        event_id=event_id,
        symbol=mes_sym,
        feature_store_root_override=tmp_path,
    )
    assert ctx is not None
    assert any(leader == "ES" for leader, _ in ctx.leader_legs)


def test_cross_asset_features_at_vis_ts_includes_es_provenance(tmp_path: Path) -> None:
    event_id = "EVT"
    mes_sym = "MES.v.0"
    es_sym = "ES.v.0"
    _make_feature_store_npz(store_path(tmp_path, mes_sym, event_id))
    _make_feature_store_npz(store_path(tmp_path, es_sym, event_id))
    ctx = resolve_fs_v1_screen_context(
        repo_root=tmp_path,
        event_id=event_id,
        symbol=mes_sym,
        feature_store_root_override=tmp_path,
    )
    assert ctx is not None
    ts = np.asarray(ctx.store["ts"], dtype=np.int64)
    vis_ts = int(ts[10]) - 1_000_000
    cross = cross_asset_features_at_vis_ts(ctx, vis_ts)
    assert "ES" in cross
    assert cross["ES"]["_symbol"] == "ES"
    assert cross["ES"]["_source_timestamp_ns"] <= vis_ts


def test_build_manifest_marks_cross_asset_when_aligned() -> None:
    manifest = build_manifest_from_feature_recipes([], cross_asset_aligned=True)
    cross = manifest["cross_asset_futures"]
    assert "leader_legs_aligned" in cross["why_not_used_or_sidelined"]


def test_filter_candidates_fs_v1_cross_asset_manifest(tmp_path: Path) -> None:
    event_id = "EVT"
    mes_sym = "MES.v.0"
    es_sym = "ES.v.0"
    _make_feature_store_npz(store_path(tmp_path, mes_sym, event_id))
    _make_feature_store_npz(store_path(tmp_path, es_sym, event_id))
    cand = CandidateModel(
        candidate_id="c1",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.01, "holding_period_bars": 5},
        thesis="test",
        metadata={"symbol": mes_sym},
    )
    result = filter_candidates(
        candidates=[cand],
        parsed=None,
        event_id=event_id,
        repo_root=tmp_path,
        feature_store_root=tmp_path,
        symbol=mes_sym,
        prefer_fs_v1_path=True,
        data_loader=lambda *_: None,
        param_grid={
            "signal_threshold": [0.01],
            "holding_period_bars": [5],
            "stop_loss_pct": [None],
            "take_profit_pct": [None],
        },
    )
    cross = result.to_dict()["feature_usage_manifest"]["cross_asset_futures"]
    assert "leader_legs_aligned" in cross["why_not_used_or_sidelined"]
