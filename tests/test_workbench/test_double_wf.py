"""Phase 10 tests for the double-WF correlator.

Covers:
- test_double_wf_agreement: two WFs with correlated results pass
- test_double_wf_disagreement: two WFs with uncorrelated results fail
- test_double_wf_matrix_join: join keys align correctly
- test_double_wf_gate_result: emits a GateResult with correct category and severity
- test_double_wf_artifact_written: writes walk_forward_correlation.json (Phase 12)
- test_double_wf_insufficient_shared_params: <3 shared params fails
- test_double_wf_zero_variance: zero variance in WF1 or WF2 fails
- test_double_wf_unknown_method: unknown correlation method fails
- test_double_wf_round_trip: to_dict / from_dict round-trip
- regression tests: non-independent matrices, unsupported join keys,
  malformed/non-finite metrics, invalid thresholds, and malformed gate results
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.src.robustness.wfc.double_wf import (
    DoubleWfResult,
    evaluate_double_wf,
    to_gate_result,
)
from hft3.validation.gate_result import GateCategory, Severity


def _row(parameter_hash: str, sharpe: float, fold_id: str = "D1") -> dict:
    """Helper to build a matrix row with parameter_hash and oos_metrics."""
    return {
        "parameter_hash": parameter_hash,
        "fold_id": fold_id,
        "oos_metrics": {"sharpe": sharpe},
    }


# ---------- agreement / disagreement ----------


def test_double_wf_agreement() -> None:
    """Two WFs with correlated OOS results (parameters that do well in
    WF1 also do well in WF2) should produce pass_fail=True."""
    wf1 = [
        _row("p1", 0.8), _row("p2", 0.6), _row("p3", 0.4),
        _row("p4", 0.2), _row("p5", 0.1),
    ]
    wf2 = [
        _row("p1", 0.7), _row("p2", 0.5), _row("p3", 0.35),
        _row("p4", 0.15), _row("p5", 0.05),
    ]
    result = evaluate_double_wf(
        wf1, wf2, ["parameter_hash"],
        method="spearman", min_score=0.20,
    )
    assert result.pass_fail is True
    assert result.correlation_score > 0.8
    assert len(result.eligible_parameter_regions) >= 2
    assert len(result.rejected_parameter_regions) >= 2


def test_double_wf_disagreement() -> None:
    """Two WFs with uncorrelated/inverted OOS results should produce
    pass_fail=False."""
    wf1 = [
        _row("p1", 0.9), _row("p2", 0.1), _row("p3", 0.8),
        _row("p4", 0.2), _row("p5", 0.7),
    ]
    wf2 = [
        _row("p1", 0.1), _row("p2", 0.9), _row("p3", 0.2),
        _row("p4", 0.8), _row("p5", 0.3),
    ]
    result = evaluate_double_wf(
        wf1, wf2, ["parameter_hash"],
        method="spearman", min_score=0.20,
    )
    assert result.pass_fail is False
    assert result.correlation_score < 0.0
    assert len(result.rejection_reasons) > 0
    assert any("Double-WF" in r for r in result.rejection_reasons)


# ---------- matrix join ----------


def test_double_wf_matrix_join() -> None:
    """Only parameters present in both matrices should be joined;
    unmatched parameters are excluded."""
    wf1 = [_row("p1", 0.5), _row("p2", 0.6), _row("p3", 0.7), _row("p4", 0.8)]
    wf2 = [_row("p2", 0.4), _row("p3", 0.5), _row("p4", 0.6), _row("p5", 0.8)]
    result = evaluate_double_wf(
        wf1, wf2, ["parameter_hash"],
        method="pearson", min_score=0.20,
    )
    assert result.stability_summary["n_shared"] == 3  # p2, p3, p4


def test_double_wf_rejects_same_matrix_object_path_or_content() -> None:
    wf1 = [
        _row("p1", 0.8), _row("p2", 0.6), _row("p3", 0.4),
        _row("p4", 0.2), _row("p5", 0.1),
    ]
    wf2 = [
        _row("p1", 0.7), _row("p2", 0.5), _row("p3", 0.35),
        _row("p4", 0.15), _row("p5", 0.05),
    ]

    same_object = evaluate_double_wf(wf1, wf1, ["parameter_hash"])
    same_path = evaluate_double_wf(wf1, wf2, ["parameter_hash"], wf1_path="wf.json", wf2_path="wf.json")
    same_content = evaluate_double_wf(wf1, list(wf1), ["parameter_hash"])
    same_content_shuffled = evaluate_double_wf(wf1, list(reversed(wf1)), ["parameter_hash"])

    assert same_object.pass_fail is False
    assert same_path.pass_fail is False
    assert same_content.pass_fail is False
    assert same_content_shuffled.pass_fail is False
    assert any("independent" in r for r in same_object.rejection_reasons)
    assert any("independent" in r for r in same_path.rejection_reasons)
    assert any("identical" in r for r in same_content.rejection_reasons)
    assert any("identical" in r for r in same_content_shuffled.rejection_reasons)


def test_double_wf_rejects_unsupported_join_keys() -> None:
    wf1 = [_row("p1", 0.5), _row("p2", 0.6), _row("p3", 0.7)]
    wf2 = [_row("p1", 0.4), _row("p2", 0.5), _row("p3", 0.6)]

    result = evaluate_double_wf(wf1, wf2, ["parameter_hash", "symbol"])

    assert result.pass_fail is False
    assert any("join_keys" in r for r in result.rejection_reasons)


def test_double_wf_rejects_nonfinite_matrix_values() -> None:
    wf1 = [_row("p1", 0.5), _row("p2", float("nan")), _row("p3", 0.7)]
    wf2 = [_row("p1", 0.4), _row("p2", 0.5), _row("p3", 0.6)]

    result = evaluate_double_wf(wf1, wf2, ["parameter_hash"])

    assert result.pass_fail is False
    assert any("Non-finite" in r for r in result.rejection_reasons)


def test_double_wf_rejects_malformed_oos_metrics_container() -> None:
    wf1 = [_row("p1", 0.5), _row("p2", 0.6), _row("p3", 0.7), _row("p4", 0.8)]
    wf1[1]["oos_metrics"] = []
    wf2 = [_row("p1", 0.4), _row("p2", 0.5), _row("p3", 0.6), _row("p4", 0.7)]

    result = evaluate_double_wf(wf1, wf2, ["parameter_hash"])

    assert result.pass_fail is False
    assert any("Malformed oos_metrics" in r for r in result.rejection_reasons)


def test_double_wf_rejects_invalid_min_score_and_gate_still_blocks() -> None:
    wf1 = [
        _row("p1", 0.9), _row("p2", 0.1), _row("p3", 0.8),
        _row("p4", 0.2), _row("p5", 0.7),
    ]
    wf2 = [
        _row("p1", 0.1), _row("p2", 0.9), _row("p3", 0.2),
        _row("p4", 0.8), _row("p5", 0.3),
    ]

    result = evaluate_double_wf(wf1, wf2, ["parameter_hash"], min_score=0.0)
    gate = to_gate_result(result)

    assert result.pass_fail is False
    assert any("minimum_required_score" in r for r in result.rejection_reasons)
    assert gate.pass_fail is False
    assert gate.severity == Severity.BLOCKING


def test_double_wf_gate_result_handles_malformed_failure_values() -> None:
    result = DoubleWfResult(
        pass_fail=False,
        minimum_required_score="bad",  # type: ignore[arg-type]
        correlation_score="nan",  # type: ignore[arg-type]
        rejection_reasons=["malformed"],
    )

    gate = to_gate_result(result)

    assert gate.pass_fail is False
    assert gate.severity == Severity.BLOCKING
    assert gate.threshold == 1.0
    assert gate.observed_value == -1.0

    finite_score = DoubleWfResult(
        pass_fail=False,
        minimum_required_score="bad",  # type: ignore[arg-type]
        correlation_score=1.0,
        rejection_reasons=["malformed"],
    )
    finite_score_gate = to_gate_result(finite_score)
    assert finite_score_gate.pass_fail is False
    assert finite_score_gate.severity == Severity.BLOCKING
    assert finite_score_gate.observed_value < finite_score_gate.threshold

    malformed_pass = DoubleWfResult(
        pass_fail=True,
        minimum_required_score="bad",  # type: ignore[arg-type]
        correlation_score=1.0,
    )
    malformed_pass_gate = to_gate_result(malformed_pass)
    assert malformed_pass_gate.pass_fail is False
    assert malformed_pass_gate.severity == Severity.BLOCKING
    assert malformed_pass_gate.reason_code == "DOUBLE_WF_CORRELATION_BELOW_MIN"


def test_double_wf_insufficient_shared_params() -> None:
    """<3 shared params fails."""
    wf1 = [_row("p1", 0.5), _row("p2", 0.6)]
    wf2 = [_row("p3", 0.4), _row("p4", 0.8)]
    result = evaluate_double_wf(
        wf1, wf2, ["parameter_hash"],
        method="spearman", min_score=0.20,
    )
    assert result.pass_fail is False
    assert any("Insufficient shared parameters" in r for r in result.rejection_reasons)


# ---------- edge cases ----------


def test_double_wf_zero_variance() -> None:
    """Zero variance in WF1 or WF2 fails."""
    wf1 = [_row("p1", 0.5), _row("p2", 0.5), _row("p3", 0.5)]
    wf2 = [_row("p1", 0.4), _row("p2", 0.5), _row("p3", 0.6)]
    result = evaluate_double_wf(
        wf1, wf2, ["parameter_hash"],
        method="spearman", min_score=0.20,
    )
    assert result.pass_fail is False
    assert any("Zero variance" in r for r in result.rejection_reasons)


def test_double_wf_unknown_method() -> None:
    """Unknown correlation method fails."""
    wf1 = [_row("p1", 0.5), _row("p2", 0.6), _row("p3", 0.7)]
    wf2 = [_row("p1", 0.4), _row("p2", 0.5), _row("p3", 0.6)]
    result = evaluate_double_wf(
        wf1, wf2, ["parameter_hash"],
        method="unknown_method", min_score=0.20,
    )
    assert result.pass_fail is False
    assert any("Unknown correlation method" in r for r in result.rejection_reasons)


# ---------- gate derivation ----------


def test_double_wf_gate_result_pass() -> None:
    """The double-WF result can be converted to a Phase 8 GateResult
    with correct category and severity when pass_fail=True."""
    wf1 = [
        _row("p1", 0.8), _row("p2", 0.6), _row("p3", 0.4),
        _row("p4", 0.2), _row("p5", 0.1),
    ]
    wf2 = [
        _row("p1", 0.7), _row("p2", 0.5), _row("p3", 0.35),
        _row("p4", 0.15), _row("p5", 0.05),
    ]
    dwf = evaluate_double_wf(
        wf1, wf2, ["parameter_hash"],
        method="spearman", min_score=0.20,
    )
    gr = to_gate_result(dwf)
    assert gr.gate_category == GateCategory.WALK_FORWARD_CORRELATION
    assert gr.severity == Severity.INFO
    assert gr.blocking_status is False
    assert gr.pass_fail is True
    assert gr.reason_code == "DOUBLE_WF_CORRELATION_PASS"


def test_double_wf_gate_result_fail() -> None:
    """The double-WF result can be converted to a Phase 8 GateResult
    with BLOCKING severity when pass_fail=False."""
    wf1 = [
        _row("p1", 0.9), _row("p2", 0.1), _row("p3", 0.8),
        _row("p4", 0.2), _row("p5", 0.7),
    ]
    wf2 = [
        _row("p1", 0.1), _row("p2", 0.9), _row("p3", 0.2),
        _row("p4", 0.8), _row("p5", 0.3),
    ]
    dwf = evaluate_double_wf(
        wf1, wf2, ["parameter_hash"],
        method="spearman", min_score=0.20,
    )
    gr = to_gate_result(dwf)
    assert gr.gate_category == GateCategory.WALK_FORWARD_CORRELATION
    assert gr.severity == Severity.BLOCKING
    assert gr.blocking_status is True
    assert gr.pass_fail is False
    assert gr.reason_code == "DOUBLE_WF_CORRELATION_BELOW_MIN"


# ---------- artifact ----------


def test_double_wf_artifact_written(tmp_path: Path) -> None:
    """The walk_forward_correlation.json artifact is written (Phase 12)."""
    wf1 = [
        _row("p1", 0.8), _row("p2", 0.6), _row("p3", 0.4),
        _row("p4", 0.2), _row("p5", 0.1),
    ]
    wf2 = [
        _row("p1", 0.7), _row("p2", 0.5), _row("p3", 0.35),
        _row("p4", 0.15), _row("p5", 0.05),
    ]
    dwf = evaluate_double_wf(
        wf1, wf2, ["parameter_hash"],
        method="spearman", min_score=0.20,
    )
    out = tmp_path / "walk_forward_correlation.json"
    out.write_text(json.dumps(dwf.to_dict(), indent=2), encoding="utf-8")
    assert out.is_file()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert "correlation_score" in loaded
    assert "eligible_parameter_regions" in loaded
    assert "rejected_parameter_regions" in loaded
    assert "stability_summary" in loaded


# ---------- round-trip ----------


def test_double_wf_round_trip() -> None:
    """to_dict / from_dict round-trip preserves all fields."""
    wf1 = [
        _row("p1", 0.8), _row("p2", 0.6), _row("p3", 0.4),
        _row("p4", 0.2), _row("p5", 0.1),
    ]
    wf2 = [
        _row("p1", 0.7), _row("p2", 0.5), _row("p3", 0.35),
        _row("p4", 0.15), _row("p5", 0.05),
    ]
    dwf = evaluate_double_wf(
        wf1, wf2, ["parameter_hash"],
        method="spearman", min_score=0.20,
    )
    d = dwf.to_dict()
    dwf2 = DoubleWfResult.from_dict(d)
    assert dwf2.correlation_score == dwf.correlation_score
    assert dwf2.pass_fail == dwf.pass_fail
    assert dwf2.eligible_parameter_regions == dwf.eligible_parameter_regions
    assert dwf2.rejected_parameter_regions == dwf.rejected_parameter_regions
