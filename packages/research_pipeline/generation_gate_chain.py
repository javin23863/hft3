"""Strict runtime gate-chain contract for autoresearch generations.

Evaluates gate receipts in order (ontology → manifest → VectorBT → … → HFT).
Phase 1: orchestrator + receipt schema only — individual gate producers wired in Phase 2+.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from research_pipeline.candidate_manifest import verify_frozen_manifest_integrity

GATE_ONTOLOGY = "ontology_gate"
GATE_MANIFEST = "manifest_gate"
GATE_VECTORBT = "vectorbt_gate"
GATE_SURFACE = "surface_stability_gate"
GATE_REGULAR_WF = "regular_walk_forward_gate"
GATE_WFC = "walk_forward_correlation_gate"
GATE_STATISTICAL = "statistical_robustness_gate"
GATE_HFT = "hftbacktest_gate"
GATE_FINAL = "final_certification_gate"

GATE_CHAIN_ORDER: tuple[str, ...] = (
    GATE_ONTOLOGY,
    GATE_MANIFEST,
    GATE_VECTORBT,
    GATE_SURFACE,
    GATE_REGULAR_WF,
    GATE_WFC,
    GATE_STATISTICAL,
    GATE_HFT,
)

GATE_RECEIPT_STATUSES: frozenset[str] = frozenset({"PASS", "REJECT", "BLOCKED", "NOT_RUN"})

GATE_RECEIPT_REQUIRED_FIELDS: tuple[str, ...] = (
    "gate_id",
    "gate_version",
    "candidate_id",
    "feature_recipe_hash",
    "manifest_hash",
    "status",
    "required_checks",
    "required_check_count",
    "passed_check_count",
    "failed_check_count",
    "missing_check_count",
    "authority_refs",
    "input_artifacts",
    "input_hashes",
    "output_artifacts",
    "output_hashes",
    "failure_reasons",
    "started_at_utc",
    "finished_at_utc",
)

MANIFEST_REQUIRED_FIELDS: tuple[str, ...] = (
    "candidate_id",
    "feature_recipe_hash",
    "manifest_hash",
    "manifest_schema",
)

FINAL_PASS = "FINAL_PASS"
FINAL_ONTOLOGY_REJECTED = "ONTOLOGY_REJECTED"
FINAL_VECTORBT_REJECTED = "VECTORBT_REJECTED"
FINAL_SURFACE_REJECTED = "SURFACE_REJECTED"
FINAL_REGULAR_WF_REJECTED = "REGULAR_WF_REJECTED"
FINAL_WFC_REJECTED = "WFC_REJECTED"
FINAL_STATISTICAL_REJECTED = "STATISTICAL_REJECTED"
FINAL_HFT_REJECTED = "HFT_REJECTED"
FINAL_BLOCKED_MISSING = "BLOCKED_MISSING_EVIDENCE"
FINAL_INFRASTRUCTURE_FAILED = "INFRASTRUCTURE_FAILED"

_GATE_TO_FINAL_REJECT: dict[str, str] = {
    GATE_ONTOLOGY: FINAL_ONTOLOGY_REJECTED,
    GATE_MANIFEST: FINAL_BLOCKED_MISSING,
    GATE_VECTORBT: FINAL_VECTORBT_REJECTED,
    GATE_SURFACE: FINAL_SURFACE_REJECTED,
    GATE_REGULAR_WF: FINAL_REGULAR_WF_REJECTED,
    GATE_WFC: FINAL_WFC_REJECTED,
    GATE_STATISTICAL: FINAL_STATISTICAL_REJECTED,
    GATE_HFT: FINAL_HFT_REJECTED,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_gate_receipt(
    *,
    gate_id: str,
    gate_version: str,
    candidate_id: str,
    feature_recipe_hash: str,
    manifest_hash: str,
    status: str,
    required_checks: Sequence[str] | None = None,
    required_check_count: int | None = None,
    passed_check_count: int | None = None,
    failed_check_count: int = 0,
    missing_check_count: int = 0,
    authority_refs: Sequence[str] | None = None,
    input_artifacts: Sequence[str] | None = None,
    input_hashes: Mapping[str, str] | None = None,
    output_artifacts: Sequence[str] | None = None,
    output_hashes: Mapping[str, str] | None = None,
    failure_reasons: Sequence[str] | None = None,
    started_at_utc: str | None = None,
    finished_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a gate receipt dict with all required schema fields."""
    checks = list(required_checks or [])
    req_count = required_check_count if required_check_count is not None else len(checks)
    passed_count = passed_check_count if passed_check_count is not None else (req_count if status == "PASS" else 0)
    receipt: dict[str, Any] = {
        "gate_id": gate_id,
        "gate_version": gate_version,
        "candidate_id": candidate_id,
        "feature_recipe_hash": feature_recipe_hash,
        "manifest_hash": manifest_hash,
        "status": status,
        "required_checks": checks,
        "required_check_count": int(req_count),
        "passed_check_count": int(passed_count),
        "failed_check_count": int(failed_check_count),
        "missing_check_count": int(missing_check_count),
        "authority_refs": list(authority_refs or []),
        "input_artifacts": list(input_artifacts or []),
        "input_hashes": dict(input_hashes or {}),
        "output_artifacts": list(output_artifacts or []),
        "output_hashes": dict(output_hashes or {}),
        "failure_reasons": list(failure_reasons or []),
        "started_at_utc": started_at_utc or _utc_now_iso(),
        "finished_at_utc": finished_at_utc or _utc_now_iso(),
    }
    return receipt


