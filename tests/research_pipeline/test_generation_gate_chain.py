"""Tests for strict generation gate-chain contract (Phase 1)."""

from __future__ import annotations

import pytest

from research_pipeline.generation_gate_chain import (
    FINAL_PASS,
    FINAL_REGULAR_WF_REJECTED,
    FINAL_VECTORBT_REJECTED,
    FINAL_WFC_REJECTED,
    FINAL_BLOCKED_MISSING,
    GATE_HFT,
    GATE_ONTOLOGY,
    GATE_REGULAR_WF,
    GATE_STATISTICAL,
    GATE_SURFACE,
    GATE_VECTORBT,
    GATE_WFC,
    build_gate_receipt,
    evaluate_manifest_gate,
    normalize_gate_receipt,
    receipt_is_strict_pass,
    run_generation_gate_chain,
    validate_gate_receipt_schema,
)


def _manifest(**overrides: object) -> dict:
    from research_pipeline.candidate_manifest import compute_manifest_hash

    base = {
        "manifest_schema": "candidate_manifest.v1",
        "candidate_id": "cand-001",
        "feature_recipe_hash": "recipe-abc",
        "model_id": "HYP_5",
    }
    base.update(overrides)
    if "manifest_hash" not in overrides:
        base["manifest_hash"] = compute_manifest_hash(base)
    return base


def _pass_receipt(gate_id: str, *, candidate_id: str = "cand-001") -> dict:
    m = _manifest()
    return build_gate_receipt(
        gate_id=gate_id,
        gate_version="1.0.0",
        candidate_id=candidate_id,
        feature_recipe_hash="recipe-abc",
        manifest_hash=str(m["manifest_hash"]),
        status="PASS",
        required_checks=["check_a", "check_b"],
        required_check_count=2,
        passed_check_count=2,
        failed_check_count=0,
        missing_check_count=0,
        authority_refs=["docs/project/ROBUSTNESS_TESTING_SPEC.md"],
        output_artifacts=[f"generation_0/gates/{candidate_id}/{gate_id}.json"],
    )


def test_receipt_is_strict_pass_requires_exact_counts() -> None:
    receipt = _pass_receipt(GATE_ONTOLOGY)
    assert receipt_is_strict_pass(receipt) is True

    permissive = dict(receipt)
    permissive["passed_check_count"] = 1
    assert receipt_is_strict_pass(permissive) is False

    false_pass = dict(receipt)
    false_pass["status"] = "PASS"
    false_pass["failed_check_count"] = 1
    assert receipt_is_strict_pass(false_pass) is False


def test_normalize_rejects_permissive_pass_status() -> None:
    receipt = _pass_receipt(GATE_VECTORBT)
    receipt["passed_check_count"] = 1
    evaluation = normalize_gate_receipt(receipt)
    assert evaluation["effective_status"] == "REJECT"
    assert evaluation["strict_pass"] is False
    assert any("strict PASS rule" in r for r in evaluation["failure_reasons"])


def test_normalize_missing_receipt_is_not_run() -> None:
    evaluation = normalize_gate_receipt(None)
    assert evaluation["effective_status"] == "NOT_RUN"
    assert evaluation["strict_pass"] is False


def test_validate_gate_receipt_schema_requires_all_fields() -> None:
    receipt = _pass_receipt(GATE_SURFACE)
    del receipt["gate_version"]
    errors = validate_gate_receipt_schema(receipt)
    assert any("gate_version" in e for e in errors)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("PASS", "PASS"),
        ("REJECT", "REJECT"),
        ("BLOCKED", "BLOCKED"),
        ("NOT_RUN", "NOT_RUN"),
    ],
)
def test_normalize_preserves_gate_statuses(status: str, expected: str) -> None:
    receipt = _pass_receipt(GATE_STATISTICAL)
    receipt["status"] = status
    if status != "PASS":
        receipt["passed_check_count"] = 0
    evaluation = normalize_gate_receipt(receipt)
    assert evaluation["effective_status"] == expected


def test_manifest_gate_passes_valid_manifest() -> None:
    evaluation = evaluate_manifest_gate(_manifest())
    assert evaluation["effective_status"] == "PASS"
    assert evaluation["strict_pass"] is True


def test_manifest_gate_blocked_on_missing_fields() -> None:
    bad = _manifest()
    del bad["manifest_hash"]
    evaluation = evaluate_manifest_gate(bad)
    assert evaluation["effective_status"] == "BLOCKED"
    assert evaluation["strict_pass"] is False


