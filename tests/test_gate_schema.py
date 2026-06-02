"""Phase 8 tests for the unified HFT3 gate schema.

Covers:
- test_gate_schema_round_trip: GateResult → dict → JSON serializes cleanly
- test_all_17_categories_registered: every spec category exists in the enum
- test_severity_blocking_invariant: Severity.BLOCKING ↔ blocking_status=True
- test_severity_warn_does_not_block: warn failures don't fail the verdict
- test_severity_info_recorded_only: info gates go to artifact but not verdict
- test_aggregate_promotion_reduces_correctly
- test_reason_code_naming_convention
- test_write_robustness_gates_json_atomic
- test_promotion_gate_emits_gates_list
- test_promotion_gate_backward_compat_failures_strings
- test_promotion_gate_threshold_config_driven
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hft3.validation.gate_result import (
    COMPARISON_OPERATORS,
    GateCategory,
    GateResult,
    SCHEMA_VERSION,
    Severity,
    aggregate_promotion,
    blocking_failures,
    warnings as gate_warnings,
    write_robustness_gates_json,
)
from hft3.validation.promotion_gate import (
    ROBUSTNESS_GATES_REL,
    evaluate_promotion_gate,
    evaluate_promotion_gates,
    write_robustness_gates_for_promotion,
)
from hft3.validation.certification_registry import CertificationRecord, save_registry


# ---------- schema round-trip ----------


def test_gate_schema_round_trip() -> None:
    g = GateResult(
        gate_name="wfc_pearson",
        gate_category=GateCategory.WALK_FORWARD_CORRELATION,
        metric_name="pearson",
        threshold=0.20,
        observed_value=0.34,
        comparison_operator=">=",
        pass_fail=True,
        severity=Severity.BLOCKING,
        reason_code="WFC_PEARSON_ABOVE_MIN",
        artifact_reference="artifacts/.../wfc_summary.json",
    )
    d = g.to_dict()
    assert d["gate_name"] == "wfc_pearson"
    assert d["gate_category"] == "walk_forward_correlation"
    assert d["severity"] == "blocking"
    assert d["pass_fail"] is True
    assert d["threshold"] == 0.20
    assert d["observed_value"] == 0.34
    parsed = json.loads(g.to_json())
    assert parsed == d


# ---------- all 17 categories registered ----------


def test_all_17_categories_registered() -> None:
    expected = {
        "data_integrity", "leakage_prevention", "backtest_validity",
        "execution_realism", "robustness", "walk_forward",
        "walk_forward_correlation", "latency_sensitivity",
        "cost_sensitivity", "slippage_sensitivity", "liquidity_capacity",
        "regime_stability", "parameter_stability", "drawdown_tail_risk",
        "model_combination_attribution", "registry_eligibility",
        "artifact_completeness",
    }
    assert {c.value for c in GateCategory} == expected
    assert len(GateCategory) == 17


# ---------- severity / blocking invariant ----------


def test_severity_blocking_must_match_blocking_status() -> None:
    with pytest.raises(ValueError):
        GateResult(
            gate_name="g",
            gate_category=GateCategory.ROBUSTNESS,
            metric_name="m",
            severity=Severity.BLOCKING,
            blocking_status=False,
        )
    with pytest.raises(ValueError):
        GateResult(
            gate_name="g",
            gate_category=GateCategory.ROBUSTNESS,
            metric_name="m",
            severity=Severity.WARN,
            blocking_status=True,
        )


def test_severity_warn_does_not_block() -> None:
    gates = [
        GateResult(
            gate_name="latency_buffer_negative",
            gate_category=GateCategory.LATENCY_SENSITIVITY,
            metric_name="buffer_us",
            threshold=10.0,
            observed_value=5.0,
            comparison_operator=">=",
            pass_fail=False,
            severity=Severity.WARN,
            blocking_status=False,
            reason_code="LATENCY_BUFFER_LOW",
        ),
        GateResult(
            gate_name="registry_status_green",
            gate_category=GateCategory.REGISTRY_ELIGIBILITY,
            metric_name="status",
            comparison_operator="==",
            pass_fail=True,
            severity=Severity.BLOCKING,
            reason_code="REGISTRY_STATUS_GREEN",
        ),
    ]
    passed, failures, warns = aggregate_promotion(gates)
    assert passed is True
    assert failures == []
    assert len(warns) == 1
    assert "LATENCY_BUFFER_LOW" in warns[0]


def test_severity_info_recorded_only() -> None:
    g = GateResult(
        gate_name="info_gate",
        gate_category=GateCategory.DATA_INTEGRITY,
        metric_name="npz_files",
        comparison_operator="==",
        pass_fail=True,
        severity=Severity.INFO,
        blocking_status=False,
        reason_code="DATA_NPZ_OK",
    )
    assert blocking_failures([g]) == []
    assert gate_warnings([g]) == []
    assert aggregate_promotion([g])[0] is True


def test_aggregate_promotion_reduces_correctly() -> None:
    g1 = GateResult(
        gate_name="g1", gate_category=GateCategory.ROBUSTNESS,
        metric_name="m", threshold=0.5, observed_value=0.3,
        comparison_operator=">=", pass_fail=False,
        severity=Severity.BLOCKING, reason_code="G1_BELOW_MIN",
    )
    g2 = GateResult(
        gate_name="g2", gate_category=GateCategory.REGISTRY_ELIGIBILITY,
        metric_name="m", comparison_operator="==", pass_fail=True,
        severity=Severity.BLOCKING, reason_code="G2_OK",
    )
    passed, failures, _ = aggregate_promotion([g1, g2])
    assert passed is False
    assert any("G1_BELOW_MIN" in f for f in failures)
    assert not any("G2_OK" in f for f in failures)


# ---------- reason code naming ----------


def test_reason_code_naming_convention() -> None:
    """Reason codes must be UPPER_SNAKE_CASE."""
    import re
    pattern = re.compile(r"^[A-Z][A-Z0-9_]*$")
    gates = evaluate_promotion_gates(
        event_id="CPI_2024", symbol="ES", latency_ms=1.0, queue_model="LogProbQueueModel2"
    )
    assert gates, "expected at least one gate from a default evaluation"
    for g in gates:
        assert pattern.match(g.reason_code), f"bad reason_code: {g.reason_code!r}"


# ---------- write robustness_gates.json atomic ----------


def test_write_robustness_gates_json_atomic(tmp_path: Path) -> None:
    gates = [
        GateResult(
            gate_name="g1", gate_category=GateCategory.ROBUSTNESS,
            metric_name="m", threshold=0.5, observed_value=0.7,
            comparison_operator=">=", pass_fail=True,
            severity=Severity.BLOCKING, reason_code="G1_OK",
        )
    ]
    out = write_robustness_gates_json(
        tmp_path / "robustness_gates.json",
        gates,
        tier="T3",
        run_id="r1",
        git_sha="abc123",
        thresholds_source="cfg/x.yaml",
        timestamp_utc="2026-06-02T12:00:00Z",
    )
    assert out.is_file()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["tier"] == "T3"
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["passed_overall"] is True
    # No leftover .tmp file
    assert not list(tmp_path.glob("robustness_gates.json.*.tmp"))


# ---------- promotion gate emits gates list ----------


def test_promotion_gate_emits_gates_list(tmp_path: Path) -> None:
    save_registry(
        CertificationRecord(
            latest_certification_status="GREEN",
            latest_certification_commit="abc",
        ),
        tmp_path,
    )
    result = evaluate_promotion_gate(
        event_id="CPI_2024", symbol="ES", latency_ms=1.0,
        queue_model="LogProbQueueModel2",
        root=tmp_path,
        skip_t0_rerun=True,
    )
    assert isinstance(result.gates, list)
    assert len(result.gates) >= 4
    # Each gate has the spec's 11 required fields
    for g in result.gates:
        d = g.to_dict()
        for key in (
            "gate_name", "gate_category", "metric_name", "threshold",
            "observed_value", "comparison_operator", "pass_fail",
            "severity", "reason_code", "artifact_reference", "blocking_status",
        ):
            assert key in d, f"missing {key} in {d}"


def test_promotion_gate_backward_compat_failures_strings(tmp_path: Path) -> None:
    """The free-text substrings the existing tests look for must still appear
    in the legacy `failures` list when the corresponding gate fails."""
    save_registry(
        CertificationRecord(latest_certification_status="MISSING"),
        tmp_path,
    )
    result = evaluate_promotion_gate(
        event_id="CPI_2024", symbol="ES", latency_ms=1.0,
        queue_model="LogProbQueueModel2",
        root=tmp_path,
        skip_t0_rerun=True,
    )
    assert result.passed is False
    blob = " ".join(result.failures).lower()
    # Existing test contract: substring match for "GREEN", "MISSING", or "missing"
    assert any(s in blob for s in ("green", "missing"))


def test_promotion_gate_threshold_config_driven(tmp_path: Path, monkeypatch) -> None:
    """The promotion gate's thresholds come from the registry
    (covered_latency_bands, covered_symbols, etc.), not from hardcoded
    literals in promotion_gate.py."""
    save_registry(
        CertificationRecord(
            latest_certification_status="GREEN",
            latest_certification_commit="abc",
            covered_symbols=["ES"],
            covered_latency_bands=[1.0, 5.0],
        ),
        tmp_path,
    )
    result = evaluate_promotion_gate(
        event_id="CPI_2024", symbol="ES", latency_ms=1.0,
        queue_model="LogProbQueueModel2",
        root=tmp_path,
        skip_t0_rerun=True,
    )
    # Latency in covered band → no failure for coverage
    assert "not in covered" not in " ".join(result.failures)


# ---------- write_robustness_gates_for_promotion ----------


def test_write_robustness_gates_for_promotion(tmp_path: Path) -> None:
    save_registry(
        CertificationRecord(latest_certification_status="GREEN", latest_certification_commit="abc"),
        tmp_path,
    )
    result = evaluate_promotion_gate(
        event_id="CPI_2024", symbol="ES", latency_ms=1.0,
        queue_model="LogProbQueueModel2",
        root=tmp_path,
        skip_t0_rerun=True,
    )
    out = write_robustness_gates_for_promotion(result.gates, tmp_path, run_id="r1")
    assert out.name == ROBUSTNESS_GATES_REL.name
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["tier"] == "T4"
    assert payload["gates"]