def validate_gate_receipt_schema(receipt: Mapping[str, Any]) -> list[str]:
    """Return schema validation errors; empty list means structurally valid."""
    errors: list[str] = []
    for field in GATE_RECEIPT_REQUIRED_FIELDS:
        if field not in receipt:
            errors.append(f"missing field: {field}")
    status = receipt.get("status")
    if status is not None and status not in GATE_RECEIPT_STATUSES:
        errors.append(f"invalid status: {status!r}")
    for count_field in (
        "required_check_count",
        "passed_check_count",
        "failed_check_count",
        "missing_check_count",
    ):
        value = receipt.get(count_field)
        if value is not None and not isinstance(value, int):
            errors.append(f"{count_field} must be int, got {type(value).__name__}")
    return errors


def receipt_is_strict_pass(receipt: Mapping[str, Any]) -> bool:
    """True only when status is PASS and check counts match exactly."""
    if receipt.get("status") != "PASS":
        return False
    required = receipt.get("required_check_count")
    passed = receipt.get("passed_check_count")
    failed = receipt.get("failed_check_count")
    missing = receipt.get("missing_check_count")
    if not isinstance(required, int) or not isinstance(passed, int):
        return False
    if not isinstance(failed, int) or not isinstance(missing, int):
        return False
    return passed == required and failed == 0 and missing == 0


