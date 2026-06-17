"""Tests for the VBT-4 robustness integration bridge.

Verifies that ``backtest_pipeline.src.robustness_bridge.compute_robustness_evidence``
correctly wires the existing DSR/PBO/CSCV/bootstrap/WFC producers into the
VectorBT screening artifact fields, and that promoted candidates can achieve
``replay_eligibility_status=eligible`` when all gates pass.
"""
from __future__ import annotations

import copy
import sys
from types import SimpleNamespace
from typing import Any, Dict, List

import numpy as np
import pytest

from backtest_pipeline.src.robustness_bridge import compute_robustness_evidence
from backtest_pipeline.src.promotion_gate import PromotedCandidate, PromotionGate
from backtest_pipeline.src.vectorbt_adapter import (
    _normalise_promoted_screening_row,
    validate_screening_artifact,
)
from research_pipeline.types import CandidateModel


# ---------------------------------------------------------------------------
# Helpers: construct robustness input data
# ---------------------------------------------------------------------------


def _passing_wfc_rows(
    n_params: int = 120,
    n_folds: int = 4,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    """Build WFC fold rows that produce a PASS gate result."""
    rng = np.random.default_rng(seed)
    rows: List[Dict[str, Any]] = []
    for p in range(n_params):
        base_is = float(rng.uniform(0.5, 2.0))
        for f in range(n_folds):
            is_val = base_is + float(rng.normal(0, 0.1))
            oos_val = 0.6 * is_val + float(rng.normal(0, 0.05))
            rows.append({
                "parameter_hash": f"ph_{p}",
                "fold_id": f"fold_{f}",
                "is_metrics": {
                    "sharpe": float(is_val),
                    "net_return": float(is_val * 100),
                },
                "oos_metrics": {
                    "sharpe": float(oos_val),
                    "net_return": float(oos_val * 100),
                    "net_return_adjusted": float(oos_val * 100),
                    "profit_factor": 1.5,
                    "max_drawdown": -10.0,
                    "max_drawdown_adj_return": -10.0,
                    "trade_count": 50,
                },
            })
    return rows


def _passing_wfc_cfg() -> Dict[str, Any]:
    """WFC gate config that the passing rows satisfy."""
    return {
        "enabled": True,
        "min_parameter_combinations": 100,
        "min_walk_forward_folds": 3,
        "primary_metric": "sharpe",
        "pearson_min": 0.20,
        "spearman_min": 0.20,
        "correlation_p_value_max": 0.10,
        "min_positive_fold_ratio": 0.60,
        "require_oos_net_profit_positive": True,
        "require_oos_risk_adjusted_positive": True,
        "max_oos_drawdown_limit": -500.0,
        "permutation_samples": 100,
        "bootstrap_samples": 100,
        "outlier_winsor_pct": 0.01,
    }


def _passing_expectancies(seed: int = 42, n: int = 200) -> List[float]:
    """Per-event expectancies with high SNR → DSR passes."""
    rng = np.random.default_rng(seed)
    return rng.normal(0.05, 0.02, n).tolist()


def _failing_dsr_expectancies(seed: int = 42, n: int = 50) -> List[float]:
    """Per-event expectancies with very low SNR → DSR fails."""
    rng = np.random.default_rng(seed)
    return rng.normal(0.001, 0.05, n).tolist()


def _passing_cscv_matrix(seed: int = 42) -> np.ndarray:
    """CSCV matrix where the best config generalises → PBO near 0."""
    rng = np.random.default_rng(seed)
    n_blocks = 8
    n_configs = 5
    matrix = np.zeros((n_blocks, n_configs))
    for i in range(n_blocks):
        for j in range(n_configs):
            matrix[i, j] = 0.05 - j * 0.01 + float(rng.normal(0, 0.002))
    return matrix


def _failing_pbo_matrix(seed: int = 134) -> np.ndarray:
    """Random CSCV matrix → PBO >= 0.5 (fail)."""
    rng = np.random.default_rng(seed)
    return rng.normal(0, 0.01, (8, 5))


def _full_passing_input(seed: int = 42) -> Dict[str, Any]:
    """Complete robustness input where all four gates pass."""
    return {
        "per_event_expectancies": _passing_expectancies(seed=seed),
        "n_trials": 10,
        "cscv_matrix": _passing_cscv_matrix(seed=seed),
        "wfc_rows": _passing_wfc_rows(seed=seed),
        "wfc_cfg": _passing_wfc_cfg(),
    }


# ---------------------------------------------------------------------------
# Required output keys
# ---------------------------------------------------------------------------

REQUIRED_OUTPUT_KEYS = {
    "wfc_status",
    "dsr_status",
    "pbo_status",
    "cscv_status",
    "robustness_artifact_staleness",
    "bootstrap_ci_or_not_run",
    "dsr_or_not_run",
    "pbo_or_not_run",
    "cscv_count_or_not_run",
    "fee_stress_or_not_run",
    "slippage_stress_or_not_run",
    "latency_stress_or_not_run",
    "holm_bh_or_not_run",
    "null_battery_or_not_run",
    "planted_alpha_or_not_run",
    "adversarial_or_not_run",
    "parameter_perturbation_or_not_run",
    "walk_forward_metrics",
    "wfc_metrics",
}

REQUIRED_WFC_METRICS_KEYS = {
    "metric_in_sample",
    "metric_out_of_sample",
    "pearson",
    "spearman",
    "scatter_data",
    "quadrant_counts",
    "high_is_high_oos_region",
    "rejection_reason",
}

REQUIRED_WF_METRICS_KEYS = {
    "fold_matrix",
    "fold_train_test_dates",
    "fold_metrics",
    "walk_forward_efficiency",
    "fold_dispersion",
    "is_oos_gap",
    "oos_decay",
}


# ---------------------------------------------------------------------------
# Test: all gates pass
# ---------------------------------------------------------------------------


class TestAllGatesPass:
    def test_all_status_fields_pass(self):
        result = compute_robustness_evidence(_full_passing_input(), candidate_id="c1")
        assert result["wfc_status"] == "pass"
        assert result["dsr_status"] == "pass"
        assert result["pbo_status"] == "pass"
        assert result["cscv_status"] == "pass"
        assert result["robustness_artifact_staleness"] == "fresh"

    def test_staleness_is_fresh(self):
        result = compute_robustness_evidence(_full_passing_input(), candidate_id="c1")
        assert result["robustness_artifact_staleness"] == "fresh"

    def test_dsr_or_not_run_has_pass_status(self):
        result = compute_robustness_evidence(_full_passing_input(), candidate_id="c1")
        assert result["dsr_or_not_run"]["status"] == "pass"
        assert result["dsr_or_not_run"]["dsr_pass"] is True
        assert result["dsr_or_not_run"]["dsr_cdf"] >= 0.95

    def test_pbo_or_not_run_has_pass_status(self):
        result = compute_robustness_evidence(_full_passing_input(), candidate_id="c1")
        assert result["pbo_or_not_run"]["status"] == "pass"
        assert result["pbo_or_not_run"]["pbo_pass"] is True
        assert result["pbo_or_not_run"]["pbo"] < 0.5

    def test_bootstrap_ci_has_pass_status(self):
        result = compute_robustness_evidence(_full_passing_input(), candidate_id="c1")
        assert result["bootstrap_ci_or_not_run"]["status"] == "pass"
        assert result["bootstrap_ci_or_not_run"]["lower"] > 0

    def test_cscv_count_has_pass_status(self):
        result = compute_robustness_evidence(_full_passing_input(), candidate_id="c1")
        assert result["cscv_count_or_not_run"]["status"] == "pass"
        assert result["cscv_count_or_not_run"]["n_partitions"] > 0
        assert result["cscv_count_or_not_run"]["n_configs"] >= 2


# ---------------------------------------------------------------------------
# Test: DSR fails
# ---------------------------------------------------------------------------


class TestDSRFails:
    def test_dsr_fail_status(self):
        inp = _full_passing_input()
        inp["per_event_expectancies"] = _failing_dsr_expectancies()
        result = compute_robustness_evidence(inp, candidate_id="c2")
        assert result["dsr_status"] == "fail"
        assert result["robustness_artifact_staleness"] == "stale"

    def test_dsr_fail_does_not_affect_others(self):
        inp = _full_passing_input()
        inp["per_event_expectancies"] = _failing_dsr_expectancies()
        result = compute_robustness_evidence(inp, candidate_id="c2")
        # WFC and PBO should still pass independently.
        assert result["wfc_status"] == "pass"
        assert result["pbo_status"] == "pass"
        assert result["cscv_status"] == "pass"


# ---------------------------------------------------------------------------
# Test: PBO fails
# ---------------------------------------------------------------------------


class TestPBOFails:
    def test_pbo_fail_status(self):
        inp = _full_passing_input()
        inp["cscv_matrix"] = _failing_pbo_matrix()
        result = compute_robustness_evidence(inp, candidate_id="c3")
        assert result["pbo_status"] == "fail"
        # cscv_status is now derived independently from whether the CSCV
        # partition/config analysis ran (n_partitions/n_configs > 0), not
        # aliased to pbo_status.  The failing-PBO matrix still produces valid
        # CSCV structure, so cscv_status is "pass" even though pbo_status fails.
        assert result["cscv_status"] == "pass"
        assert result["robustness_artifact_staleness"] == "stale"

    def test_pbo_fail_does_not_affect_dsr(self):
        inp = _full_passing_input()
        inp["cscv_matrix"] = _failing_pbo_matrix()
        result = compute_robustness_evidence(inp, candidate_id="c3")
        assert result["dsr_status"] == "pass"
        assert result["wfc_status"] == "pass"


# ---------------------------------------------------------------------------
# Test: WFC returns CONDITIONAL or ERROR
# ---------------------------------------------------------------------------


class TestWFCConditional:
    def test_wfc_conditional_is_fail(self):
        """WFC CONDITIONAL should count as fail, not pass."""
        inp = _full_passing_input()
        # Use a config with very high thresholds so the gate returns
        # CONDITIONAL (weak but non-random correlation).
        cfg = _passing_wfc_cfg()
        cfg["pearson_min"] = 0.99  # impossibly high → core pass fails
        cfg["spearman_min"] = 0.99
        inp["wfc_cfg"] = cfg
        result = compute_robustness_evidence(inp, candidate_id="c4")
        # CONDITIONAL or FAIL — either way it's not PASS.
        assert result["wfc_status"] != "pass"
        assert result["wfc_status"] == "fail"
        assert result["robustness_artifact_staleness"] == "stale"


class TestWFCError:
    def test_wfc_error_is_fail(self):
        """WFC ERROR should count as fail."""
        inp = _full_passing_input()
        # Empty rows → WFC gate returns ERROR.
        inp["wfc_rows"] = []
        result = compute_robustness_evidence(inp, candidate_id="c5")
        assert result["wfc_status"] == "not_run"
        assert result["robustness_artifact_staleness"] == "stale"

    def test_wfc_disabled_is_fail(self):
        """WFC gate disabled in config → ERROR → fail."""
        inp = _full_passing_input()
        cfg = _passing_wfc_cfg()
        cfg["enabled"] = False
        inp["wfc_cfg"] = cfg
        result = compute_robustness_evidence(inp, candidate_id="c5b")
        assert result["wfc_status"] == "fail"


# ---------------------------------------------------------------------------
# Test: missing input
# ---------------------------------------------------------------------------


class TestMissingInput:
    def test_none_input_all_not_run(self):
        result = compute_robustness_evidence(None, candidate_id="c6")  # type: ignore[arg-type]
        assert result["wfc_status"] == "not_run"
        assert result["dsr_status"] == "not_run"
        assert result["pbo_status"] == "not_run"
        assert result["cscv_status"] == "not_run"
        assert result["robustness_artifact_staleness"] == "stale"

    def test_empty_input_all_not_run(self):
        result = compute_robustness_evidence({}, candidate_id="c6b")
        assert result["wfc_status"] == "not_run"
        assert result["dsr_status"] == "not_run"
        assert result["pbo_status"] == "not_run"
        assert result["cscv_status"] == "not_run"
        assert result["robustness_artifact_staleness"] == "stale"
        assert result["bootstrap_ci_or_not_run"]["status"] == "not_run"
        assert result["dsr_or_not_run"]["status"] == "not_run"
        assert result["pbo_or_not_run"]["status"] == "not_run"
        assert result["cscv_count_or_not_run"]["status"] == "not_run"

    def test_all_empty_values_all_not_run(self):
        """Input with all empty/None values → all not_run."""
        inp = {
            "per_event_expectancies": [],
            "n_trials": 0,
            "cscv_matrix": None,
            "wfc_rows": [],
        }
        result = compute_robustness_evidence(inp, candidate_id="c6c")
        assert result["wfc_status"] == "not_run"
        assert result["dsr_status"] == "not_run"
        assert result["pbo_status"] == "not_run"


# ---------------------------------------------------------------------------
# Test: partial input
# ---------------------------------------------------------------------------


class TestPartialInput:
    def test_dsr_only_pbo_not_run(self):
        """Only DSR data (no PBO matrix) → DSR runs, PBO stays not_run."""
        inp = {
            "per_event_expectancies": _passing_expectancies(),
            "n_trials": 10,
            # No cscv_matrix, no wfc_rows
        }
        result = compute_robustness_evidence(inp, candidate_id="c7")
        assert result["dsr_status"] == "pass"
        assert result["pbo_status"] == "not_run"
        assert result["cscv_status"] == "not_run"
        assert result["wfc_status"] == "not_run"
        assert result["robustness_artifact_staleness"] == "stale"

    def test_dsr_only_bootstrap_runs(self):
        """DSR data also triggers bootstrap_ci."""
        inp = {
            "per_event_expectancies": _passing_expectancies(),
            "n_trials": 10,
        }
        result = compute_robustness_evidence(inp, candidate_id="c7b")
        assert result["bootstrap_ci_or_not_run"]["status"] == "pass"
        assert result["dsr_or_not_run"]["status"] == "pass"

    def test_pbo_only_dsr_not_run(self):
        """Only CSCV matrix → PBO runs, DSR stays not_run."""
        inp = {
            "cscv_matrix": _passing_cscv_matrix(),
        }
        result = compute_robustness_evidence(inp, candidate_id="c7c")
        assert result["pbo_status"] == "pass"
        assert result["dsr_status"] == "not_run"
        assert result["wfc_status"] == "not_run"


# ---------------------------------------------------------------------------
# Test: determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_output(self):
        inp = _full_passing_input()
        r1 = compute_robustness_evidence(inp, candidate_id="c8")
        r2 = compute_robustness_evidence(copy.deepcopy(inp), candidate_id="c8")
        assert r1 == r2

    def test_different_candidate_id_same_results(self):
        inp = _full_passing_input()
        r1 = compute_robustness_evidence(inp, candidate_id="c8a")
        r2 = compute_robustness_evidence(inp, candidate_id="c8b")
        # The candidate_id field differs, but all robustness fields match.
        r1_copy = dict(r1)
        r2_copy = dict(r2)
        r1_copy.pop("candidate_id", None)
        r2_copy.pop("candidate_id", None)
        assert r1_copy == r2_copy


# ---------------------------------------------------------------------------
# Test: producer error handling
# ---------------------------------------------------------------------------


class TestProducerErrorHandling:
    def test_malformed_expectancies_fail_closed(self):
        """Malformed per_event_expectancies → DSR fail-closed with reason."""
        inp = {
            "per_event_expectancies": "not_a_list",  # type: ignore[dict-item]
            "n_trials": 10,
        }
        result = compute_robustness_evidence(inp, candidate_id="c9")
        # Either not_run (if the type check fails before the producer) or
        # a producer error sentinel.  The key requirement is fail-closed.
        assert result["dsr_status"] in {"not_run", "fail"}
        assert result["robustness_artifact_staleness"] == "stale"

    def test_malformed_matrix_fail_closed(self):
        """Malformed cscv_matrix → PBO fail-closed."""
        inp = {
            "cscv_matrix": "not_an_array",  # type: ignore[dict-item]
        }
        result = compute_robustness_evidence(inp, candidate_id="c9b")
        assert result["pbo_status"] in {"not_run", "fail"}
        assert result["robustness_artifact_staleness"] == "stale"

    def test_producer_error_has_reason(self):
        """When a producer fails, the result includes a reason string."""
        inp = {
            "per_event_expectancies": [None, None, None],  # type: ignore[list-item]
            "n_trials": 10,
        }
        result = compute_robustness_evidence(inp, candidate_id="c9c")
        dsr = result["dsr_or_not_run"]
        # The DSR producer may return a dict with reason="insufficient_events"
        # or the bridge may catch an exception and return a producer_error.
        # Either way, it should be fail-closed.
        assert dsr["status"] in {"not_run", "fail"}


# ---------------------------------------------------------------------------
# Test: output dict has all required keys
# ---------------------------------------------------------------------------


class TestOutputShape:
    def test_output_has_all_required_keys(self):
        result = compute_robustness_evidence(_full_passing_input(), candidate_id="c10")
        assert REQUIRED_OUTPUT_KEYS.issubset(result.keys())

    def test_wfc_metrics_has_required_keys(self):
        result = compute_robustness_evidence(_full_passing_input(), candidate_id="c10")
        wfc_metrics = result["wfc_metrics"]
        assert REQUIRED_WFC_METRICS_KEYS.issubset(wfc_metrics.keys())

    def test_walk_forward_metrics_has_required_keys(self):
        result = compute_robustness_evidence(_full_passing_input(), candidate_id="c10")
        wf_metrics = result["walk_forward_metrics"]
        assert REQUIRED_WF_METRICS_KEYS.issubset(wf_metrics.keys())

    def test_not_run_output_has_all_required_keys(self):
        result = compute_robustness_evidence({}, candidate_id="c10b")
        assert REQUIRED_OUTPUT_KEYS.issubset(result.keys())

    def test_not_run_wfc_metrics_has_required_keys(self):
        result = compute_robustness_evidence({}, candidate_id="c10b")
        wfc_metrics = result["wfc_metrics"]
        assert REQUIRED_WFC_METRICS_KEYS.issubset(wfc_metrics.keys())

    def test_not_run_walk_forward_metrics_has_required_keys(self):
        result = compute_robustness_evidence({}, candidate_id="c10b")
        wf_metrics = result["walk_forward_metrics"]
        assert REQUIRED_WF_METRICS_KEYS.issubset(wf_metrics.keys())


# ---------------------------------------------------------------------------
# Integration test: calling through _normalise_promoted_screening_row
# ---------------------------------------------------------------------------


def _surface_stability_passing_grid() -> dict:
    """A flat parameter surface that passes the surface-stability check."""
    from backtest_pipeline.src.surface_stability import compute_surface_stability
    grid = {
        (r, c): {"net_return": 0.10, "trade_count": 50}
        for r in range(3)
        for c in range(3)
    }
    result = compute_surface_stability(grid)
    assert result["status"] == "pass", f"surface stability should pass: {result}"
    return grid


def _build_promoted_candidate_with_robustness(
    robustness_input: Dict[str, Any],
    surface_grid: dict | None = None,
) -> PromotedCandidate:
    """Build a PromotedCandidate that carries robustness_input in its metrics."""
    vbt_results: Dict[str, Any] = {
        "oos_expectancy": 1.5,
        "num_trades": 100,
        "expectancy": 0.05,
        "profit_factor": 1.4,
        "sharpe": 0.8,
        "sortino": 1.1,
        "max_drawdown_pct": -0.2,
        "turnover_mean_pct": 50.0,
        "wf_consistency": 0.8,
    }
    vbt_results["robustness_input"] = robustness_input
    if surface_grid is not None:
        vbt_results["parameter_surface"] = surface_grid
    return PromotedCandidate(
        candidate_id="test_robust_123",
        hypothesis_id="HYP_5",
        strategy_family="SpreadBlowout",
        asset_class="CME_FUTURES",
        symbol="MES",
        timeframe="1m",
        param_values={"signal_threshold": 0.15},
        vectorbt_run_id="vbt_test",
        vectorbt_results=vbt_results,
        pass_reason="all_gates_passed",
        in_sample_results={"expectancy": 2.0},
        out_of_sample_results={"expectancy": 1.5},
    )


def _build_filter_result() -> Any:
    """Build a minimal FilterResult for the promoted row normaliser."""
    from backtest_pipeline.src.vectorbt_adapter import FilterResult
    return FilterResult(
        promoted=[],
        rejected=[],
        research_clock="test_clock",
        screening_scope="pilot",
    )


class TestIntegrationNormalisePromotedRow:
    def test_eligible_candidate_with_full_passing_robustness(self, monkeypatch):
        """A candidate with full passing robustness_input → eligible."""
        # We need the surface_stability to pass too. Inject a flat grid.
        grid = _surface_stability_passing_grid()
        candidate = _build_promoted_candidate_with_robustness(
            _full_passing_input(),
            surface_grid=grid,
        )
        fr = _build_filter_result()
        row = _normalise_promoted_screening_row(candidate, fr)

        assert row["wfc_status"] == "pass"
        assert row["dsr_status"] == "pass"
        assert row["pbo_status"] == "pass"
        assert row["cscv_status"] == "pass"
        assert row["robustness_artifact_staleness"] == "fresh"
        assert row["replay_eligibility_status"] == "eligible"
        assert row["rejection_reason_or_null"] is None

    def test_not_eligible_without_robustness_input(self):
        """A candidate without robustness_input → not_eligible."""
        candidate = _build_promoted_candidate_with_robustness({})
        # Remove the robustness_input so the bridge is not called.
        candidate.vectorbt_results = {
            "oos_expectancy": 1.5,
            "num_trades": 100,
        }
        fr = _build_filter_result()
        row = _normalise_promoted_screening_row(candidate, fr)

        assert row["replay_eligibility_status"] == "not_eligible"
        assert row["wfc_status"] == "not_run"
        assert row["dsr_status"] == "not_run"

    def test_not_eligible_when_dsr_fails(self):
        """DSR failure → not_eligible even with passing surface stability."""
        grid = _surface_stability_passing_grid()
        inp = _full_passing_input()
        inp["per_event_expectancies"] = _failing_dsr_expectancies()
        candidate = _build_promoted_candidate_with_robustness(
            inp,
            surface_grid=grid,
        )
        fr = _build_filter_result()
        row = _normalise_promoted_screening_row(candidate, fr)

        assert row["dsr_status"] == "fail"
        assert row["replay_eligibility_status"] == "not_eligible"
        assert row["robustness_artifact_staleness"] == "stale"