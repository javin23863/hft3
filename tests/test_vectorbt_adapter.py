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

import copy
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
    build_parameter_space_artifact,
    compute_parameter_space_hash,
    compute_screening_artifact_hash,
    expand_parameter_grid,
    filter_candidates,
    persist_screening_artifact,
    validate_screening_artifact,
    validate_screening_artifact_or_raise,
    validate_parameter_space_artifact,
    SCREENING_ARTIFACT_REQUIRED_FIELDS,
    SCREENING_CANDIDATE_REQUIRED_FIELDS,
    ScreeningArtifactError,
    ParameterSpaceArtifactError,
    _rust_required_for_scope,
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


def _duplicate_base_candidates() -> list[CandidateModel]:
    return [
        CandidateModel(
            candidate_id="same_base_candidate",
            model_id="HYP_5",
            strategy_params={"signal_threshold": 0.15},
            thesis="same model and params, MES idea",
            metadata={
                "strategy_family": "HYP_5",
                "symbol": "MES",
                "idea_id": "idea_mes",
                "feature_set_id": "fs_macro_momentum",
            },
        ),
        CandidateModel(
            candidate_id="same_base_candidate",
            model_id="HYP_5",
            strategy_params={"signal_threshold": 0.15},
            thesis="same model and params, MNQ idea",
            metadata={
                "strategy_family": "HYP_5",
                "symbol": "MNQ",
                "idea_id": "idea_mnq",
                "feature_set_id": "fs_macro_momentum",
            },
        ),
    ]


def _valid_external_robustness_evidence() -> dict:
    return {
        "walk_forward_metrics": {
            "fold_matrix": [["2018-2020", "2021"], ["2019-2021", "2022"]],
            "fold_train_test_dates": [
                {"train": ["2018-01-01", "2020-12-31"], "test": ["2021-01-01", "2021-12-31"]},
                {"train": ["2019-01-01", "2021-12-31"], "test": ["2022-01-01", "2022-12-31"]},
            ],
            "fold_metrics": [{"sharpe": 1.0}, {"sharpe": 1.1}],
            "walk_forward_efficiency": 0.72,
            "fold_dispersion": 0.08,
            "is_oos_gap": 0.12,
            "oos_decay": 0.18,
        },
        "wfc_metrics": {
            "metric_in_sample": [1.2, 1.0, 0.9],
            "metric_out_of_sample": [1.0, 0.86, 0.78],
            "pearson": 0.64,
            "spearman": 0.58,
            "scatter_data": [{"is": 1.2, "oos": 1.0}],
            "quadrant_counts": {"high_is_high_oos": 2, "high_is_low_oos": 0},
            "high_is_high_oos_region": {"threshold": 0.8, "count": 2},
            "rejection_reason": "not_rejected",
        },
        "surface_stability_metrics": {
            "status": "pass",
            "formula_authority_status": "defined",
            "literature_or_ontology_citation": "docs/project/ROBUSTNESS_TESTING_SPEC.md:130-144",
            "required_checks": [
                "plateau_width",
                "neighbor_stability",
                "cliff_distance_from_loss_regions",
                "parameter_perturbation_sensitivity",
                "peak_vs_plateau_comparison",
                "minimum_sample_size",
            ],
            "plateau_score": 0.81,
            "plateau_width": 3,
            "neighbor_stability": 0.76,
            "cliff_distance_from_loss_regions": 2,
            "parameter_perturbation_sensitivity": 0.12,
            "peak_vs_plateau_comparison": 0.93,
            "minimum_sample_size": 32,
        },
        "robustness_gate_scope": "screen",
        "wfc_status": "pass",
        "dsr_status": "pass",
        "pbo_status": "pass",
        "cscv_status": "pass",
        "robustness_artifact_staleness": "fresh",
        "bootstrap_ci_or_not_run": {"status": "pass", "lower": 0.01, "upper": 0.05},
        "dsr_or_not_run": {"status": "pass", "dsr_pass": True, "dsr_cdf": 0.96},
        "pbo_or_not_run": {"status": "pass", "pbo_pass": True, "pbo": 0.12, "maximum_pbo": 0.2},
        "cscv_count_or_not_run": {"status": "pass", "n_partitions": 16, "n_configs": 8},
        "fee_stress_or_not_run": {"status": "pass"},
        "slippage_stress_or_not_run": {"status": "pass"},
        "latency_stress_or_not_run": {"status": "pass"},
        "holm_bh_or_not_run": {"status": "pass"},
        "null_battery_or_not_run": {"status": "pass"},
        "planted_alpha_or_not_run": {"status": "pass"},
        "adversarial_or_not_run": {"status": "pass"},
        "parameter_perturbation_or_not_run": {"status": "pass"},
    }


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


def _parameter_definitions():
    return {
        "holding_period_bars": {
            "parameter_type": "integer",
            "unit": "bars",
            "lower_bound": 5,
            "upper_bound": 15,
            "default_value": 5,
            "range_reason": "pilot finite grid",
            "literature_or_ontology_citation": "docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md#VBT-1",
        },
        "signal_threshold": {
            "parameter_type": "float",
            "unit": "normalized_signal",
            "lower_bound": 0.1,
            "upper_bound": 0.2,
            "default_value": 0.1,
            "range_reason": "pilot finite grid",
            "literature_or_ontology_citation": "docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md#VBT-1",
        },
    }


def _parameter_space_artifact():
    return build_parameter_space_artifact(
        param_grid={"signal_threshold": [0.1, 0.2], "holding_period_bars": [5, 15]},
        parameter_definitions=_parameter_definitions(),
        model_id="HYP_5",
        feature_set_id="fs_cme_microstructure_v1",
        research_clock="continuous_intraday",
        symbol_universe=["MES"],
        data_manifest_hash="data_manifest_sha256",
        split_scheme_id="wf_2018_2025",
        max_trials=4,
        parameter_space_id="manual_id",
        created_at_utc="2026-06-16T00:00:00+00:00",
    )


def _complete_vbt_stats(
    *,
    total_return_pct: float = 1.25,
    total_trades: int = 1,
    expectancy: float = 0.01,
    max_drawdown_pct: float = -0.2,
) -> dict:
    return {
        "Total Return [%]": total_return_pct,
        "Total Trades": total_trades,
        "Expectancy": expectancy,
        "Profit Factor": 1.4,
        "Sharpe Ratio": 0.8,
        "Sortino Ratio": 1.1,
        "Max Drawdown [%]": max_drawdown_pct,
    }