def normalize_gate_receipt(receipt: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate receipt schema and enforce strict PASS semantics on counts."""
    if receipt is None:
        return {
            "effective_status": "NOT_RUN",
            "strict_pass": False,
            "schema_valid": False,
            "failure_reasons": ["receipt missing"],
            "receipt": None,
        }
    schema_errors = validate_gate_receipt_schema(receipt)
    if schema_errors:
        return {
            "effective_status": "BLOCKED",
            "strict_pass": False,
            "schema_valid": False,
            "failure_reasons": schema_errors,
            "receipt": dict(receipt),
        }
    status = str(receipt.get("status"))
    failure_reasons = list(receipt.get("failure_reasons") or [])
    if status == "PASS" and not receipt_is_strict_pass(receipt):
        failure_reasons = failure_reasons + [
            "status PASS but check counts do not satisfy strict PASS rule "
            f"(required={receipt.get('required_check_count')}, "
            f"passed={receipt.get('passed_check_count')}, "
            f"failed={receipt.get('failed_check_count')}, "
            f"missing={receipt.get('missing_check_count')})"
        ]
        return {
            "effective_status": "REJECT",
            "strict_pass": False,
            "schema_valid": True,
            "failure_reasons": failure_reasons,
            "receipt": dict(receipt),
        }
    strict_pass = receipt_is_strict_pass(receipt)
    effective_status = status if status in GATE_RECEIPT_STATUSES else "BLOCKED"
    return {
        "effective_status": effective_status,
        "strict_pass": strict_pass,
        "schema_valid": True,
        "failure_reasons": failure_reasons,
        "receipt": dict(receipt),
    }


def evaluate_manifest_gate(candidate_manifest: Mapping[str, Any] | None) -> dict[str, Any]:
    """Gate 1 — frozen candidate manifest (no separate receipt parameter in chain API)."""
    if candidate_manifest is None:
        return normalize_gate_receipt(None)
    integrity_errors = verify_frozen_manifest_integrity(candidate_manifest)
    if integrity_errors:
        missing = [e for e in integrity_errors if e.startswith("missing:")]
        status = "BLOCKED" if missing else "REJECT"
        return {
            "effective_status": status,
            "strict_pass": False,
            "schema_valid": not missing,
            "failure_reasons": integrity_errors,
            "receipt": None,
        }
    receipt = build_gate_receipt(
        gate_id=GATE_MANIFEST,
        gate_version="1.0.0",
        candidate_id=str(candidate_manifest["candidate_id"]),
        feature_recipe_hash=str(candidate_manifest["feature_recipe_hash"]),
        manifest_hash=str(candidate_manifest["manifest_hash"]),
        status="PASS",
        required_checks=["manifest_schema", "manifest_hash", "feature_recipe_hash", "manifest_immutability"],
        required_check_count=4,
        passed_check_count=4,
        authority_refs=["packages/research_pipeline/candidate_manifest.py"],
    )
    return normalize_gate_receipt(receipt)


def passes_gates_before_hft(chain_result: Mapping[str, Any]) -> bool:
    """True when gates 0–6 (ontology through statistical) all strict-PASS."""
    for outcome in chain_result.get("gate_outcomes") or []:
        gate_id = str(outcome.get("gate_id") or "")
        if gate_id == GATE_HFT:
            break
        if not outcome.get("strict_pass") or outcome.get("effective_status") != "PASS":
            return False
    return chain_result.get("stopped_at_gate") == GATE_HFT


def _final_status_for_gate(gate_id: str, effective_status: str) -> str:
    if effective_status == "REJECT":
        return _GATE_TO_FINAL_REJECT.get(gate_id, FINAL_BLOCKED_MISSING)
    if effective_status == "BLOCKED":
        return FINAL_BLOCKED_MISSING
    if effective_status == "NOT_RUN":
        return FINAL_BLOCKED_MISSING
    return FINAL_BLOCKED_MISSING


def run_generation_gate_chain(
    *,
    candidate_manifest: dict,
    ontology_receipt: dict | None,
    vectorbt_receipt: dict | None,
    surface_receipt: dict | None,
    regular_walk_forward_receipt: dict | None,
    walk_forward_correlation_receipt: dict | None,
    statistical_receipt: dict | None,
    hftbacktest_receipt: dict | None,
    certification_mode: bool,
) -> dict[str, Any]:
    """Evaluate gate receipts in order; return aggregate chain result."""
    manifest_eval = evaluate_manifest_gate(candidate_manifest)
    candidate_id = str(
        (candidate_manifest or {}).get("candidate_id")
        or (ontology_receipt or {}).get("candidate_id")
        or ""
    )
    feature_recipe_hash = str(
        (candidate_manifest or {}).get("feature_recipe_hash")
        or (ontology_receipt or {}).get("feature_recipe_hash")
        or ""
    )
    manifest_hash = str(
        (candidate_manifest or {}).get("manifest_hash")
        or (ontology_receipt or {}).get("manifest_hash")
        or ""
    )

    gate_specs: list[tuple[str, dict[str, Any]]] = [
        (GATE_ONTOLOGY, normalize_gate_receipt(ontology_receipt)),
        (GATE_MANIFEST, manifest_eval),
        (GATE_VECTORBT, normalize_gate_receipt(vectorbt_receipt)),
        (GATE_SURFACE, normalize_gate_receipt(surface_receipt)),
        (GATE_REGULAR_WF, normalize_gate_receipt(regular_walk_forward_receipt)),
        (GATE_WFC, normalize_gate_receipt(walk_forward_correlation_receipt)),
        (GATE_STATISTICAL, normalize_gate_receipt(statistical_receipt)),
        (GATE_HFT, normalize_gate_receipt(hftbacktest_receipt)),
    ]

    gate_outcomes: list[dict[str, Any]] = []
    stopped_at_gate: str | None = None
    final_status = FINAL_PASS
    chain_failure_reasons: list[str] = []
    chain_stopped = False

    for gate_id, evaluation in gate_specs:
        if chain_stopped:
            gate_outcomes.append(
                {
                    "gate_id": gate_id,
                    "effective_status": "NOT_RUN",
                    "strict_pass": False,
                    "schema_valid": False,
                    "failure_reasons": [f"skipped: upstream stopped at {stopped_at_gate}"],
                    "receipt": evaluation.get("receipt"),
                }
            )
            continue

        outcome = {
            "gate_id": gate_id,
            "effective_status": evaluation["effective_status"],
            "strict_pass": evaluation["strict_pass"],
            "schema_valid": evaluation["schema_valid"],
            "failure_reasons": list(evaluation["failure_reasons"]),
            "receipt": evaluation["receipt"],
        }
        gate_outcomes.append(outcome)

        if outcome["strict_pass"] and outcome["effective_status"] == "PASS":
            continue

        chain_stopped = True
        stopped_at_gate = gate_id
        effective = str(outcome["effective_status"])
        final_status = _final_status_for_gate(gate_id, effective)
        chain_failure_reasons.extend(outcome["failure_reasons"] or [f"{gate_id} {effective}"])

    return {
        "candidate_id": candidate_id,
        "feature_recipe_hash": feature_recipe_hash,
        "manifest_hash": manifest_hash,
        "certification_mode": certification_mode,
        "final_status": final_status,
        "final_pass": final_status == FINAL_PASS,
        "stopped_at_gate": stopped_at_gate,
        "gate_chain_order": list(GATE_CHAIN_ORDER),
        "gate_outcomes": gate_outcomes,
        "failure_reasons": chain_failure_reasons,
    }
