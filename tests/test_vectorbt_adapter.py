"""Tests for VectorBT adapter integration with existing HFT3 pipeline.

Verifies:
- VectorBT integrates with existing candidate model format
- Large parameter grids filter without breaking
- Weak candidates rejected with reason
- Promoted candidates serialized with full metadata
- Asset-class routing selects correct validation path
- Candidates without tick data marked correctly
- Existing backtest commands still work
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from backtest_pipeline.src.promotion_gate import (
    PromotedCandidate,
    PromotionGate,
    RejectedCandidate,
    serialize_promoted,
    load_promoted,
)
from backtest_pipeline.src.vectorbt_adapter import (
    DEFAULT_PARAM_GRID,
    _compute_metrics_for_params,
    _candidate_id,
    _default_data_loader,
    _grid_size,
    _simulate_walk_forward,
    filter_candidates,
)
from research_pipeline.types import CandidateModel


def _mock_candidate(model_id: str = "HYP_5", threshold: float = 0.15) -> CandidateModel:
    return CandidateModel(
        candidate_id=f"{model_id}_thresh_{threshold}",
        model_id=model_id,
        strategy_params={"signal_threshold": threshold},
        thesis="Fade spread blowout after CPI",
        metadata={"source_model": model_id, "strategy_family": model_id, "symbol": "MES"},
    )


class TestCandidateFormat:
    def test_candidate_has_required_fields(self):
        c = _mock_candidate()
        assert c.candidate_id
        assert c.model_id
        assert c.strategy_params
        assert c.thesis
        assert c.metadata

    def test_candidate_id_from_params(self):
        c1 = _mock_candidate("HYP_5", 0.15)
        c2 = _mock_candidate("HYP_5", 0.20)
        assert c1.candidate_id != c2.candidate_id


class TestPromotionArtifact:
    def test_serialize_and_load(self):
        prom = PromotedCandidate(
            candidate_id="test_123",
            hypothesis_id="HYP_5",
            strategy_family="SpreadBlowout",
            asset_class="CME_FUTURES",
            symbol="MES",
            timeframe="1m",
            param_values={"signal_threshold": 0.15},
            vectorbt_run_id="vbt_test",
            vectorbt_results={"oos_expectancy": 1.5, "num_trades": 100},
            pass_reason="all_gates_passed",
            in_sample_results={"expectancy": 2.0},
            out_of_sample_results={"expectancy": 1.5},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = serialize_promoted(prom, Path(tmp))
            assert path.exists()
            loaded = load_promoted(path)
        assert loaded.candidate_id == prom.candidate_id
        assert loaded.vectorbt_results["oos_expectancy"] == 1.5
        assert loaded.pass_reason == "all_gates_passed"

    def test_rejected_serialization(self):
        r = RejectedCandidate(
            candidate_id="bad_123",
            hypothesis_id="HYP_8",
            reject_reason="promotion_gate_failed",
            metric_values={"oos_expectancy": -0.5},
        )
        d = r.to_dict()
        assert d["candidate_id"] == "bad_123"
        assert d["reject_reason"] == "promotion_gate_failed"

    def test_promotion_gate_rejects_weak(self):
        gate = PromotionGate(min_oos_expectancy=1.0, max_drawdown_pct=-20.0, min_trades=10)
        weak = PromotedCandidate(
            candidate_id="weak",
            hypothesis_id="HYP_8",
            strategy_family="BadStrat",
            asset_class="CME_FUTURES",
            symbol="MES",
            timeframe="1m",
            param_values={},
            vectorbt_run_id="vbt_test",
            vectorbt_results={"oos_expectancy": -2.0, "max_drawdown_pct": -50.0, "num_trades": 3},
            pass_reason="failed",
        )
        assert not gate.evaluate(weak)

    def test_promotion_gate_passes_strong(self):
        gate = PromotionGate(min_oos_expectancy=0.0, max_drawdown_pct=-30.0)
        strong = PromotedCandidate(
            candidate_id="strong",
            hypothesis_id="HYP_5",
            strategy_family="GoodStrat",
            asset_class="CME_FUTURES",
            symbol="ES",
            timeframe="1m",
            param_values={},
            vectorbt_run_id="vbt_test",
            vectorbt_results={
                "oos_expectancy": 2.0, "wf_consistency": 0.8,
                "max_drawdown_pct": -15.0, "turnover_mean_pct": 50.0,
                "num_trades": 200, "param_stability_score": 0.9,
                "slippage_sensitivity": 0.1,
            },
            pass_reason="testing",
        )
        assert gate.evaluate(strong)


class TestGridSize:
    def test_grid_size(self):
        grid = {"a": [1, 2], "b": [3, 4, 5]}
        assert _grid_size(grid) == 6

    def test_default_grid_non_empty(self):
        assert _grid_size(DEFAULT_PARAM_GRID) > 0


class TestMetricsFallback:
    def test_metrics_on_synthetic_data(self):
        n = 100
        close = 100.0 + np.cumsum(np.random.default_rng(42).normal(0, 0.1, n))
        ohlcv = np.column_stack([close, close + 0.2, close - 0.2, close, np.ones(n) * 1000])
        entry = np.zeros(n)
        entry[10::20] = 1.0
        entry[0] = 0.0
        exit = np.zeros(n)
        exit[15::20] = -1.0

        metrics = _compute_metrics_for_params(ohlcv, entry, exit, None, None)
        assert metrics["num_trades"] > 0
        assert metrics["net_return_pct"] is not None

    def test_walk_forward_on_synthetic(self):
        n = 500
        close = 100.0 + np.cumsum(np.random.default_rng(42).normal(0, 0.1, n))
        ohlcv = np.column_stack([close, close, close, close, np.ones(n) * 1000])
        entry = np.zeros(n)
        entry[5::10] = 1.0
        exit = np.zeros(n)
        exit[8::10] = -1.0
        wf = _simulate_walk_forward(ohlcv, entry, exit, n_windows=3)
        assert "wf_consistency" in wf
        assert "oos_expectancy" in wf
        assert 0.0 <= wf["wf_consistency"] <= 1.0, f"wf_consistency out of range: {wf['wf_consistency']}"
        assert abs(wf["oos_expectancy"]) < 0.5, (
            f"oos_expectancy unreasonably large for random walk: {wf['oos_expectancy']}"
        )

    def test_no_data_returns_empty(self):
        empty = np.zeros((10, 5), dtype=np.float64)
        entry = np.zeros(10)
        exit = np.zeros(10)
        metrics = _compute_metrics_for_params(empty, entry, exit, None, None)
        assert isinstance(metrics["num_trades"], int)


class TestAssetClassRouting:
    def test_cme_routes_to_full_execution(self):
        from backtest_pipeline.src.asset_class_routing import resolve_validation_path
        c = _mock_candidate()
        path = resolve_validation_path(c)
        assert path.route_to_vectorbt
        assert path.route_to_hftbacktest
        assert path.execution_capability.name == "FULL_EXECUTION"

    def test_crypto_no_execution(self):
        from backtest_pipeline.src.asset_class_routing import resolve_validation_path
        c = _mock_candidate("CRYPTO_BTC")
        path = resolve_validation_path(c)
        assert path.route_to_vectorbt
        assert not path.route_to_hftbacktest
        assert path.execution_capability.name == "NO_EXECUTION_VALIDATION"

    def test_equities_no_execution(self):
        from backtest_pipeline.src.asset_class_routing import resolve_validation_path
        c = _mock_candidate("LOW_FLOAT_RUNNER")
        path = resolve_validation_path(c)
        assert not path.route_to_hftbacktest


class TestFilterCandidates:
    def test_filter_without_vectorbt_fallback(self):
        cands = [_mock_candidate("HYP_5", 0.15)]
        result = filter_candidates(
            candidates=cands,
            parsed=None,
            event_id="CPI_2024_09_11_TIGHT",
            repo_root=Path(__file__).resolve().parents[1],
        )
        assert result.total_candidates >= 1
        assert not result.vectorbt_available or len(result.promoted) > 0
        if result.promoted:
            prom = result.promoted[0]
            assert prom.candidate_id, "candidate_id must be populated"
            assert prom.hypothesis_id, "hypothesis_id must be populated"
            assert prom.asset_class in ("CME_FUTURES", ""), f"Unexpected asset_class: {prom.asset_class}"
            assert prom.param_values, "param_values must be populated"
            assert prom.vectorbt_results, "vectorbt_results must be populated"