def _promoted_screening_artifact(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    from backtest_pipeline.src import vectorbt_adapter

    class FakePortfolio:
        def stats(self):
            return _complete_vbt_stats()

    fake_vectorbt = SimpleNamespace(
        Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
    )
    monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
    monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
    monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
    monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

    close = 100.0 + np.arange(80, dtype=float) * 0.1
    ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

    result = filter_candidates(
        candidates=[_mock_candidate("HYP_5", 0.15)],
        parsed=None,
        event_id="SYNTHETIC",
        repo_root=tmp_path,
        gates=PromotionGate(
            min_oos_expectancy=-1.0,
            min_walk_forward_consistency=0.0,
            max_drawdown_pct=-30.0,
            min_trades=0,
            param_stability_rtol=1.0,
            max_slippage_sensitivity=1.0,
        ),
        param_grid={
            "signal_threshold": [0.15],
            "holding_period_bars": [15],
            "stop_loss_pct": [None],
            "take_profit_pct": [None],
        },
        data_loader=lambda *_: ohlcv,
        signal_computer=lambda *_: (
            np.r_[0.0, 1.0, np.zeros(len(ohlcv) - 2)],
            np.r_[np.zeros(len(ohlcv) - 1), -1.0],
        ),
    )
    return result.to_dict()


def _rejected_screening_artifact(tmp_path):
    return filter_candidates(
        candidates=[_mock_candidate("HYP_5", 0.15)],
        parsed=None,
        event_id="NO_DATA",
        repo_root=tmp_path,
        data_loader=lambda *_: None,
        param_grid={
            "signal_threshold": [0.15],
            "holding_period_bars": [15],
            "stop_loss_pct": [None],
            "take_profit_pct": [None],
        },
    ).to_dict()


class TestParameterSpaceArtifact:
    def test_build_validates_missing_unit(self):
        definitions = _parameter_definitions()
        definitions["signal_threshold"]["unit"] = ""

        with pytest.raises(ParameterSpaceArtifactError, match="missing unit"):
            build_parameter_space_artifact(
                param_grid={"signal_threshold": [0.1], "holding_period_bars": [5]},
                parameter_definitions=definitions,
                model_id="HYP_5",
                feature_set_id="fs_cme_microstructure_v1",
                research_clock="continuous_intraday",
                symbol_universe=["MES"],
                data_manifest_hash="data_manifest_sha256",
                split_scheme_id="wf_2018_2025",
                max_trials=1,
            )

    def test_build_validates_missing_citation(self):
        definitions = _parameter_definitions()
        definitions["holding_period_bars"]["literature_or_ontology_citation"] = ""

        with pytest.raises(ParameterSpaceArtifactError, match="literature_or_ontology_citation"):
            build_parameter_space_artifact(
                param_grid={"signal_threshold": [0.1], "holding_period_bars": [5]},
                parameter_definitions=definitions,
                model_id="HYP_5",
                feature_set_id="fs_cme_microstructure_v1",
                research_clock="continuous_intraday",
                symbol_universe=["MES"],
                data_manifest_hash="data_manifest_sha256",
                split_scheme_id="wf_2018_2025",
                max_trials=1,
            )

    @pytest.mark.parametrize("bad_budget", [0, -1, 1.5, "1.5"])
    def test_build_rejects_malformed_max_trials(self, bad_budget):
        with pytest.raises(ParameterSpaceArtifactError, match="max_trials"):
            build_parameter_space_artifact(
                param_grid={"signal_threshold": [0.1], "holding_period_bars": [5]},
                parameter_definitions=_parameter_definitions(),
                model_id="HYP_5",
                feature_set_id="fs_cme_microstructure_v1",
                research_clock="continuous_intraday",
                symbol_universe=["MES"],
                data_manifest_hash="data_manifest_sha256",
                split_scheme_id="wf_2018_2025",
                max_trials=bad_budget,
            )

    def test_validate_rejects_post_hoc_flag_false(self):
        artifact = _parameter_space_artifact()
        artifact["forbidden_post_hoc_change"] = False

        with pytest.raises(ParameterSpaceArtifactError, match="forbidden_post_hoc_change"):
            validate_parameter_space_artifact(artifact)

    def test_expansion_sorts_parameter_names_independent_of_insertion_order(self):
        grid_a = {"signal_threshold": [0.1, 0.2], "holding_period_bars": [5, 15]}
        grid_b = {"holding_period_bars": [5, 15], "signal_threshold": [0.1, 0.2]}

        assert expand_parameter_grid(grid_a) == expand_parameter_grid(grid_b)

        artifact_a = build_parameter_space_artifact(
            param_grid=grid_a,
            parameter_definitions=_parameter_definitions(),
            model_id="HYP_5",
            feature_set_id="fs_cme_microstructure_v1",
            research_clock="continuous_intraday",
            symbol_universe=["MES"],
            data_manifest_hash="data_manifest_sha256",
            split_scheme_id="wf_2018_2025",
            max_trials=4,
            parameter_space_id="a",
            created_at_utc="2026-06-16T00:00:00+00:00",
        )
        artifact_b = build_parameter_space_artifact(
            param_grid=grid_b,
            parameter_definitions=_parameter_definitions(),
            model_id="HYP_5",
            feature_set_id="fs_cme_microstructure_v1",
            research_clock="continuous_intraday",
            symbol_universe=["MES"],
            data_manifest_hash="data_manifest_sha256",
            split_scheme_id="wf_2018_2025",
            max_trials=4,
            parameter_space_id="b",
            created_at_utc="2027-01-01T00:00:00+00:00",
        )

        assert artifact_a["candidates"] == artifact_b["candidates"]
        assert artifact_a["parameter_space_hash"] == artifact_b["parameter_space_hash"]

    def test_validate_rejects_hash_mismatch(self):
        artifact = _parameter_space_artifact()
        artifact["candidates"][0]["parameter_values"]["signal_threshold"] = 0.3

        with pytest.raises(ParameterSpaceArtifactError, match="hash mismatch"):
            validate_parameter_space_artifact(artifact)

    @pytest.mark.parametrize("bad_budget", [0, -1, 1.5, "1.5"])
    def test_validate_rejects_malformed_max_trials(self, bad_budget):
        artifact = _parameter_space_artifact()
        artifact["max_trials"] = bad_budget
        artifact["parameter_space_hash"] = compute_parameter_space_hash(artifact)

        with pytest.raises(ParameterSpaceArtifactError, match="max_trials"):
            validate_parameter_space_artifact(artifact)

    def test_build_rejects_invalid_research_clock(self):
        with pytest.raises(ParameterSpaceArtifactError, match="research_clock_invalid"):
            build_parameter_space_artifact(
                param_grid={"signal_threshold": [0.1]},
                parameter_definitions=_parameter_definitions(),
                model_id="HYP_5",
                feature_set_id="fs_cme_microstructure_v1",
                research_clock="bogus_lane",
                symbol_universe=["MES"],
                data_manifest_hash="data_manifest_sha256",
                split_scheme_id="wf_2018_2025",
                max_trials=1,
            )


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

    def test_walk_forward_preserves_open_position_at_oos_boundary(self):
        n = 120
        close = 100.0 + np.arange(n, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones(n) * 1000])
        entry = np.zeros(n)
        exit = np.zeros(n)
        entry[10] = 1.0
        exit[110] = -1.0

        wf = _simulate_walk_forward(ohlcv, entry, exit, n_windows=4)

        assert wf["wf_consistency"] > 0.0
        assert wf["oos_expectancy"] > 0.0

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

    def test_options_no_execution(self):
        from backtest_pipeline.src.asset_class_routing import resolve_validation_path
        c = _mock_candidate("OPTIONS_PARITY_BASIS")
        path = resolve_validation_path(c)
        assert path.route_to_vectorbt
        assert not path.route_to_hftbacktest
        assert path.execution_capability.name == "NO_EXECUTION_VALIDATION"


