"""Gate receipt producers for autoresearch generation_loop (Phase 2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from backtest_pipeline.src.ontology_gate import (
    SOURCE_UNBACKED,
    FableChecklist,
    run_gate,
    validate_fable_entry_checklist,
)
from research_pipeline.generation_gate_chain import (
    GATE_ONTOLOGY,
    GATE_REGULAR_WF,
    GATE_VECTORBT,
    GATE_WFC,
    build_gate_receipt,
)

ONTOLOGY_GATE_VERSION = "1.0.0"
REGULAR_WF_GATE_VERSION = "1.0.0"
WFC_GATE_VERSION = "1.0.0"
VECTORBT_GATE_VERSION = "1.0.0"

BLOCKED_UNBACKED_AUTHORITY = "BLOCKED_UNBACKED_AUTHORITY"

_DEFAULT_AUTHORITY_REFS: tuple[str, ...] = (
    "docs/project/ONTOLOGY_GATE_AGENT_SPEC.md",
    "docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md",
    "docs/project/ROBUSTNESS_TESTING_SPEC.md",
    "docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md",
    "docs/REVIEWER_CHARTER.md",
    "apps/workbench/config/walk_forward.yaml",
    "apps/workbench/config/wfc_gate.yaml",
    "vendor/vectorbt/VENDOR.lock",
    "vendor/hftbacktest/VENDOR.lock",
)

_DEFAULT_ONTOLOGY_CITATIONS: tuple[dict[str, str], ...] = (
    {
        "paper_id": "cont-kukanov-stoikov-2011-ofi",
        "spec_ref": "ONTOLOGY_GATE_AGENT_SPEC.md",
        "tool_doc_ref": "Portfolio.from_signals::vectorbt==1.0.0",
    },
    {
        "spec_ref": "VECTORBT_SCREENING_ENGINE_SPEC.md",
        "tool_doc_ref": "none",
    },
    {
        "spec_ref": "ROBUSTNESS_TESTING_SPEC.md",
        "tool_doc_ref": "none",
    },
    {
        "spec_ref": "HFTBACKTEST_REALISM_ENGINE_SPEC.md",
        "tool_doc_ref": "hftbacktest::vendor/hftbacktest",
    },
)


def gate_receipt_dir(gen_dir: Path, candidate_id: str) -> Path:
    return gen_dir / "gates" / candidate_id


def gate_receipt_path(gen_dir: Path, candidate_id: str, gate_id: str) -> Path:
    return gate_receipt_dir(gen_dir, candidate_id) / f"{gate_id}.json"


def write_gate_receipt(path: Path, receipt: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(receipt), indent=2) + "\n", encoding="utf-8")
    return path


def default_fable_checklist() -> FableChecklist:
    return validate_fable_entry_checklist(
        grounded=True,
        vault_read=True,
        authority_located=True,
        no_assumptions=True,
        fable_active=True,
    )


def ontology_citations_for_manifest(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    """Resolve ontology citations; metadata override supports planted unbacked tests."""
    recipe = dict(manifest.get("feature_recipe") or {})
    meta = dict(recipe.get("metadata") or manifest.get("metadata") or {})
    override = meta.get("ontology_citations") or manifest.get("ontology_citations")
    if override:
        return [dict(c) for c in override]
    return [dict(c) for c in _DEFAULT_ONTOLOGY_CITATIONS]


def _ontology_receipt_status(verdict) -> tuple[str, list[str]]:
    if verdict.passed:
        return "PASS", []
    reasons = list(verdict.reasons)
    unbacked = any(
        not c.backed or c.source_type == SOURCE_UNBACKED for c in verdict.citation_results
    ) or any("unbacked" in str(r).lower() for r in reasons)
    if unbacked:
        if BLOCKED_UNBACKED_AUTHORITY not in reasons:
            reasons.append(BLOCKED_UNBACKED_AUTHORITY)
        return "BLOCKED", reasons
    return "REJECT", reasons


def run_ontology_gate_for_candidate(
    *,
    manifest: Mapping[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    """Gate 0 — ontology admission before VectorBT compute."""
    candidate_id = str(manifest["candidate_id"])
    feature_recipe_hash = str(manifest["feature_recipe_hash"])
    manifest_hash = str(manifest["manifest_hash"])
    citations = ontology_citations_for_manifest(manifest)
    verdict = run_gate(
        fable_checklist=default_fable_checklist(),
        citations=citations,
        area="research_pipeline",
        call_sites=[
            {
                "tool": "vectorbt",
                "api_name": "Portfolio.from_signals",
                "args": {"close": True, "entries": True, "exits": True},
                "engine": "rust",
                "scope": "paid-compute",
                "version": "1.0.0",
            }
        ],
    )
    status, failure_reasons = _ontology_receipt_status(verdict)
    required_checks = [
        "fable_entry_checklist",
        "citation_trace",
        "invariant_check",
        "tool_usage",
    ]
    req_count = len(required_checks)
    passed_count = req_count if status == "PASS" else 0
    failed_count = 0 if status == "PASS" else max(1, verdict.red_count)
    missing_count = 0 if status == "PASS" else (req_count - passed_count - failed_count)
    receipt = build_gate_receipt(
        gate_id=GATE_ONTOLOGY,
        gate_version=ONTOLOGY_GATE_VERSION,
        candidate_id=candidate_id,
        feature_recipe_hash=feature_recipe_hash,
        manifest_hash=manifest_hash,
        status=status,
        required_checks=required_checks,
        required_check_count=req_count,
        passed_check_count=passed_count,
        failed_check_count=failed_count,
        missing_check_count=max(0, missing_count),
        authority_refs=list(_DEFAULT_AUTHORITY_REFS),
        output_artifacts=[f"gates/{candidate_id}/ontology_gate.json"],
        failure_reasons=failure_reasons,
        output_hashes={"ontology_verdict": str(hash(tuple(verdict.reasons)))},
    )
    receipt["ontology_verdict"] = verdict.as_dict()
    return receipt


def build_vectorbt_gate_receipt(
    *,
    manifest: Mapping[str, Any],
    promoted_row: Mapping[str, Any],
    screening_path: Path | None = None,
) -> dict[str, Any]:
    """Gate 2 — VectorBT screen receipt from promoted screening row."""
    candidate_id = str(manifest["candidate_id"])
    vbt = dict(promoted_row.get("vectorbt_results") or {})
    rejected = bool(promoted_row.get("rejected"))
    status = "PASS" if not rejected and vbt else "REJECT"
    required_checks = [
        "vectorbt_screen",
        "screening_artifact_hash",
        "feature_recipe_hash",
        "manifest_hash",
    ]
    req_count = len(required_checks)
    passed_count = req_count if status == "PASS" else 0
    failure_reasons: list[str] = []
    if status != "PASS":
        failure_reasons.append("vectorbt_screen_reject")
    receipt = build_gate_receipt(
        gate_id=GATE_VECTORBT,
        gate_version=VECTORBT_GATE_VERSION,
        candidate_id=candidate_id,
        feature_recipe_hash=str(manifest["feature_recipe_hash"]),
        manifest_hash=str(manifest["manifest_hash"]),
        status=status,
        required_checks=required_checks,
        required_check_count=req_count,
        passed_check_count=passed_count,
        failed_check_count=0 if status == "PASS" else req_count,
        missing_check_count=0,
        authority_refs=["docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md"],
        input_artifacts=[str(screening_path)] if screening_path else [],
        output_artifacts=[f"gates/{candidate_id}/vectorbt_gate.json"],
        failure_reasons=failure_reasons,
    )
    return receipt


def build_regular_walk_forward_gate_receipt(
    *,
    manifest: Mapping[str, Any],
    campaign_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Gate 4 — regular walk-forward from campaign period outputs (not WFC)."""
    candidate_id = str(manifest["candidate_id"])
    required_checks = [
        "regular_walk_forward_status",
        "period_fold_metrics",
        "holdout_evaluate_only",
    ]
    req_count = len(required_checks)
    if campaign_summary is None:
        return build_gate_receipt(
            gate_id=GATE_REGULAR_WF,
            gate_version=REGULAR_WF_GATE_VERSION,
            candidate_id=candidate_id,
            feature_recipe_hash=str(manifest["feature_recipe_hash"]),
            manifest_hash=str(manifest["manifest_hash"]),
            status="NOT_RUN",
            required_checks=required_checks,
            required_check_count=req_count,
            passed_check_count=0,
            failed_check_count=0,
            missing_check_count=req_count,
            authority_refs=["apps/workbench/config/walk_forward.yaml"],
            failure_reasons=["regular_walk_forward_not_run"],
        )
    wf_status = str(campaign_summary.get("status") or "")
    periods = list(campaign_summary.get("periods") or [])
    period_pass = all(bool(p.get("gate_pass")) for p in periods if periods) if periods else False
    status = "PASS" if wf_status == "PASS" and period_pass else "REJECT"
    failure_reasons: list[str] = []
    if status != "PASS":
        failure_reasons.append(f"regular_walk_forward_status={wf_status}")
        if periods and not period_pass:
            failure_reasons.append("one_or_more_period_gate_fail")
    receipt = build_gate_receipt(
        gate_id=GATE_REGULAR_WF,
        gate_version=REGULAR_WF_GATE_VERSION,
        candidate_id=candidate_id,
        feature_recipe_hash=str(manifest["feature_recipe_hash"]),
        manifest_hash=str(manifest["manifest_hash"]),
        status=status,
        required_checks=required_checks,
        required_check_count=req_count,
        passed_check_count=req_count if status == "PASS" else 0,
        failed_check_count=0 if status == "PASS" else max(1, req_count),
        missing_check_count=0,
        authority_refs=["apps/workbench/config/walk_forward.yaml"],
        input_artifacts=[str(campaign_summary.get("artifact_dir") or "")] if campaign_summary.get("artifact_dir") else [],
        output_artifacts=[f"gates/{candidate_id}/regular_walk_forward_gate.json"],
        failure_reasons=failure_reasons,
    )
    receipt["regular_walk_forward_status"] = wf_status
    receipt["period_count"] = len(periods)
    return receipt


