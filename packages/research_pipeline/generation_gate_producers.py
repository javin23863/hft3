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
from backtest_pipeline.src.robustness_bridge import compute_robustness_evidence
from backtest_pipeline.src.surface_stability import REQUIRED_CHECKS as SURFACE_REQUIRED_CHECKS
from backtest_pipeline.src.vectorbt_adapter import (
    is_screening_not_run,
    is_surface_stability_defined,
    screening_status_text,
)
from research_pipeline.src.robustness_producers import holm_bh_correction
from research_pipeline.candidate_manifest import verify_frozen_manifest_integrity
from research_pipeline.generation_gate_chain import (
    GATE_HFT,
    GATE_MANIFEST,
    GATE_ONTOLOGY,
    GATE_REGULAR_WF,
    GATE_STATISTICAL,
    GATE_SURFACE,
    GATE_VECTORBT,
    GATE_WFC,
    build_gate_receipt,
)

ONTOLOGY_GATE_VERSION = "1.0.0"
REGULAR_WF_GATE_VERSION = "1.0.0"
WFC_GATE_VERSION = "1.0.0"
VECTORBT_GATE_VERSION = "1.0.0"
SURFACE_GATE_VERSION = "1.0.0"
STATISTICAL_GATE_VERSION = "1.0.0"
MANIFEST_GATE_VERSION = "1.0.0"
HFT_GATE_VERSION = "1.0.0"

_VECTORBT_OFFICIAL_STATS: tuple[str, ...] = (
    "gross_return",
    "net_return",
    "net_pnl",
    "total_fees",
    "total_slippage",
    "trade_count",
    "hit_rate",
    "expectancy_per_trade",
    "profit_factor",
    "sharpe",
    "sortino",
    "max_drawdown",
    "turnover",
)

_STATISTICAL_REQUIRED_CHECKS: tuple[str, ...] = (
    "bootstrap_confidence_interval",
    "deflated_sharpe_ratio",
    "cscv_pbo",
    "holm_multiple_testing_correction",
    "benjamini_hochberg_correction",
    "fee_multiplier_stress",
    "slippage_multiplier_stress",
    "latency_stress",
    "parameter_perturbation",
    "null_strategy_battery",
    "planted_alpha_control",
    "adversarial_perturbation",
    "robustness_artifact_staleness",
)

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


def _metric_present(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, Mapping):
        return not is_screening_not_run(value)
    if isinstance(value, str) and value.strip().lower().startswith("not_run"):
        return False
    return True


def _holm_bh_evidence(p_values: Sequence[float] | None, *, method: str) -> dict[str, Any]:
    if not p_values:
        return {"status": "not_run", "reason": "no_p_values"}
    result = holm_bh_correction(list(p_values), method=method)
    reason = result.get("reason")
    n_rejected = result.get("n_rejected")
    if reason is not None:
        status = "fail"
    elif n_rejected is not None and n_rejected > 0:
        status = "pass"
    else:
        status = "fail"
    return {"status": status, **result}


def _statistical_row_has_input(promoted_row: Mapping[str, Any]) -> bool:
    if _robustness_input_from_promoted_row(promoted_row):
        return True
    probe_keys = (
        "dsr_status",
        "pbo_status",
        "cscv_status",
        "robustness_artifact_staleness",
        "bootstrap_ci_or_not_run",
        "dsr_or_not_run",
        "pbo_or_not_run",
        "fee_stress_or_not_run",
        "slippage_stress_or_not_run",
        "latency_stress_or_not_run",
        "holm_stepdown_or_not_run",
        "holm_bh_or_not_run",
        "null_battery_or_not_run",
        "planted_alpha_or_not_run",
        "adversarial_or_not_run",
        "parameter_perturbation_or_not_run",
    )
    return any(promoted_row.get(key) for key in probe_keys)


def _robustness_input_from_promoted_row(promoted_row: Mapping[str, Any]) -> dict[str, Any] | None:
    vbt = dict(promoted_row.get("vectorbt_results") or {})
    for key in ("robustness_input",):
        raw = promoted_row.get(key) or vbt.get(key)
        if isinstance(raw, Mapping) and raw:
            return dict(raw)
    return None