class TestFilterCandidates:
    def test_filter_rejects_missing_ohlcv_even_when_vectorbt_installed(self, tmp_path):
        from backtest_pipeline.src import vectorbt_adapter

        vectorbt_adapter._vectorbt_version = None
        vectorbt_adapter._rust_engine_available = None
        cands = [_mock_candidate("HYP_5", 0.15)]
        result = filter_candidates(
            candidates=cands,
            parsed=None,
            event_id="CPI_2024_09_11_TIGHT",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            prefer_fs_v1_path=False,
        )
        assert result.total_candidates >= 1
        assert result.backend == "no_ohlcv_data"
        assert not result.promoted
        assert len(result.rejected) == 1
        assert result.rejected[0].reject_reason == "no_ohlcv_data"
        artifact = result.to_dict()
        for field in (
            "screening_backend",
            "vectorbt_version",
            "vectorbt_engine",
            "engine_parity_status",
            "rust_engine_required_for_scope",
            "rust_engine_available",
            "license_review",
            "research_clock",
            "parameter_space_id",
            "parameter_space_hash",
            "max_trials",
            "trials_run",
            "run_budget_id",
            "max_models",
            "max_symbols",
            "max_feature_sets",
            "max_total_trials",
            "max_wall_clock_seconds",
            "max_peak_memory_mb_or_null",
            "abort_on_budget_exhaustion",
            "screening_scope",
            "split_scheme_id",
            "candidate_ids",
            "promoted_ids",
            "rejected_ids",
            "promoted_reasons",
            "rejected_reasons",
            "stop_reasons",
            "no_lookahead_signal_shift_proof",
            "feature_set_id",
            "feature_set_hash",
            "data_manifest_hash",
            "lake_manifest_hash",
            "events_csv_hash_or_not_applicable",
            "fees_model_id",
            "slippage_model_id",
            "bar_construction_id",
            "screening_artifact_hash",
        ):
            assert field in artifact
        assert artifact["screening_backend"] == "vectorbt"
        assert artifact["max_trials"] == 32
        assert artifact["max_total_trials"] == 32
        assert artifact["max_models"] == 1
        assert artifact["max_symbols"] == 1
        assert artifact["max_feature_sets"] == 1
        assert artifact["max_wall_clock_seconds"] is None
        assert artifact["max_peak_memory_mb_or_null"] is None
        assert artifact["abort_on_budget_exhaustion"] is True
        assert artifact["trials_run"] == 0
        assert artifact["candidate_ids"] == [result.rejected[0].candidate_id]
        assert artifact["rejected_ids"] == [result.rejected[0].candidate_id]
        assert artifact["rejected"][0]["base_candidate_id"] == "HYP_5_thresh_0.15"

    def test_filter_without_vectorbt_fails_closed_when_data_and_signal_exist(self, monkeypatch):
        from backtest_pipeline.src import vectorbt_adapter

        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", False)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", None)
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", None)
        cands = [_mock_candidate("HYP_5", 0.15)]
        close = 100.0 + np.arange(120, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        def data_loader(event_id, repo_root):
            return ohlcv

        def signal_computer(cand, bars, parsed, repo_root):
            entry = np.zeros(len(bars))
            exit_ = np.zeros(len(bars))
            entry[1] = 1.0
            exit_[-1] = -1.0
            return entry, exit_

        result = filter_candidates(
            candidates=cands,
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=Path(__file__).resolve().parents[1],
            gates=PromotionGate(min_oos_expectancy=0.0, min_walk_forward_consistency=0.0, min_trades=1),
            param_grid={"signal_threshold": [0.15], "holding_period_bars": [15], "stop_loss_pct": [None], "take_profit_pct": [None]},
            data_loader=data_loader,
            signal_computer=signal_computer,
        )

        assert result.vectorbt_available is False
        assert result.backend == "vectorbt_unavailable"
        assert not result.promoted
        assert len(result.rejected) == 1
        assert result.rejected[0].reject_reason == "vectorbt_unavailable_fail_closed"
        artifact = result.to_dict()
        assert artifact["vectorbt_engine"] == "unavailable"
        assert artifact["engine_parity_status"] == "vectorbt_unavailable_fail_closed"
        assert artifact["stop_reasons"] == ["vectorbt_unavailable_fail_closed"]

    def test_filter_without_vectorbt_rejects_missing_signal_binding(self, monkeypatch):
        from backtest_pipeline.src import vectorbt_adapter

        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", False)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", None)
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", None)
        cands = [_mock_candidate("CRYPTO_H1", 0.15)]
        close = 100.0 + np.arange(40, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        result = filter_candidates(
            candidates=cands,
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=Path(__file__).resolve().parents[1],
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (_ for _ in ()).throw(ValueError("signal binding unavailable")),
        )

        assert result.vectorbt_available is False
        assert result.backend == "vectorbt_unavailable"
        assert not result.promoted
        assert result.rejected[0].reject_reason == "vectorbt_unavailable_fail_closed"

    def test_parameter_grid_recomputes_signal_per_trial(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                return _complete_vbt_stats(total_return_pct=0.0, expectancy=0.0)

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        cands = [_mock_candidate("HYP_5", 0.15)]
        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])
        seen_thresholds = []

        def signal_computer(cand, bars, parsed, repo_root):
            threshold = cand.strategy_params["signal_threshold"]
            seen_thresholds.append(threshold)
            entry = np.zeros(len(bars))
            exit_ = np.zeros(len(bars))
            if threshold < 0.5:
                entry[1] = 1.0
                exit_[-1] = -1.0
            return entry, exit_

        result = filter_candidates(
            candidates=cands,
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(min_oos_expectancy=0.0, min_walk_forward_consistency=0.0, min_trades=0),
            param_grid={
                "signal_threshold": [0.1, 0.9],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=signal_computer,
        )

        assert seen_thresholds == [0.1, 0.9]
        assert result.trials_run == 2
        observed_ids = [p.candidate_id for p in result.promoted] + [r.candidate_id for r in result.rejected]
        assert len(set(observed_ids)) == 2

    def test_max_total_trials_stops_before_extra_parameter_trials(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                return _complete_vbt_stats(total_return_pct=0.0, expectancy=0.0)

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])
        seen_thresholds = []

        def signal_computer(cand, bars, parsed, repo_root):
            seen_thresholds.append(cand.strategy_params["signal_threshold"])
            entry = np.zeros(len(bars))
            exit_ = np.zeros(len(bars))
            entry[1] = 1.0
            exit_[-1] = -1.0
            return entry, exit_

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(
                min_oos_expectancy=0.0,
                min_walk_forward_consistency=0.0,
                min_trades=0,
                param_stability_rtol=1.0,
            ),
            param_grid={
                "signal_threshold": [0.1, 0.2, 0.3],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=signal_computer,
            max_total_trials=1,
        )
        artifact = result.to_dict()

        assert seen_thresholds == [0.1]
        assert result.trials_run == 1
        assert artifact["max_trials"] == 3
        assert artifact["max_total_trials"] == 1
        assert artifact["trials_run"] == 1
        assert artifact["stop_reasons"] == ["RUN_BUDGET_REACHED"]
        assert len(artifact["candidate_ids"]) == 3
        assert len(artifact["rejected"]) == 2
        assert artifact["rejected"][0]["rejection_reason_or_null"] == "RUN_BUDGET_REACHED"
        assert artifact["rejected"][0]["parameter_values"]["signal_threshold"] == 0.2
        assert artifact["rejected"][1]["parameter_values"]["signal_threshold"] == 0.3
        validate_screening_artifact_or_raise(artifact)

    def test_negative_max_total_trials_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="max_total_trials"):
            filter_candidates(
                candidates=[_mock_candidate("HYP_5", 0.15)],
                parsed=None,
                event_id="NO_DATA",
                repo_root=tmp_path,
                data_loader=lambda *_: None,
                max_total_trials=-1,
            )

    @pytest.mark.parametrize("bad_budget", [-0.5, 1.5, "1.5"])
    def test_fractional_max_total_trials_is_rejected(self, tmp_path, bad_budget):
        with pytest.raises(ValueError, match="max_total_trials"):
            filter_candidates(
                candidates=[_mock_candidate("HYP_5", 0.15)],
                parsed=None,
                event_id="NO_DATA",
                repo_root=tmp_path,
                data_loader=lambda *_: None,
                max_total_trials=bad_budget,
            )

    def test_zero_max_total_trials_is_valid_dry_run(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                return {"Total Return [%]": 0.0}

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            param_grid={
                "signal_threshold": [0.1],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (_ for _ in ()).throw(AssertionError("budget dry-run must not simulate")),
            max_total_trials=0,
        )
        artifact = result.to_dict()

        assert result.trials_run == 0
        assert artifact["max_total_trials"] == 0
        assert len(artifact["candidate_ids"]) == 1
        assert len(artifact["rejected"]) == 1
        assert artifact["rejected"][0]["rejection_reason_or_null"] == "RUN_BUDGET_REACHED"
        assert artifact["rejected"][0]["parameter_values"]["signal_threshold"] == 0.1
        assert artifact["stop_reasons"] == ["RUN_BUDGET_REACHED"]
        validate_screening_artifact_or_raise(artifact)

    def test_validator_rejects_trials_run_exceeding_max_total_trials(self, tmp_path):
        artifact = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            max_total_trials=0,
        ).to_dict()
        artifact["trials_run"] = 1
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(ScreeningArtifactError, match="trials_run_exceeds_max_total_trials"):
            validate_screening_artifact_or_raise(artifact)

    @pytest.mark.parametrize("bad_budget", [-0.5, 1.5, "1.5"])
    def test_validator_rejects_fractional_max_total_trials(self, tmp_path, bad_budget):
        artifact = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            max_total_trials=0,
        ).to_dict()
        artifact["max_total_trials"] = bad_budget
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(ScreeningArtifactError, match="trial_budget_fields_malformed"):
            validate_screening_artifact_or_raise(artifact)

    def test_zero_max_trials_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="max_trials"):
            filter_candidates(
                candidates=[_mock_candidate("HYP_5", 0.15)],
                parsed=None,
                event_id="NO_DATA",
                repo_root=tmp_path,
                data_loader=lambda *_: None,
                run_budget={"max_trials": 0},
            )

    @pytest.mark.parametrize("bad_budget", [-1, 0, 1.5, "1.5"])
    def test_validator_rejects_malformed_max_trials(self, tmp_path, bad_budget):
        artifact = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
        ).to_dict()
        artifact["max_trials"] = bad_budget
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(ScreeningArtifactError, match="trial_budget_fields_malformed"):
            validate_screening_artifact_or_raise(artifact)

    def test_run_budget_rejects_excess_model_count_before_data_lookup(self, tmp_path):
        cands = [_mock_candidate("HYP_5", 0.15), _mock_candidate("HYP_6", 0.15)]

        result = filter_candidates(
            candidates=cands,
            parsed=None,
            event_id="SHOULD_NOT_LOAD",
            repo_root=tmp_path,
            data_loader=lambda *_: (_ for _ in ()).throw(AssertionError("budget cap must precede data load")),
            run_budget={"max_models": 1},
        )
        artifact = result.to_dict()

        assert result.backend == "run_budget_fail_closed"
        assert result.trials_run == 0
        assert artifact["max_models"] == 1
        assert artifact["stop_reasons"] == ["RUN_BUDGET_REACHED"]
        assert set(artifact["candidate_ids"]) == {cand.candidate_id for cand in result.rejected}
        assert all(row["rejection_reason_or_null"] == "RUN_BUDGET_REACHED" for row in artifact["rejected"])
        assert artifact["rejected"][0]["budget_field"] == "max_models"
        validate_screening_artifact_or_raise(artifact)

    def test_run_budget_reject_ids_include_symbol_idea_context(self, tmp_path):
        result = filter_candidates(
            candidates=_duplicate_base_candidates(),
            parsed=None,
            event_id="SHOULD_NOT_LOAD",
            repo_root=tmp_path,
            data_loader=lambda *_: (_ for _ in ()).throw(AssertionError("budget cap must precede data load")),
            run_budget={"max_symbols": 1},
        )
        artifact = result.to_dict()

        assert result.backend == "run_budget_fail_closed"
        assert len(artifact["rejected_ids"]) == 2
        assert len(set(artifact["rejected_ids"])) == 2
        assert set(artifact["candidate_reasons"]) == set(artifact["candidate_ids"])
        assert {row["base_candidate_id"] for row in artifact["rejected"]} == {"same_base_candidate"}
        assert {row["base_candidate_metadata"]["symbol"] for row in artifact["rejected"]} == {"MES", "MNQ"}
        validate_screening_artifact_or_raise(artifact)

    def test_run_budget_rejects_excess_symbol_count_before_data_lookup(self, tmp_path):
        mes = _mock_candidate("HYP_5", 0.15)
        nq = _mock_candidate("HYP_5", 0.20)
        nq.metadata = {**nq.metadata, "symbol": "NQ"}

        result = filter_candidates(
            candidates=[mes, nq],
            parsed=None,
            event_id="SHOULD_NOT_LOAD",
            repo_root=tmp_path,
            data_loader=lambda *_: (_ for _ in ()).throw(AssertionError("budget cap must precede data load")),
            run_budget={"max_symbols": 1},
        )
        artifact = result.to_dict()

        assert result.backend == "run_budget_fail_closed"
        assert artifact["max_symbols"] == 1
        assert artifact["rejected"][0]["budget_field"] == "max_symbols"
        validate_screening_artifact_or_raise(artifact)

    def test_run_budget_symbol_cap_requires_explicit_symbol_metadata(self, tmp_path):
        cand = _mock_candidate("HYP_5", 0.15)
        cand.metadata = {key: value for key, value in cand.metadata.items() if key != "symbol"}

        result = filter_candidates(
            candidates=[cand],
            parsed=None,
            event_id="SHOULD_NOT_LOAD",
            repo_root=tmp_path,
            data_loader=lambda *_: (_ for _ in ()).throw(AssertionError("missing symbol must fail before data load")),
            run_budget={"max_symbols": 1},
        )
        artifact = result.to_dict()

        assert result.backend == "run_budget_fail_closed"
        assert artifact["rejected"][0]["budget_field"] == "max_symbols"
        assert artifact["rejected"][0]["missing_budget_dimension"] == "symbol"
        validate_screening_artifact_or_raise(artifact)

    def test_run_budget_symbol_cap_rejects_strategy_param_only_symbol(self, tmp_path):
        cand = _mock_candidate("HYP_5", 0.15)
        cand.metadata = {key: value for key, value in cand.metadata.items() if key != "symbol"}
        cand.strategy_params = {**cand.strategy_params, "symbol": "MES"}

        result = filter_candidates(
            candidates=[cand],
            parsed=None,
            event_id="SHOULD_NOT_LOAD",
            repo_root=tmp_path,
            data_loader=lambda *_: (_ for _ in ()).throw(AssertionError("strategy param symbol is not cap proof")),
            run_budget={"max_symbols": 1},
        )
        artifact = result.to_dict()

        assert artifact["rejected"][0]["budget_field"] == "max_symbols"
        assert artifact["rejected"][0]["missing_budget_dimension"] == "symbol"
        validate_screening_artifact_or_raise(artifact)

    def test_run_budget_rejects_excess_feature_set_count_before_data_lookup(self, tmp_path):
        a = _mock_candidate("HYP_5", 0.15)
        b = _mock_candidate("HYP_5", 0.20)
        a.metadata = {**a.metadata, "feature_set_id": "fs_a"}
        b.metadata = {**b.metadata, "feature_set_id": "fs_b"}

        result = filter_candidates(
            candidates=[a, b],
            parsed=None,
            event_id="SHOULD_NOT_LOAD",
            repo_root=tmp_path,
            data_loader=lambda *_: (_ for _ in ()).throw(AssertionError("budget cap must precede data load")),
            run_budget={"max_feature_sets": 1},
        )
        artifact = result.to_dict()

        assert result.backend == "run_budget_fail_closed"
        assert artifact["max_feature_sets"] == 1
        assert artifact["rejected"][0]["budget_field"] == "max_feature_sets"
        validate_screening_artifact_or_raise(artifact)

    def test_run_budget_feature_set_cap_requires_explicit_feature_set_metadata(self, tmp_path):
        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SHOULD_NOT_LOAD",
            repo_root=tmp_path,
            data_loader=lambda *_: (_ for _ in ()).throw(AssertionError("missing feature set must fail before data load")),
            run_budget={"max_feature_sets": 1},
        )
        artifact = result.to_dict()

        assert result.backend == "run_budget_fail_closed"
        assert artifact["rejected"][0]["budget_field"] == "max_feature_sets"
        assert artifact["rejected"][0]["missing_budget_dimension"] == "feature_set_id"
        validate_screening_artifact_or_raise(artifact)

    def test_run_budget_feature_set_cap_rejects_strategy_param_only_feature_set(self, tmp_path):
        cand = _mock_candidate("HYP_5", 0.15)
        cand.strategy_params = {**cand.strategy_params, "feature_set_id": "fs_from_params"}

        result = filter_candidates(
            candidates=[cand],
            parsed=None,
            event_id="SHOULD_NOT_LOAD",
            repo_root=tmp_path,
            data_loader=lambda *_: (_ for _ in ()).throw(AssertionError("strategy param feature set is not cap proof")),
            run_budget={"max_feature_sets": 1},
        )
        artifact = result.to_dict()

        assert artifact["rejected"][0]["budget_field"] == "max_feature_sets"
        assert artifact["rejected"][0]["missing_budget_dimension"] == "feature_set_id"
        validate_screening_artifact_or_raise(artifact)

    def test_wall_clock_budget_stops_before_first_trial(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        fake_vectorbt = SimpleNamespace(Portfolio=SimpleNamespace(from_signals=lambda *_, **__: None))
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            param_grid={
                "signal_threshold": [0.1],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (_ for _ in ()).throw(AssertionError("wall-clock dry-run must not simulate")),
            run_budget={"max_wall_clock_seconds": 0},
        )
        artifact = result.to_dict()

        assert result.trials_run == 0
        assert artifact["max_wall_clock_seconds"] == 0
        assert artifact["stop_reasons"] == ["WALL_CLOCK_BUDGET_REACHED"]
        assert artifact["rejected"][0]["rejection_reason_or_null"] == "WALL_CLOCK_BUDGET_REACHED"
        validate_screening_artifact_or_raise(artifact)

    def test_memory_budget_request_fails_closed_until_monitor_exists(self, tmp_path):
        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SHOULD_NOT_LOAD",
            repo_root=tmp_path,
            data_loader=lambda *_: (_ for _ in ()).throw(AssertionError("memory cap must fail before data load")),
            run_budget={"max_peak_memory_mb_or_null": 1},
        )
        artifact = result.to_dict()

        assert result.backend == "run_budget_fail_closed"
        assert artifact["max_peak_memory_mb_or_null"] == 1
        assert artifact["stop_reasons"] == ["MEMORY_BUDGET_REACHED"]
        assert artifact["rejected"][0]["memory_monitor_status"] == "unsupported_fail_closed"
        validate_screening_artifact_or_raise(artifact)

    def test_validator_rejects_missing_abort_on_budget_exhaustion(self, tmp_path):
        artifact = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
        ).to_dict()
        artifact["abort_on_budget_exhaustion"] = False
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(ScreeningArtifactError, match="trial_budget_fields_malformed"):
            validate_screening_artifact_or_raise(artifact)

    def test_screen_scope_requires_rust_engine(self, monkeypatch, tmp_path):
        from backtest_pipeline.src import vectorbt_adapter

        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)
        cands = [_mock_candidate("HYP_5", 0.15)]
        close = 100.0 + np.arange(40, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        result = filter_candidates(
            candidates=cands,
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (_ for _ in ()).throw(AssertionError("screen must fail before sim")),
            screening_scope="screen",
        )

        artifact = result.to_dict()
        assert result.backend == "vectorbt_rust_unavailable"
        assert result.trials_run == 0
        assert not result.promoted
        assert result.rejected[0].reject_reason == "rust_engine_required_unavailable_fail_closed"
        assert artifact["screening_scope"] == "screen"
        assert artifact["rust_engine_required_for_scope"] is True
        assert artifact["rust_engine_available"] is False
        assert artifact["engine_parity_status"] == "rust_engine_required_unavailable_fail_closed"

    def test_screen_scope_requires_rust_runtime_proof(self, monkeypatch, tmp_path):
        from backtest_pipeline.src import vectorbt_adapter

        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", True)
        monkeypatch.setattr(vectorbt_adapter, "_VECTORBT_ENGINE_RUNTIME_PROOF", False)
        monkeypatch.setattr(vectorbt_adapter, "_establish_vectorbt_rust_runtime_proof", lambda: False)
        close = 100.0 + np.arange(40, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (_ for _ in ()).throw(AssertionError("screen must fail before sim")),
            screening_scope="paid-compute",
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        assert result.trials_run == 0
        assert not result.promoted
        assert artifact["vectorbt_engine"] == "numba"
        assert artifact["rust_engine_required_for_scope"] is True
        assert artifact["rust_engine_available"] is True
        assert artifact["vectorbt_engine_runtime_proof"] is False
        assert artifact["engine_parity_status"] == "rust_runtime_proof_missing_fail_closed"
        assert artifact["stop_reasons"] == ["rust_runtime_proof_missing_fail_closed"]
        assert artifact["rejected"][0]["rejection_reason_or_null"] == "rust_runtime_proof_missing_fail_closed"

    def test_rust_preflight_reject_ids_include_symbol_idea_context(self, monkeypatch, tmp_path):
        from backtest_pipeline.src import vectorbt_adapter

        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", True)
        monkeypatch.setattr(vectorbt_adapter, "_VECTORBT_ENGINE_RUNTIME_PROOF", False)
        monkeypatch.setattr(vectorbt_adapter, "_establish_vectorbt_rust_runtime_proof", lambda: False)

        result = filter_candidates(
            candidates=_duplicate_base_candidates(),
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            data_loader=lambda *_: (_ for _ in ()).throw(AssertionError("rust preflight must not load data")),
            screening_scope="paid-compute",
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        assert len(artifact["rejected_ids"]) == 2
        assert len(set(artifact["rejected_ids"])) == 2
        assert set(artifact["candidate_reasons"]) == set(artifact["candidate_ids"])
        assert {row["base_candidate_id"] for row in artifact["rejected"]} == {"same_base_candidate"}
        assert {row["base_candidate_metadata"]["symbol"] for row in artifact["rejected"]} == {"MES", "MNQ"}

    def test_no_data_reject_ids_include_symbol_idea_context(self, monkeypatch, tmp_path):
        from backtest_pipeline.src import vectorbt_adapter

        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        result = filter_candidates(
            candidates=_duplicate_base_candidates(),
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            screening_scope="pilot",
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        assert len(artifact["rejected_ids"]) == 2
        assert len(set(artifact["rejected_ids"])) == 2
        assert set(artifact["candidate_reasons"]) == set(artifact["candidate_ids"])
        assert {row["base_candidate_metadata"]["idea_id"] for row in artifact["rejected"]} == {
            "idea_mes",
            "idea_mnq",
        }

    def test_screen_scope_rust_preflight_runs_before_data_lookup(self, monkeypatch, tmp_path):
        from backtest_pipeline.src import vectorbt_adapter

        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            screening_scope="screen",
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)

        assert result.backend == "vectorbt_rust_unavailable"
        assert artifact["stop_reasons"] == ["rust_engine_required_unavailable_fail_closed"]
        assert artifact["rejected"][0]["rejection_reason_or_null"] == "rust_engine_required_unavailable_fail_closed"

    def test_refine_scope_uses_refine_trial_budget_and_requires_rust(self, monkeypatch, tmp_path):
        from backtest_pipeline.src import vectorbt_adapter

        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SHOULD_NOT_LOAD",
            repo_root=tmp_path,
            data_loader=lambda *_: (_ for _ in ()).throw(AssertionError("refine must fail before data load")),
            screening_scope="refine",
        )
        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)

        assert result.backend == "vectorbt_rust_unavailable"
        assert artifact["screening_scope"] == "refine"
        assert artifact["max_trials"] == 64
        assert artifact["max_total_trials"] == 64
        assert artifact["engine_parity_status"] == "rust_engine_required_unavailable_fail_closed"

    def test_screen_scope_without_vectorbt_uses_rust_required_fail_closed_status(self, monkeypatch, tmp_path):
        from backtest_pipeline.src import vectorbt_adapter

        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", False)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", None)
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", None)

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            screening_scope="screen",
        )
        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)

        assert artifact["vectorbt_engine"] == "unavailable"
        assert artifact["engine_parity_status"] == "rust_engine_required_unavailable_fail_closed"
        assert artifact["stop_reasons"] == ["rust_engine_required_unavailable_fail_closed"]

    def test_arbitrary_rust_named_modules_do_not_satisfy_vectorbt_rust_source_lock(self, monkeypatch):
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        def fake_find_spec(name):
            if name in {"vectorbt_rust", "vectorbtpro.rust"}:
                return SimpleNamespace(origin="C:/tmp/not-vectorbt/rust.py")
            return None

        monkeypatch.setattr(vectorbt_adapter.importlib.util, "find_spec", fake_find_spec)
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", None)

        assert vectorbt_adapter._detect_vectorbt_rust_engine() is False

    def test_official_vectorbt_rust_module_requires_source_lock(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        dist_root = tmp_path / "site-packages"
        rust_origin = dist_root / "vectorbt" / "rust.py"
        rust_origin.parent.mkdir(parents=True)
        rust_origin.write_text("# fake official module", encoding="utf-8")

        fake_dist = SimpleNamespace(locate_file=lambda _: dist_root)
        monkeypatch.setattr(
            vectorbt_adapter.importlib.metadata,
            "distribution",
            lambda name: fake_dist if name == "vectorbt" else None,
        )
        monkeypatch.setattr(
            vectorbt_adapter.importlib.util,
            "find_spec",
            lambda name: SimpleNamespace(origin=str(rust_origin)) if name == "vectorbt.rust" else None,
        )
        monkeypatch.setattr(vectorbt_adapter, "_VECTORBT_RUST_SOURCE_LOCK", tmp_path / "missing.lock")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", None)

        assert vectorbt_adapter._detect_vectorbt_rust_engine() is False

    def test_official_vectorbt_rust_module_with_source_lock_is_detected(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        dist_root = tmp_path / "site-packages"
        rust_origin = dist_root / "vectorbt" / "rust.py"
        rust_origin.parent.mkdir(parents=True)
        rust_origin.write_text("# fake official module", encoding="utf-8")
        source_lock = tmp_path / "VENDOR.lock"
        source_lock.write_text(
            "source-lock\npolakowo/vectorbt\nvectorbt[rust]\nparity\n",
            encoding="utf-8",
        )

        fake_dist = SimpleNamespace(locate_file=lambda _: dist_root)
        monkeypatch.setattr(
            vectorbt_adapter.importlib.metadata,
            "distribution",
            lambda name: fake_dist if name == "vectorbt" else None,
        )
        monkeypatch.setattr(
            vectorbt_adapter.importlib.util,
            "find_spec",
            lambda name: SimpleNamespace(origin=str(rust_origin)) if name == "vectorbt.rust" else None,
        )
        monkeypatch.setattr(vectorbt_adapter, "_VECTORBT_RUST_SOURCE_LOCK", source_lock)
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", None)

        assert vectorbt_adapter._detect_vectorbt_rust_engine() is True

    def test_close_derived_signals_shift_one_bar_before_vectorbt(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        captured = {}

        class FakePortfolio:
            def stats(self):
                return {"Total Return [%]": 0.0}

        def from_signals(close, entries, exits, **kwargs):
            captured["entries"] = np.asarray(entries, dtype=bool)
            captured["exits"] = np.asarray(exits, dtype=bool)
            return FakePortfolio()

        fake_vectorbt = SimpleNamespace(Portfolio=SimpleNamespace(from_signals=from_signals))
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        def signal_computer(cand, bars, parsed, repo_root):
            entry = np.zeros(len(bars))
            exit_ = np.zeros(len(bars))
            entry[0] = 1.0
            exit_[3] = -1.0
            return entry, exit_

        filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(min_oos_expectancy=-1.0, min_walk_forward_consistency=0.0, min_trades=0),
            param_grid={"signal_threshold": [0.15], "holding_period_bars": [0], "stop_loss_pct": [None], "take_profit_pct": [None]},
            data_loader=lambda *_: ohlcv,
            signal_computer=signal_computer,
        )

        assert captured["entries"].tolist() == [False, True, False, False, False, False]
        assert captured["exits"].tolist() == [False, False, False, False, True, False]

    def test_same_close_jump_signal_does_not_enter_on_jump_close(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        captured = {}

        class FakePortfolio:
            def __init__(self, stats):
                self._stats = stats

            def stats(self):
                return self._stats

        def from_signals(close, entries, exits, **kwargs):
            close_arr = np.asarray(close, dtype=float)
            entry_arr = np.asarray(entries, dtype=bool)
            exit_arr = np.asarray(exits, dtype=bool)
            captured["entries"] = entry_arr
            captured["exits"] = exit_arr
            position = False
            entry_price = 0.0
            returns = []
            for index, price in enumerate(close_arr):
                if not position and entry_arr[index]:
                    position = True
                    entry_price = float(price)
                elif position and exit_arr[index]:
                    returns.append((float(price) - entry_price) / entry_price)
                    position = False
            if position:
                returns.append((float(close_arr[-1]) - entry_price) / entry_price)
            expectancy = float(np.mean(returns)) if returns else 0.0
            total_return_pct = float(np.sum(returns) * 100.0)
            return FakePortfolio(
                _complete_vbt_stats(
                    total_return_pct=total_return_pct,
                    total_trades=len(returns),
                    expectancy=expectancy,
                    max_drawdown_pct=0.0,
                )
            )

        fake_vectorbt = SimpleNamespace(Portfolio=SimpleNamespace(from_signals=from_signals))
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = np.array([100.0, 200.0, 200.0, 200.0])
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        def signal_computer(cand, bars, parsed, repo_root):
            entry = np.zeros(len(bars))
            exit_ = np.zeros(len(bars))
            entry[0] = 1.0
            exit_[1] = -1.0
            return entry, exit_

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(
                min_oos_expectancy=0.5,
                min_walk_forward_consistency=0.0,
                min_trades=1,
                param_stability_rtol=1.0,
            ),
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [0],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=signal_computer,
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        rejected = artifact["rejected"][0]

        assert captured["entries"].tolist() == [False, True, False, False]
        assert captured["exits"].tolist() == [False, False, True, False]
        assert artifact["promoted"] == []
        assert rejected["rejection_reason_or_null"] == "promotion_gate_failed"
        assert rejected["expectancy_per_trade"] == 0.0
        assert rejected["metric_values"]["oos_expectancy"] == 0.0
        assert rejected["metric_values"]["net_return_pct"] == 0.0

    def test_filter_skips_global_promotion_persistence_by_default(self, monkeypatch, tmp_path):
        from backtest_pipeline.src import vectorbt_adapter

        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", False)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", None)
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", None)
        cands = [_mock_candidate("HYP_5", 0.15)]
        close = 100.0 + np.arange(120, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        def signal_computer(cand, bars, parsed, repo_root):
            entry = np.zeros(len(bars))
            exit_ = np.zeros(len(bars))
            entry[1] = 1.0
            exit_[-1] = -1.0
            return entry, exit_

        result = filter_candidates(
            candidates=cands,
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(min_oos_expectancy=0.0, min_walk_forward_consistency=0.0, min_trades=1),
            param_grid={"signal_threshold": [0.15], "holding_period_bars": [15], "stop_loss_pct": [None], "take_profit_pct": [None]},
            data_loader=lambda *_: ohlcv,
            signal_computer=signal_computer,
        )

        assert not result.promoted
        assert not (tmp_path / "research_cards" / "promotion").exists()


def test_json_primitive_screening_payload_serializes_nat():
    import pandas as pd

    from backtest_pipeline.src.vectorbt_adapter import _json_primitive_screening_payload

    payload = {"end_ts": pd.NaT, "ok": 1}
    cleaned = _json_primitive_screening_payload(payload)
    assert cleaned == {"end_ts": None, "ok": 1}
    json.dumps(cleaned)


class TestFilterCandidatesScreeningArtifactPersistence:
    def test_screening_artifact_hash_ignores_hash_and_timestamps(self, tmp_path):
        cands = [_mock_candidate("HYP_5", 0.15)]
        result = filter_candidates(
            candidates=cands,
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        )
        artifact_a = result.to_dict()
        artifact_b = dict(artifact_a)
        artifact_b["created_at_utc"] = "2099-01-01T00:00:00+00:00"
        artifact_b["screening_artifact_hash"] = "mutated"

        assert compute_screening_artifact_hash(artifact_a) == artifact_a["screening_artifact_hash"]
        assert compute_screening_artifact_hash(artifact_a) == compute_screening_artifact_hash(artifact_b)

    def test_persistence_normalizes_non_json_metric_values(self, tmp_path):
        class NonJsonMetric:
            def __str__(self):
                return "non_json_metric"

        artifact = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        ).to_dict()
        artifact["rejected"][0]["in_sample_metrics"]["raw_object"] = NonJsonMetric()
        artifact["rejected"][0]["out_of_sample_metrics"]["np_scalar"] = np.float64(1.25)
        artifact["rejected"][0]["walk_forward_metrics"]["np_array"] = np.array([1, 2])

        path = persist_screening_artifact(artifact, tmp_path / "screening_artifact.json")
        loaded = json.loads(path.read_text(encoding="utf-8"))

        assert loaded["rejected"][0]["in_sample_metrics"]["raw_object"] == "non_json_metric"
        assert loaded["rejected"][0]["out_of_sample_metrics"]["np_scalar"] == 1.25
        assert loaded["rejected"][0]["walk_forward_metrics"]["np_array"] == [1, 2]
        assert loaded["screening_artifact_hash"] == compute_screening_artifact_hash(loaded)
        validate_screening_artifact_or_raise(loaded)

    def test_validator_rejects_missing_and_stale_promoted_reasons(self, monkeypatch, tmp_path):
        artifact = _promoted_screening_artifact(monkeypatch, tmp_path)

        missing = copy.deepcopy(artifact)
        missing["promoted_reasons"] = {}
        missing["screening_artifact_hash"] = compute_screening_artifact_hash(missing)
        with pytest.raises(ScreeningArtifactError, match="promoted_reasons"):
            validate_screening_artifact_or_raise(missing)

        stale = copy.deepcopy(artifact)
        stale["promoted_reasons"]["stale_candidate"] = "old_reason"
        stale["screening_artifact_hash"] = compute_screening_artifact_hash(stale)
        with pytest.raises(ScreeningArtifactError, match="promoted_reasons"):
            validate_screening_artifact_or_raise(stale)

        mismatched = copy.deepcopy(artifact)
        promoted_id = mismatched["promoted_ids"][0]
        mismatched["promoted_reasons"][promoted_id] = "wrong_reason"
        mismatched["screening_artifact_hash"] = compute_screening_artifact_hash(mismatched)
        with pytest.raises(ScreeningArtifactError, match="promoted_reasons"):
            validate_screening_artifact_or_raise(mismatched)

    def test_validator_rejects_invalid_research_clock(self, monkeypatch, tmp_path):
        artifact = _promoted_screening_artifact(monkeypatch, tmp_path)
        bad = copy.deepcopy(artifact)
        bad["research_clock"] = "bogus_lane"
        bad["promoted"][0]["research_clock"] = "bogus_lane"
        bad["screening_artifact_hash"] = compute_screening_artifact_hash(bad)
        with pytest.raises(ScreeningArtifactError, match="research_clock_invalid"):
            validate_screening_artifact_or_raise(bad)

    def test_validator_rejects_missing_and_stale_rejected_reasons(self, tmp_path):
        artifact = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        ).to_dict()

        missing = copy.deepcopy(artifact)
        missing["rejected_reasons"] = {}
        missing["screening_artifact_hash"] = compute_screening_artifact_hash(missing)
        with pytest.raises(ScreeningArtifactError, match="rejected_reasons"):
            validate_screening_artifact_or_raise(missing)

        stale = copy.deepcopy(artifact)
        stale["rejected_reasons"]["stale_candidate"] = "old_reason"
        stale["screening_artifact_hash"] = compute_screening_artifact_hash(stale)
        with pytest.raises(ScreeningArtifactError, match="rejected_reasons"):
            validate_screening_artifact_or_raise(stale)

        mismatched = copy.deepcopy(artifact)
        rejected_id = mismatched["rejected_ids"][0]
        mismatched["rejected_reasons"][rejected_id] = "wrong_reason"
        mismatched["screening_artifact_hash"] = compute_screening_artifact_hash(mismatched)
        with pytest.raises(ScreeningArtifactError, match="rejected_reasons"):
            validate_screening_artifact_or_raise(mismatched)

    def test_validator_rejects_duplicate_emitted_candidate_ids(self, tmp_path):
        artifact = filter_candidates(
            candidates=_duplicate_base_candidates(),
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        ).to_dict()

        duplicate_id = artifact["rejected_ids"][0]
        artifact["rejected"][1]["candidate_id"] = duplicate_id
        artifact["rejected_ids"][1] = duplicate_id
        artifact["candidate_ids"][1] = duplicate_id
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(ScreeningArtifactError, match="rejected_ids_not_unique"):
            validate_screening_artifact_or_raise(artifact)

    def test_rust_unavailable_pilot_label_when_vectorbt_available(self, monkeypatch, tmp_path):
        from backtest_pipeline.src import vectorbt_adapter

        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)
        cands = [_mock_candidate("HYP_5", 0.15)]

        result = filter_candidates(
            candidates=cands,
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        )

        artifact = result.to_dict()
        assert artifact["vectorbt_engine"] == "numba"
        assert artifact["engine_parity_status"] == "rust_unavailable_pilot_only"
        assert artifact["rust_engine_required_for_scope"] is False
        assert artifact["rust_engine_available"] is False

    def test_rust_required_scope_aliases_include_broad_and_paid_compute(self):
        assert _rust_required_for_scope("broad-screen") is True
        assert _rust_required_for_scope("broad_screen") is True
        assert _rust_required_for_scope("paid-compute") is True
        assert _rust_required_for_scope("paid_compute") is True

    def test_validator_derives_rust_requirement_from_scope(self, tmp_path):
        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        )
        artifact = result.to_dict()
        artifact["screening_scope"] = "paid-compute"
        artifact["rust_engine_required_for_scope"] = False
        artifact["vectorbt_engine"] = "numba"
        artifact["rust_engine_available"] = False
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(ScreeningArtifactError, match="rust_required_scope"):
            validate_screening_artifact_or_raise(artifact)

    def test_validator_requires_screening_scope(self, tmp_path):
        artifact = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        ).to_dict()
        artifact.pop("screening_scope")
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(ScreeningArtifactError, match="screening_scope"):
            validate_screening_artifact_or_raise(artifact)

    @pytest.mark.parametrize("field_name", SCREENING_ARTIFACT_REQUIRED_FIELDS)
    def test_validator_rejects_each_missing_required_top_level_field(self, tmp_path, field_name):
        artifact = _rejected_screening_artifact(tmp_path)
        artifact.pop(field_name)
        if field_name != "screening_artifact_hash":
            artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(ScreeningArtifactError, match=f"missing required field: {field_name}"):
            validate_screening_artifact_or_raise(artifact)

    @pytest.mark.parametrize("field_name", SCREENING_CANDIDATE_REQUIRED_FIELDS)
    def test_validator_rejects_each_missing_required_candidate_field(self, tmp_path, field_name):
        artifact = _rejected_screening_artifact(tmp_path)
        artifact["rejected"][0].pop(field_name)
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(ScreeningArtifactError, match=f"missing candidate field: {field_name}"):
            validate_screening_artifact_or_raise(artifact)

    def test_rejected_rows_emit_complete_fail_closed_handoff_schema(self, tmp_path):
        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        rejected = artifact["rejected"][0]

        for field_name in SCREENING_CANDIDATE_REQUIRED_FIELDS:
            assert field_name in rejected
        assert artifact["candidate_ids"] == artifact["promoted_ids"] + artifact["rejected_ids"]
        assert rejected["candidate_id"] in artifact["candidate_reasons"]
        assert rejected["screening_status"] == "rejected"
        assert rejected["replay_eligibility_status"] == "not_eligible"
        assert rejected["rejection_reason_or_null"] == "no_ohlcv_data"
        assert rejected["wfc_status"] == "not_run"
        assert rejected["dsr_status"] == "not_run"
        assert rejected["pbo_status"] == "not_run"
        assert rejected["cscv_status"] == "not_run"

    def test_promoted_pilot_rows_emit_schema_but_remain_replay_ineligible(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                return _complete_vbt_stats()

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        def signal_computer(cand, bars, parsed, repo_root):
            entry = np.zeros(len(bars))
            exit_ = np.zeros(len(bars))
            entry[1] = 1.0
            exit_[-1] = -1.0
            return entry, exit_

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(
                min_oos_expectancy=-1.0,
                min_walk_forward_consistency=0.0,
                max_drawdown_pct=-30.0,
                min_trades=0,
                param_stability_rtol=1.0,
                max_slippage_sensitivity=1.0,
            ),
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=signal_computer,
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        promoted = artifact["promoted"][0]

        for field_name in SCREENING_CANDIDATE_REQUIRED_FIELDS:
            assert field_name in promoted
        assert artifact["candidate_ids"] == artifact["promoted_ids"] + artifact["rejected_ids"]
        assert promoted["candidate_id"] in artifact["candidate_reasons"]
        assert promoted["screening_status"] == "pass"
        assert promoted["replay_eligibility_status"] == "not_eligible"
        assert promoted["rejection_reason_or_null"].startswith(
            "vbt2_pilot_screen_only_without_real_wfc_dsr_pbo_cscv_pass_evidence"
        )
        assert promoted["wfc_status"] == "not_run"
        assert promoted["dsr_status"] == "not_run"
        assert promoted["pbo_status"] == "not_run"
        assert promoted["cscv_status"] == "not_run"
        surface = promoted["surface_stability_metrics"]
        assert surface["status"] == "not_run"
        assert surface["reason"] == "surface_stability_formula_authority_missing"
        assert surface["formula_authority_status"] == "missing"
        assert surface["failure_semantics"] == "SURFACE_STABILITY_FORMULA_MISSING"
        assert surface["literature_or_ontology_citation"] == (
            "docs/project/ROBUSTNESS_TESTING_SPEC.md:130-144"
        )
        assert surface["required_checks"] == [
            "plateau_width",
            "neighbor_stability",
            "cliff_distance_from_loss_regions",
            "parameter_perturbation_sensitivity",
            "peak_vs_plateau_comparison",
            "minimum_sample_size",
        ]
        assert "plateau_score" not in surface

    def test_vbt2_pilot_default_gate_passes_on_official_stats_only(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                return _complete_vbt_stats(total_trades=10, expectancy=0.01)

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(),
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (
                np.r_[0.0, 1.0, np.zeros(len(ohlcv) - 2)],
                np.r_[np.zeros(len(ohlcv) - 1), -1.0],
            ),
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        assert artifact["promoted_ids"]
        promoted = artifact["promoted"][0]
        vectorbt_results = promoted["vectorbt_results"]
        for field_name in (
            "turnover_mean_pct",
            "param_stability_score",
            "slippage_sensitivity",
        ):
            assert field_name not in vectorbt_results
        assert "wf_consistency" in vectorbt_results
        assert "oos_expectancy" in vectorbt_results
        assert vectorbt_results["gate_metric_non_stats_status"] == {
            "turnover_mean_pct": "not_measured_not_used_by_vbt2_pilot_gate",
            "param_stability_score": "not_measured_not_used_by_vbt2_pilot_gate",
            "slippage_sensitivity": "not_measured_not_used_by_vbt2_pilot_gate",
        }
        assert vectorbt_results["pilot_gate_evaluation"] == {
            "scope": "official_vectorbt_stats_with_walk_forward_oos",
            "used_fields": {
                "oos_expectancy": "auxiliary_numpy_walk_forward",
                "wf_consistency": "auxiliary_numpy_walk_forward",
                "max_drawdown_pct": "Max Drawdown [%]",
                "num_trades": "Total Trades",
            },
            "skipped_unmeasured_fields": [
                "turnover_mean_pct",
                "param_stability_score",
                "slippage_sensitivity",
            ],
            "failure_semantics": "screening_only_not_replay_or_robustness_eligible",
            "failures": [],
        }
        assert promoted["walk_forward_metrics"]["wf_consistency"] is not None
        assert promoted["turnover"]["status"] == "not_run"

    def test_total_return_is_optional_for_vbt2_pilot_gate(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                stats = _complete_vbt_stats(total_trades=10, expectancy=0.01)
                stats.pop("Total Return [%]")
                return stats

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(),
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (
                np.r_[0.0, 1.0, np.zeros(len(ohlcv) - 2)],
                np.r_[np.zeros(len(ohlcv) - 1), -1.0],
            ),
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        assert artifact["promoted_ids"]
        promoted = artifact["promoted"][0]
        assert promoted["vectorbt_results"]["pilot_gate_evaluation"]["failures"] == []
        assert promoted["gross_return"] == 0.0

    def test_missing_expectancy_rejects_even_when_total_trades_zero(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                stats = _complete_vbt_stats(total_trades=0)
                stats.pop("Expectancy")
                return stats

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(
                min_oos_expectancy=-1.0,
                min_walk_forward_consistency=0.0,
                min_trades=0,
                param_stability_rtol=1.0,
            ),
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (
                np.r_[0.0, 1.0, np.zeros(len(ohlcv) - 2)],
                np.r_[np.zeros(len(ohlcv) - 1), -1.0],
            ),
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        assert artifact["promoted"] == []
        rejected = artifact["rejected"][0]
        assert rejected["rejection_reason_or_null"] == "vectorbt_stats_missing_gate_fields"
        assert rejected["metric_values"]["missing_vectorbt_stats_fields"] == ["Expectancy"]

    def test_trial_candidate_ids_include_symbol_context(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                return _complete_vbt_stats()

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])
        candidates = [
            CandidateModel(
                candidate_id="same_base_candidate",
                model_id="HYP_5",
                strategy_params={"signal_threshold": 0.15},
                thesis="same model and params, MES context",
                metadata={"strategy_family": "HYP_5", "symbol": "MES"},
            ),
            CandidateModel(
                candidate_id="same_base_candidate",
                model_id="HYP_5",
                strategy_params={"signal_threshold": 0.15},
                thesis="same model and params, MNQ context",
                metadata={"strategy_family": "HYP_5", "symbol": "MNQ"},
            ),
        ]

        result = filter_candidates(
            candidates=candidates,
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(
                min_oos_expectancy=-1.0,
                min_walk_forward_consistency=0.0,
                max_drawdown_pct=-30.0,
                min_trades=0,
                param_stability_rtol=1.0,
                max_slippage_sensitivity=1.0,
            ),
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (
                np.r_[0.0, 1.0, np.zeros(len(ohlcv) - 2)],
                np.r_[np.zeros(len(ohlcv) - 1), -1.0],
            ),
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        assert len(artifact["promoted_ids"]) == 2
        assert len(set(artifact["promoted_ids"])) == 2
        assert set(artifact["candidate_reasons"]) == set(artifact["candidate_ids"])

    def test_external_robustness_evidence_is_preserved_but_pilot_stays_replay_ineligible(
        self, monkeypatch, tmp_path
    ):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                return _complete_vbt_stats()

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])
        candidate = _mock_candidate("HYP_5", 0.15)
        candidate.metadata = {
            **candidate.metadata,
            "robustness_evidence": _valid_external_robustness_evidence(),
        }

        result = filter_candidates(
            candidates=[candidate],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(
                min_oos_expectancy=-1.0,
                min_walk_forward_consistency=0.0,
                max_drawdown_pct=-30.0,
                min_trades=0,
                param_stability_rtol=1.0,
                max_slippage_sensitivity=1.0,
            ),
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (
                np.r_[0.0, 1.0, np.zeros(len(ohlcv) - 2)],
                np.r_[np.zeros(len(ohlcv) - 1), -1.0],
            ),
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        promoted = artifact["promoted"][0]
        assert promoted["replay_eligibility_status"] == "not_eligible"
        assert promoted["rejection_reason_or_null"].startswith(
            "vbt2_pilot_screen_only_without_real_wfc_dsr_pbo_cscv_pass_evidence"
        )
        assert promoted["robustness_artifact_staleness"]["status"] == "not_run"
        assert promoted["wfc_status"] == "not_run"
        assert promoted["dsr_or_not_run"]["status"] == "not_run"
        assert promoted["surface_stability_metrics"]["formula_authority_status"] == "missing"
        assert promoted["external_robustness_evidence"]["dsr_or_not_run"]["dsr_cdf"] == 0.96
        assert promoted["external_robustness_evidence_status"] == (
            "preserved_not_replay_eligible_until_hftbacktest_native_replay_milestone"
        )
        assert artifact["candidate_reasons"][promoted["candidate_id"]] == (
            "vectorbt_screen_passed_replay_not_eligible"
        )

    def test_validator_rejects_bare_plateau_score_without_formula_authority(self, monkeypatch, tmp_path):
        artifact = _promoted_screening_artifact(monkeypatch, tmp_path)
        artifact["promoted"][0]["surface_stability_metrics"] = {
            "status": "pass",
            "plateau_score": 0.81,
        }
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(
            ScreeningArtifactError,
            match="surface_stability_plateau_score_formula_missing",
        ):
            validate_screening_artifact_or_raise(artifact)

    def test_validator_rejects_defined_surface_missing_required_evidence(self, monkeypatch, tmp_path):
        artifact = _promoted_screening_artifact(monkeypatch, tmp_path)
        surface = _valid_external_robustness_evidence()["surface_stability_metrics"]
        surface.pop("neighbor_stability")
        artifact["promoted"][0]["surface_stability_metrics"] = surface
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(
            ScreeningArtifactError,
            match="surface_stability_formula_authority_missing",
        ):
            validate_screening_artifact_or_raise(artifact)

    def test_validator_rejects_replay_eligible_surface_formula_missing(self, monkeypatch, tmp_path):
        artifact = _promoted_screening_artifact(monkeypatch, tmp_path)
        promoted = artifact["promoted"][0]
        promoted["replay_eligibility_status"] = "eligible"
        promoted["rejection_reason_or_null"] = None
        promoted["robustness_artifact_staleness"] = "fresh"
        for field_name in ("wfc_status", "dsr_status", "pbo_status", "cscv_status"):
            promoted[field_name] = "pass"
        for field_name in (
            "bootstrap_ci_or_not_run",
            "dsr_or_not_run",
            "pbo_or_not_run",
            "cscv_count_or_not_run",
        ):
            promoted[field_name] = {"status": "pass"}
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(
            ScreeningArtifactError,
            match="surface_stability_formula_authority_missing",
        ):
            validate_screening_artifact_or_raise(artifact)

    def test_validator_rejects_replay_eligible_failed_robustness_map(self, monkeypatch, tmp_path):
        artifact = _promoted_screening_artifact(monkeypatch, tmp_path)
        promoted = artifact["promoted"][0]
        promoted.update(_valid_external_robustness_evidence())
        promoted["replay_eligibility_status"] = "eligible"
        promoted["rejection_reason_or_null"] = None
        promoted["dsr_or_not_run"] = {"status": "fail", "dsr_cdf": 0.41}
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(
            ScreeningArtifactError,
            match="dsr_or_not_run_not_pass",
        ):
            validate_screening_artifact_or_raise(artifact)

    def test_validator_rejects_replay_eligible_empty_pass_robustness_map(self, monkeypatch, tmp_path):
        artifact = _promoted_screening_artifact(monkeypatch, tmp_path)
        promoted = artifact["promoted"][0]
        promoted.update(_valid_external_robustness_evidence())
        promoted["replay_eligibility_status"] = "eligible"
        promoted["rejection_reason_or_null"] = None
        promoted["dsr_or_not_run"] = {"status": "pass"}
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(
            ScreeningArtifactError,
            match="dsr_or_not_run_missing:dsr_pass",
        ):
            validate_screening_artifact_or_raise(artifact)

    def test_validator_rejects_replay_eligible_non_positive_bootstrap_lower(self, monkeypatch, tmp_path):
        artifact = _promoted_screening_artifact(monkeypatch, tmp_path)
        promoted = artifact["promoted"][0]
        promoted.update(_valid_external_robustness_evidence())
        promoted["replay_eligibility_status"] = "eligible"
        promoted["rejection_reason_or_null"] = None
        promoted["bootstrap_ci_or_not_run"] = {"status": "pass", "lower": 0.0, "upper": 0.05}
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(
            ScreeningArtifactError,
            match="bootstrap_ci_or_not_run_lower_bound_not_positive",
        ):
            validate_screening_artifact_or_raise(artifact)

    def test_persist_promotions_does_not_write_replay_ineligible_vectorbt_pilot(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                return _complete_vbt_stats()

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(
                min_oos_expectancy=-1.0,
                min_walk_forward_consistency=0.0,
                max_drawdown_pct=-30.0,
                min_trades=0,
                param_stability_rtol=1.0,
                max_slippage_sensitivity=1.0,
            ),
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (
                np.r_[0.0, 1.0, np.zeros(len(ohlcv) - 2)],
                np.r_[np.zeros(len(ohlcv) - 1), -1.0],
            ),
            persist_promotions=True,
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        promoted = artifact["promoted"][0]

        assert promoted["replay_eligibility_status"] == "not_eligible"
        assert promoted["pass_reason"] == "vectorbt_screen_passed_replay_not_eligible"
        assert promoted["pass_reason"] == artifact["candidate_reasons"][promoted["candidate_id"]]
        assert not (tmp_path / "research_cards" / "promotion").exists()

    def test_validator_rejects_eligible_candidate_with_not_run_robustness(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                return _complete_vbt_stats()

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(
                min_oos_expectancy=-1.0,
                min_walk_forward_consistency=0.0,
                min_trades=0,
                param_stability_rtol=1.0,
            ),
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (
                np.r_[0.0, 1.0, np.zeros(len(ohlcv) - 2)],
                np.r_[np.zeros(len(ohlcv) - 1), -1.0],
            ),
        )

        artifact = result.to_dict()
        artifact["promoted"][0]["replay_eligibility_status"] = "eligible"
        artifact["promoted"][0]["rejection_reason_or_null"] = None
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(ScreeningArtifactError, match="wfc_status_not_pass"):
            validate_screening_artifact_or_raise(artifact)

    def test_validator_rejects_candidate_parameter_hash_mismatch(self, tmp_path):
        artifact = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        ).to_dict()
        artifact["rejected"][0]["parameter_values"] = {"signal_threshold": 999.0}
        artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)

        with pytest.raises(ScreeningArtifactError, match="parameter_values_hash_mismatch"):
            validate_screening_artifact_or_raise(artifact)

    def test_promotion_gate_failed_row_preserves_tested_parameters(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        from backtest_pipeline.src import vectorbt_adapter

        class FakePortfolio:
            def stats(self):
                return _complete_vbt_stats(total_return_pct=-0.5, expectancy=-0.01)

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=lambda *_, **__: FakePortfolio())
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)

        close = 100.0 + np.arange(80, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])
        params = {
            "signal_threshold": [0.42],
            "holding_period_bars": [7],
            "stop_loss_pct": [None],
            "take_profit_pct": [None],
        }

        result = filter_candidates(
            candidates=[_mock_candidate("HYP_5", 0.15)],
            parsed=None,
            event_id="SYNTHETIC",
            repo_root=tmp_path,
            gates=PromotionGate(min_oos_expectancy=999.0, min_walk_forward_consistency=0.0, min_trades=0),
            param_grid=params,
            data_loader=lambda *_: ohlcv,
            signal_computer=lambda *_: (
                np.r_[0.0, 1.0, np.zeros(len(ohlcv) - 2)],
                np.r_[np.zeros(len(ohlcv) - 1), -1.0],
            ),
        )

        artifact = result.to_dict()
        validate_screening_artifact_or_raise(artifact)
        rejected = artifact["rejected"][0]

        assert rejected["rejection_reason_or_null"] == "promotion_gate_failed"
        assert rejected["parameter_values"] == {
            "signal_threshold": 0.42,
            "holding_period_bars": 7,
            "stop_loss_pct": None,
            "take_profit_pct": None,
        }
        assert artifact["candidate_ids"] == artifact["rejected_ids"]

    def test_missing_ohlcv_escape_hatch_cannot_promote(self, monkeypatch, tmp_path):
        cands = [_mock_candidate("HYP_5", 0.15)]
        monkeypatch.setenv("HFT3_ALLOW_UNFILTERED", "1")

        result = filter_candidates(
            candidates=cands,
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
        )

        assert not result.promoted
        assert result.rejected[0].reject_reason == "no_ohlcv_data"
        assert result.rejected[0].metric_values["operator_escape_ignored"] is True
        assert not (tmp_path / "research_cards" / "promotion").exists()