def build_walk_forward_correlation_gate_receipt(
    *,
    manifest: Mapping[str, Any],
    campaign_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Gate 5 — WFC from existing evaluate_wfc_gate outputs (Pearson/Spearman)."""
    candidate_id = str(manifest["candidate_id"])
    required_checks = [
        "wfc_status",
        "pearson_correlation",
        "spearman_correlation",
        "parameter_surface_alignment",
    ]
    req_count = len(required_checks)
    if campaign_summary is None:
        return build_gate_receipt(
            gate_id=GATE_WFC,
            gate_version=WFC_GATE_VERSION,
            candidate_id=candidate_id,
            feature_recipe_hash=str(manifest["feature_recipe_hash"]),
            manifest_hash=str(manifest["manifest_hash"]),
            status="NOT_RUN",
            required_checks=required_checks,
            required_check_count=req_count,
            passed_check_count=0,
            failed_check_count=0,
            missing_check_count=req_count,
            authority_refs=["apps/workbench/config/wfc_gate.yaml", "docs/project/ROBUSTNESS_TESTING_SPEC.md"],
            failure_reasons=["walk_forward_correlation_not_run"],
        )
    wfc = dict(campaign_summary.get("wfc") or {})
    wfc_status = str(campaign_summary.get("wfc_status") or wfc.get("wfc_status") or "NOT_RUN")
    pearson = wfc.get("pearson")
    spearman = wfc.get("spearman")
    pearson_present = pearson is not None
    spearman_present = spearman is not None
    status = "PASS" if wfc_status == "PASS" and pearson_present and spearman_present else "REJECT"
    if wfc_status in ("SKIPPED", "NOT_RUN"):
        status = "NOT_RUN"
    failure_reasons: list[str] = []
    if status == "REJECT":
        failure_reasons.append(f"wfc_status={wfc_status}")
        if not pearson_present:
            failure_reasons.append("pearson_missing")
        if not spearman_present:
            failure_reasons.append("spearman_missing")
    passed_count = req_count if status == "PASS" else 0
    receipt = build_gate_receipt(
        gate_id=GATE_WFC,
        gate_version=WFC_GATE_VERSION,
        candidate_id=candidate_id,
        feature_recipe_hash=str(manifest["feature_recipe_hash"]),
        manifest_hash=str(manifest["manifest_hash"]),
        status=status,
        required_checks=required_checks,
        required_check_count=req_count,
        passed_check_count=passed_count,
        failed_check_count=0 if status in ("PASS", "NOT_RUN") else max(1, req_count),
        missing_check_count=req_count if status == "NOT_RUN" else 0,
        authority_refs=["apps/workbench/config/wfc_gate.yaml", "docs/project/ROBUSTNESS_TESTING_SPEC.md"],
        output_artifacts=[f"gates/{candidate_id}/walk_forward_correlation_gate.json"],
        failure_reasons=failure_reasons,
    )
    receipt["wfc_status"] = wfc_status
    receipt["pearson"] = pearson
    receipt["spearman"] = spearman
    receipt["n_parameter_combinations"] = wfc.get("n_parameter_combinations")
    receipt["n_folds"] = wfc.get("n_folds")
    aligned_hashes = sorted(
        {str(r.get("parameter_hash", "")) for r in (campaign_summary.get("wfc_matrix_rows") or []) if r.get("parameter_hash")}
    )
    if aligned_hashes:
        receipt["aligned_parameter_hashes"] = aligned_hashes
    return receipt


def emit_candidate_gate_receipts(
    *,
    gen_dir: Path,
    manifest: Mapping[str, Any],
    ontology_receipt: dict[str, Any] | None = None,
    vectorbt_receipt: dict[str, Any] | None = None,
    regular_wf_receipt: dict[str, Any] | None = None,
    wfc_receipt: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Write gate receipt JSON files under generation_<N>/gates/<candidate_id>/."""
    candidate_id = str(manifest["candidate_id"])
    paths: dict[str, Path] = {}
    for gate_id, receipt in (
        (GATE_ONTOLOGY, ontology_receipt),
        (GATE_VECTORBT, vectorbt_receipt),
        (GATE_REGULAR_WF, regular_wf_receipt),
        (GATE_WFC, wfc_receipt),
    ):
        if receipt is None:
            continue
        path = gate_receipt_path(gen_dir, candidate_id, gate_id)
        write_gate_receipt(path, receipt)
        paths[gate_id] = path
    return paths
