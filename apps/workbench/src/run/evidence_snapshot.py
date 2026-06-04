"""Normalized run evidence for Workbench tabs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from workbench.src.artifacts.paths import workbench_runs_dir_for


CoverageStatus = Literal[
    "OBSERVED",
    "OBSERVED_DIAGNOSTIC_ONLY",
    "PRESENT_NOT_WIRED",
    "BLOCKING",
    "MISSING",
    "STALE",
    "CONFIGURED_NOT_OBSERVED",
    "NOT_CONFIGURED",
]

_VALID_COVERAGE_STATUSES = {
    "OBSERVED",
    "OBSERVED_DIAGNOSTIC_ONLY",
    "PRESENT_NOT_WIRED",
    "BLOCKING",
    "MISSING",
    "STALE",
    "CONFIGURED_NOT_OBSERVED",
    "NOT_CONFIGURED",
}

_HFT_REPLAY_CLASSES = {"FULL_EXECUTION", "L3_VALIDATED"}
_EXECUTION_REALISM_CLASSES = {"FULL_EXECUTION", "L3_VALIDATED"}


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


@dataclass
class RunEvidenceSnapshot:
    source: str
    run_id: str = ""
    state: str = "idle"
    current_stage: str = ""
    started_at: str = ""
    finished_at: str = ""
    root: str = ""
    stages: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    registry: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    backtest: dict[str, Any] = field(default_factory=dict)
    latency: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    robustness: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] = field(default_factory=dict)
    reports: dict[str, Any] = field(default_factory=dict)
    after_action: dict[str, Any] = field(default_factory=dict)
    relationships: dict[str, Any] = field(default_factory=dict)
    self_learning_loop: dict[str, Any] = field(default_factory=dict)
    system: dict[str, Any] = field(default_factory=dict)

    @property
    def has_run(self) -> bool:
        return bool(self.run_id)


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


def _latest_dir_with(path: Path, filename: str) -> Path | None:
    if not path.is_dir():
        return None
    candidates = [p for p in path.iterdir() if p.is_dir() and (p / filename).is_file()]
    return max(candidates, key=_mtime) if candidates else None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _crypto_reports(repo: Path, run_dir: Path | None = None) -> list[dict[str, Any]]:
    if run_dir is not None:
        run_reports = run_dir / "smoke_reports"
        reports: list[dict[str, Any]] = []
        if run_reports.is_dir():
            for path in sorted(run_reports.glob("*.json"), key=lambda p: _mtime(p), reverse=True):
                payload = read_json(path)
                if payload:
                    payload["_path"] = str(path)
                    reports.append(payload)
        return reports
    root = repo / "research_cards" / "crypto"
    reports: list[dict[str, Any]] = []
    if not root.is_dir():
        return reports
    for path in sorted(root.glob("*/smoke_report.json"), key=lambda p: _mtime(p), reverse=True):
        payload = read_json(path)
        if payload:
            payload["_path"] = str(path)
            reports.append(payload)
    return reports


def _crypto_validation_reports(repo: Path, run_dir: Path | None = None) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if run_dir is not None:
        run_reports = run_dir / "validation_reports"
        if run_reports.is_dir():
            for path in sorted(run_reports.glob("*.json"), key=lambda p: _mtime(p), reverse=True):
                payload = read_json(path)
                if payload:
                    payload["_path"] = str(path)
                    reports.append(payload)
        return reports
    root = repo / "research_cards" / "crypto"
    if not root.is_dir():
        return reports
    for path in sorted(root.glob("*/validation_report.json"), key=lambda p: _mtime(p), reverse=True):
        payload = read_json(path)
        if payload:
            payload["_path"] = str(path)
            reports.append(payload)
    return reports


def _crypto_robustness_summary(run_dir: Path | None = None) -> dict[str, Any]:
    if run_dir is None:
        return {}
    return read_json(run_dir / "robustness_summary.json")


def _crypto_vectorbt_summary(run_dir: Path | None = None) -> dict[str, Any]:
    if run_dir is None:
        return {}
    return read_json(run_dir / "vectorbt_summary.json")


def _provider_status(repo: Path) -> dict[str, Any]:
    try:
        from data_layer.llm import openai_compatible_client as llm_client

        openai_configured = llm_client.llm_available()
        aar_model = llm_client.DEFAULT_AAR_MODEL
        reasoning_effort = llm_client.DEFAULT_REASONING_EFFORT
        base_url = llm_client.DEFAULT_BASE_URL
    except Exception:
        openai_configured = bool(os.environ.get("HFT3_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        aar_model = os.environ.get("HFT3_AAR_LLM_MODEL") or os.environ.get("HFT3_LLM_MODEL") or ""
        reasoning_effort = os.environ.get("HFT3_LLM_REASONING_EFFORT", "xhigh").lower()
        base_url = os.environ.get("HFT3_LLM_BASE_URL", "https://api.openai.com/v1")
    alpha_path = repo / "vendor" / "alphageometry"
    openfoundry_path = repo / "vendor" / "openfoundry"
    return {
        "alpha_geometry": {
            "present": alpha_path.is_dir(),
            "role": "symbolic/reference boundary",
            "path": str(alpha_path),
        },
        "openfoundry": {
            "present": openfoundry_path.is_dir(),
            "role": "vendored ontology/reference pack",
            "path": str(openfoundry_path),
        },
        "openai_compatible_llm": {
            "configured": openai_configured,
            "model": aar_model,
            "reasoning_effort": reasoning_effort,
            "model_target": f"{aar_model} {reasoning_effort}".strip(),
            "base_url": base_url,
            "secret_exposed": False,
            "role": "GPT-5.5 extra-high advisory after-action and analyst lane; no promotion authority",
            "runtime_access": "available" if openai_configured else "not_callable_from_workbench",
            "transport_note": (
                "The Workbench does not load OpenAI API auth for this lane. "
                "A ChatGPT Pro/browser session is operator access and is not exposed as a backend callable runtime."
            ),
        },
        "google_gemini": {
            "configured": False,
            "role": "not wired in this repo",
            "reason": "No Gemini/Google provider client is configured; DeepMind boundary is represented by vendored AlphaGeometry reference behavior.",
        },
    }


def _crypto_after_action(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None or not run_dir.is_dir():
        return {}
    meta = read_json(run_dir / "after_action_meta.json")
    response = read_json(run_dir / "after_action_response.json")
    symbolic = read_json(run_dir / "after_action_symbolic.json")
    packet = read_json(run_dir / "after_action_packet.json")
    kg_slice = read_json(run_dir / "kg_slice.json")
    report_path = run_dir / "after_action_report.md"
    paths = {
        "diagnostics": str(run_dir / "diagnostics.json") if (run_dir / "diagnostics.json").is_file() else "",
        "manifest": str(run_dir / "manifest.json") if (run_dir / "manifest.json").is_file() else "",
        "config": str(run_dir / "config.yaml") if (run_dir / "config.yaml").is_file() else "",
        "packet": str(run_dir / "after_action_packet.json") if (run_dir / "after_action_packet.json").is_file() else "",
        "symbolic": str(run_dir / "after_action_symbolic.json") if (run_dir / "after_action_symbolic.json").is_file() else "",
        "kg_slice": str(run_dir / "kg_slice.json") if (run_dir / "kg_slice.json").is_file() else "",
        "meta": str(run_dir / "after_action_meta.json") if (run_dir / "after_action_meta.json").is_file() else "",
        "response": str(run_dir / "after_action_response.json") if (run_dir / "after_action_response.json").is_file() else "",
        "report": str(report_path) if report_path.is_file() else "",
    }
    skip_reasons = list(meta.get("skip_reasons") or response.get("skip_reasons") or packet.get("skip_reasons") or [])
    llm_status = str(meta.get("llm_status") or response.get("llm_status") or "missing")
    return {
        "paths": {k: v for k, v in paths.items() if v},
        "llm_status": llm_status,
        "llm_model": meta.get("llm_model") or response.get("llm_model"),
        "symbolic_passed": meta.get("symbolic_passed", symbolic.get("passed")),
        "report_written": bool(meta.get("report_written") or report_path.is_file()),
        "response_written": bool(meta.get("response_written") or (run_dir / "after_action_response.json").is_file()),
        "required": bool(meta.get("required", True)),
        "gate_status": str(meta.get("gate_status") or ("PASS" if llm_status == "ok" else "FAIL")),
        "passed": bool(meta.get("passed", llm_status == "ok")),
        "blocking_reason": str(meta.get("blocking_reason") or ""),
        "skip_reasons": skip_reasons,
        "packet": packet,
        "symbolic": symbolic,
        "kg_slice": kg_slice,
        "response": response,
        "meta": meta,
    }


def _crypto_relationships(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None or not run_dir.is_dir():
        return {}
    summary = read_json(run_dir / "relationship_summary.json")
    payload = read_json(run_dir / "relationship_candidates.json")
    candidates = payload.get("candidates") or []
    return {
        "summary": summary,
        "candidates": candidates,
        "candidate_count": summary.get("candidate_count", len(candidates)),
        "validated_count": summary.get(
            "validated_count",
            sum(1 for candidate in candidates if candidate.get("status") == "validated"),
        ),
        "rejected_count": summary.get(
            "rejected_count",
            sum(1 for candidate in candidates if candidate.get("status") == "rejected"),
        ),
        "kg_write_status": summary.get("kg_write_status", "not_attempted"),
        "openfoundry_write_status": summary.get("openfoundry_write_status", "not_attempted"),
        "promotion_authority": bool(summary.get("promotion_authority", False)),
        "paths": {
            "relationship_candidates": str(run_dir / "relationship_candidates.json")
            if (run_dir / "relationship_candidates.json").is_file()
            else "",
            "relationship_summary": str(run_dir / "relationship_summary.json")
            if (run_dir / "relationship_summary.json").is_file()
            else "",
        },
    }


def _crypto_robustness_explanation(
    robustness_summary: dict[str, Any],
    smoke_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    pack = robustness_summary.get("robustness_pack") or {}
    checks = list(pack.get("checks") or [])
    failed_required: list[str] = []
    pending_required: list[str] = []
    passed_required: list[str] = []
    for check in checks:
        name = str(check.get("name") or "")
        if not name:
            continue
        required = bool(check.get("required", True))
        if not required:
            continue
        status = str(check.get("status") or "").upper()
        if status == "FAIL" or check.get("passed") is False:
            failed_required.append(name)
        elif status in {"PENDING", "BLOCKING", "MISSING"}:
            pending_required.append(name)
        elif status == "PASS" or check.get("passed") is True:
            passed_required.append(name)
    for name in pack.get("failed") or []:
        text = str(name)
        if text and text not in failed_required:
            failed_required.append(text)
    for name in pack.get("pending") or []:
        text = str(name)
        if text and text not in pending_required and text not in failed_required:
            pending_required.append(text)
    blocking_gates = list(robustness_summary.get("blocking_gates") or [])
    for gate in blocking_gates:
        for name in gate.get("failed") or []:
            text = str(name)
            if text and text not in failed_required:
                failed_required.append(text)
        for name in gate.get("pending") or []:
            text = str(name)
            if text and text not in pending_required and text not in failed_required:
                pending_required.append(text)
    aggregate = str(robustness_summary.get("status") or "UNKNOWN").upper()
    smoke_passes = sum(1 for row in smoke_rows if str(row.get("pass_fail") or "").lower() == "pass")
    if failed_required:
        short = (
            f"Aggregate {aggregate} because {len(failed_required)} required robustness check(s) failed: "
            + ", ".join(failed_required[:5])
            + (", ..." if len(failed_required) > 5 else "")
            + ". Smoke pass is only a prerequisite diagnostic and cannot override replay robustness."
        )
    elif pending_required:
        short = (
            f"Aggregate {aggregate} because {len(pending_required)} required robustness check(s) are pending or missing. "
            "The run is not robustness-clean until every required replay gate is observed."
        )
    elif aggregate in {"PASS", "OBSERVED"}:
        short = "Required robustness evidence is observed and no required replay robustness checks are failing."
    else:
        short = str(robustness_summary.get("reason") or "Robustness evidence is not complete for this selected run.")
    return {
        "aggregate_status": aggregate,
        "operator_explanation": short,
        "smoke_pass_count": smoke_passes,
        "smoke_pass_is_robustness_pass": False,
        "required_pass_count": len(passed_required),
        "required_fail_count": len(failed_required),
        "required_pending_count": len(pending_required),
        "failed_required_checks": failed_required,
        "pending_required_checks": pending_required,
        "passed_required_checks": passed_required,
        "blocking_gates": blocking_gates,
    }


def _crypto_self_learning_loop(
    status: dict[str, Any],
    after_action: dict[str, Any],
    relationships: dict[str, Any],
    robustness_explanation: dict[str, Any],
) -> dict[str, Any]:
    stages_by_name = {str(stage.get("name") or ""): stage for stage in status.get("stages") or []}

    def stage_status(name: str) -> str:
        return str((stages_by_name.get(name) or {}).get("status") or "missing")

    llm_status = str(after_action.get("llm_status") or "missing")
    relationship_count = int(relationships.get("candidate_count") or 0)
    stages = [
        {
            "step": "smoke",
            "status": stage_status("walk_forward_smokes"),
            "meaning": "candidate discovery, PIT features, purged smoke/OOS diagnostics",
        },
        {
            "step": "VectorBT",
            "status": stage_status("vectorbt_filter"),
            "meaning": "OOS signal tape is converted into a replayable pre-filter, with leakage controls kept upstream",
        },
        {
            "step": "HFT replay",
            "status": stage_status("hft_replay_validation"),
            "meaning": "execution realism evidence from crypto replay validation",
        },
        {
            "step": "robustness",
            "status": robustness_explanation.get("aggregate_status") or stage_status("robustness_evidence"),
            "meaning": robustness_explanation.get("operator_explanation") or "required robustness evidence",
        },
        {
            "step": "decision",
            "status": str((status.get("decision") or {}).get("action") or stage_status("decision_gate")),
            "meaning": str((status.get("decision") or {}).get("reason") or "evidence gate decision"),
        },
        {
            "step": "after-action",
            "status": llm_status,
            "meaning": "after-action packet, symbolic check, KG slice, and advisory LLM report",
        },
        {
            "step": "relationship review",
            "status": f"{relationship_count} candidate(s)",
            "meaning": "AlphaGeometry/OpenFoundry symbolic review candidates only; no writes or promotion authority",
        },
        {
            "step": "LLM status",
            "status": llm_status,
            "meaning": "advisory report only; candidate promotion remains data-gate driven",
        },
    ]
    return {
        "stages": stages,
        "llm_status": llm_status,
        "llm_can_promote": False,
        "relationship_review_only": True,
    }


def _validation_class(report: dict[str, Any]) -> str:
    return str(
        report.get("execution_classification")
        or (report.get("result") or {}).get("execution_classification")
        or ""
    ).upper()


def _row_candidate_id(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or "")


def _vectorbt_source_candidate_ids(vectorbt_summary: dict[str, Any], reports: list[dict[str, Any]]) -> set[str]:
    explicit = {
        str(value)
        for value in (vectorbt_summary.get("promoted_source_candidate_ids") or [])
        if str(value)
    }
    if explicit:
        return explicit
    return {
        _row_candidate_id(report)
        for report in reports
        if _row_candidate_id(report) and ("vectorbt_results" in report or "vectorbt_run_id" in report)
    }


def _validation_passed_candidate_ids(validation_reports: list[dict[str, Any]]) -> set[str]:
    return {
        _row_candidate_id(report)
        for report in validation_reports
        if _row_candidate_id(report)
        and not ((report.get("result") or {}).get("error"))
        and bool(report.get("npz_path"))
        and _validation_class(report) in _HFT_REPLAY_CLASSES
    }


def _robustness_trade_sample_candidate_ids(robustness_summary: dict[str, Any]) -> set[str]:
    explicit = {
        str(value)
        for value in (robustness_summary.get("trade_sample_candidate_ids") or [])
        if str(value)
    }
    if explicit:
        return explicit
    parsed: set[str] = set()
    for source in robustness_summary.get("trade_sample_sources") or []:
        candidate_id = str(source).split(":", 1)[0]
        if candidate_id:
            parsed.add(candidate_id)
    return parsed


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _positive_proxy_pnl_count(rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        value = _finite_number(row.get("proxy_net_pnl_bps"))
        if value is not None and value > 0.0:
            count += 1
    return count


def _primary_run(report: dict[str, Any]) -> dict[str, Any]:
    runs = report.get("runs") or {}
    return runs.get("with_btc_node") or runs.get("without_btc_node") or {}


def _coverage_row(
    stage: str,
    status: CoverageStatus | str,
    evidence: str,
    reason: str,
    *,
    authority: str = "",
    role: str = "required_evidence",
    artifact_contract: str = "",
) -> dict[str, Any]:
    if status not in _VALID_COVERAGE_STATUSES:
        raise ValueError(f"unknown coverage status for {stage}: {status}")
    return {
        "stage": stage,
        "status": status,
        "role": role,
        "evidence": evidence,
        "artifact_contract": artifact_contract,
        "reason": reason,
        "authority": authority,
    }


def _crypto_pipeline_coverage(
    repo: Path,
    reports: list[dict[str, Any]],
    validation_reports: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    edge_packets: dict[str, Any],
    robustness_summary: dict[str, Any] | None = None,
    vectorbt_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    vectorbt_present = (repo / "packages" / "backtest_pipeline" / "src" / "vectorbt_adapter.py").is_file()
    vectorbt_summary = vectorbt_summary or {}
    vectorbt_candidate_ids = _vectorbt_source_candidate_ids(vectorbt_summary, reports)
    vectorbt_observed = bool(vectorbt_candidate_ids) and (
        bool(vectorbt_summary.get("observed"))
        or any("vectorbt_results" in report or "vectorbt_run_id" in report for report in reports)
    )
    vectorbt_blocking = bool(vectorbt_summary) and not vectorbt_observed
    vectorbt_reason = str(
        vectorbt_summary.get("reason") or "No VectorBT promotion artifact is attached to this selected run."
    )
    vectorbt_upstream_unproven = not vectorbt_observed
    hft_paths = [
        repo / "packages" / "crypto_lane" / "src" / "validation" / "crypto_validation_workflow.py",
        repo / "packages" / "crypto_lane" / "src" / "validation" / "crypto_execution_validator.py",
        repo / "packages" / "backtest_pipeline" / "src" / "crypto_hft_builder.py",
    ]
    robustness_pack_present = (repo / "apps" / "workbench" / "src" / "robustness" / "pack.py").is_file()
    double_wf_present = (repo / "apps" / "workbench" / "src" / "robustness" / "wfc" / "double_wf.py").is_file()
    hft_present = all(path.is_file() for path in hft_paths)
    hft_smoke_candidate_ids = {
        _row_candidate_id(report)
        for report in reports
        if _row_candidate_id(report)
        and any(
            key in report
            for key in (
                "execution_validation",
                "hftbacktest_replay",
                "replay_metrics",
                "execution_classification",
                "validation_path",
            )
        )
    }
    validation_candidate_ids = _validation_passed_candidate_ids(validation_reports)
    hft_candidate_ids = hft_smoke_candidate_ids | validation_candidate_ids
    observed_validation_classes = {
        _validation_class(report)
        for report in validation_reports
        if _row_candidate_id(report) in validation_candidate_ids
    }
    hft_observed_from_validation = bool(validation_candidate_ids)
    hft_blocked_from_validation = bool(validation_reports) and not hft_observed_from_validation
    hft_observed = bool(hft_candidate_ids & vectorbt_candidate_ids)
    execution_class_sufficient = bool(validation_candidate_ids & vectorbt_candidate_ids) and bool(
        observed_validation_classes & _EXECUTION_REALISM_CLASSES
    )
    purged_candidate_ids = {
        _row_candidate_id(report)
        for report in reports
        if _row_candidate_id(report)
        and bool(report.get("purged_cv_implemented"))
        and int((_primary_run(report).get("n_splits") or 0)) > 0
    } | {
        _row_candidate_id(row)
        for row in candidate_rows
        if _row_candidate_id(row) and int(row.get("purged_splits") or 0) > 0
    }
    holdout_candidate_ids = {
        _row_candidate_id(report)
        for report in reports
        if _row_candidate_id(report) and (report.get("holdout_gate") or {}).get("status")
    } | {
        _row_candidate_id(row)
        for row in candidate_rows
        if _row_candidate_id(row) and row.get("holdout_status")
    }
    negative_control_candidate_ids = {
        _row_candidate_id(report)
        for report in reports
        if _row_candidate_id(report) and report.get("negative_controls")
    } | {
        _row_candidate_id(row)
        for row in candidate_rows
        if _row_candidate_id(row) and row.get("negative_controls_ok") is not None
    }
    purged_observed = bool(purged_candidate_ids)
    holdout_observed = bool(holdout_candidate_ids)
    negative_controls_observed = bool(negative_control_candidate_ids)
    robustness_summary = robustness_summary or {}
    robustness_pack_evidence = robustness_summary.get("robustness_pack") or {}
    double_wf_evidence = robustness_summary.get("double_walk_forward") or {}
    robustness_pack_observed = bool(robustness_pack_evidence.get("observed")) or any(
        report.get("robustness_pack") or report.get("robustness_checks") for report in reports
    )
    double_wf_observed = bool(double_wf_evidence.get("observed")) or any(
        report.get("walk_forward_correlation") or report.get("double_wf") for report in reports
    )
    robustness_pack_blocking = bool(robustness_summary) and not robustness_pack_observed
    double_wf_blocking = bool(robustness_summary) and not double_wf_observed
    proxy_pnl_observed = any((report.get("research_pnl_proxy") or {}).get("summary") for report in reports) or any(
        row.get("proxy_net_pnl_bps") is not None for row in candidate_rows
    )
    ack_candidate_ids = {
        _row_candidate_id(row)
        for row in candidate_rows
        if _row_candidate_id(row) and bool(row.get("execution_ack_measured"))
    }
    ack_measured = bool(ack_candidate_ids)
    robustness_candidate_ids = _robustness_trade_sample_candidate_ids(robustness_summary)
    full_ready_candidate_ids = (
        purged_candidate_ids
        & holdout_candidate_ids
        & negative_control_candidate_ids
        & vectorbt_candidate_ids
        & validation_candidate_ids
        & robustness_candidate_ids
        & ack_candidate_ids
    )
    execution_realism_candidate_ids = vectorbt_candidate_ids & validation_candidate_ids & ack_candidate_ids

    edge_packet_status = "OBSERVED" if edge_packets.get("observed") else str(edge_packets.get("status") or "BLOCKING")
    rows = [
        _coverage_row(
            "candidate_discovery",
            "OBSERVED" if candidate_rows else "MISSING",
            f"{len(candidate_rows)} discovered candidate rows",
            "Candidates come from the tracked crypto registry.",
            authority="crypto registry",
            role="source_inventory",
            artifact_contract="packages/crypto_lane/config/candidates/*.yaml",
        ),
        _coverage_row(
            "vectorbt_filter",
            "OBSERVED"
            if vectorbt_observed
            else ("BLOCKING" if vectorbt_blocking else ("PRESENT_NOT_WIRED" if vectorbt_present else "MISSING")),
            "vectorbt run artifacts"
            if vectorbt_observed
            else "run-local vectorbt_summary.json"
            if vectorbt_blocking
            else "packages/backtest_pipeline/src/vectorbt_adapter.py",
            vectorbt_summary.get("reason")
            if vectorbt_blocking
            else "No VectorBT promotion/filter artifact is attached to this selected run."
            if not vectorbt_observed
            else "VectorBT filter evidence is attached to this run.",
            authority="backtest pipeline",
            role="required_prefilter",
            artifact_contract="run-local vectorbt_summary.json with promoted/rejected candidates or attached vectorbt_results",
        ),
        _coverage_row(
            "purged_walk_forward_oos",
            "OBSERVED" if purged_observed else "BLOCKING",
            "smoke_report.json purged split metrics",
            "Out-of-sample rows use purged walk-forward smoke evidence.",
            authority="crypto smoke runner",
            role="required_oos_gate",
            artifact_contract="selected-run smoke_reports/*.json; legacy research_cards/crypto/*/smoke_report.json only without a selected run",
        ),
        _coverage_row(
            "holdout_gate",
            "OBSERVED" if holdout_observed else "BLOCKING",
            "holdout_gate in smoke reports",
            "Holdout status is available for the selected run."
            if holdout_observed
            else "No holdout artifact is attached to the selected run.",
            authority="crypto smoke runner",
            role="required_oos_gate",
            artifact_contract="selected-run smoke_reports/*.json holdout_gate",
        ),
        _coverage_row(
            "negative_controls",
            "OBSERVED" if negative_controls_observed else "BLOCKING",
            "label-shuffle and feature-shift controls",
            "Leakage controls are observed for this run."
            if negative_controls_observed
            else "No negative-control artifact is attached to the selected run.",
            authority="crypto smoke runner",
            role="required_leakage_control",
            artifact_contract="selected-run smoke_reports/*.json negative_controls",
        ),
        _coverage_row(
            "robustness_pack",
            "OBSERVED"
            if robustness_pack_observed
            else ("BLOCKING" if robustness_pack_blocking else ("PRESENT_NOT_WIRED" if robustness_pack_present else "MISSING")),
            "apps/workbench/src/robustness/pack.py"
            if not robustness_pack_observed and not robustness_pack_blocking
            else "run-local robustness_summary.json"
            if robustness_pack_blocking
            else "robustness pack artifacts"
            if robustness_pack_observed
            else "robustness pack artifacts",
            robustness_pack_evidence.get("reason")
            if robustness_pack_blocking
            else "The selected crypto smoke run does not execute the full Workbench robustness pack."
            if not robustness_pack_observed and robustness_pack_present
            else (
                "Full robustness-pack evidence is attached to this run."
                if robustness_pack_observed
                else "Workbench robustness pack code is missing."
            ),
            authority="workbench robustness pack",
            role="required_robustness_gate",
            artifact_contract="robustness_pack or robustness_checks artifact",
        ),
        _coverage_row(
            "double_walk_forward_correlation",
            "OBSERVED"
            if double_wf_observed
            else ("BLOCKING" if double_wf_blocking else ("PRESENT_NOT_WIRED" if double_wf_present else "MISSING")),
            "apps/workbench/src/robustness/wfc/double_wf.py"
            if not double_wf_observed and not double_wf_blocking
            else "run-local robustness_summary.json"
            if double_wf_blocking
            else "walk-forward correlation artifacts"
            if double_wf_observed
            else "walk-forward correlation artifacts",
            double_wf_evidence.get("reason")
            if double_wf_blocking
            else "The selected crypto smoke run does not emit double walk-forward correlation evidence."
            if not double_wf_observed and double_wf_present
            else (
                "Double walk-forward correlation evidence is attached to this run."
                if double_wf_observed
                else "Double walk-forward correlator code is missing."
            ),
            authority="workbench WFC",
            role="required_robustness_gate",
            artifact_contract="walk_forward_correlation or double_wf artifact",
        ),
        _coverage_row(
            "research_pnl_proxy",
            "OBSERVED_DIAGNOSTIC_ONLY" if proxy_pnl_observed else "MISSING",
            "research_pnl_proxy summary and equity curve",
            "Diagnostic P&L is useful for triage, but it is not venue fills and not a promotion gate.",
            authority="crypto smoke runner",
            role="diagnostic_only",
            artifact_contract="selected-run smoke_reports/*.json research_pnl_proxy",
        ),
        _coverage_row(
            "hftbacktest_replay",
            "OBSERVED"
            if hft_observed
            else (
                "BLOCKING"
                if hft_blocked_from_validation or vectorbt_upstream_unproven
                else ("PRESENT_NOT_WIRED" if hft_present else "MISSING")
            ),
            "crypto_validation_workflow / crypto_execution_validator"
            if not hft_observed and not vectorbt_upstream_unproven
            else "run-local hft_validation_summary.json"
            if vectorbt_upstream_unproven
            else "hftbacktest replay metrics",
            "The repo has the crypto hftbacktest validation path, but the selected crypto smoke run does not execute or attach it."
            if not hft_observed and hft_present and not hft_blocked_from_validation and not vectorbt_upstream_unproven
            else f"Crypto execution replay is blocked by upstream VectorBT filter evidence: {vectorbt_reason}"
            if vectorbt_upstream_unproven
            else "Validation was attempted, but no L3/full execution replay evidence matched a VectorBT-promoted candidate."
            if hft_blocked_from_validation
            else (
                "Crypto execution replay metrics are attached to this run."
                if hft_observed
                else "Crypto hftbacktest validation code is missing."
            ),
            authority="crypto validation workflow",
            role="required_execution_replay",
            artifact_contract="selected-run validation_reports/*.json after VectorBT promotion; legacy research_cards/crypto/*/validation_report.json only without a selected run",
        ),
        _coverage_row(
            "execution_realism",
            "OBSERVED" if execution_realism_candidate_ids and hft_observed and execution_class_sufficient else "BLOCKING",
            "hftbacktest replay plus venue submit-to-ack pairs",
            f"Execution realism is blocked by upstream VectorBT filter evidence: {vectorbt_reason}"
            if vectorbt_upstream_unproven
            else "Execution realism is incomplete until one candidate has L3/full replay evidence and real venue submit-to-ack evidence."
            if not execution_class_sufficient
            else "Execution realism is incomplete until replay metrics and real venue submit-to-ack evidence are both observed.",
            authority="execution evidence",
            role="required_execution_gate",
            artifact_contract="L3_VALIDATED or FULL_EXECUTION validation_report.json plus venue submit-to-ack evidence",
        ),
        _coverage_row(
            "bitcoin_edge_packets",
            edge_packet_status,
            edge_packets.get("transport", "bitcoin state packet stream"),
            "Bitcoin node packets are market-state/PIT evidence; they do not replace venue execution ack evidence.",
            authority="bitcoin node edge transport",
            role="market_state_only",
            artifact_contract="runtime/crypto_edge/latest_packet.json and receiver_status.json",
        ),
        _coverage_row(
            "full_backtest_readiness",
            "OBSERVED"
            if full_ready_candidate_ids and robustness_pack_observed and double_wf_observed and edge_packets.get("observed")
            else "BLOCKING",
            "OOS + vectorBT + controls + robustness pack + double WF + hftbacktest replay + execution ack",
            "Complete readiness candidate(s): " + ", ".join(sorted(full_ready_candidate_ids))
            if full_ready_candidate_ids and robustness_pack_observed and double_wf_observed and edge_packets.get("observed")
            else "A complete full-backtest claim requires the same candidate to carry every upstream evidence layer.",
            authority="workbench monitor",
            role="aggregate_readiness_gate",
            artifact_contract="all required evidence layers observed in selected run",
        ),
    ]
    return rows


def _crypto_snapshot(repo: Path) -> RunEvidenceSnapshot:
    from crypto_lane.src.align.latency_profile import load_venue_profiles, node_profile_path, resolve_node_latency
    from crypto_lane.src.config_loader import load_hypotheses, load_manifest, load_universe
    from crypto_lane.src.config_loader import list_candidate_paths, list_backtest_config_paths
    from crypto_lane.src.ingest.edge_status import load_edge_packet_status
    from crypto_lane.src.ml.candidate_registry import discover_backtest_configs, discover_candidates
    from workbench.src.run.crypto_smoke_runner import latest_status_path

    status_path = latest_status_path(repo)
    status = read_json(status_path)
    run_dir = status_path.parent / str(status.get("run_id", ""))
    selected_run_dir = run_dir if run_dir.is_dir() else None
    reports = _crypto_reports(repo, run_dir if run_dir.is_dir() else None)
    validation_reports = _crypto_validation_reports(repo, run_dir if run_dir.is_dir() else None)
    robustness_summary = _crypto_robustness_summary(selected_run_dir)
    vectorbt_summary = _crypto_vectorbt_summary(selected_run_dir)
    after_action = _crypto_after_action(selected_run_dir)
    relationships = _crypto_relationships(selected_run_dir)
    candidates = discover_candidates()
    backtests = discover_backtest_configs()
    candidate_rows = []
    source_candidates = status.get("candidates") or []
    if not source_candidates:
        source_candidates = [
            {
                "candidate_id": r.get("candidate_id", ""),
                "hypothesis_id": r.get("hypothesis_id", ""),
                "status": "done",
                "pass_fail": r.get("pass_fail", ""),
                "holdout_status": (r.get("holdout_gate") or {}).get("status", ""),
                "negative_controls_ok": all(
                    bool((r.get("negative_controls") or {}).get(k))
                    for k in ("shuffled_degraded", "shifted_degraded")
                ),
                "order_ack_status": r.get("execution_ack_status", ""),
                "execution_ack_scope": r.get("execution_ack_scope", ""),
                "btc_node_evidence_scope": r.get("btc_node_evidence_scope", ""),
                "oos_ic": _primary_run(r).get("oos_ic_baseline_mean"),
                "n_rows": _primary_run(r).get("n_rows"),
                "n_folds": _primary_run(r).get("n_folds"),
                "purged_splits": _primary_run(r).get("n_splits"),
            }
            for r in reports
        ]
    for row in source_candidates:
        candidate_rows.append(dict(row))

    universe = load_universe()
    manifest = load_manifest()
    fixture_dir = repo / "packages" / "crypto_lane" / "fixtures"
    data_files = [
        {"path": str(path), "exists": path.is_file(), "bytes": path.stat().st_size if path.is_file() else 0}
        for path in (
            fixture_dir / "spot_perp_ticks.csv",
            fixture_dir / "deribit_surface.csv",
            fixture_dir / "mempool_snapshots.csv",
        )
    ]
    venue_profiles = {k: v.__dict__ for k, v in load_venue_profiles().items()}
    node_profile = resolve_node_latency().__dict__
    node_path = node_profile_path()
    edge_packets = load_edge_packet_status(repo)

    backtest_rows = []
    hft_validation_rows = []
    proxy_leaderboard = []
    equity_curves: dict[str, list[dict[str, Any]]] = {}
    holdout_stage_rows = []
    negative_control_rows = []
    robustness_rows = []
    feature_rows = []
    for report in reports:
        primary = _primary_run(report)
        proxy = report.get("research_pnl_proxy") or {}
        proxy_summary = proxy.get("summary") or {}
        candidate_id = report.get("candidate_id", "")
        backtest_rows.append({
            "candidate_id": candidate_id,
            "hypothesis_id": report.get("hypothesis_id", ""),
            "target": report.get("target", ""),
            "pass_fail": report.get("pass_fail", ""),
            "smoke_mode": report.get("smoke_mode"),
            "oos_ic": primary.get("oos_ic_baseline_mean"),
            "rows": primary.get("n_rows"),
            "folds": primary.get("n_folds"),
            "holdout": (report.get("holdout_gate") or {}).get("status", ""),
            "proxy_net_pnl_bps": proxy_summary.get("net_pnl_bps"),
            "proxy_trades": proxy_summary.get("num_trades"),
            "proxy_hit_rate": proxy_summary.get("hit_rate"),
            "proxy_profit_factor": proxy_summary.get("profit_factor"),
            "proxy_max_drawdown_bps": proxy_summary.get("max_drawdown_bps"),
            "proxy_sharpe": proxy_summary.get("sharpe_proxy"),
        })
        proxy_leaderboard.append({
            "candidate_id": candidate_id,
            "target": report.get("target", ""),
            "oos_ic": primary.get("oos_ic_baseline_mean"),
            "proxy_net_pnl_bps": proxy_summary.get("net_pnl_bps"),
            "proxy_trades": proxy_summary.get("num_trades"),
            "proxy_hit_rate": proxy_summary.get("hit_rate"),
            "proxy_profit_factor": proxy_summary.get("profit_factor"),
            "proxy_max_drawdown_bps": proxy_summary.get("max_drawdown_bps"),
            "proxy_sharpe": proxy_summary.get("sharpe_proxy"),
            "proxy_status": proxy.get("status", ""),
            "promotion_gate": proxy.get("promotion_gate"),
        })
        equity_curves[candidate_id] = list(proxy.get("equity_curve") or [])
        for stage_name, stage in ((report.get("holdout_gate") or {}).get("stages") or {}).items():
            holdout_stage_rows.append({
                "candidate_id": candidate_id,
                "stage": stage_name,
                "mode": stage.get("mode"),
                "ic": stage.get("ic"),
                "n_rows": stage.get("n_rows"),
                "status": stage.get("status"),
            })
        controls = report.get("negative_controls") or {}
        negative_control_rows.append({
            "candidate_id": candidate_id,
            "real_oos_ic": controls.get("real_oos_ic"),
            "shuffled_labels_ic": controls.get("shuffled_labels_ic"),
            "shifted_features_ic": controls.get("shifted_features_ic"),
            "shuffled_degraded": controls.get("shuffled_degraded"),
            "shifted_degraded": controls.get("shifted_degraded"),
        })
        robustness_rows.append({
            "candidate_id": candidate_id,
            "purged_cv": report.get("purged_cv_implemented"),
            "purged_splits": primary.get("n_splits"),
            "holdout": (report.get("holdout_gate") or {}).get("status", ""),
            "shuffled_degraded": (report.get("negative_controls") or {}).get("shuffled_degraded"),
            "shifted_degraded": (report.get("negative_controls") or {}).get("shifted_degraded"),
            "randomized_degraded": (report.get("negative_controls") or {}).get("randomized_degraded"),
        })
    for candidate in candidates:
        feature_rows.append({
            "candidate_id": candidate.get("candidate_id", ""),
            "hypothesis_id": candidate.get("hypothesis_id", ""),
            "target": candidate.get("target", ""),
            "features": ", ".join(candidate.get("features") or []),
            "btc_node_required": candidate.get("btc_node_required"),
            "ablation": candidate.get("ablation", {}),
        })
    for report in validation_reports:
        result = report.get("result") or {}
        hft_validation_rows.append({
            "candidate_id": report.get("candidate_id", ""),
            "model_id": report.get("model_id", ""),
            "classification": report.get("execution_classification", ""),
            "validation_path": str(report.get("validation_path", "")),
            "npz_path": report.get("npz_path", ""),
            "net_pnl": result.get("net_pnl"),
            "gross_pnl": result.get("gross_pnl"),
            "num_trades": result.get("num_trades"),
            "num_intents": result.get("num_intents"),
            "fill_rate": result.get("fill_rate"),
            "slippage_bps": result.get("slippage_bps"),
            "adverse_selection_cost": result.get("adverse_selection_cost"),
            "tail_loss": result.get("tail_loss"),
            "sharpe_ratio": result.get("sharpe_ratio"),
            "error": result.get("error", ""),
            "report_path": report.get("_path", ""),
        })

    decision = dict(status.get("decision") or {
        "action": "NO_RUN",
        "reason": "No crypto candidate loop status observed.",
        "top_smoke_candidate": "",
        "live_registry_ready": False,
    })
    decision_action = str(decision.get("action") or "").upper()
    blocking_gates = [
        gate
        for gate in (decision.get("blocking_gates") or [])
        if not (isinstance(gate, dict) and gate.get("gate") == "bitcoin_edge_packets")
    ]
    if after_action and not after_action.get("passed"):
        blocking_gates.append(
            {
                "gate": "after_action_gpt55_xhigh",
                "status": after_action.get("gate_status") or "FAIL",
                "reason": after_action.get("blocking_reason") or "GPT-5.5 xhigh after-action is required and did not pass.",
            }
        )
    if decision_action == "REJECT":
        decision["failed_gates"] = blocking_gates
    if not edge_packets.get("observed") and decision_action != "REJECT":
        blocking_gates.append(
            {
                "gate": "bitcoin_edge_packets",
                "status": edge_packets.get("status"),
                "reason": edge_packets.get("reason"),
            }
        )
    pipeline_coverage = _crypto_pipeline_coverage(
        repo,
        reports,
        validation_reports,
        candidate_rows,
        edge_packets,
        robustness_summary,
        vectorbt_summary,
    )
    run_smoke_reports = run_dir / "smoke_reports"
    legacy_smoke_reports = (repo / "research_cards" / "crypto").resolve()
    robustness_explanation = _crypto_robustness_explanation(robustness_summary, candidate_rows)
    self_learning_loop = _crypto_self_learning_loop(status, after_action, relationships, robustness_explanation)
    provider_status = _provider_status(repo)
    return RunEvidenceSnapshot(
        source="crypto_lane",
        run_id=str(status.get("run_id", "crypto_lane")),
        state=str(status.get("state", "idle")),
        current_stage=str(status.get("current_stage", "")),
        started_at=str(status.get("started_at", "")),
        finished_at=str(status.get("finished_at", "")),
        root=str(status_path.parent),
        stages=list(status.get("stages") or []),
        artifacts={
            "latest_status": str(status_path),
            "candidate_registry": str((repo / "packages" / "crypto_lane" / "config" / "candidates").resolve()),
            "smoke_reports": str(run_smoke_reports) if run_smoke_reports.is_dir() else "",
            "legacy_smoke_reports": str(legacy_smoke_reports) if legacy_smoke_reports.is_dir() else "",
            "robustness_summary": str(run_dir / "robustness_summary.json") if (run_dir / "robustness_summary.json").is_file() else "",
            "vectorbt_summary": str(run_dir / "vectorbt_summary.json") if (run_dir / "vectorbt_summary.json").is_file() else "",
            "after_action_meta": str(run_dir / "after_action_meta.json") if (run_dir / "after_action_meta.json").is_file() else "",
            "after_action_packet": str(run_dir / "after_action_packet.json") if (run_dir / "after_action_packet.json").is_file() else "",
            "after_action_symbolic": str(run_dir / "after_action_symbolic.json") if (run_dir / "after_action_symbolic.json").is_file() else "",
            "kg_slice": str(run_dir / "kg_slice.json") if (run_dir / "kg_slice.json").is_file() else "",
            "relationship_candidates": str(run_dir / "relationship_candidates.json") if (run_dir / "relationship_candidates.json").is_file() else "",
            "relationship_summary": str(run_dir / "relationship_summary.json") if (run_dir / "relationship_summary.json").is_file() else "",
        },
        registry={
            "hypotheses": [h.get("hypothesis_id", "") for h in load_hypotheses()],
            "candidates": [c.get("candidate_id", "") for c in candidates],
            "candidate_paths": [str(p) for p in list_candidate_paths()],
            "backtests": [b.get("config_id", "") for b in backtests],
            "backtest_paths": [str(p) for p in list_backtest_config_paths()],
            "manifest": manifest,
        },
        data={
            "universe": universe,
            "data_files": data_files,
            "missing": [f for f in data_files if not f["exists"]],
            "btc_node": {"profile": node_profile, "profile_path": str(node_path), "profile_exists": node_path.is_file()},
            "bitcoin_edge_packets": edge_packets,
        },
        backtest={
            "rows": backtest_rows,
            "reports": reports,
            "hft_validation_rows": hft_validation_rows,
            "proxy_leaderboard": sorted(
                proxy_leaderboard,
                key=lambda r: float(r.get("proxy_net_pnl_bps") or 0.0),
                reverse=True,
            ),
            "equity_curves": equity_curves,
            "holdout_stage_rows": holdout_stage_rows,
            "negative_control_rows": negative_control_rows,
            "vectorbt_summary": vectorbt_summary,
        },
        latency={
            "venue_profiles": venue_profiles,
            "node_profile": node_profile,
            "bitcoin_edge_packets": edge_packets,
            "edge_packet_history": edge_packets.get("packet_history", []),
            "execution_ack_rows": [
                {
                    "candidate_id": c.get("candidate_id", ""),
                    "scope": c.get("execution_ack_scope") or "crypto_venue_submit_ack",
                    "measured": bool(c.get("execution_ack_measured")),
                    "status": c.get("order_ack_status") or c.get("execution_ack_status", ""),
                    "btc_node_scope": c.get("btc_node_evidence_scope", ""),
                }
                for c in candidate_rows
            ],
        },
        diagnostics={
            "feature_rows": feature_rows,
            "feature_builders": manifest.get("feature_builders", []),
            "align_modules": manifest.get("align_modules", []),
            "edge_packet_schema": edge_packets.get("schema", []),
        },
        robustness={
            "rows": robustness_rows,
            "crypto_robustness_summary": robustness_summary,
            "robustness_pack": robustness_summary.get("robustness_pack", {}),
            "double_walk_forward": robustness_summary.get("double_walk_forward", {}),
            "pending": (robustness_summary.get("robustness_pack") or {}).get("pending", []),
            "failed": (robustness_summary.get("robustness_pack") or {}).get("failed", []),
            "explanation": robustness_explanation,
            "artifact_links": {
                "robustness_summary": str(run_dir / "robustness_summary.json")
                if (run_dir / "robustness_summary.json").is_file()
                else "",
                "replay_wf1_matrix": str(run_dir / "replay_wf1_matrix.json")
                if (run_dir / "replay_wf1_matrix.json").is_file()
                else "",
                "replay_wf2_matrix": str(run_dir / "replay_wf2_matrix.json")
                if (run_dir / "replay_wf2_matrix.json").is_file()
                else "",
                "walk_forward_correlation": str(run_dir / "walk_forward_correlation.json")
                if (run_dir / "walk_forward_correlation.json").is_file()
                else "",
            },
        },
        decision={
            **decision,
            "smoke_triage_order": status.get("smoke_triage_order", status.get("ranking", [])),
            "vectorbt_promoted_order": status.get("vectorbt_promoted_order", []),
            "smoke_pass_count": sum(1 for c in candidate_rows if str(c.get("pass_fail", "")).lower() == "pass"),
            "economic_diagnostic_pass_count": _positive_proxy_pnl_count(candidate_rows),
            "live_registry_ready": bool(decision.get("live_registry_ready")) and bool(edge_packets.get("observed")),
            "bitcoin_edge_packet_status": edge_packets.get("status"),
            "blocking_gates": blocking_gates,
        },
        reports={
            "smoke_reports": [r.get("_path", "") for r in reports],
            "validation_reports": [r.get("_path", "") for r in validation_reports],
            "robustness_summary": str(run_dir / "robustness_summary.json") if (run_dir / "robustness_summary.json").is_file() else "",
            "vectorbt_summary": str(run_dir / "vectorbt_summary.json") if (run_dir / "vectorbt_summary.json").is_file() else "",
            "after_action_report": (after_action.get("paths") or {}).get("report", ""),
            "after_action_meta": (after_action.get("paths") or {}).get("meta", ""),
            "after_action_packet": (after_action.get("paths") or {}).get("packet", ""),
            "after_action_symbolic": (after_action.get("paths") or {}).get("symbolic", ""),
            "kg_slice": (after_action.get("paths") or {}).get("kg_slice", ""),
            "relationship_candidates": (relationships.get("paths") or {}).get("relationship_candidates", ""),
            "relationship_summary": (relationships.get("paths") or {}).get("relationship_summary", ""),
        },
        after_action=after_action,
        relationships=relationships,
        self_learning_loop=self_learning_loop,
        system={
            "status": status,
            "manifest": manifest,
            "runtime_path": str(status_path),
            "bitcoin_edge_packets": edge_packets,
            "pipeline_coverage": pipeline_coverage,
            "llm_providers": provider_status,
        },
    )


def _workbench_snapshot(repo: Path, campaign_id: str = "") -> RunEvidenceSnapshot:
    root = workbench_runs_dir_for(repo)
    run_dir = root / campaign_id if campaign_id else _latest_dir_with(root, "summary.json")
    if run_dir is None or not run_dir.is_dir():
        return RunEvidenceSnapshot(source="workbench_campaign", state="idle")
    summary = read_json(run_dir / "summary.json")
    status = read_json(run_dir / "status.json")
    campaign = read_json(run_dir / "campaign.json")
    periods = summary.get("periods") or []
    event_rows = [event for period in periods for event in (period.get("event_results") or [])]
    latest_event_dir = None
    for event_dir in sorted((run_dir / "periods").glob("*/events/*"), key=_mtime, reverse=True):
        if event_dir.is_dir():
            latest_event_dir = event_dir
            break
    event_diag = read_json(latest_event_dir / "diagnostics.json") if latest_event_dir else {}
    wfc = read_json(run_dir / "wfc" / "wfc_summary.json") or summary.get("wfc", {})
    return RunEvidenceSnapshot(
        source="workbench_campaign",
        run_id=run_dir.name,
        state=str(status.get("state") or summary.get("status") or "unknown"),
        current_stage=str(status.get("period") or summary.get("status") or ""),
        root=str(run_dir),
        stages=[
            {"name": "campaign_manifest", "status": "done" if campaign else "missing"},
            {"name": "walk_forward_correlation", "status": wfc.get("wfc_status", "SKIPPED")},
            {"name": "period_backtests", "status": summary.get("status", "unknown")},
            {"name": "decision_gate", "status": "done" if summary else "missing"},
        ],
        artifacts={
            "campaign": str(run_dir / "campaign.json"),
            "summary": str(run_dir / "summary.json"),
            "latest_event": str(latest_event_dir or ""),
        },
        registry={"model_id": summary.get("model_id") or campaign.get("model_id"), "composition": summary.get("composition") or campaign.get("composition")},
        data={"symbol": summary.get("symbol") or campaign.get("symbol"), "periods": periods},
        backtest={"rows": event_rows, "periods": periods, "summary": summary},
        latency={"latest_event_diagnostics": event_diag, "cpp_latency_profile": event_diag.get("cpp_latency_profile", {})},
        diagnostics={"composition": summary.get("composition", {}), "latest_event_diagnostics": event_diag},
        robustness={
            "wfc": wfc,
            "robustness_checks": summary.get("robustness_checks", []),
            "robustness_passed": summary.get("robustness_passed"),
            "pending": summary.get("robustness_pending_checks", []),
            "failed": summary.get("robustness_failed_checks", []),
        },
        decision={
            "action": "PROMOTE" if summary.get("promote_candidate") else "QUARANTINE",
            "reason": summary.get("promote_note", ""),
            "live_registry_ready": bool(summary.get("promote_candidate")),
            "ranking": event_rows,
        },
        reports={
            "summary": str(run_dir / "summary.json"),
            "latest_report": str(latest_event_dir / "report.md") if latest_event_dir else "",
            "after_action_report": str(latest_event_dir / "after_action_report.md") if latest_event_dir else "",
        },
        system={"summary": summary, "status": status, "campaign": campaign},
    )


def _autonomous_snapshot(repo: Path) -> RunEvidenceSnapshot:
    artifacts_root = repo / "artifacts" / "runs"
    state_root = repo / "runtime" / "research"
    run_dir = _latest_dir_with(artifacts_root, "manifest.json")
    if run_dir is None:
        return RunEvidenceSnapshot(source="autonomous", state="idle")
    run_id = run_dir.name
    state = read_json(state_root / run_id / "state.json")
    manifest = read_json(run_dir / "manifest.json")
    stages = [
        {"name": name, "status": "done", "artifact": path}
        for name, path in (manifest.get("artifacts") or {}).items()
    ]
    data_resolution = read_json(run_dir / "data_resolution.json")
    data_lineage = read_json(run_dir / "data_lineage.json")
    feature_lineage = read_json(run_dir / "feature_lineage.json")
    model_combo = read_json(run_dir / "model_combination.json")
    experiment_spec = read_json(run_dir / "experiment_spec.json")
    backtest = read_json(run_dir / "backtest_metrics.json")
    gates = read_json(run_dir / "robustness_gates.json")
    wf = read_json(run_dir / "walk_forward_results.json")
    wfc = read_json(run_dir / "walk_forward_correlation.json")
    scoring = read_json(run_dir / "scoring_summary.json")
    decision = read_json(run_dir / "promotion_decision.json")
    return RunEvidenceSnapshot(
        source="autonomous",
        run_id=run_id,
        state="completed" if manifest else "unknown",
        current_stage=str((state.get("completed_stages") or [""])[-1] if state else ""),
        started_at=str(manifest.get("started_at", "")),
        root=str(run_dir),
        stages=stages,
        artifacts={k: str(run_dir / Path(v).name) for k, v in (manifest.get("artifacts") or {}).items()},
        registry={"model_combination": model_combo, "experiment_spec": experiment_spec},
        data={"data_resolution": data_resolution, "data_lineage": data_lineage},
        backtest=backtest,
        latency={"feature_lineage": feature_lineage, "latency_profile": feature_lineage.get("latency_profile", {})},
        diagnostics={"feature_lineage": feature_lineage, "model_combination": model_combo},
        robustness={"gates": gates, "walk_forward": wf, "wfc": wfc},
        decision={**decision, "scoring_summary": scoring},
        reports={"report_md": str(run_dir / "report.md")},
        system={
            "manifest": manifest,
            "artifact_bundle_validation": read_json(run_dir / "artifact_bundle_validation.json"),
            "registry_update": read_json(run_dir / "registry_update.json"),
        },
    )


def load_run_evidence(repo: Path, source: str, *, campaign_id: str = "") -> RunEvidenceSnapshot:
    if source == "workbench_campaign":
        return _workbench_snapshot(repo, campaign_id)
    if source == "autonomous":
        return _autonomous_snapshot(repo)
    return _crypto_snapshot(repo)


def default_source(repo: Path) -> str:
    crypto_path = repo / "runtime" / "workbench" / "crypto_smoke" / "latest_status.json"
    crypto_status = read_json(crypto_path)
    if crypto_status.get("state") == "running":
        return "crypto_lane"
    runs = workbench_runs_dir_for(repo)
    for path in runs.glob("*/status.json") if runs.is_dir() else []:
        status = read_json(path)
        if str(status.get("state", "")).lower() == "running":
            return "workbench_campaign"
    choices: list[tuple[float, str]] = []
    if crypto_path.is_file():
        choices.append((_mtime(crypto_path), "crypto_lane"))
    latest_campaign = _latest_dir_with(runs, "summary.json")
    if latest_campaign is not None:
        choices.append((_mtime(latest_campaign / "summary.json"), "workbench_campaign"))
    latest_autonomous = _latest_dir_with(repo / "artifacts" / "runs", "manifest.json")
    if latest_autonomous is not None:
        choices.append((_mtime(latest_autonomous / "manifest.json"), "autonomous"))
    return max(choices, default=(0.0, "crypto_lane"))[1]