class TestFilterCandidatesDefaultDataLoaderSymbol:
    def test_default_loader_receives_screen_symbol_when_fs_v1_unavailable(
        self, monkeypatch, tmp_path,
    ):
        from backtest_pipeline.src import vectorbt_adapter
        from backtest_pipeline.src.vectorbt_adapter import FilterResult

        captured: dict[str, object] = {}
        close = 100.0 + np.arange(40, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        def spy_default(event_id, repo_root, symbol=None):
            captured["symbol"] = symbol
            captured["event_id"] = event_id
            return ohlcv

        monkeypatch.setattr(vectorbt_adapter, "_default_data_loader", spy_default)
        monkeypatch.setattr(
            vectorbt_adapter,
            "_run_vectorbt_simulation",
            lambda ohlcv_arg, *args, **kwargs: FilterResult(
                backend="vectorbt",
                run_id="symbol_scope_test",
                screening_scope="pilot",
            ),
        )

        filter_candidates(
            candidates=[_mock_candidate()],
            parsed=None,
            event_id="EVT_SYMBOL_SCOPE",
            repo_root=tmp_path,
            screening_scope="pilot",
            prefer_fs_v1_path=False,
        )

        assert captured.get("symbol") == "MES"
        assert captured.get("event_id") == "EVT_SYMBOL_SCOPE"

    def test_custom_two_arg_loader_not_passed_symbol(self, monkeypatch, tmp_path):
        from backtest_pipeline.src import vectorbt_adapter
        from backtest_pipeline.src.vectorbt_adapter import FilterResult

        calls: list[tuple[str, Path]] = []
        close = 100.0 + np.arange(40, dtype=float) * 0.1
        ohlcv = np.column_stack([close, close, close, close, np.ones_like(close)])

        def custom_loader(event_id, repo_root):
            calls.append((event_id, repo_root))
            return ohlcv

        monkeypatch.setattr(
            vectorbt_adapter,
            "_run_vectorbt_simulation",
            lambda ohlcv_arg, *args, **kwargs: FilterResult(
                backend="vectorbt",
                run_id="custom_loader_test",
                screening_scope="pilot",
            ),
        )

        filter_candidates(
            candidates=[_mock_candidate()],
            parsed=None,
            event_id="EVT_CUSTOM_LOADER",
            repo_root=tmp_path,
            screening_scope="pilot",
            data_loader=custom_loader,
            prefer_fs_v1_path=False,
        )

        assert calls == [("EVT_CUSTOM_LOADER", tmp_path)]