def test_run_generation_gate_chain_all_pass_final_pass() -> None:
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt(GATE_ONTOLOGY),
        vectorbt_receipt=_pass_receipt(GATE_VECTORBT),
        surface_receipt=_pass_receipt(GATE_SURFACE),
        regular_walk_forward_receipt=_pass_receipt(GATE_REGULAR_WF),
        walk_forward_correlation_receipt=_pass_receipt(GATE_WFC),
        statistical_receipt=_pass_receipt(GATE_STATISTICAL),
        hftbacktest_receipt=_pass_receipt(GATE_HFT),
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_PASS
    assert result["final_pass"] is True
    assert result["stopped_at_gate"] is None
    assert all(o["strict_pass"] for o in result["gate_outcomes"])


def test_run_generation_gate_chain_vectorbt_reject_short_circuits() -> None:
    reject = _pass_receipt(GATE_VECTORBT)
    reject["status"] = "REJECT"
    reject["passed_check_count"] = 0
    reject["failed_check_count"] = 2
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt(GATE_ONTOLOGY),
        vectorbt_receipt=reject,
        surface_receipt=_pass_receipt(GATE_SURFACE),
        regular_walk_forward_receipt=_pass_receipt(GATE_REGULAR_WF),
        walk_forward_correlation_receipt=_pass_receipt(GATE_WFC),
        statistical_receipt=_pass_receipt(GATE_STATISTICAL),
        hftbacktest_receipt=_pass_receipt(GATE_HFT),
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_VECTORBT_REJECTED
    assert result["stopped_at_gate"] == GATE_VECTORBT
    downstream = result["gate_outcomes"][3:]
    assert all(o["effective_status"] == "NOT_RUN" for o in downstream)


def test_run_generation_gate_chain_regular_wf_reject() -> None:
    reject = _pass_receipt(GATE_REGULAR_WF)
    reject["status"] = "REJECT"
    reject["passed_check_count"] = 0
    reject["failed_check_count"] = 1
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt(GATE_ONTOLOGY),
        vectorbt_receipt=_pass_receipt(GATE_VECTORBT),
        surface_receipt=_pass_receipt(GATE_SURFACE),
        regular_walk_forward_receipt=reject,
        walk_forward_correlation_receipt=_pass_receipt(GATE_WFC),
        statistical_receipt=_pass_receipt(GATE_STATISTICAL),
        hftbacktest_receipt=_pass_receipt(GATE_HFT),
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_REGULAR_WF_REJECTED
    assert result["stopped_at_gate"] == GATE_REGULAR_WF


def test_run_generation_gate_chain_wfc_reject() -> None:
    reject = _pass_receipt(GATE_WFC)
    reject["status"] = "REJECT"
    reject["passed_check_count"] = 0
    reject["failed_check_count"] = 1
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt(GATE_ONTOLOGY),
        vectorbt_receipt=_pass_receipt(GATE_VECTORBT),
        surface_receipt=_pass_receipt(GATE_SURFACE),
        regular_walk_forward_receipt=_pass_receipt(GATE_REGULAR_WF),
        walk_forward_correlation_receipt=reject,
        statistical_receipt=_pass_receipt(GATE_STATISTICAL),
        hftbacktest_receipt=_pass_receipt(GATE_HFT),
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_WFC_REJECTED
    assert result["stopped_at_gate"] == GATE_WFC


def test_run_generation_gate_chain_blocked_ontology() -> None:
    blocked = _pass_receipt(GATE_ONTOLOGY)
    blocked["status"] = "BLOCKED"
    blocked["passed_check_count"] = 0
    blocked["missing_check_count"] = 2
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=blocked,
        vectorbt_receipt=None,
        surface_receipt=None,
        regular_walk_forward_receipt=None,
        walk_forward_correlation_receipt=None,
        statistical_receipt=None,
        hftbacktest_receipt=None,
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_BLOCKED_MISSING
    assert result["stopped_at_gate"] == GATE_ONTOLOGY


def test_run_generation_gate_chain_not_run_ontology_blocks_certification() -> None:
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=None,
        vectorbt_receipt=None,
        surface_receipt=None,
        regular_walk_forward_receipt=None,
        walk_forward_correlation_receipt=None,
        statistical_receipt=None,
        hftbacktest_receipt=None,
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_BLOCKED_MISSING
    assert result["stopped_at_gate"] == GATE_ONTOLOGY
    assert result["gate_outcomes"][0]["effective_status"] == "NOT_RUN"