def _producer_status_pass(value: Any) -> bool:
    if isinstance(value, Mapping):
        return screening_status_text(value) == "pass"
    return str(value or "").strip().lower() == "pass"


def build_vectorbt_gate_receipt(
    *,
    manifest: Mapping[str, Any],
    promoted_row: Mapping[str, Any],
    screening_path: Path | None = None,
    screening: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Gate 2 — VectorBT screen receipt from promoted screening row."""
    candidate_id = str(manifest["candidate_id"])
    vbt = dict(promoted_row.get("vectorbt_results") or {})
    rejected = bool(promoted_row.get("rejected")) or str(
        promoted_row.get("screening_status") or ""
    ).lower() in ("reject", "fail")
    screening_hash = str(
        (screening or {}).get("screening_artifact_hash")
        or promoted_row.get("screening_artifact_hash")
        or ""
    )
    official_stats: dict[str, Any] = {}
    for field in _VECTORBT_OFFICIAL_STATS:
        official_stats[field] = promoted_row.get(field)
        if not _metric_present(official_stats[field]):
            official_stats[field] = vbt.get(field)
        if field == "trade_count" and not _metric_present(official_stats[field]):
            official_stats[field] = vbt.get("num_trades")
    missing_stats = [name for name, val in official_stats.items() if not _metric_present(val)]
    required_checks = [
        "vectorbt_screen",
        "screening_artifact_hash",
        "feature_recipe_hash",
        "manifest_hash",
        "official_vectorbt_stats",
    ]
    req_count = len(required_checks)
    failure_reasons: list[str] = []
    if rejected:
        failure_reasons.append("vectorbt_screen_reject")
    if not vbt and not rejected:
        failure_reasons.append("vectorbt_results_missing")
    if not screening_hash:
        failure_reasons.append("screening_artifact_hash_missing")
    if missing_stats:
        failure_reasons.append(f"official_stats_missing:{','.join(missing_stats)}")
    status = "PASS" if not failure_reasons else "REJECT"
    passed_count = req_count if status == "PASS" else 0
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
        authority_refs=[
            "docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md",
            "vendor/vectorbt/VENDOR.lock",
        ],
        input_artifacts=[str(screening_path)] if screening_path else [],
        input_hashes={"screening_artifact_hash": screening_hash} if screening_hash else {},
        output_artifacts=[f"gates/{candidate_id}/vectorbt_gate.json"],
        failure_reasons=failure_reasons,
    )
    receipt["screening_artifact_hash"] = screening_hash
    receipt["official_stats"] = official_stats
    receipt["rejection_reasons"] = list(promoted_row.get("rejection_reasons") or [])
    if promoted_row.get("rejection_reason_or_null"):
        receipt["rejection_reasons"].append(str(promoted_row["rejection_reason_or_null"]))
    return receipt


def build_surface_stability_gate_receipt(
    *,
    manifest: Mapping[str, Any],
    promoted_row: Mapping[str, Any],
    screening_path: Path | None = None,
) -> dict[str, Any]:
    """Gate 3 — in-sample parameter-surface stability from screening metrics."""
    candidate_id = str(manifest["candidate_id"])
    surface = dict(promoted_row.get("surface_stability_metrics") or {})
    required_checks = list(SURFACE_REQUIRED_CHECKS)
    req_count = len(required_checks)
    failure_reasons: list[str] = []
    if not surface:
        return build_gate_receipt(
            gate_id=GATE_SURFACE,
            gate_version=SURFACE_GATE_VERSION,
            candidate_id=candidate_id,
            feature_recipe_hash=str(manifest["feature_recipe_hash"]),
            manifest_hash=str(manifest["manifest_hash"]),
            status="NOT_RUN",
            required_checks=required_checks,
            required_check_count=req_count,
            passed_check_count=0,
            failed_check_count=0,
            missing_check_count=req_count,
            authority_refs=["docs/project/ROBUSTNESS_TESTING_SPEC.md"],
            input_artifacts=[str(screening_path)] if screening_path else [],
            output_artifacts=[f"gates/{candidate_id}/surface_stability_gate.json"],
            failure_reasons=["surface_stability_metrics_missing"],
        )
    surface_status = screening_status_text(surface)
    defined = is_surface_stability_defined(surface)
    if surface_status == "pass" and defined:
        status = "PASS"
    else:
        status = "REJECT"
        if surface_status != "pass":
            failure_reasons.append(f"surface_status={surface_status or 'missing'}")
        if not defined:
            failure_reasons.append("surface_evidence_incomplete")
        if surface.get("reason"):
            failure_reasons.append(str(surface["reason"]))
    passed_count = req_count if status == "PASS" else 0
    receipt = build_gate_receipt(
        gate_id=GATE_SURFACE,
        gate_version=SURFACE_GATE_VERSION,
        candidate_id=candidate_id,
        feature_recipe_hash=str(manifest["feature_recipe_hash"]),
        manifest_hash=str(manifest["manifest_hash"]),
        status=status,
        required_checks=required_checks,
        required_check_count=req_count,
        passed_check_count=passed_count,
        failed_check_count=0 if status == "PASS" else max(1, req_count),
        missing_check_count=0,
        authority_refs=["docs/project/ROBUSTNESS_TESTING_SPEC.md"],
        input_artifacts=[str(screening_path)] if screening_path else [],
        output_artifacts=[f"gates/{candidate_id}/surface_stability_gate.json"],
        failure_reasons=failure_reasons,
    )
    receipt["surface_stability_metrics"] = surface
    receipt["surface_input_hash"] = surface.get("surface_input_hash")
    receipt["formula_authority_status"] = surface.get("formula_authority_status")
    return receipt


def _statistical_evidence_from_row(promoted_row: Mapping[str, Any]) -> dict[str, Any]:
    """Build robustness-bridge evidence dict from a promoted screening row."""
    return {
        "wfc_status": promoted_row.get("wfc_status"),
        "dsr_status": promoted_row.get("dsr_status"),
        "pbo_status": promoted_row.get("pbo_status"),
        "cscv_status": promoted_row.get("cscv_status"),
        "robustness_artifact_staleness": promoted_row.get("robustness_artifact_staleness"),
        "bootstrap_ci_or_not_run": dict(promoted_row.get("bootstrap_ci_or_not_run") or {}),
        "dsr_or_not_run": dict(promoted_row.get("dsr_or_not_run") or {}),
        "pbo_or_not_run": dict(promoted_row.get("pbo_or_not_run") or {}),
        "cscv_count_or_not_run": dict(promoted_row.get("cscv_count_or_not_run") or {}),
        "fee_stress_or_not_run": dict(promoted_row.get("fee_stress_or_not_run") or {}),
        "slippage_stress_or_not_run": dict(promoted_row.get("slippage_stress_or_not_run") or {}),
        "latency_stress_or_not_run": dict(promoted_row.get("latency_stress_or_not_run") or {}),
        "holm_stepdown_or_not_run": dict(promoted_row.get("holm_stepdown_or_not_run") or {}),
        "holm_bh_or_not_run": dict(promoted_row.get("holm_bh_or_not_run") or {}),
        "null_battery_or_not_run": dict(promoted_row.get("null_battery_or_not_run") or {}),
        "planted_alpha_or_not_run": dict(promoted_row.get("planted_alpha_or_not_run") or {}),
        "adversarial_or_not_run": dict(promoted_row.get("adversarial_or_not_run") or {}),
        "parameter_perturbation_or_not_run": dict(
            promoted_row.get("parameter_perturbation_or_not_run") or {}
        ),
    }


def _evaluate_statistical_checks(
    evidence: Mapping[str, Any],
    *,
    allow_partial: bool,
) -> tuple[list[str], list[str]]:
    """Return (passed_checks, failure_reasons) for Gate 6 statistical gauntlet."""
    passed: list[str] = []
    failures: list[str] = []
    check_map: list[tuple[str, Any]] = [
        ("bootstrap_confidence_interval", evidence.get("bootstrap_ci_or_not_run")),
        ("deflated_sharpe_ratio", evidence.get("dsr_or_not_run")),
        ("cscv_pbo", evidence.get("pbo_or_not_run")),
        ("holm_multiple_testing_correction", evidence.get("holm_stepdown_or_not_run")),
        ("benjamini_hochberg_correction", evidence.get("holm_bh_or_not_run")),
        ("fee_multiplier_stress", evidence.get("fee_stress_or_not_run")),
        ("slippage_multiplier_stress", evidence.get("slippage_stress_or_not_run")),
        ("latency_stress", evidence.get("latency_stress_or_not_run")),
        ("parameter_perturbation", evidence.get("parameter_perturbation_or_not_run")),
        ("null_strategy_battery", evidence.get("null_battery_or_not_run")),
        ("planted_alpha_control", evidence.get("planted_alpha_or_not_run")),
        ("adversarial_perturbation", evidence.get("adversarial_or_not_run")),
    ]
    for check_name, payload in check_map:
        if _producer_status_pass(payload):
            passed.append(check_name)
        elif isinstance(payload, Mapping) and screening_status_text(payload) == "not_run":
            failures.append(f"{check_name}_not_run")
        else:
            failures.append(f"{check_name}_fail")
    staleness = str(evidence.get("robustness_artifact_staleness") or "").lower()
    if staleness == "fresh":
        passed.append("robustness_artifact_staleness")
    else:
        failures.append(f"robustness_artifact_staleness={staleness or 'missing'}")
    if not allow_partial and any(reason.endswith("_not_run") for reason in failures):
        failures.append("allow_partial_false_blocks_not_run")
    dsr_status = str(evidence.get("dsr_status") or "").lower()
    pbo_status = str(evidence.get("pbo_status") or "").lower()
    cscv_status = str(evidence.get("cscv_status") or "").lower()
    for label, value in (("dsr_status", dsr_status), ("pbo_status", pbo_status), ("cscv_status", cscv_status)):
        if value and value not in ("pass",):
            failures.append(f"{label}={value}")
    return passed, failures


def build_statistical_robustness_gate_receipt(
    *,
    manifest: Mapping[str, Any],
    promoted_row: Mapping[str, Any],
    allow_partial: bool = False,
) -> dict[str, Any]:
    """Gate 6 — statistical/Monte Carlo gauntlet via robustness_bridge producers."""
    candidate_id = str(manifest["candidate_id"])
    required_checks = list(_STATISTICAL_REQUIRED_CHECKS)
    req_count = len(required_checks)
    robustness_input = _robustness_input_from_promoted_row(promoted_row)
    if robustness_input:
        evidence = compute_robustness_evidence(robustness_input, candidate_id=candidate_id)
        p_values = robustness_input.get("p_values")
        evidence["holm_stepdown_or_not_run"] = _holm_bh_evidence(p_values, method="holm")
        if not evidence.get("holm_bh_or_not_run"):
            evidence["holm_bh_or_not_run"] = _holm_bh_evidence(p_values, method="bh")
    else:
        evidence = _statistical_evidence_from_row(promoted_row)
        p_values = promoted_row.get("p_values")
        if p_values and not evidence.get("holm_stepdown_or_not_run"):
            evidence["holm_stepdown_or_not_run"] = _holm_bh_evidence(p_values, method="holm")
    has_any_input = _statistical_row_has_input(promoted_row)
    if not has_any_input:
        return build_gate_receipt(
            gate_id=GATE_STATISTICAL,
            gate_version=STATISTICAL_GATE_VERSION,
            candidate_id=candidate_id,
            feature_recipe_hash=str(manifest["feature_recipe_hash"]),
            manifest_hash=str(manifest["manifest_hash"]),
            status="NOT_RUN",
            required_checks=required_checks,
            required_check_count=req_count,
            passed_check_count=0,
            failed_check_count=0,
            missing_check_count=req_count,
            authority_refs=["docs/project/ROBUSTNESS_TESTING_SPEC.md"],
            output_artifacts=[f"gates/{candidate_id}/statistical_robustness_gate.json"],
            failure_reasons=["statistical_robustness_not_run"],
        )
    passed_checks, failure_reasons = _evaluate_statistical_checks(
        evidence,
        allow_partial=allow_partial,
    )
    unique_passed = sorted(set(passed_checks))
    status = "PASS" if len(passed_checks) == req_count and not failure_reasons else "REJECT"
    if not allow_partial and any(
        str(evidence.get(key) or "").lower() == "not_run"
        for key in ("dsr_status", "pbo_status", "cscv_status")
    ):
        status = "REJECT"
        failure_reasons.append("partial_robustness_not_run")
    passed_count = req_count if status == "PASS" else len(unique_passed)
    if status == "PASS":
        failed_check_count = 0
        missing_check_count = 0
    else:
        failed_check_count = max(1, req_count - passed_count)
        missing_check_count = max(0, req_count - passed_count - failed_check_count)
    receipt = build_gate_receipt(
        gate_id=GATE_STATISTICAL,
        gate_version=STATISTICAL_GATE_VERSION,
        candidate_id=candidate_id,
        feature_recipe_hash=str(manifest["feature_recipe_hash"]),
        manifest_hash=str(manifest["manifest_hash"]),
        status=status,
        required_checks=required_checks,
        required_check_count=req_count,
        passed_check_count=passed_count,
        failed_check_count=failed_check_count,
        missing_check_count=missing_check_count,
        authority_refs=["docs/project/ROBUSTNESS_TESTING_SPEC.md"],
        output_artifacts=[f"gates/{candidate_id}/statistical_robustness_gate.json"],
        failure_reasons=sorted(set(failure_reasons)),
    )
    receipt["allow_partial"] = allow_partial
    receipt["robustness_artifact_staleness"] = evidence.get("robustness_artifact_staleness")
    receipt["bootstrap_ci"] = evidence.get("bootstrap_ci_or_not_run")
    receipt["deflated_sharpe_ratio"] = evidence.get("dsr_or_not_run")
    receipt["cscv_pbo"] = evidence.get("pbo_or_not_run")
    receipt["holm_stepdown"] = evidence.get("holm_stepdown_or_not_run")
    receipt["holm_bh"] = evidence.get("holm_bh_or_not_run")
    receipt["fee_stress"] = evidence.get("fee_stress_or_not_run")
    receipt["slippage_stress"] = evidence.get("slippage_stress_or_not_run")
    receipt["latency_stress"] = evidence.get("latency_stress_or_not_run")
    receipt["null_strategy_battery"] = evidence.get("null_battery_or_not_run")
    receipt["planted_alpha_control"] = evidence.get("planted_alpha_or_not_run")
    receipt["adversarial_perturbation"] = evidence.get("adversarial_or_not_run")
    receipt["parameter_perturbation"] = evidence.get("parameter_perturbation_or_not_run")
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


def build_manifest_gate_receipt(
    *,
    manifest: Mapping[str, Any],
    frozen_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Gate 1 — frozen candidate manifest with immutability hash enforcement."""
    candidate_id = str(manifest.get("candidate_id") or "")
    feature_recipe_hash = str(manifest.get("feature_recipe_hash") or "")
    manifest_hash = str(manifest.get("manifest_hash") or "")
    required_checks = [
        "manifest_schema",
        "manifest_hash",
        "feature_recipe_hash",
        "manifest_immutability",
    ]
    req_count = len(required_checks)
    integrity_errors = verify_frozen_manifest_integrity(manifest)
    failure_reasons = list(integrity_errors)
    status = "PASS" if not failure_reasons else "REJECT"
    if not manifest.get("manifest_schema"):
        status = "BLOCKED"
    passed_count = req_count if status == "PASS" else 0
    receipt = build_gate_receipt(
        gate_id=GATE_MANIFEST,
        gate_version=MANIFEST_GATE_VERSION,
        candidate_id=candidate_id,
        feature_recipe_hash=feature_recipe_hash,
        manifest_hash=manifest_hash,
        status=status,
        required_checks=required_checks,
        required_check_count=req_count,
        passed_check_count=passed_count,
        failed_check_count=0 if status == "PASS" else max(1, req_count),
        missing_check_count=0,
        authority_refs=[
            "packages/research_pipeline/candidate_manifest.py",
            "packages/research_pipeline/feature_recipe.py",
        ],
        input_artifacts=[str(frozen_manifest_path)] if frozen_manifest_path else [],
        output_artifacts=[f"gates/{candidate_id}/manifest_gate.json"],
        failure_reasons=failure_reasons,
    )
    receipt["manifest_schema"] = manifest.get("manifest_schema")
    receipt["frozen_at_utc"] = manifest.get("frozen_at_utc")
    return receipt


_PASSING_HFT_CERTIFICATION = frozenset(
    {"full_fidelity_declared", "scheduled_event_replay_not_full_feature_plane"}
)
_FAILING_HFT_CERTIFICATION = frozenset(
    {
        "accelerated_not_certifying",
        "fail",
        "integration_smoke_not_production",
        "missing_native_hot_path_evidence",
    }
)


def _hft_replay_parity_failures(
    *,
    manifest: Mapping[str, Any],
    replay: Mapping[str, Any],
    screening_artifact_hash: str,
    robustness_artifact_hash: str,
) -> list[str]:
    """Compare replay artifact identity fields to frozen manifest and upstream hashes."""
    failures: list[str] = []
    candidate_id = str(manifest.get("candidate_id") or "")
    feature_recipe_hash = str(manifest.get("feature_recipe_hash") or "")
    manifest_hash = str(manifest.get("manifest_hash") or "")

    replay_cid = str(replay.get("candidate_id") or "")
    if not replay_cid:
        failures.append("candidate_id_hash_parity_missing")
    elif replay_cid != candidate_id:
        failures.append("candidate_id_hash_parity_violation")

    replay_manifest = str(replay.get("manifest_hash") or "")
    if not replay_manifest:
        failures.append("manifest_hash_parity_missing")
    elif replay_manifest != manifest_hash:
        failures.append("manifest_hash_parity_violation")

    replay_recipe = str(replay.get("feature_recipe_hash") or "")
    if not replay_recipe:
        failures.append("feature_recipe_hash_parity_missing")
    elif replay_recipe != feature_recipe_hash:
        failures.append("feature_recipe_hash_parity_violation")

    if screening_artifact_hash:
        replay_screen = str(
            replay.get("screening_artifact_hash")
            or replay.get("upstream_screening_artifact_hash")
            or ""
        )
        if not replay_screen:
            failures.append("screening_artifact_hash_parity_missing")
        elif replay_screen != screening_artifact_hash:
            failures.append("screening_artifact_hash_parity_violation")

    if robustness_artifact_hash:
        replay_rob = str(replay.get("robustness_artifact_hash") or "")
        if not replay_rob:
            failures.append("robustness_artifact_hash_parity_missing")
        elif replay_rob != robustness_artifact_hash:
            failures.append("robustness_artifact_hash_parity_violation")

    return failures


def build_hftbacktest_gate_receipt(
    *,
    manifest: Mapping[str, Any],
    scenario_results: Sequence[Mapping[str, Any]] | None = None,
    screening_path: Path | None = None,
    screening_artifact_hash: str = "",
    robustness_artifact_hash: str = "",
    skipped_reason: str | None = None,
    accelerated_mode: bool = False,
) -> dict[str, Any]:
    """Gate 7 — per-candidate HftBacktest realism with hash parity."""
    candidate_id = str(manifest["candidate_id"])
    feature_recipe_hash = str(manifest["feature_recipe_hash"])
    manifest_hash = str(manifest["manifest_hash"])
    required_checks = [
        "candidate_id_hash_parity",
        "feature_recipe_hash_parity",
        "manifest_hash_parity",
        "screening_artifact_hash_parity",
        "robustness_artifact_hash_parity",
        "hft_replay_completed",
        "certifying_mode_not_accelerated",
    ]
    req_count = len(required_checks)
    failure_reasons: list[str] = []

    if skipped_reason:
        return build_gate_receipt(
            gate_id=GATE_HFT,
            gate_version=HFT_GATE_VERSION,
            candidate_id=candidate_id,
            feature_recipe_hash=feature_recipe_hash,
            manifest_hash=manifest_hash,
            status="NOT_RUN",
            required_checks=required_checks,
            required_check_count=req_count,
            passed_check_count=0,
            failed_check_count=0,
            missing_check_count=req_count,
            authority_refs=["docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md"],
            input_artifacts=[str(screening_path)] if screening_path else [],
            output_artifacts=[f"gates/{candidate_id}/hftbacktest_gate.json"],
            failure_reasons=[skipped_reason],
        )

    results = list(scenario_results or [])
    if accelerated_mode:
        failure_reasons.append("accelerated_mode_not_certifying")

    for result in results:
        replay = dict(result.get("replay_result") or {})
        if str(result.get("status") or "") != "completed":
            failure_reasons.append(f"scenario_{result.get('scenario_id')}_status={result.get('status')}")
            continue
        if not replay:
            failure_reasons.append(f"scenario_{result.get('scenario_id')}_replay_result_empty")
            continue
        if replay.get("error"):
            failure_reasons.append(f"scenario_{result.get('scenario_id')}_replay_error")
            continue
        cert = str(replay.get("certification_status") or "")
        if not cert:
            failure_reasons.append(f"scenario_{result.get('scenario_id')}_certification_status_missing")
        elif cert in _FAILING_HFT_CERTIFICATION:
            failure_reasons.append(f"certification_status={cert}")
        elif cert not in _PASSING_HFT_CERTIFICATION:
            failure_reasons.append(f"certification_status_not_passing={cert}")
        failure_reasons.extend(
            _hft_replay_parity_failures(
                manifest=manifest,
                replay=replay,
                screening_artifact_hash=screening_artifact_hash,
                robustness_artifact_hash=robustness_artifact_hash,
            )
        )

    if not screening_artifact_hash:
        failure_reasons.append("screening_artifact_hash_missing")
    if not robustness_artifact_hash:
        failure_reasons.append("robustness_artifact_hash_missing")
    if not results:
        failure_reasons.append("hft_scenarios_not_run")

    status = "PASS" if not failure_reasons and results else "REJECT"
    if not results and not failure_reasons:
        status = "NOT_RUN"
        failure_reasons.append("hft_not_run")

    passed_count = req_count if status == "PASS" else 0
    receipt = build_gate_receipt(
        gate_id=GATE_HFT,
        gate_version=HFT_GATE_VERSION,
        candidate_id=candidate_id,
        feature_recipe_hash=feature_recipe_hash,
        manifest_hash=manifest_hash,
        status=status,
        required_checks=required_checks,
        required_check_count=req_count,
        passed_check_count=passed_count,
        failed_check_count=0 if status == "PASS" else max(1, req_count),
        missing_check_count=req_count if status == "NOT_RUN" else 0,
        authority_refs=[
            "docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md",
            "vendor/hftbacktest/VENDOR.lock",
        ],
        input_artifacts=[str(screening_path)] if screening_path else [],
        input_hashes={
            "screening_artifact_hash": screening_artifact_hash,
            "robustness_artifact_hash": robustness_artifact_hash,
            "manifest_hash": manifest_hash,
            "feature_recipe_hash": feature_recipe_hash,
        },
        output_artifacts=[f"gates/{candidate_id}/hftbacktest_gate.json"],
        failure_reasons=sorted(set(failure_reasons)),
    )
    receipt["scenario_count"] = len(results)
    receipt["accelerated_mode"] = accelerated_mode
    receipt["screening_artifact_hash"] = screening_artifact_hash
    receipt["robustness_artifact_hash"] = robustness_artifact_hash
    return receipt


def emit_candidate_gate_receipts(
    *,
    gen_dir: Path,
    manifest: Mapping[str, Any],
    ontology_receipt: dict[str, Any] | None = None,
    manifest_receipt: dict[str, Any] | None = None,
    vectorbt_receipt: dict[str, Any] | None = None,
    surface_receipt: dict[str, Any] | None = None,
    regular_wf_receipt: dict[str, Any] | None = None,
    wfc_receipt: dict[str, Any] | None = None,
    statistical_receipt: dict[str, Any] | None = None,
    hft_receipt: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Write gate receipt JSON files under generation_<N>/gates/<candidate_id>/."""
    candidate_id = str(manifest["candidate_id"])
    paths: dict[str, Path] = {}
    for gate_id, receipt in (
        (GATE_ONTOLOGY, ontology_receipt),
        (GATE_MANIFEST, manifest_receipt),
        (GATE_VECTORBT, vectorbt_receipt),
        (GATE_SURFACE, surface_receipt),
        (GATE_REGULAR_WF, regular_wf_receipt),
        (GATE_WFC, wfc_receipt),
        (GATE_STATISTICAL, statistical_receipt),
        (GATE_HFT, hft_receipt),
    ):
        if receipt is None:
            continue
        path = gate_receipt_path(gen_dir, candidate_id, gate_id)
        write_gate_receipt(path, receipt)
        paths[gate_id] = path
    return paths
