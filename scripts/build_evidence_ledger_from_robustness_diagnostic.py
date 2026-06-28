#!/usr/bin/env python3
"""Build a diagnostic evidence ledger from robustness bridge artifacts.

This script is read-only with respect to VectorBT screening artifacts. It
summarizes the diagnostic robustness sensitivity report and derives
HftBacktest readiness only from the existing strict replay eligibility
contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.hftbacktest_realism import validate_candidate_replay_eligibility
from backtest_pipeline.src.vectorbt_adapter import screening_status_text
from scripts.build_robustness_raw_inputs_from_screening import (
    SENSITIVITY_REPORT_SCHEMA,
    MeasuredRow,
    _compact_json,
    _load_json_object,
    _load_screening_evidence,
    _source_path,
)

LEDGER_SCHEMA = "hft3_diagnostic_evidence_ledger_v1"
FAMILY_READINESS_SCHEMA = "hft3_family_readiness_diagnostic_v1"
GATE_SUMMARY_SCHEMA = "hft3_evidence_ledger_gate_summary_v1"
DATA_VS_PIPELINE_AUDIT_SCHEMA = "hft3_data_vs_pipeline_audit_v1"
REPORT_NAME = "robustness_bridge_readiness_report.md"
DATA_VS_PIPELINE_AUDIT_REPORT_NAME = "data_vs_pipeline_audit.md"
FAMILY_ID_FIELDS = ("model_id", "symbol", "event_type", "research_clock", "context_set_id")
BUCKETS = (
    "robustness_pass_needs_evidence_apply",
    "robustness_fail_complete_evidence",
    "surface_incomplete_missing_cells",
    "adapter_contract_failure",
    "data_quality_failure",
    "hftbacktest_eligible_derived",
)
DATA_VS_PIPELINE_LABELS = (
    "download_or_build_missing_data",
    "rerun_vectorbt_surface_shape",
    "fix_pipeline_measurement_or_gate",
    "reject_model_family",
    "ready_for_hftbacktest_decision",
    "apply_robustness_evidence_first",
)
SURFACE_REASON_MARKERS = (
    "incomplete_event_parameter_surface",
    "missing_event_parameter_cells",
    "insufficient_events",
    "insufficient_complete_parameter_combinations",
    "insufficient_walk_forward_folds",
)
ADAPTER_REASON_MARKERS = (
    "adapter",
    "contract",
    "schema",
    "malformed",
    "duplicate_event_parameter_cell",
    "family_key_missing",
    "family_missing_from_sensitivity_report",
)
DATA_REASON_MARKERS = (
    "data_quality",
    "measured_metrics_missing",
    "event_date_unparseable",
    "insufficient_trade",
    "bad_npz",
    "hash_mismatch",
)
MISSING_DATA_REASON_MARKERS = (
    "bad_npz",
    "artifact_missing",
    "artifact missing",
    "hash_mismatch",
    "hash mismatch",
    "source_data_missing",
    "source data missing",
    "source data unavailable",
)
PIPELINE_MEASUREMENT_REASON_MARKERS = (
    "zero_trade",
    "zero trades",
    "0 trades",
    "insufficient_trade",
    "insufficient trades",
    "measured_metrics_missing",
)
TRADE_QUALITY_REASON_MARKERS = (
    "zero_trade",
    "zero trades",
    "0 trades",
    "insufficient_trade",
    "insufficient trades",
)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def _family_slug(family_id: str) -> str:
    stem = "".join(ch if ch.isalnum() else "_" for ch in family_id).strip("_")
    stem = stem[:80] or "family"
    digest = hashlib.sha256(family_id.encode("utf-8")).hexdigest()[:12]
    return f"{stem}_{digest}"


def _error(reason: str, **extra: Any) -> int:
    payload: dict[str, Any] = {"status": "error", "reason": reason}
    payload.update(extra)
    print(_compact_json(payload), file=sys.stderr)
    return 2


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _status(value: Any) -> str:
    return screening_status_text(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"nan", "none", "null", "not_run"}:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _first_number(mapping: Mapping[str, Any], *fields: str) -> float | None:
    for field in fields:
        value = _number(mapping.get(field))
        if value is not None:
            return value
    return None


def _string_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _load_sensitivity_report(path: Path) -> dict[str, Any]:
    report = _load_json_object(path, "sensitivity_report")
    if report.get("schema") != SENSITIVITY_REPORT_SCHEMA:
        raise ValueError(f"unsupported_sensitivity_report_schema:{report.get('schema')}")
    families = report.get("families")
    if not isinstance(families, list):
        raise ValueError("sensitivity_report_families_must_be_list")
    return report


def _family_map(value: Mapping[str, Any] | None) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}
    return {field: str(source.get(field) or "") for field in FAMILY_ID_FIELDS}


def _family_id(family: Mapping[str, Any]) -> str:
    family_map = _family_map(family)
    return "|".join(f"{field}={family_map[field]}" for field in FAMILY_ID_FIELDS)


def _metadata(row: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = row.get("base_candidate_metadata")
    if isinstance(metadata, Mapping):
        return metadata
    metric_values = row.get("metric_values")
    if isinstance(metric_values, Mapping):
        metadata = metric_values.get("base_candidate_metadata")
        if isinstance(metadata, Mapping):
            return metadata
    return {}


def _candidate_family_map(row: Mapping[str, Any], measured: MeasuredRow | None) -> dict[str, str]:
    if measured is not None:
        return dict(measured.family_key_map)
    metadata = _metadata(row)
    return _family_map(
        {
            "model_id": row.get("model_id") or row.get("hypothesis_id"),
            "symbol": metadata.get("symbol") or row.get("symbol"),
            "event_type": metadata.get("event_type")
            or row.get("target_event_type_or_null")
            or row.get("opportunity_type_or_event_type"),
            "research_clock": metadata.get("research_clock") or row.get("research_clock"),
            "context_set_id": metadata.get("context_set_id")
            or metadata.get("allowed_context_set_id")
            or row.get("allowed_context_set_id_or_null"),
        }
    )


def _candidate_event_id(row: Mapping[str, Any], measured: MeasuredRow | None) -> str | None:
    if measured is not None:
        return measured.event_id
    metadata = _metadata(row)
    return _string_or_none(
        metadata.get("event_id")
        or metadata.get("target_event_id")
        or row.get("event_id")
        or row.get("target_event_id")
    )


def _receipt_status(row: Mapping[str, Any]) -> str:
    return "present" if isinstance(row.get("robustness_evidence_receipt"), Mapping) else "missing"


def _validator_result(
    row: Mapping[str, Any],
    artifact: Mapping[str, Any] | None,
) -> tuple[bool, list[str]]:
    if artifact is None:
        reasons = ["screening_artifact_missing_for_validator"]
        reasons.extend(validate_candidate_replay_eligibility(row, screening_artifact=None))
        return False, reasons
    reasons = validate_candidate_replay_eligibility(row, screening_artifact=artifact)
    derived = (
        _status(row.get("replay_eligibility_status")) == "eligible"
        and _receipt_status(row) == "present"
        and not reasons
    )
    return derived, reasons


def _expected_surface_cells(report: Mapping[str, Any]) -> int | None:
    observed = report.get("parameter_cell_count")
    if not isinstance(observed, int):
        return None
    missing = 0
    rejected_events = report.get("rejected_events")
    if isinstance(rejected_events, list):
        for event in rejected_events:
            if not isinstance(event, Mapping):
                continue
            missing += int(event.get("missing_parameter_cell_count") or 0)
    if missing:
        return observed + missing
    if report.get("packaging_eligible") is True:
        return observed
    return None


def _surface_completeness(report: Mapping[str, Any]) -> float | None:
    observed = report.get("parameter_cell_count")
    expected = _expected_surface_cells(report)
    if not isinstance(observed, int) or not expected:
        return None
    return round(observed / expected, 6)


def _fold_persistence_score(report: Mapping[str, Any]) -> float | None:
    metrics = report.get("fold_is_surface_metrics")
    if not isinstance(metrics, Mapping):
        return None
    surface_count = _number(metrics.get("surface_count"))
    pass_count = _number(metrics.get("surface_pass_count"))
    if surface_count and pass_count is not None:
        return round(pass_count / surface_count, 6)
    return _first_number(metrics, "median_plateau_score", "plateau_score", "downside_plateau_score")


def _surface_stability_score(report: Mapping[str, Any]) -> float | None:
    metrics = report.get("event_0_surface_metrics")
    if isinstance(metrics, Mapping):
        value = _first_number(metrics, "plateau_score", "median_plateau_score")
        if value is not None:
            return value
    metrics = report.get("median_event_surface_metrics")
    if isinstance(metrics, Mapping):
        return _first_number(metrics, "median_plateau_score", "plateau_score")
    return None


def _report_reason(report: Mapping[str, Any], selected_surface_policy: str) -> str:
    if report.get("packaging_eligible") is not True:
        return str(report.get("packaging_failure_reason") or "packaging_evidence_incomplete")
    policy_reasons = report.get("policy_failure_reasons")
    if isinstance(policy_reasons, Mapping):
        return str(
            policy_reasons.get(selected_surface_policy)
            or policy_reasons.get("current_first_event")
            or "surface_stability_metrics_not_replay_ready"
        )
    return "surface_stability_metrics_not_replay_ready"


def _has_rejected_trade_quality(report: Mapping[str, Any]) -> bool:
    rejected_events = report.get("rejected_events")
    if not isinstance(rejected_events, list):
        return False
    for event in rejected_events:
        if not isinstance(event, Mapping):
            continue
        if int(event.get("insufficient_trade_cell_count") or 0) > 0:
            return True
        reasons = event.get("reasons")
        if isinstance(reasons, list):
            for reason in reasons:
                reason_text = str(reason)
                if (
                    "trade" in reason_text.lower()
                    or _reason_contains(reason_text, DATA_REASON_MARKERS)
                ):
                    return True
    return False


def _reason_contains(reason: str, markers: tuple[str, ...]) -> bool:
    lowered = reason.lower()
    return any(marker in lowered for marker in markers)


def _reason_list_contains(reasons: Iterable[Any], markers: tuple[str, ...]) -> bool:
    return any(_reason_contains(str(reason), markers) for reason in reasons)


def _selected_policy_pass(report: Mapping[str, Any], selected_surface_policy: str) -> bool:
    value = report.get(f"{selected_surface_policy}_pass")
    if isinstance(value, bool):
        return value
    return bool(report.get("current_first_event_pass") is True)


def _family_replay_status(candidate_rows: list[Mapping[str, Any]]) -> str:
    statuses = {_status(row.get("replay_eligibility_status")) or "missing" for row in candidate_rows}
    if not statuses:
        return "missing"
    if len(statuses) == 1:
        return next(iter(statuses))
    return "mixed"


def _family_receipt_status(candidate_rows: list[Mapping[str, Any]]) -> str:
    statuses = {_receipt_status(row) for row in candidate_rows}
    if not statuses:
        return "missing"
    if len(statuses) == 1:
        return next(iter(statuses))
    return "mixed"


def _recommended_action(bucket: str, reason: str) -> str:
    if bucket == "hftbacktest_eligible_derived":
        return "Candidate satisfies the existing strict HftBacktest handoff contract; operator may decide whether to spend replay compute."
    if bucket == "robustness_pass_needs_evidence_apply":
        return "Run the explicit robustness evidence applicator with min-eligible >= 1, then rebuild the ledger before HftBacktest."
    if bucket == "robustness_fail_complete_evidence":
        return "Review robustness sensitivity diagnostics before changing thresholds or rerunning VectorBT."
    if bucket == "surface_incomplete_missing_cells":
        return "Backfill the missing event-parameter surface cells for this family before treating it as an alpha failure."
    if bucket == "data_quality_failure":
        return "Fix measured-row data quality or insufficient-trade evidence, then rebuild the diagnostic sensitivity report."
    if bucket == "adapter_contract_failure":
        return "Fix artifact/schema/identity contract issues before any downstream robustness or replay decision."
    return f"Investigate unclassified diagnostic reason: {reason}"


def _classify_family(
    report: Mapping[str, Any],
    *,
    selected_surface_policy: str,
    has_hftbacktest_eligible_candidate: bool,
) -> tuple[str, str, list[str]]:
    if has_hftbacktest_eligible_candidate:
        return "hftbacktest_eligible_derived", "", []

    reason = _report_reason(report, selected_surface_policy)
    secondary: list[str] = []
    rejected_events = report.get("rejected_events")
    if isinstance(rejected_events, list):
        for event in rejected_events:
            if isinstance(event, Mapping):
                secondary.extend(str(reason) for reason in event.get("reasons") or [])

    if report.get("packaging_eligible") is not True:
        if _reason_contains(reason, ADAPTER_REASON_MARKERS):
            return "adapter_contract_failure", reason, secondary
        if _reason_contains(reason, DATA_REASON_MARKERS) or _has_rejected_trade_quality(report):
            return "data_quality_failure", reason, secondary
        if _reason_contains(reason, SURFACE_REASON_MARKERS):
            return "surface_incomplete_missing_cells", reason, secondary
        return "surface_incomplete_missing_cells", reason, secondary

    if not _selected_policy_pass(report, selected_surface_policy):
        return "robustness_fail_complete_evidence", reason, secondary

    return "robustness_pass_needs_evidence_apply", "strict_replay_contract_not_satisfied", secondary


def _family_readiness_row(
    *,
    report: Mapping[str, Any],
    family_id: str,
    family_candidates: list[Mapping[str, Any]],
    selected_surface_policy: str,
    bucket: str,
    primary_failure_reason: str,
    secondary_failure_reasons: list[str],
    hftbacktest_eligible_candidate_count: int,
    run_id: str,
    unit_artifact_count: int | None,
    source_report: str,
) -> dict[str, Any]:
    hftbacktest_eligible = (
        bucket == "hftbacktest_eligible_derived"
        and hftbacktest_eligible_candidate_count > 0
    )
    packaging_status = "pass" if report.get("packaging_eligible") is True else "fail"
    if bucket in {"adapter_contract_failure", "data_quality_failure"} and packaging_status == "fail":
        packaging_status = bucket
    robustness_status = "pass" if _selected_policy_pass(report, selected_surface_policy) else "fail"
    if packaging_status != "pass":
        robustness_status = "not_run"
    return {
        "schema": FAMILY_READINESS_SCHEMA,
        "run_id": run_id,
        "source_sensitivity_report": source_report,
        "family_id": family_id,
        "model_family": _family_map(
            report.get("model_family") if isinstance(report.get("model_family"), Mapping) else None
        ),
        "classification_bucket": bucket,
        "vectorbt_promoted_rows": int(report.get("vectorbt_promoted_count") or 0),
        "unit_artifact_count": unit_artifact_count,
        "event_count": report.get("event_count"),
        "parameter_cell_count": report.get("parameter_cell_count"),
        "expected_surface_cells": _expected_surface_cells(report),
        "observed_surface_cells": report.get("parameter_cell_count"),
        "surface_completeness_ratio": _surface_completeness(report),
        "surface_stability_score": _surface_stability_score(report),
        "fold_persistence_score": _fold_persistence_score(report),
        "robustness_gate_status": robustness_status,
        "packaging_gate_status": packaging_status,
        "replay_eligibility_status": _family_replay_status(family_candidates),
        "robustness_evidence_receipt_status": _family_receipt_status(family_candidates),
        "hftbacktest_eligible_derived": hftbacktest_eligible,
        "hftbacktest_eligible_candidate_count_derived": hftbacktest_eligible_candidate_count,
        "primary_failure_reason": primary_failure_reason or None,
        "secondary_failure_reasons": sorted(set(secondary_failure_reasons)),
        "recommended_next_action": _recommended_action(bucket, primary_failure_reason),
        "diagnostic_artifact_refs": {},
        "sensitivity_diagnostic": {
            "selected_surface_policy": selected_surface_policy,
            "current_threshold_result": report.get("current_first_event_pass"),
            "relaxed_threshold_result": report.get("pooled_train_events_pass"),
            "pooled_surface_result": report.get("median_event_surface_pass"),
            "fold_level_result": report.get("fold_is_surface_pass"),
            "policy_failure_reasons": report.get("policy_failure_reasons") or {},
        },
        "backfill_plan": {
            "missing_events": [
                event.get("event_id")
                for event in report.get("rejected_events") or []
                if isinstance(event, Mapping) and event.get("event_id")
            ],
            "missing_parameter_hashes": [],
            "missing_symbols": [],
            "missing_feature_recipe_hashes": [],
            "vectorbt_command_or_config": "not_available_in_diagnostic_report",
        },
    }


def _measured_row_payload(row: MeasuredRow) -> dict[str, Any]:
    return {
        "candidate_id": row.candidate_id,
        "screening_status": row.screening_status,
        "event_id": row.event_id,
        "event_date": row.event_date.isoformat(),
        "parameter_hash": row.parameter_hash,
        "parameter_values": row.parameter_values,
        "feature_recipe_hash": row.feature_recipe_hash,
        "expectancy": row.expectancy,
        "net_return": row.net_return,
        "net_pnl": row.net_pnl,
        "sharpe": row.sharpe,
        "profit_factor": row.profit_factor,
        "profit_factor_missing": row.profit_factor_missing,
        "max_drawdown": row.max_drawdown,
        "trade_count": row.trade_count,
    }


def _write_family_diagnostic_evidence(
    *,
    out_dir: Path,
    source_root: Path | None,
    family_row: Mapping[str, Any],
    family_report: Mapping[str, Any],
    measured_rows: list[MeasuredRow],
    selected_surface_policy: str,
) -> dict[str, str]:
    if family_row.get("classification_bucket") == "hftbacktest_eligible_derived":
        return {}

    evidence_dir = out_dir / "diagnostic_evidence" / _family_slug(str(family_row["family_id"]))
    matrix_path = evidence_dir / "family_surface_matrix.jsonl"
    coverage_path = evidence_dir / "family_surface_coverage.json"
    decision_path = evidence_dir / "family_gate_decision.json"
    fold_matrix_path = evidence_dir / "fold_persistence_matrix.json"
    fold_decision_path = evidence_dir / "fold_gate_decision.json"

    _write_jsonl(matrix_path, [_measured_row_payload(row) for row in measured_rows])
    _write_json(
        coverage_path,
        {
            "family_id": family_row["family_id"],
            "model_family": family_row.get("model_family"),
            "event_count": family_row.get("event_count"),
            "expected_surface_cells": family_row.get("expected_surface_cells"),
            "observed_surface_cells": family_row.get("observed_surface_cells"),
            "surface_completeness_ratio": family_row.get("surface_completeness_ratio"),
            "rejected_events": family_report.get("rejected_events") or [],
            "backfill_plan": family_row.get("backfill_plan") or {},
        },
    )
    _write_json(
        decision_path,
        {
            "family_id": family_row["family_id"],
            "classification_bucket": family_row.get("classification_bucket"),
            "selected_surface_policy": selected_surface_policy,
            "packaging_gate_status": family_row.get("packaging_gate_status"),
            "robustness_gate_status": family_row.get("robustness_gate_status"),
            "primary_failure_reason": family_row.get("primary_failure_reason"),
            "secondary_failure_reasons": family_row.get("secondary_failure_reasons"),
            "policy_failure_reasons": family_report.get("policy_failure_reasons") or {},
            "recommended_next_action": family_row.get("recommended_next_action"),
        },
    )
    _write_json(
        fold_matrix_path,
        {
            "family_id": family_row["family_id"],
            "fold_is_surface_metrics": family_report.get("fold_is_surface_metrics") or {},
            "surface_training_event_ids": family_report.get("surface_training_event_ids") or [],
        },
    )
    _write_json(
        fold_decision_path,
        {
            "family_id": family_row["family_id"],
            "fold_level_result": family_report.get("fold_is_surface_pass"),
            "fold_persistence_score": family_row.get("fold_persistence_score"),
            "primary_failure_reason": family_row.get("primary_failure_reason"),
        },
    )
    return {
        "family_surface_matrix_jsonl": _source_path(matrix_path, source_root),
        "family_surface_coverage_json": _source_path(coverage_path, source_root),
        "family_gate_decision_json": _source_path(decision_path, source_root),
        "fold_persistence_matrix_json": _source_path(fold_matrix_path, source_root),
        "fold_gate_decision_json": _source_path(fold_decision_path, source_root),
    }


def _candidate_row(
    *,
    candidate_id: str,
    row: Mapping[str, Any],
    measured: MeasuredRow | None,
    artifact: Mapping[str, Any] | None,
    artifact_source: str | None,
    family_bucket: str,
    family_id: str,
    run_id: str,
    validator_reasons: list[str],
    hftbacktest_eligible_derived: bool,
) -> dict[str, Any]:
    family_map = _candidate_family_map(row, measured)
    return {
        "schema": LEDGER_SCHEMA,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "family_id": family_id,
        "model_id": _string_or_none(row.get("model_id") or row.get("hypothesis_id") or family_map.get("model_id")),
        "family_model_id": family_map.get("model_id") or None,
        "symbol": _string_or_none(row.get("symbol") or family_map.get("symbol")),
        "root_symbol": _string_or_none(row.get("root_symbol")),
        "contract": _string_or_none(row.get("contract") or row.get("instrument_id")),
        "event_id": _candidate_event_id(row, measured),
        "event_set_id": _string_or_none(row.get("event_set_id")),
        "event_type": _string_or_none(
            family_map.get("event_type")
            or row.get("target_event_type_or_null")
            or row.get("opportunity_type_or_event_type")
        ),
        "parameter_values_hash": _string_or_none(row.get("parameter_values_hash")),
        "parameter_space_hash": _string_or_none(
            row.get("parameter_space_hash") or (artifact or {}).get("parameter_space_hash")
        ),
        "feature_recipe_hash": _string_or_none(row.get("feature_recipe_hash") or (measured.feature_recipe_hash if measured else None)),
        "data_manifest_hash": _string_or_none(row.get("data_manifest_hash") or (artifact or {}).get("data_manifest_hash")),
        "lake_manifest_hash": _string_or_none(row.get("lake_manifest_hash") or (artifact or {}).get("lake_manifest_hash")),
        "screening_artifact_hash": _string_or_none((artifact or {}).get("screening_artifact_hash")),
        "code_commit": _string_or_none((artifact or {}).get("code_commit")),
        "engine_name": _string_or_none((artifact or {}).get("screening_backend") or "vectorbt"),
        "engine_run_id": _string_or_none((artifact or {}).get("run_id")),
        "gate_name": "robustness_bridge_readiness_diagnostic",
        "gate_status": "pass" if hftbacktest_eligible_derived else "blocked",
        "family_classification_bucket": family_bucket,
        "failure_reason": None if hftbacktest_eligible_derived else (validator_reasons[0] if validator_reasons else family_bucket),
        "failure_severity": None if hftbacktest_eligible_derived else "blocking",
        "artifact_refs": {"screening_artifact": artifact_source},
        "artifact_sha256": _string_or_none((artifact or {}).get("screening_artifact_hash")),
        "promotion_state": "hftbacktest_eligible_derived" if hftbacktest_eligible_derived else "diagnostic_only_blocked",
        "created_at": _utc_now(),
        "screening_status": _string_or_none(row.get("screening_status")),
        "replay_eligibility_status": _string_or_none(row.get("replay_eligibility_status")),
        "robustness_evidence_receipt_status": _receipt_status(row),
        "hftbacktest_eligible_derived": hftbacktest_eligible_derived,
        "validator_reasons": validator_reasons,
        "trade_count": measured.trade_count if measured is not None else row.get("trade_count"),
        "expectancy": measured.expectancy if measured is not None else _number(row.get("expectancy_per_trade")),
        "net_return": measured.net_return if measured is not None else _number(row.get("net_return")),
        "net_pnl": measured.net_pnl if measured is not None else _number(row.get("net_pnl")),
        "sharpe": measured.sharpe if measured is not None else _number(row.get("sharpe")),
    }


def _report_section_lines(
    title: str,
    rows: list[Mapping[str, Any]],
    *,
    empty: str,
    limit: int = 10,
) -> list[str]:
    lines = [f"## {title}", ""]
    if not rows:
        lines.extend([empty, ""])
        return lines
    for row in rows[:limit]:
        reason = row.get("primary_failure_reason") or "none"
        lines.append(f"- {row['family_id']} - {reason}")
    if len(rows) > limit:
        lines.append(f"- ... {len(rows) - limit} more")
    lines.append("")
    return lines


def _family_rejected_events(family_report: Mapping[str, Any]) -> list[dict[str, Any]]:
    events = family_report.get("rejected_events")
    if not isinstance(events, list):
        return []
    return [dict(event) for event in events if isinstance(event, Mapping)]


def _cell_count(value: Any) -> int:
    number = _number(value)
    if number is None:
        return 0
    return max(0, int(number))


def _rejected_event_cell_summary(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    rejected_event_count = 0
    events_with_missing_parameter_cells = 0
    events_with_insufficient_trade_cells = 0
    missing_parameter_cells = 0
    insufficient_trade_cells = 0
    for event in events:
        rejected_event_count += 1
        missing = _cell_count(event.get("missing_parameter_cell_count"))
        insufficient = _cell_count(event.get("insufficient_trade_cell_count"))
        missing_parameter_cells += missing
        insufficient_trade_cells += insufficient
        if missing:
            events_with_missing_parameter_cells += 1
        if insufficient:
            events_with_insufficient_trade_cells += 1
    return {
        "rejected_event_count": rejected_event_count,
        "missing_parameter_cells": missing_parameter_cells,
        "insufficient_trade_cells": insufficient_trade_cells,
        "events_with_missing_parameter_cells": events_with_missing_parameter_cells,
        "events_with_insufficient_trade_cells": events_with_insufficient_trade_cells,
    }


def _family_audit_reasons(
    family_row: Mapping[str, Any],
    family_report: Mapping[str, Any],
    candidate_rows: list[Mapping[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    for field in ("primary_failure_reason",):
        value = family_row.get(field)
        if value:
            reasons.append(str(value))
    for value in family_row.get("secondary_failure_reasons") or []:
        reasons.append(str(value))
    for event in _family_rejected_events(family_report):
        for value in event.get("reasons") or []:
            reasons.append(str(value))
    for row in candidate_rows:
        for value in row.get("validator_reasons") or []:
            reasons.append(str(value))
    return reasons


def _data_vs_pipeline_label(
    family_row: Mapping[str, Any],
    reasons: Iterable[Any],
) -> str:
    bucket = str(family_row.get("classification_bucket") or "")
    if _reason_list_contains(reasons, MISSING_DATA_REASON_MARKERS):
        return "download_or_build_missing_data"
    if bucket == "robustness_fail_complete_evidence":
        return "reject_model_family"
    if bucket == "hftbacktest_eligible_derived":
        return "ready_for_hftbacktest_decision"
    if bucket == "robustness_pass_needs_evidence_apply":
        return "apply_robustness_evidence_first"
    if _reason_list_contains(reasons, PIPELINE_MEASUREMENT_REASON_MARKERS):
        return "fix_pipeline_measurement_or_gate"
    if bucket == "surface_incomplete_missing_cells" or _reason_list_contains(
        reasons,
        SURFACE_REASON_MARKERS,
    ):
        return "rerun_vectorbt_surface_shape"
    return "fix_pipeline_measurement_or_gate"


def _data_vs_pipeline_explanation(label: str) -> str:
    if label == "download_or_build_missing_data":
        return "Source data or bound artifacts are missing, corrupt, or hash-mismatched; rebuild or download data before rerunning diagnostics."
    if label == "rerun_vectorbt_surface_shape":
        return "The family lacks complete event-parameter surface shape evidence without data-quality markers; backfill the VectorBT surface shape."
    if label == "fix_pipeline_measurement_or_gate":
        return "The evidence points to zero/insufficient trades, missing measured metrics, or a pipeline/gate contract issue."
    if label == "reject_model_family":
        return "The family has complete robustness evidence and failed robustness; reject it unless the research thesis changes."
    if label == "ready_for_hftbacktest_decision":
        return "The existing strict replay contract derived HftBacktest eligibility; the operator can decide whether to spend replay compute."
    if label == "apply_robustness_evidence_first":
        return "The family passed robustness but still needs explicit robustness evidence application before any replay decision."
    return "Unclassified diagnostic state."


def _measurement_gate_subdiagnosis(
    *,
    label: str,
    measurement_or_gate_evidence: bool,
    cell_summary: Mapping[str, int],
) -> str:
    insufficient = int(cell_summary.get("insufficient_trade_cells") or 0)
    missing = int(cell_summary.get("missing_parameter_cells") or 0)
    if insufficient and missing:
        return "insufficient_trade_cells_with_missing_parameter_cells"
    if insufficient:
        return "zero_or_insufficient_trade_cells"
    if missing and label == "fix_pipeline_measurement_or_gate":
        return "missing_parameter_cells_with_measurement_gate_context"
    if measurement_or_gate_evidence:
        return "measured_metrics_missing_or_gate_contract"
    if missing:
        return "missing_parameter_cells_only"
    return "not_applicable"


def _data_vs_pipeline_action_detail(
    *,
    label: str,
    subdiagnosis: str,
    cell_summary: Mapping[str, int],
) -> str:
    missing = int(cell_summary.get("missing_parameter_cells") or 0)
    insufficient = int(cell_summary.get("insufficient_trade_cells") or 0)
    if label == "fix_pipeline_measurement_or_gate" and insufficient:
        return (
            "Do not download data first. Inspect signal density, official VectorBT stats "
            f"nulls, and zero/insufficient closed-trade gate semantics; insufficient-trade "
            f"cells={insufficient}, missing-parameter cells={missing}."
        )
    if label == "fix_pipeline_measurement_or_gate" and missing:
        return (
            "Inspect why parameter cells are absent from measured rows before rerunning; "
            f"missing-parameter cells={missing}, insufficient-trade cells={insufficient}."
        )
    return _data_vs_pipeline_explanation(label)


def _build_data_vs_pipeline_audit(
    *,
    run_id: str,
    created_at_utc: str,
    source_report: str,
    screening_artifact_dir: str,
    family_rows: list[Mapping[str, Any]],
    family_report_by_id: Mapping[str, Mapping[str, Any]],
    candidate_rows: list[Mapping[str, Any]],
    measured_rows_by_family_id: Mapping[str, list[MeasuredRow]],
) -> dict[str, Any]:
    candidates_by_family: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        candidates_by_family[str(row.get("family_id") or "")].append(row)

    audit_rows: list[dict[str, Any]] = []
    for family_row in family_rows:
        family_id = str(family_row["family_id"])
        family_report = family_report_by_id.get(family_id, {})
        family_candidates = candidates_by_family.get(family_id, [])
        rejected_events = _family_rejected_events(family_report)
        cell_summary = _rejected_event_cell_summary(rejected_events)
        reasons = _family_audit_reasons(family_row, family_report, family_candidates)
        measured_rows = measured_rows_by_family_id.get(family_id, [])
        zero_or_insufficient_trade_evidence = any(
            row.trade_count <= 0 for row in measured_rows
        ) or _reason_list_contains(reasons, TRADE_QUALITY_REASON_MARKERS)
        if zero_or_insufficient_trade_evidence:
            reasons.append("zero_trades")
        missing_data_evidence = _reason_list_contains(reasons, MISSING_DATA_REASON_MARKERS)
        surface_shape_evidence = str(family_row.get("classification_bucket") or "") == (
            "surface_incomplete_missing_cells"
        ) or _reason_list_contains(reasons, SURFACE_REASON_MARKERS)
        measurement_or_gate_evidence = _reason_list_contains(
            reasons,
            PIPELINE_MEASUREMENT_REASON_MARKERS,
        )
        label = _data_vs_pipeline_label(family_row, reasons)
        measurement_gate_subdiagnosis = _measurement_gate_subdiagnosis(
            label=label,
            measurement_or_gate_evidence=measurement_or_gate_evidence,
            cell_summary=cell_summary,
        )
        audit_rows.append(
            {
                "family_id": family_id,
                "model_family": family_row.get("model_family"),
                "final_diagnosis": label,
                "final_diagnosis_label": label,
                "diagnosis_explanation": _data_vs_pipeline_explanation(label),
                "classification_bucket": family_row.get("classification_bucket"),
                "family_classification_bucket": family_row.get("classification_bucket"),
                "primary_failure_reason": family_row.get("primary_failure_reason"),
                "secondary_failure_reasons": family_row.get("secondary_failure_reasons") or [],
                "rejected_events": rejected_events,
                "measurement_gate_subdiagnosis": measurement_gate_subdiagnosis,
                "measurement_gate_cell_summary": cell_summary,
                "zero_or_insufficient_trade_evidence": zero_or_insufficient_trade_evidence,
                "missing_data_evidence": missing_data_evidence,
                "surface_shape_evidence": surface_shape_evidence,
                "measurement_or_gate_evidence": measurement_or_gate_evidence,
                "recommended_next_action": family_row.get("recommended_next_action"),
                "recommended_next_action_detail": _data_vs_pipeline_action_detail(
                    label=label,
                    subdiagnosis=measurement_gate_subdiagnosis,
                    cell_summary=cell_summary,
                ),
                "readiness_context": {
                    "vectorbt_promoted_rows": family_row.get("vectorbt_promoted_rows"),
                    "event_count": family_row.get("event_count"),
                    "parameter_cell_count": family_row.get("parameter_cell_count"),
                    "expected_surface_cells": family_row.get("expected_surface_cells"),
                    "observed_surface_cells": family_row.get("observed_surface_cells"),
                    "surface_completeness_ratio": family_row.get("surface_completeness_ratio"),
                    "robustness_gate_status": family_row.get("robustness_gate_status"),
                    "packaging_gate_status": family_row.get("packaging_gate_status"),
                    "replay_eligibility_status": family_row.get("replay_eligibility_status"),
                    "robustness_evidence_receipt_status": family_row.get(
                        "robustness_evidence_receipt_status"
                    ),
                    "hftbacktest_eligible_derived": family_row.get("hftbacktest_eligible_derived"),
                    "hftbacktest_eligible_candidate_count_derived": family_row.get(
                        "hftbacktest_eligible_candidate_count_derived"
                    ),
                },
                "diagnostic_row_context": {
                    "candidate_count": len(family_candidates),
                    "candidate_ids": [row.get("candidate_id") for row in family_candidates],
                    "candidate_validator_reasons": sorted(
                        {
                            str(reason)
                            for row in family_candidates
                            for reason in row.get("validator_reasons") or []
                        }
                    ),
                    "measured_row_count": len(measured_rows),
                    "measured_trade_counts": [
                        row.trade_count for row in measured_rows if row.trade_count is not None
                    ],
                },
            }
        )

    counts = Counter(str(row["final_diagnosis_label"]) for row in audit_rows)
    for label in DATA_VS_PIPELINE_LABELS:
        counts.setdefault(label, 0)
    return {
        "schema": DATA_VS_PIPELINE_AUDIT_SCHEMA,
        "run_id": run_id,
        "created_at_utc": created_at_utc,
        "source_sensitivity_report": source_report,
        "screening_artifact_dir": screening_artifact_dir,
        "summary": {
            "family_count": len(audit_rows),
            "families_by_final_diagnosis": dict(sorted(counts.items())),
            "hftbacktest_route_created": False,
        },
        "families": audit_rows,
    }


def _markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _build_data_vs_pipeline_markdown(audit: Mapping[str, Any]) -> str:
    rows = audit.get("families") if isinstance(audit.get("families"), list) else []
    counts = ((audit.get("summary") or {}).get("families_by_final_diagnosis") or {})
    lines = [
        f"# Data vs Pipeline Audit - {audit['run_id']}",
        "",
        "This audit is diagnostic-only and does not route any family to HftBacktest.",
        "",
        "## Summary",
        "",
    ]
    for label in DATA_VS_PIPELINE_LABELS:
        lines.append(f"- {label}: {counts.get(label, 0)} families")
    lines.extend(["", "## Families", ""])
    if not rows:
        lines.extend(["None.", ""])
        return "\n".join(lines).rstrip() + "\n"
    lines.append(
        "| Final diagnosis | Subdiagnosis | Family | Bucket | Primary reason | Missing parameter cells | Insufficient-trade cells |"
    )
    lines.append("|---|---|---|---|---|---:|---:|")
    for row in rows:
        cell_summary = row.get("measurement_gate_cell_summary")
        if not isinstance(cell_summary, Mapping):
            cell_summary = {}
        lines.append(
            "| {label} | {subdiagnosis} | {family} | {bucket} | {reason} | {missing} | {insufficient} |".format(
                label=_markdown_cell(row.get("final_diagnosis_label")),
                subdiagnosis=_markdown_cell(row.get("measurement_gate_subdiagnosis")),
                family=_markdown_cell(row.get("family_id")),
                bucket=_markdown_cell(row.get("family_classification_bucket")),
                reason=_markdown_cell(row.get("primary_failure_reason")),
                missing=_markdown_cell(cell_summary.get("missing_parameter_cells")),
                insufficient=_markdown_cell(cell_summary.get("insufficient_trade_cells")),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _build_markdown_report(
    *,
    run_id: str,
    summary: Mapping[str, Any],
    family_rows: list[Mapping[str, Any]],
    candidate_rows: list[Mapping[str, Any]],
) -> str:
    by_bucket: dict[str, list[Mapping[str, Any]]] = {bucket: [] for bucket in BUCKETS}
    for row in family_rows:
        by_bucket[str(row.get("classification_bucket"))].append(row)
    eligible_candidates = [
        row for row in candidate_rows if row.get("hftbacktest_eligible_derived") is True
    ]
    complete = (
        len(by_bucket["robustness_pass_needs_evidence_apply"])
        + len(by_bucket["robustness_fail_complete_evidence"])
        + len(by_bucket["hftbacktest_eligible_derived"])
    )
    lines = [
        f"# Robustness Bridge Readiness Report - {run_id}",
        "",
        "## Seven Questions",
        "",
        f"1. Did VectorBT produce usable screening evidence? {'Yes' if summary['candidate_count'] else 'No'}; promoted candidates loaded: {summary['candidate_count']}.",
        f"2. Did the bridge have complete surfaces? Complete diagnostic families: {complete}/{summary['family_count']}.",
        f"3. Which families passed robustness but still need evidence application? {len(by_bucket['robustness_pass_needs_evidence_apply'])} families; see bucket list below.",
        f"4. Which families failed due to missing surface cells? {len(by_bucket['surface_incomplete_missing_cells'])} families; see bucket list below.",
        f"5. Which families failed due to adapter or data issues? {len(by_bucket['adapter_contract_failure'])} adapter families and {len(by_bucket['data_quality_failure'])} data-quality families.",
        f"6. Is any candidate eligible for HftBacktest? {'Yes' if eligible_candidates else 'No'}; derived eligible candidates: {len(eligible_candidates)}.",
        "7. If none, what exact next action is required? Use the next-action bucket list below; do not route to HftBacktest unless strict derived eligibility is true.",
        "",
        "## Gate Summary",
        "",
        f"- Families: {summary['family_count']}",
        f"- Candidates: {summary['candidate_count']}",
        f"- Any HftBacktest-derived eligible candidates: {str(summary['any_hftbacktest_eligible_derived']).lower()}",
        "",
        "## Next Actions By Bucket",
        "",
    ]
    for bucket in BUCKETS:
        rows = by_bucket[bucket]
        action = _recommended_action(bucket, rows[0].get("primary_failure_reason") if rows else "")
        lines.append(f"- {bucket}: {len(rows)} families. {action}")
    lines.append("")
    lines.extend(
        _report_section_lines(
            "Robustness Failures With Complete Evidence",
            by_bucket["robustness_fail_complete_evidence"],
            empty="None.",
        )
    )
    lines.extend(
        _report_section_lines(
            "Robustness Pass / Evidence Apply Needed",
            by_bucket["robustness_pass_needs_evidence_apply"],
            empty="None.",
        )
    )
    lines.extend(
        _report_section_lines(
            "Surface Incomplete / Missing Cells",
            by_bucket["surface_incomplete_missing_cells"],
            empty="None.",
        )
    )
    lines.extend(
        _report_section_lines(
            "Adapter Or Data Issues",
            by_bucket["adapter_contract_failure"] + by_bucket["data_quality_failure"],
            empty="None.",
        )
    )
    lines.extend(
        _report_section_lines(
            "HftBacktest Derived Eligible Families",
            by_bucket["hftbacktest_eligible_derived"],
            empty="None.",
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def build_evidence_ledger(
    *,
    sensitivity_report_path: Path,
    screening_artifact_dir: Path,
    out_dir: Path,
    run_id: str,
    source_root: Path | None,
) -> dict[str, Any]:
    report = _load_sensitivity_report(sensitivity_report_path)
    evidence = _load_screening_evidence(
        screening_artifact_path=None,
        screening_artifact_dir=screening_artifact_dir,
        source_root=source_root,
    )
    artifacts_by_candidate = evidence.artifacts_by_candidate
    artifact_sources = evidence.artifact_sources_by_candidate
    report_hash = report.get("screening_artifact_hash")
    evidence_hash = evidence.artifact.get("screening_artifact_hash")
    if not report_hash:
        raise ValueError("sensitivity_report_screening_artifact_hash_missing")
    if not evidence_hash:
        raise ValueError("screening_artifact_dir_hash_missing")
    if str(report_hash) != str(evidence_hash):
        raise ValueError("sensitivity_report_screening_artifact_hash_mismatch")
    report_unit_set_hash = report.get("unit_artifact_set_hash")
    evidence_unit_set_hash = evidence.artifact.get("unit_artifact_set_hash")
    if not report_unit_set_hash:
        raise ValueError("sensitivity_report_unit_artifact_set_hash_missing")
    if not evidence_unit_set_hash:
        raise ValueError("screening_artifact_dir_unit_artifact_set_hash_missing")
    if str(report_unit_set_hash) != str(evidence_unit_set_hash):
        raise ValueError("sensitivity_report_unit_artifact_set_hash_mismatch")
    report_unit_count = report.get("unit_artifact_count")
    evidence_unit_count = evidence.artifact.get("unit_artifact_count")
    if not isinstance(report_unit_count, int):
        raise ValueError("sensitivity_report_unit_artifact_count_missing")
    if not isinstance(evidence_unit_count, int):
        raise ValueError("screening_artifact_dir_unit_artifact_count_missing")
    if report_unit_count != evidence_unit_count:
        raise ValueError("sensitivity_report_unit_artifact_count_mismatch")

    source_report = _source_path(sensitivity_report_path, source_root)
    selected_surface_policy = str(report.get("selected_surface_policy") or "current_first_event")
    unit_artifact_count = evidence.artifact.get("unit_artifact_count")
    unit_count = int(unit_artifact_count) if isinstance(unit_artifact_count, int) else None

    candidate_contexts: dict[str, dict[str, Any]] = {}
    family_candidates: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    family_has_eligible: dict[str, bool] = defaultdict(bool)
    family_eligible_counts: Counter[str] = Counter()
    for candidate_id, row in sorted(evidence.promoted_by_id.items()):
        measured = evidence.promoted_measured.get(candidate_id)
        family_map = _candidate_family_map(row, measured)
        family_id = _family_id(family_map)
        artifact = artifacts_by_candidate.get(candidate_id)
        derived, reasons = _validator_result(row, artifact)
        family_candidates[family_id].append(row)
        family_has_eligible[family_id] = family_has_eligible[family_id] or derived
        if derived:
            family_eligible_counts[family_id] += 1
        candidate_contexts[candidate_id] = {
            "row": row,
            "measured": measured,
            "family_id": family_id,
            "artifact": artifact,
            "artifact_source": artifact_sources.get(candidate_id),
            "validator_reasons": reasons,
            "hftbacktest_eligible_derived": derived,
        }

    family_readiness: list[dict[str, Any]] = []
    family_bucket_by_id: dict[str, str] = {}
    family_pre_eligibility_bucket_by_id: dict[str, str] = {}
    family_report_by_id: dict[str, Mapping[str, Any]] = {}
    seen_family_ids: set[str] = set()
    measured_rows_by_family_id: dict[str, list[MeasuredRow]] = {
        _family_id(rows[0].family_key_map): rows
        for rows in evidence.family_rows.values()
        if rows
    }
    for family_report in report.get("families", []):
        if not isinstance(family_report, Mapping):
            continue
        model_family = _family_map(
            family_report.get("model_family")
            if isinstance(family_report.get("model_family"), Mapping)
            else None
        )
        family_id = _family_id(model_family)
        seen_family_ids.add(family_id)
        family_report_by_id[family_id] = family_report
        bucket, reason, secondary = _classify_family(
            family_report,
            selected_surface_policy=selected_surface_policy,
            has_hftbacktest_eligible_candidate=family_has_eligible.get(family_id, False),
        )
        pre_eligibility_bucket, _pre_reason, _pre_secondary = _classify_family(
            family_report,
            selected_surface_policy=selected_surface_policy,
            has_hftbacktest_eligible_candidate=False,
        )
        family_bucket_by_id[family_id] = bucket
        family_pre_eligibility_bucket_by_id[family_id] = pre_eligibility_bucket
        family_readiness.append(
            _family_readiness_row(
                report=family_report,
                family_id=family_id,
                family_candidates=family_candidates.get(family_id, []),
                selected_surface_policy=selected_surface_policy,
                bucket=bucket,
                primary_failure_reason=reason,
                secondary_failure_reasons=secondary,
                hftbacktest_eligible_candidate_count=family_eligible_counts[family_id],
                run_id=run_id,
                unit_artifact_count=unit_count,
                source_report=source_report,
            )
        )

    for family_key, rows in sorted(evidence.family_rows.items(), key=lambda item: item[0]):
        family_map = rows[0].family_key_map if rows else {}
        family_id = _family_id(family_map)
        if family_id in seen_family_ids:
            continue
        bucket = "adapter_contract_failure"
        family_bucket_by_id[family_id] = bucket
        family_pre_eligibility_bucket_by_id[family_id] = bucket
        fallback_report = {
            "model_family": family_map,
            "vectorbt_promoted_count": sum(1 for row in rows if _status(row.screening_status) == "pass"),
            "packaging_eligible": False,
            "packaging_failure_reason": "family_missing_from_sensitivity_report",
            "event_count": len({row.event_id for row in rows}),
            "parameter_cell_count": len(rows),
            "rejected_events": [],
        }
        family_report_by_id[family_id] = fallback_report
        family_readiness.append(
            _family_readiness_row(
                report=fallback_report,
                family_id=family_id,
                family_candidates=family_candidates.get(family_id, []),
                selected_surface_policy=selected_surface_policy,
                bucket=bucket,
                primary_failure_reason="family_missing_from_sensitivity_report",
                secondary_failure_reasons=[],
                hftbacktest_eligible_candidate_count=family_eligible_counts[family_id],
                run_id=run_id,
                unit_artifact_count=unit_count,
                source_report=source_report,
            )
        )

    for row in family_readiness:
        family_id = str(row["family_id"])
        row["diagnostic_artifact_refs"] = _write_family_diagnostic_evidence(
            out_dir=out_dir,
            source_root=source_root,
            family_row=row,
            family_report=family_report_by_id.get(family_id, {}),
            measured_rows=measured_rows_by_family_id.get(family_id, []),
            selected_surface_policy=selected_surface_policy,
        )

    candidate_rows: list[dict[str, Any]] = []
    for candidate_id, context in sorted(candidate_contexts.items()):
        family_id = str(context["family_id"])
        derived = bool(context["hftbacktest_eligible_derived"])
        family_bucket = (
            "hftbacktest_eligible_derived"
            if derived
            else family_pre_eligibility_bucket_by_id.get(family_id, "adapter_contract_failure")
        )
        candidate_rows.append(
            _candidate_row(
                candidate_id=candidate_id,
                row=context["row"],
                measured=context["measured"],
                artifact=context["artifact"],
                artifact_source=context["artifact_source"],
                family_bucket=family_bucket,
                family_id=family_id,
                run_id=run_id,
                validator_reasons=list(context["validator_reasons"]),
                hftbacktest_eligible_derived=derived,
            )
        )

    family_bucket_counts = Counter(str(row["classification_bucket"]) for row in family_readiness)
    candidate_bucket_counts = Counter(str(row["family_classification_bucket"]) for row in candidate_rows)
    for bucket in BUCKETS:
        family_bucket_counts.setdefault(bucket, 0)
        candidate_bucket_counts.setdefault(bucket, 0)
    eligible_candidate_ids = [
        row["candidate_id"]
        for row in candidate_rows
        if row.get("hftbacktest_eligible_derived") is True
    ]
    created_at_utc = _utc_now()
    summary = {
        "schema": GATE_SUMMARY_SCHEMA,
        "run_id": run_id,
        "created_at_utc": created_at_utc,
        "source_sensitivity_report": source_report,
        "screening_artifact_dir": _source_path(screening_artifact_dir, source_root),
        "screening_artifact_hash": evidence.artifact.get("screening_artifact_hash"),
        "unit_artifact_count": unit_count,
        "family_count": len(family_readiness),
        "candidate_count": len(candidate_rows),
        "families_by_bucket": dict(sorted(family_bucket_counts.items())),
        "candidates_by_bucket": dict(sorted(candidate_bucket_counts.items())),
        "any_hftbacktest_eligible_derived": bool(eligible_candidate_ids),
        "hftbacktest_eligible_candidate_ids": eligible_candidate_ids,
        "hftbacktest_eligible_candidate_count": len(eligible_candidate_ids),
    }
    data_vs_pipeline_audit = _build_data_vs_pipeline_audit(
        run_id=run_id,
        created_at_utc=created_at_utc,
        source_report=source_report,
        screening_artifact_dir=_source_path(screening_artifact_dir, source_root),
        family_rows=family_readiness,
        family_report_by_id=family_report_by_id,
        candidate_rows=candidate_rows,
        measured_rows_by_family_id=measured_rows_by_family_id,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "candidate_evidence.jsonl", candidate_rows)
    _write_jsonl(out_dir / "family_readiness.jsonl", family_readiness)
    _write_json(out_dir / "gate_summary.json", summary)
    _write_json(out_dir / "data_vs_pipeline_audit.json", data_vs_pipeline_audit)
    (out_dir / DATA_VS_PIPELINE_AUDIT_REPORT_NAME).write_text(
        _build_data_vs_pipeline_markdown(data_vs_pipeline_audit),
        encoding="utf-8",
    )
    (out_dir / REPORT_NAME).write_text(
        _build_markdown_report(
            run_id=run_id,
            summary=summary,
            family_rows=family_readiness,
            candidate_rows=candidate_rows,
        ),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a diagnostic evidence ledger from a robustness sensitivity report.",
    )
    parser.add_argument("--sensitivity-report", required=True, type=Path)
    parser.add_argument("--screening-artifact-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--source-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_id = args.run_id or args.sensitivity_report.stem
    try:
        summary = build_evidence_ledger(
            sensitivity_report_path=args.sensitivity_report,
            screening_artifact_dir=args.screening_artifact_dir,
            out_dir=args.out_dir,
            run_id=run_id,
            source_root=args.source_root,
        )
    except Exception as exc:
        return _error(str(exc))
    print(_compact_json({"status": "ok", **summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
