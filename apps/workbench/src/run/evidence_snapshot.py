"""Normalized run evidence for Workbench tabs."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from workbench.src.artifacts.paths import workbench_runs_dir_for
from workbench.src.run.feature_fabric import (
    ARTIFACT_NAMES as FEATURE_FABRIC_ARTIFACT_NAMES,
    ensure_catalog_feature_fabric,
    source_to_lane,
)
from economic_event_universe.service import inventory as event_universe_inventory
from economic_event_universe.service import list_calendar_rows, list_runnable_events


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


LANE_RUN_SOURCES = {
    "cme_rithmic": "cme_futures",
    "equities": "equities",
    "options": "options",
}
STALE_WHEN_ACTIVE_SOURCES = {"workbench_campaign", "autonomous", "crypto_lane"}

ALL_LANES_TERMINAL_STATES = {
    "EXECUTED",
    "BLOCKED_MISSING_DATA",
    "BLOCKED_ENDPOINT",
    "BLOCKED_VALIDATION",
    "BLOCKED_ROBUSTNESS",
    "BLOCKED_LATENCY",
    "QUARANTINED",
    "PROMOTED",
}


def _source_lane(source: str) -> str:
    return source_to_lane(source)


def workbench_run_sources() -> list[str]:
    return ["hbt_runs", "all_lanes", "cme_rithmic", "equities", "options", "workbench_campaign", "autonomous"]


def _active_run_path(repo: Path) -> Path:
    return repo / "runtime" / "workbench" / "active_run.json"


def _active_run_manifest(repo: Path) -> dict[str, Any]:
    return read_json(_active_run_path(repo))


def _active_all_lanes_run_dir(repo: Path) -> tuple[str, Path | None]:
    active = _active_run_manifest(repo)
    run_id = str(active.get("run_id") or "")
    if not run_id:
        return "", None
    return run_id, repo / "runtime" / "workbench" / "all_lanes" / run_id


def _lane_registry_snapshot(repo: Path) -> dict[str, Any]:
    try:
        from hft3.validation.lanes.lane_registry import LaneRegistry
        from hft3.validation.lanes.registration import register_all_lanes

        register_all_lanes()
        rows: list[dict[str, Any]] = []
        by_lane: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, Any]] = []
        for registration in LaneRegistry.instance().all_registrations():
            lane_value = registration.lane.value
            try:
                config = registration.config_loader()
                config_payload = config.to_dict() if hasattr(config, "to_dict") else {}
                load_status = "loaded"
                load_error = ""
            except Exception as exc:
                config_payload = {}
                load_status = "error"
                load_error = str(exc)
                errors.append(
                    {
                        "lane": lane_value,
                        "stage": "config_loader",
                        "status": "BLOCKING",
                        "error": load_error,
                    }
                )
            capability = config_payload.get("capability_profile") or {}
            row = {
                "lane": lane_value,
                "source": "cme_rithmic" if lane_value == "cme_futures" else lane_value,
                "symbols": ", ".join(map(str, config_payload.get("symbols") or [])),
                "event_types": ", ".join(map(str, config_payload.get("event_types") or [])),
                "test_paths": ", ".join(map(str, registration.test_paths)),
                "capability": capability.get("name", ""),
                "is_hft": capability.get("is_hft"),
                "dma": capability.get("dma"),
                "node_direct": capability.get("node_direct"),
                "load_status": load_status,
                "load_error": load_error,
                "config": config_payload,
            }
            rows.append(row)
            by_lane[lane_value] = row
        if not rows:
            errors.append(
                {
                    "lane": "",
                    "stage": "registration",
                    "status": "BLOCKING",
                    "error": "Lane registry returned no registered lanes.",
                }
            )
        blocking_gates = [
            {
                "gate": "lane_registry",
                "status": error["status"],
                "lane": error.get("lane", ""),
                "reason": error.get("error", ""),
            }
            for error in errors
        ]
        return {
            "status": "BLOCKING" if errors else "PASS",
            "rows": rows,
            "by_lane": by_lane,
            "errors": errors,
            "blocking_gates": blocking_gates,
            "repo": str(repo),
        }
    except Exception as exc:
        error = {
            "lane": "",
            "stage": "registration",
            "status": "BLOCKING",
            "error": str(exc),
        }
        return {
            "status": "BLOCKING",
            "rows": [],
            "by_lane": {},
            "errors": [error],
            "blocking_gates": [
                {
                    "gate": "lane_registry",
                    "status": "BLOCKING",
                    "lane": "",
                    "reason": str(exc),
                }
            ],
            "repo": str(repo),
        }


def _json_rows(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _first_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric / 1_000_000_000.0 if numeric > 1_000_000_000_000 else numeric
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            numeric = float(text)
            return numeric / 1_000_000_000.0 if numeric > 1_000_000_000_000 else numeric
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _pit_issues(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    availability_keys = (
        "source_available_timestamp",
        "available_timestamp",
        "availability_timestamp",
        "available_at",
        "data_available_timestamp",
    )
    decision_keys = (
        "decision_timestamp",
        "prediction_cutoff_timestamp",
        "cutoff_timestamp",
        "consumer_decision_timestamp",
        "decision_time",
    )
    unsafe_statuses = {"FAIL", "FAILED", "BLOCKING", "UNSAFE", "LEAKAGE", "LEAKY", "VIOLATION"}
    for index, row in enumerate(rows):
        feature = str(
            row.get("feature")
            or row.get("feature_name")
            or row.get("name")
            or row.get("id")
            or f"row_{index}"
        )
        available_raw = _first_value(row, availability_keys)
        decision_raw = _first_value(row, decision_keys)
        available = _timestamp_seconds(available_raw)
        decision = _timestamp_seconds(decision_raw)
        if available is None:
            issues.append(
                {
                    "feature": feature,
                    "issue": "missing_or_invalid_source_available_timestamp",
                    "value": available_raw,
                }
            )
        if decision is None:
            issues.append(
                {
                    "feature": feature,
                    "issue": "missing_or_invalid_decision_timestamp",
                    "value": decision_raw,
                }
            )
        if available is not None and decision is not None and available > decision:
            issues.append(
                {
                    "feature": feature,
                    "issue": "source_available_after_decision_timestamp",
                    "available_timestamp": available_raw,
                    "decision_timestamp": decision_raw,
                }
            )
        explicit_status = str(row.get("pit_status") or row.get("leakage_audit_status") or "").upper()
        if explicit_status in unsafe_statuses:
            issues.append(
                {
                    "feature": feature,
                    "issue": "explicit_pit_status_failed",
                    "pit_status": explicit_status,
                }
            )
        for flag in ("pit_safe", "is_pit_safe", "leakage_safe"):
            if flag in row and row.get(flag) is False:
                issues.append(
                    {
                        "feature": feature,
                        "issue": f"{flag}_false",
                        "pit_status": "FAIL",
                    }
                )
    return issues


def _run_identity_issues(
    *,
    selected_run_id: str,
    artifacts: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not selected_run_id:
        return []
    issues: list[dict[str, Any]] = []
    for name, payload in artifacts.items():
        if not payload:
            continue
        artifact_run_id = str(payload.get("run_id") or "")
        if artifact_run_id != selected_run_id:
            issues.append(
                {
                    "artifact": name,
                    "issue": "artifact_run_id_mismatch",
                    "expected_run_id": selected_run_id,
                    "observed_run_id": artifact_run_id,
                }
            )
    for index, row in enumerate(rows):
        row_run_id = str(row.get("run_id") or "")
        if row_run_id != selected_run_id:
            issues.append(
                {
                    "artifact": "feature_lineage",
                    "feature": row.get("feature", f"row_{index}"),
                    "issue": "lineage_row_run_id_mismatch",
                    "expected_run_id": selected_run_id,
                    "observed_run_id": row_run_id,
                }
            )
    return issues


def _feature_fabric_snapshot(
    repo: Path,
    *,
    selected_root: str | Path | None = None,
    consumer_lane: str = "",
    selected_run_id: str = "",
) -> dict[str, Any]:
    root = Path(selected_root) if selected_root else repo / "runtime" / "workbench" / "feature_fabric"
    artifact_names = (
        "feature_fabric_manifest.json",
        "feature_lineage.json",
        "feature_pit_audit.json",
        "rejected_features.json",
    )
    paths = {name: root / name for name in artifact_names}
    observed_paths = {name: str(path) for name, path in paths.items() if path.is_file()}
    manifest = read_json(paths["feature_fabric_manifest.json"])
    lineage = read_json(paths["feature_lineage.json"])
    pit_audit = read_json(paths["feature_pit_audit.json"])
    rejected = read_json(paths["rejected_features.json"])
    lane_registry = _lane_registry_snapshot(repo)
    lane_values = [row["lane"] for row in lane_registry.get("rows", []) if row.get("lane")]
    lineage_rows = (
        _json_rows(lineage, "features", "rows", "feature_lineage")
        or _json_rows(pit_audit, "features", "rows", "audits")
    )
    missing_artifacts = [name for name, path in paths.items() if not path.is_file()]
    issues = _pit_issues(lineage_rows)
    identity_issues = _run_identity_issues(
        selected_run_id=selected_run_id,
        artifacts={
            "feature_fabric_manifest.json": manifest,
            "feature_lineage.json": lineage,
            "feature_pit_audit.json": pit_audit,
            "rejected_features.json": rejected,
        },
        rows=lineage_rows,
    )
    blocking_gates: list[dict[str, Any]] = []
    if missing_artifacts:
        blocking_gates.append(
            {
                "gate": "feature_fabric_artifacts",
                "status": "MISSING",
                "reason": "Cross-lane feature fabric evidence artifacts are missing.",
                "missing_artifacts": missing_artifacts,
            }
        )
    if not lineage_rows:
        blocking_gates.append(
            {
                "gate": "feature_fabric_lineage",
                "status": "MISSING",
                "reason": "No feature lineage rows were observed for PIT validation.",
            }
        )
    if issues:
        blocking_gates.append(
            {
                "gate": "feature_pit_audit",
                "status": "FAIL",
                "reason": "One or more cross-lane features failed point-in-time validation.",
                "issue_count": len(issues),
            }
        )
    if identity_issues:
        blocking_gates.append(
            {
                "gate": "feature_fabric_run_identity",
                "status": "STALE",
                "reason": "Feature fabric artifacts do not belong to the selected active run.",
                "issue_count": len(identity_issues),
                "selected_run_id": selected_run_id,
            }
        )
    pit_validation_status = "PASS" if lineage_rows and not issues else ("FAIL" if issues else "MISSING")
    status = "OBSERVED" if not blocking_gates else "BLOCKING"
    return {
        "status": status,
        "gate_status": "PASS" if not blocking_gates else "BLOCKING",
        "evidence_gate_passed": not blocking_gates,
        "policy": "cross_lane_features_allowed_only_when_point_in_time_safe",
        "consumer_lane": consumer_lane,
        "allowed_source_lanes": lane_values,
        "pit_rule": "source_available_timestamp <= decision_timestamp",
        "pit_validation_status": pit_validation_status,
        "catalog_pit_eligibility_status": manifest.get("catalog_pit_eligibility_status", pit_validation_status),
        "model_feature_usage_status": manifest.get(
            "model_feature_usage_status",
            lineage.get("model_feature_usage_status", "not_observed"),
        ),
        "model_feature_usage_observed": str(
            manifest.get("model_feature_usage_status")
            or lineage.get("model_feature_usage_status")
            or "not_observed"
        ).lower()
        not in {"", "not_observed", "none", "missing"},
        "artifact_root": str(root),
        "artifact_paths": observed_paths,
        "expected_artifacts": {name: str(path) for name, path in paths.items()},
        "required_artifacts": list(artifact_names),
        "missing_artifacts": missing_artifacts,
        "manifest": manifest,
        "lineage": lineage,
        "pit_audit": pit_audit,
        "pit_issues": issues,
        "run_identity_issues": identity_issues,
        "blocking_gates": blocking_gates,
        "rejected_features": rejected,
        "rows": lineage_rows,
        "secret_exposed": False,
    }


def _has_feature_fabric_artifacts(root: Path) -> bool:
    return all((root / name).is_file() for name in FEATURE_FABRIC_ARTIFACT_NAMES)


def _ensure_catalog_feature_fabric(
    repo: Path,
    *,
    consumer_lane: str,
    selected_root: str | Path | None = None,
    run_id: str = "",
) -> Path:
    root = Path(selected_root) if selected_root else repo / "runtime" / "workbench" / "feature_fabric"
    manifest = read_json(root / "feature_fabric_manifest.json") if _has_feature_fabric_artifacts(root) else {}
    identity_mismatch = bool(run_id) and str(manifest.get("run_id") or "") != run_id
    if not _has_feature_fabric_artifacts(root) or identity_mismatch:
        ensure_catalog_feature_fabric(repo, consumer_lane, output_root=root, run_id=run_id)
    return root


def _shared_blocking_gates(lane_registry: dict[str, Any], feature_fabric: dict[str, Any]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for gate in (lane_registry.get("blocking_gates") or []):
        gates.append(dict(gate))
    for gate in (feature_fabric.get("blocking_gates") or []):
        gates.append(dict(gate))
    return gates


def _append_decision_blocking_gates(snapshot: RunEvidenceSnapshot, gates: list[dict[str, Any]]) -> None:
    if not gates:
        return
    decision = dict(snapshot.decision or {})
    existing = list(decision.get("blocking_gates") or [])
    seen = {(gate.get("gate"), gate.get("lane"), gate.get("status")) for gate in existing}
    for gate in gates:
        key = (gate.get("gate"), gate.get("lane"), gate.get("status"))
        if key not in seen:
            existing.append(gate)
            seen.add(key)
    decision["blocking_gates"] = existing
    decision["live_registry_ready"] = False
    if str(decision.get("action") or "").upper() == "PROMOTE":
        gate_names = ", ".join(str(gate.get("gate", "")) for gate in gates if gate.get("gate"))
        decision["action"] = "QUARANTINE"
        decision["reason"] = f"Shared Workbench evidence gates are blocking: {gate_names}."
    snapshot.decision = decision


def _rithmic_endpoint_status(repo: Path, *, force_paper: bool = False) -> dict[str, Any]:
    from data_system.rithmic_trial.endpoint_status import (
        PAPER_ENDPOINT_PROFILE,
        default_api_config_for_profile,
        endpoint_status_from_config,
    )

    env_config = os.environ.get("RITHMIC_API_CONFIG", "").strip()
    profile = os.environ.get("RITHMIC_ENDPOINT_PROFILE", "").strip()
    if force_paper:
        profile = PAPER_ENDPOINT_PROFILE
    if env_config and not force_paper:
        config_path = Path(env_config)
        if not config_path.is_absolute():
            config_path = repo / config_path
    else:
        config_path = default_api_config_for_profile(
            repo,
            PAPER_ENDPOINT_PROFILE if profile == PAPER_ENDPOINT_PROFILE or force_paper else "test_orangeburg",
        )
    return endpoint_status_from_config(config_path, repo_root=repo)


def _same_path(left: str | Path | None, right: str | Path | None) -> bool:
    if not left or not right:
        return False
    try:
        return str(Path(left).resolve()).lower() == str(Path(right).resolve()).lower()
    except OSError:
        return str(left).lower() == str(right).lower()


def _input_files_include(payload: dict[str, Any], expected: Path) -> bool:
    return any(_same_path(item, expected) for item in (payload.get("input_files") or []))


def _rithmic_report_binding(
    *,
    manifest: dict[str, Any],
    reports: dict[str, dict[str, Any]],
    raw_file: Path,
    normalized_file: Path,
    replay_file: Path,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    expected_row_count = manifest.get("row_count")
    expected_checksum = manifest.get("checksum_sha256")

    capture = reports.get("data_capture") or {}
    capture_manifest = capture.get("manifest") or {}
    if not capture:
        issues.append({"artifact": "data_capture_report.json", "issue": "missing"})
    else:
        if not _same_path(capture_manifest.get("raw_file"), raw_file):
            issues.append(
                {
                    "artifact": "data_capture_report.json",
                    "issue": "raw_file_mismatch",
                    "expected": str(raw_file),
                    "actual": capture_manifest.get("raw_file"),
                }
            )
        if expected_checksum and capture_manifest.get("checksum_sha256") != expected_checksum:
            issues.append(
                {
                    "artifact": "data_capture_report.json",
                    "issue": "checksum_mismatch",
                    "expected": expected_checksum,
                    "actual": capture_manifest.get("checksum_sha256"),
                }
            )
        if expected_row_count is not None and capture_manifest.get("row_count") != expected_row_count:
            issues.append(
                {
                    "artifact": "data_capture_report.json",
                    "issue": "row_count_mismatch",
                    "expected": expected_row_count,
                    "actual": capture_manifest.get("row_count"),
                }
            )

    schema = reports.get("schema_mapping") or {}
    if not schema:
        issues.append({"artifact": "schema_mapping_report.json", "issue": "missing"})
    else:
        if not _same_path(schema.get("raw_file"), raw_file):
            issues.append(
                {
                    "artifact": "schema_mapping_report.json",
                    "issue": "raw_file_mismatch",
                    "expected": str(raw_file),
                    "actual": schema.get("raw_file"),
                }
            )
        if not _same_path(schema.get("normalized_file"), normalized_file):
            issues.append(
                {
                    "artifact": "schema_mapping_report.json",
                    "issue": "normalized_file_mismatch",
                    "expected": str(normalized_file),
                    "actual": schema.get("normalized_file"),
                }
            )

    quality = reports.get("data_quality") or {}
    if not quality:
        issues.append({"artifact": "data_quality_report.json", "issue": "missing"})
    else:
        if not _input_files_include(quality, normalized_file):
            issues.append(
                {
                    "artifact": "data_quality_report.json",
                    "issue": "normalized_input_mismatch",
                    "expected": str(normalized_file),
                    "actual": quality.get("input_files"),
                }
            )
        if expected_row_count is not None and quality.get("event_count") != expected_row_count:
            issues.append(
                {
                    "artifact": "data_quality_report.json",
                    "issue": "event_count_mismatch",
                    "expected": expected_row_count,
                    "actual": quality.get("event_count"),
                }
            )

    latency = reports.get("latency_profile") or {}
    if not latency:
        issues.append({"artifact": "latency_profile.json", "issue": "missing"})
    elif not _input_files_include(latency, normalized_file):
        issues.append(
            {
                "artifact": "latency_profile.json",
                "issue": "normalized_input_mismatch",
                "expected": str(normalized_file),
                "actual": latency.get("input_files"),
            }
        )

    conversion = reports.get("hftbacktest_conversion") or {}
    if not conversion:
        issues.append({"artifact": "hftbacktest_conversion_report.json", "issue": "missing"})
    else:
        if not _same_path(conversion.get("output_file"), replay_file):
            issues.append(
                {
                    "artifact": "hftbacktest_conversion_report.json",
                    "issue": "replay_output_mismatch",
                    "expected": str(replay_file),
                    "actual": conversion.get("output_file"),
                }
            )
        if not replay_file.is_file():
            issues.append(
                {
                    "artifact": "hftbacktest_conversion_report.json",
                    "issue": "replay_npz_missing",
                    "expected": str(replay_file),
                }
            )

    if not (reports.get("paper_order_summary") or reports.get("rithmic_test_order_summary")):
        issues.append({"artifact": "paper_order_summary.json", "issue": "missing"})

    return {
        "status": "PASS" if not issues else "BLOCKING",
        "issues": issues,
        "bound_manifest_checksum": expected_checksum,
        "bound_raw_file": str(raw_file),
        "bound_normalized_file": str(normalized_file),
        "bound_replay_file": str(replay_file),
    }


def _latest_rithmic_trial_bundle(repo: Path) -> dict[str, Any]:
    capture_root = repo / "data" / "raw" / "rithmic_trial_live_capture"
    manifests = [path for path in capture_root.glob("*/*/manifest.json") if path.is_file()]
    if not manifests:
        return {}
    manifest_path = max(manifests, key=_mtime)
    symbol_dir = manifest_path.parent
    date_dir = symbol_dir.parent
    capture_date = date_dir.name
    symbol = symbol_dir.name
    symbol_reports_dir = repo / "reports" / "rithmic_trial" / capture_date / symbol
    legacy_reports_dir = repo / "reports" / "rithmic_trial" / capture_date
    reports_dir = symbol_reports_dir if symbol_reports_dir.is_dir() else legacy_reports_dir
    normalized_file = repo / "data" / "normalized" / "rithmic_trial_live_capture" / capture_date / symbol / "events.ndjson"
    replay_file = (
        repo
        / "data"
        / "replay"
        / "hftbacktest"
        / "rithmic_trial"
        / capture_date
        / symbol
        / f"{symbol}_{capture_date}_trial.npz"
    )
    reports = {
        "data_capture": read_json(reports_dir / "data_capture_report.json"),
        "schema_mapping": read_json(reports_dir / "schema_mapping_report.json"),
        "data_quality": read_json(reports_dir / "data_quality_report.json"),
        "book_reconstruction": read_json(reports_dir / "book_reconstruction_report.json"),
        "latency_profile": read_json(reports_dir / "latency_profile.json"),
        "paper_order_summary": read_json(reports_dir / "paper_order_summary.json"),
        "rithmic_test_order_summary": read_json(reports_dir / "rithmic_test_order_summary.json"),
        "hftbacktest_conversion": read_json(reports_dir / "hftbacktest_conversion_report.json"),
    }
    paths = {
        "manifest": str(manifest_path),
        "raw_events": str(symbol_dir / "events.ndjson"),
        "normalized_events": str(normalized_file),
        "npz": str(replay_file),
        "reports_dir": str(reports_dir),
        "data_capture_report": str(reports_dir / "data_capture_report.json"),
        "schema_mapping_report": str(reports_dir / "schema_mapping_report.json"),
        "data_quality_report": str(reports_dir / "data_quality_report.json"),
        "book_reconstruction_report": str(reports_dir / "book_reconstruction_report.json"),
        "latency_profile": str(reports_dir / "latency_profile.json"),
        "paper_order_summary": str(reports_dir / "paper_order_summary.json"),
        "hftbacktest_conversion_report": str(reports_dir / "hftbacktest_conversion_report.json"),
    }
    manifest = read_json(manifest_path)
    report_binding = _rithmic_report_binding(
        manifest=manifest,
        reports=reports,
        raw_file=symbol_dir / "events.ndjson",
        normalized_file=normalized_file,
        replay_file=replay_file,
    )
    quality = reports["data_quality"]
    conversion = reports["hftbacktest_conversion"]
    latency = reports["latency_profile"]
    paper_summary = reports["paper_order_summary"] or reports["rithmic_test_order_summary"]
    event_type_counts = quality.get("event_type_counts") or {}
    row_count = quality.get("event_count") or manifest.get("row_count") or 0
    trade_count = event_type_counts.get("trade", 0)
    quote_count = event_type_counts.get("quote", 0)
    depth_count = event_type_counts.get("depth", 0)
    paired_count = paper_summary.get("paired_count", latency.get("paired_count", 0))
    quality_status = str(quality.get("status") or "missing")
    conversion_status = str(conversion.get("status") or "missing")
    latency_status = str(latency.get("status") or "missing")
    return {
        "run_id": f"rithmic_paper_{capture_date}_{symbol}",
        "root": str(symbol_dir),
        "capture_date": capture_date,
        "symbol": manifest.get("symbol") or symbol,
        "exchange": manifest.get("exchange") or "",
        "capture_environment": manifest.get("capture_environment") or "",
        "started_at": manifest.get("capture_start_time", ""),
        "finished_at": manifest.get("capture_end_time", ""),
        "manifest": manifest,
        "reports": reports,
        "capture_endpoint": ((manifest.get("known_limitations") or {}).get("endpoint_status") or {}),
        "reports_dir": str(reports_dir),
        "report_binding": report_binding,
        "report_binding_status": report_binding["status"],
        "report_binding_issues": report_binding["issues"],
        "legacy_report_layout": reports_dir == legacy_reports_dir,
        "paths": paths,
        "event_type_counts": event_type_counts,
        "row_count": row_count,
        "trade_count": trade_count,
        "quote_count": quote_count,
        "depth_count": depth_count,
        "paired_count": paired_count,
        "quality_status": quality_status,
        "conversion_status": conversion_status,
        "latency_status": latency_status,
        "npz_exists": replay_file.is_file(),
        "normalized_exists": normalized_file.is_file(),
    }


def _iso_timestamp_seconds(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _latest_latency_baseline_summary(
    repo: Path,
    *,
    broker: str = "",
    environment: str = "",
) -> dict[str, Any]:
    reports_root = repo / "reports" / "latency_baselines"
    if not reports_root.is_dir():
        return {}

    def matches_requested_endpoint(payload: dict[str, Any]) -> bool:
        broker_mode = payload.get("broker_mode") or {}
        if not broker_mode:
            return False
        if str(broker_mode.get("status") or "").lower() != "observed":
            return False
        if broker and str(broker_mode.get("broker") or "").lower() != broker.lower():
            return False
        if environment and str(broker_mode.get("environment") or "").lower() != environment.lower():
            return False
        return True

    def normalized_payload(path: Path, payload: dict[str, Any], *, role: str) -> dict[str, Any]:
        payload = dict(payload)
        payload["_path"] = str(path)
        payload["_baseline_role"] = payload.get("baseline_role") or role
        sample_path = payload.get("sample_path")
        if sample_path:
            resolved_sample_path = _resolve_latency_sample_path(repo, sample_path)
            payload["_sample_path"] = str(resolved_sample_path or sample_path)
            _backfill_latency_trigger_metrics(payload, resolved_sample_path)
        return payload

    current_path = reports_root / "current_baseline.json"
    current_payload = read_json(current_path)
    if (
        current_payload.get("schema_version") == "latency_baseline_summary_v1"
        and matches_requested_endpoint(current_payload)
    ):
        return normalized_payload(current_path, current_payload, role="current_baseline")

    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    for path in reports_root.glob("*_summary.json"):
        payload = read_json(path)
        if payload.get("schema_version") != "latency_baseline_summary_v1":
            continue
        if not matches_requested_endpoint(payload):
            continue
        timestamp = _iso_timestamp_seconds(payload.get("generated_at_utc"))
        candidates.append((timestamp if timestamp is not None else _mtime(path), path, payload))
    if not candidates:
        return {}
    _, path, payload = max(candidates, key=lambda item: item[0])
    return normalized_payload(path, payload, role="latest_observed_summary")


def _resolve_latency_sample_path(repo: Path, sample_path: Any) -> Path | None:
    if not sample_path:
        return None
    path = Path(str(sample_path))
    if path.is_file():
        return path
    text = str(sample_path).replace("\\", "/")
    marker = "data/latency_baselines/"
    if marker in text:
        candidate = repo / text[text.index(marker):]
        if candidate.is_file():
            return candidate
    candidate = repo / str(sample_path)
    if candidate.is_file():
        return candidate
    return None


def _read_latency_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _duration_us_from_raw(raw: dict[str, Any], start: str, end: str) -> float | None:
    try:
        start_ns = int(raw.get(start) or 0)
        end_ns = int(raw.get(end) or 0)
    except (TypeError, ValueError):
        return None
    if start_ns <= 0 or end_ns <= 0 or end_ns < start_ns:
        return None
    return (end_ns - start_ns) / 1000.0


def _metric_from_values(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "min_us": None,
            "mean_us": None,
            "p50_us": None,
            "p90_us": None,
            "p95_us": None,
            "p99_us": None,
            "p99_9_us": None,
            "max_us": None,
        }
    ordered = sorted(values)

    def percentile(pct: float) -> float:
        idx = round((pct / 100.0) * (len(ordered) - 1))
        idx = max(0, min(len(ordered) - 1, int(idx)))
        return ordered[idx]

    return {
        "count": len(ordered),
        "min_us": ordered[0],
        "mean_us": sum(ordered) / len(ordered),
        "p50_us": percentile(50.0),
        "p90_us": percentile(90.0),
        "p95_us": percentile(95.0),
        "p99_us": percentile(99.0),
        "p99_9_us": percentile(99.9),
        "max_us": ordered[-1],
    }


def _backfill_latency_trigger_metrics(payload: dict[str, Any], sample_path: Path | None) -> None:
    metrics = payload.setdefault("metrics", {})
    if not isinstance(metrics, dict):
        return
    trigger_metric = metrics.get("tick_to_send_trigger_us")
    if isinstance(trigger_metric, dict) and int(trigger_metric.get("count") or 0) > 0:
        return
    rows = [
        row for row in _read_latency_jsonl(sample_path)
        if row.get("order_action") == "new" and row.get("success") is True
    ]
    decision_values: list[float] = []
    tick_values: list[float] = []
    for row in rows:
        raw = row.get("raw_timestamps")
        if not isinstance(raw, dict):
            continue
        decision = _duration_us_from_raw(raw, "decision_ready_ts", "order_api_call_start_ts")
        tick = _duration_us_from_raw(raw, "market_event_received_ts", "order_api_call_start_ts")
        if decision is not None:
            decision_values.append(decision)
        if tick is not None:
            tick_values.append(tick)
    if tick_values:
        metrics["decision_to_send_trigger_us"] = _metric_from_values(decision_values)
        metrics["tick_to_send_trigger_us"] = _metric_from_values(tick_values)
        payload.setdefault("placement_trigger_kpi", "tick_to_send_trigger_us")


def _latency_metric_count(summary: dict[str, Any], metric: str) -> int:
    try:
        return int(((summary.get("metrics") or {}).get(metric) or {}).get("count") or 0)
    except (TypeError, ValueError):
        return 0


def _event_universe_snapshot(repo: Path) -> dict[str, Any]:
    try:
        inv = event_universe_inventory(repo)
        calendar_rows = list_calendar_rows(repo, include_seed=True)
        runnable_rows = list_runnable_events(repo)
        snapshot_dir = repo / "runtime" / "event_snapshots"
        snapshot_files = list(snapshot_dir.glob("*")) if snapshot_dir.is_dir() else []
        return {
            "status": "PASS",
            "canonical_config_root": inv.get("canonical_config_root", ""),
            "generated_events_csv": inv.get("generated_events_csv", ""),
            "event_type_count": inv.get("event_type_count", 0),
            "calendar_row_count": inv.get("calendar_row_count", 0),
            "runnable_event_count": inv.get("runnable_event_count", 0),
            "row_status_counts": inv.get("row_status_counts", {}),
            "event_types": inv.get("event_types", []),
            "calendar_rows": calendar_rows,
            "runnable_events": runnable_rows,
            "snapshot_artifact_count": len([p for p in snapshot_files if p.is_file()]),
            "snapshot_artifact_dir": str(snapshot_dir),
            "universe_defined_label": "Universe defined",
            "runnable_label": "Runnable data available",
        }
    except Exception as exc:
        return {
            "status": "BLOCKING",
            "error": str(exc),
            "blocking_gates": [
                {
                    "gate": "event_universe",
                    "status": "BLOCKING",
                    "reason": str(exc),
                }
            ],
        }


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
    trade_manager: dict[str, Any] = field(default_factory=dict)
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


_HBT_RUN_ARTIFACTS = (
    "run_manifest.json",
    "data_manifest.json",
    "hbt_config.json",
    "strategy_config.json",
    "normalized_input_manifest.json",
    "data_validation.json",
    "recorder_result.npz",
    "stats_summary.json",
    "latency_report.json",
    "fill_quality_report.json",
    "queue_diagnostics.json",
    "robustness_report.json",
    "promotion_decision.json",
    "audit.md",
)


def _hbt_runs_root(repo: Path) -> Path:
    return repo / "artifacts" / "hbt_runs"


def _latest_hbt_run_dir(repo: Path) -> Path | None:
    return _latest_dir_with(_hbt_runs_root(repo), "run_manifest.json")


def _hbt_artifact_paths(run_dir: Path) -> dict[str, str]:
    return {
        name.rsplit(".", 1)[0]: str(run_dir / name)
        for name in _HBT_RUN_ARTIFACTS
        if (run_dir / name).is_file()
    }


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


_SESSION_JSON_OBJECT_ARTIFACTS = (
    "session_manifest.json",
    "active_models.json",
    "registry_references.json",
    "risk_limits.json",
    "latency_metrics.json",
    "slippage_metrics.json",
    "session_metrics.json",
)

_SESSION_JSONL_ARTIFACTS = (
    "order_intents.jsonl",
    "order_state_transitions.jsonl",
    "risk_rejections.jsonl",
    "fills.jsonl",
    "positions.jsonl",
    "pnl_timeseries.jsonl",
    "incident_log.jsonl",
    "kill_switch_events.jsonl",
)

_SESSION_ARTIFACTS = _SESSION_JSON_OBJECT_ARTIFACTS + _SESSION_JSONL_ARTIFACTS + ("session_report.md",)


def _latest_session_dir(repo: Path) -> Path | None:
    candidates: list[Path] = []
    for root in (
        repo / "artifacts" / "sessions",
        repo / "runtime" / "trade_manager" / "sessions",
        repo / "runtime" / "sessions",
    ):
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if path.is_dir() and any((path / name).is_file() for name in _SESSION_ARTIFACTS):
                candidates.append(path)
    return max(candidates, key=_mtime) if candidates else None


def _active_models_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("active_models", payload.get("models", []))
    if isinstance(raw, dict):
        raw = list(raw.values())
    return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []


def _latest_by_key(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = next((str(row.get(key)) for key in keys if row.get(key)), "")
        if not identifier:
            continue
        prev = latest.get(identifier)
        if prev is None or _timestamp_ns(row) >= _timestamp_ns(prev):
            latest[identifier] = row
    return [latest[key] for key in sorted(latest)]


def _timestamp_ns(row: dict[str, Any]) -> int:
    value = row.get("timestamp_ns", -1)
    if isinstance(value, bool):
        return -1
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return -1


def _numeric_field(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field}: NUMERIC_REQUIRED")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(f"{field}: NUMERIC_REQUIRED") from exc
    else:
        raise ValueError(f"{field}: NUMERIC_REQUIRED")
    if not math.isfinite(number):
        raise ValueError(f"{field}: NON_FINITE_NUMBER")
    return number


def _position_has_exposure(row: dict[str, Any]) -> bool:
    values: list[float] = []
    for key in ("quantity", "net_position"):
        if key in row:
            number = _numeric_field(row.get(key), key)
            if number is not None:
                values.append(number)
    positions = row.get("positions")
    if isinstance(positions, dict):
        for symbol, quantity in positions.items():
            number = _numeric_field(quantity, f"positions.{symbol}")
            if number is not None:
                values.append(number)
    elif positions is not None:
        raise ValueError("positions: JSON_OBJECT_REQUIRED")
    return any(abs(value) > 0.0 for value in values)


def _run_ids_from_payload(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"run_id", "source_run_id", "workbench_run_id", "candidate_run_id"} and child:
                found.add(str(child))
            else:
                found.update(_run_ids_from_payload(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_run_ids_from_payload(child))
    return found


def _path_is_within(path: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        resolved_path = path.resolve()
        resolved_root = root.resolve()
    except OSError:
        return False
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _selected_run_link(
    *,
    selected_run_id: str,
    selected_root: Path | None,
    session_dir: Path,
    session_run_ids: set[str],
) -> tuple[str, str]:
    if _path_is_within(session_dir, selected_root):
        return "MATCHED", "Trade Manager session artifacts live under the selected run root."
    if not selected_run_id:
        return "NOT_SELECTED", "No selected run id was supplied for session linkage."
    if selected_run_id in session_run_ids:
        return "MATCHED", "Trade Manager session run id matches the selected Workbench run."
    if session_run_ids:
        return (
            "UNLINKED",
            "Latest Trade Manager session references a different run id than the selected Workbench run.",
        )
    return (
        "UNDECLARED",
        "Latest Trade Manager session does not declare a run id, so it cannot be attached to the selected Workbench run.",
    )


def _trade_manager_artifact_error(
    *,
    repo: Path,
    session_dir: Path,
    promoted_models: list[dict[str, Any]],
    activation_errors: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    activation_errors = [*activation_errors, {"stage": "session_artifact_load", "reason": reason}]
    return {
        "status": "artifact_error",
        "reason": f"Trade Manager session artifact load failed: {reason}",
        "sessions_root": str(session_dir.parent if session_dir else repo / "artifacts" / "sessions"),
        "session_id": session_dir.name if session_dir else "",
        "session_path": str(session_dir) if session_dir else "",
        "promoted_model_count": len(promoted_models),
        "promoted_models": promoted_models,
        "active_models": [],
        "open_positions": [],
        "open_orders": [],
        "latest_order_states": [],
        "order_intents": [],
        "risk_rejections": [],
        "fills": [],
        "pnl_latest": {},
        "pnl_timeseries": [],
        "latency": {},
        "slippage": {},
        "kill_switch": {"status": "ARTIFACT_ERROR", "active": None},
        "incidents": [],
        "session_metrics": {},
        "risk_limits": {},
        "registry_references": {},
        "artifact_counts": {name: 0 for name in _SESSION_JSONL_ARTIFACTS},
        "unavailable_artifacts": [],
        "activation_errors": activation_errors,
        "selected_run_link_status": "ERROR",
        "selected_run_link_reason": "Session artifacts could not be loaded safely.",
        "session_run_ids": [],
        "live_routing_status": "NOT_WIRED",
        "live_routing_reason": "Live routing stays blocked because Trade Manager session evidence is malformed.",
    }


def _trade_manager_snapshot(
    repo: Path,
    *,
    selected_run_id: str = "",
    selected_root: str | Path | None = None,
) -> dict[str, Any]:
    sessions_root = repo / "artifacts" / "sessions"
    session_dir = _latest_session_dir(repo)
    selected_root_path = Path(selected_root) if selected_root else None
    promoted_models: list[dict[str, Any]] = []
    activation_errors: list[dict[str, Any]] = []
    try:
        from hft3.validation.certification_registry import list_promotion_models, load_latest_promotion

        for model_id in list_promotion_models(repo):
            record = load_latest_promotion(model_id, repo)
            if record is not None:
                row = record.to_dict()
                if row.get("promotion_status") == "PROMOTED":
                    promoted_models.append(row)
    except Exception as exc:
        activation_errors.append({"stage": "promotion_registry", "reason": str(exc)})

    if session_dir is None:
        return {
            "status": "not_observed",
            "reason": "No Trade Manager session artifacts were found.",
            "sessions_root": str(sessions_root),
            "session_id": "",
            "session_path": "",
            "promoted_model_count": len(promoted_models),
            "promoted_models": promoted_models,
            "active_models": [],
            "open_positions": [],
            "open_orders": [],
            "latest_order_states": [],
            "risk_rejections": [],
            "fills": [],
            "pnl_latest": {},
            "pnl_timeseries": [],
            "latency": {},
            "slippage": {},
            "kill_switch": {"status": "NOT_OBSERVED", "active": None},
            "incidents": [],
            "session_metrics": {},
            "artifact_counts": {name: 0 for name in _SESSION_JSONL_ARTIFACTS},
            "unavailable_artifacts": list(_SESSION_ARTIFACTS),
            "activation_errors": activation_errors,
            "selected_run_id": selected_run_id,
            "selected_run_link_status": "NO_SESSION",
            "selected_run_link_reason": "No Trade Manager session artifacts were found.",
            "session_run_ids": [],
            "live_routing_status": "NOT_WIRED",
            "live_routing_reason": "Trade Manager artifacts are present as inert/session evidence; no live execution orchestration is wired.",
        }

    try:
        from observer.read_model import ArtifactLoadError, load_observer_view
        from observer.read_model import _read_json_object as _strict_json_object
        from observer.read_model import _read_jsonl_objects as _strict_jsonl_objects
        from trade_manager.order_state import ORDER_STATE_VALUES, TERMINAL_ORDER_STATES
    except Exception as exc:
        return _trade_manager_artifact_error(
            repo=repo,
            session_dir=session_dir,
            promoted_models=promoted_models,
            activation_errors=activation_errors,
            reason=f"STRICT_LOADER_UNAVAILABLE: {exc}",
        )

    try:
        observer_view = load_observer_view(session_dir.parent, session_dir.name)
        objects = {
            name: _strict_json_object(session_dir / name) if (session_dir / name).is_file() else {}
            for name in _SESSION_JSON_OBJECT_ARTIFACTS
        }
        records = {
            name: list(_strict_jsonl_objects(session_dir / name)) if (session_dir / name).is_file() else []
            for name in _SESSION_JSONL_ARTIFACTS
        }
        terminal_states = {str(getattr(state, "value", state)).upper() for state in TERMINAL_ORDER_STATES}
        valid_states = {str(state).upper() for state in ORDER_STATE_VALUES}
    except ArtifactLoadError as exc:
        return _trade_manager_artifact_error(
            repo=repo,
            session_dir=session_dir,
            promoted_models=promoted_models,
            activation_errors=activation_errors,
            reason=str(exc),
        )

    try:
        active_models = _active_models_from_payload(objects.get("active_models.json", {}))
        positions = records["positions.jsonl"]
        order_states = _latest_by_key(records["order_state_transitions.jsonl"], ("order_intent_id", "order_id"))
        invalid_states = [
            str(row.get("state") or "")
            for row in order_states
            if str(row.get("state") or "").upper() not in valid_states
        ]
        if invalid_states:
            raise ValueError(f"order_state_transitions.jsonl: UNKNOWN_ORDER_STATE: {', '.join(invalid_states)}")
        open_orders = [
            row
            for row in order_states
            if str(row.get("state") or "").upper() not in terminal_states
        ]
        open_positions = [row for row in positions if _position_has_exposure(row)]
    except ValueError as exc:
        return _trade_manager_artifact_error(
            repo=repo,
            session_dir=session_dir,
            promoted_models=promoted_models,
            activation_errors=activation_errors,
            reason=str(exc),
        )

    pnl_rows = records["pnl_timeseries.jsonl"]
    kill_rows = records["kill_switch_events.jsonl"]
    incident_rows = records["incident_log.jsonl"]
    unavailable = [name for name in _SESSION_ARTIFACTS if not (session_dir / name).is_file()]
    session_run_ids = (
        _run_ids_from_payload(objects.get("session_manifest.json"))
        | _run_ids_from_payload(active_models)
        | _run_ids_from_payload(records["order_intents.jsonl"])
    )
    link_status, link_reason = _selected_run_link(
        selected_run_id=selected_run_id,
        selected_root=selected_root_path,
        session_dir=session_dir,
        session_run_ids=session_run_ids,
    )
    status = "observed" if link_status in {"MATCHED", "NOT_SELECTED"} else "observed_unlinked"
    reason = (
        "Trade Manager session artifacts observed."
        if status == "observed"
        else f"Trade Manager session artifacts observed but not linked to the selected run. {link_reason}"
    )
    return {
        "status": status,
        "reason": reason,
        "sessions_root": str(session_dir.parent),
        "session_id": session_dir.name,
        "session_path": str(session_dir),
        "promoted_model_count": len(promoted_models),
        "promoted_models": promoted_models,
        "active_models": active_models,
        "open_positions": open_positions,
        "open_orders": open_orders,
        "latest_order_states": order_states,
        "observer_symbols": list(observer_view.symbols),
        "order_intents": records["order_intents.jsonl"][-50:],
        "risk_rejections": records["risk_rejections.jsonl"][-50:],
        "fills": records["fills.jsonl"][-50:],
        "pnl_latest": pnl_rows[-1] if pnl_rows else {},
        "pnl_timeseries": pnl_rows[-250:],
        "latency": objects.get("latency_metrics.json", {}),
        "slippage": objects.get("slippage_metrics.json", {}),
        "kill_switch": kill_rows[-1] if kill_rows else {"status": "NOT_OBSERVED", "active": None},
        "incidents": incident_rows[-50:],
        "session_metrics": objects.get("session_metrics.json", {}),
        "risk_limits": objects.get("risk_limits.json", {}),
        "registry_references": objects.get("registry_references.json", {}),
        "artifact_counts": {name: len(records[name]) for name in _SESSION_JSONL_ARTIFACTS},
        "unavailable_artifacts": unavailable,
        "activation_errors": activation_errors,
        "selected_run_id": selected_run_id,
        "selected_run_link_status": link_status,
        "selected_run_link_reason": link_reason,
        "session_run_ids": sorted(session_run_ids),
        "live_routing_status": "NOT_WIRED",
        "live_routing_reason": "Trade Manager Phase 14-23 artifacts are observable; broker/live routing remains blocked until execution orchestration is implemented.",
    }


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


def _model_metrics_artifacts(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None or not run_dir.is_dir():
        return {}
    root = run_dir / "model_metrics"
    scorecard = read_json(root / "model_scorecard.json")
    envelope = read_json(root / "model_behavior_envelope.json")
    metric_values = read_json(root / "model_metric_values.json")
    logs = read_json(root / "model_metric_calculation_logs.json")
    paths = {
        "model_metric_values": root / "model_metric_values.json",
        "model_scorecard": root / "model_scorecard.json",
        "model_behavior_envelope": root / "model_behavior_envelope.json",
        "model_metric_calculation_logs": root / "model_metric_calculation_logs.json",
    }
    present_paths = {name: str(path) for name, path in paths.items() if path.is_file()}
    return {
        "status": "observed" if scorecard and envelope else ("error" if logs.get("status") == "ERROR" else "missing"),
        "scorecard": scorecard,
        "envelope": envelope,
        "metric_values": metric_values,
        "calculation_logs": logs,
        "paths": present_paths,
    }


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
        repo / "packages" / "backtest_pipeline" / "src" / "hft_backtest_builder.py",
        repo / "packages" / "backtest_pipeline" / "src" / "signal_backtester.py",
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
            artifact_contract="crypto candidate registry yaml (lane moved to hft3-crypto-lane)",
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


def _workbench_snapshot(repo: Path, campaign_id: str = "") -> RunEvidenceSnapshot:
    root = workbench_runs_dir_for(repo)
    if not campaign_id:
        return RunEvidenceSnapshot(
            source="workbench_campaign",
            state="blocked",
            current_stage="campaign_selection_required",
            root=str(root),
            decision={
                "action": "BLOCKED",
                "reason": "Workbench campaign evidence requires an explicit selected campaign id.",
                "live_registry_ready": False,
                "blocking_gates": [
                    {
                        "gate": "campaign_selection",
                        "status": "MISSING",
                        "reason": "Latest-campaign fallback is disabled to prevent stale artifact leakage.",
                    }
                ],
            },
        )
    run_dir = root / campaign_id
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
    event_latency_envelope = read_json(latest_event_dir / "latency_operating_envelope.json") if latest_event_dir else {}
    campaign_latency_envelope = read_json(run_dir / "campaign_latency_operating_envelope.json")
    wfc = read_json(run_dir / "wfc" / "wfc_summary.json") or summary.get("wfc", {})
    institutional_metrics = _model_metrics_artifacts(run_dir)
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
            "latest_event_latency_operating_envelope": str(latest_event_dir / "latency_operating_envelope.json") if latest_event_dir else "",
            "campaign_latency_operating_envelope": str(run_dir / "campaign_latency_operating_envelope.json"),
            "model_scorecard": (institutional_metrics.get("paths") or {}).get("model_scorecard", ""),
            "model_behavior_envelope": (institutional_metrics.get("paths") or {}).get("model_behavior_envelope", ""),
        },
        registry={"model_id": summary.get("model_id") or campaign.get("model_id"), "composition": summary.get("composition") or campaign.get("composition")},
        data={"symbol": summary.get("symbol") or campaign.get("symbol"), "periods": periods},
        backtest={"rows": event_rows, "periods": periods, "summary": summary},
        latency={
            "latest_event_diagnostics": event_diag,
            "cpp_latency_profile": event_diag.get("cpp_latency_profile", {}),
            "latency_operating_envelope": event_latency_envelope,
            "campaign_latency_operating_envelope": campaign_latency_envelope,
        },
        diagnostics={"composition": summary.get("composition", {}), "latest_event_diagnostics": event_diag},
        robustness={
            "wfc": wfc,
            "robustness_checks": summary.get("robustness_checks", []),
            "robustness_passed": summary.get("robustness_passed"),
            "pending": summary.get("robustness_pending_checks", []),
            "failed": summary.get("robustness_failed_checks", []),
            "latency_operating_envelope_status": summary.get("latency_operating_envelope_status"),
            "latency_operating_envelope_blockers": summary.get("latency_operating_envelope_blockers", []),
        },
        decision={
            "action": "PROMOTE" if summary.get("promote_candidate") else "QUARANTINE",
            "reason": summary.get("promote_note", ""),
            "live_registry_ready": bool(summary.get("promote_candidate")),
            "ranking": event_rows,
            "institutional_metrics": institutional_metrics,
            "blocking_gates": summary.get("blocking_gates", []),
        },
        reports={
            "summary": str(run_dir / "summary.json"),
            "latest_report": str(latest_event_dir / "report.md") if latest_event_dir else "",
            "latest_latency_operating_envelope": str(latest_event_dir / "latency_operating_envelope.md") if latest_event_dir else "",
            "campaign_latency_operating_envelope": str(run_dir / "campaign_latency_operating_envelope.md"),
            "after_action_report": str(latest_event_dir / "after_action_report.md") if latest_event_dir else "",
            "model_scorecard": (institutional_metrics.get("paths") or {}).get("model_scorecard", ""),
            "model_behavior_envelope": (institutional_metrics.get("paths") or {}).get("model_behavior_envelope", ""),
        },
        system={
            "summary": summary,
            "status": status,
            "campaign": campaign,
            "institutional_metrics": institutional_metrics,
            "campaign_latency_operating_envelope": campaign_latency_envelope,
        },
    )


def _hbt_runs_snapshot(repo: Path) -> RunEvidenceSnapshot:
    root = _hbt_runs_root(repo)
    run_dir = _latest_hbt_run_dir(repo)
    feature_fabric = {
        "status": "not_applicable",
        "gate_status": "PASS",
        "evidence_gate_passed": True,
        "consumer_lane": "hbt_runs",
        "blocking_gates": [],
        "rows": [],
        "reason": "HBT run artifacts are direct active backtest evidence, not cross-lane feature-fabric evidence.",
    }
    if run_dir is None:
        return RunEvidenceSnapshot(
            source="hbt_runs",
            state="idle",
            current_stage="hbt_run_required",
            root=str(root),
            stages=[
                {"name": "hbt_run_manifest", "status": "missing"},
                {"name": "hbt_strategy_results", "status": "blocked"},
                {"name": "hbt_promotion_decision", "status": "blocked"},
            ],
            diagnostics={"feature_fabric": feature_fabric},
            decision={
                "action": "BLOCKED",
                "reason": "No artifacts/hbt_runs/<run_id>/run_manifest.json was found.",
                "live_registry_ready": False,
                "blocking_gates": [
                    {
                        "gate": "hbt_run_manifest",
                        "status": "MISSING",
                        "reason": "Run scripts/run_hftbacktest_only.py to emit an active HftBacktest run artifact folder.",
                    }
                ],
            },
            system={
                "pipeline_coverage": [
                    _coverage_row(
                        "hbt_run_manifest",
                        "MISSING",
                        "artifacts/hbt_runs/<run_id>/run_manifest.json",
                        "No active HftBacktest run artifact is available.",
                        authority="HftBacktest-only pipeline",
                        role="active_run_identity",
                        artifact_contract="artifacts/hbt_runs/<run_id>/run_manifest.json",
                    )
                ],
                "feature_fabric": feature_fabric,
            },
        )

    manifest = read_json(run_dir / "run_manifest.json")
    data_manifest = read_json(run_dir / "data_manifest.json")
    hbt_config = read_json(run_dir / "hbt_config.json")
    strategy_config = read_json(run_dir / "strategy_config.json")
    normalized_input = read_json(run_dir / "normalized_input_manifest.json")
    data_validation = read_json(run_dir / "data_validation.json")
    stats = read_json(run_dir / "stats_summary.json")
    latency_report = read_json(run_dir / "latency_report.json")
    fill_quality = read_json(run_dir / "fill_quality_report.json")
    queue_diagnostics = read_json(run_dir / "queue_diagnostics.json")
    robustness_report = read_json(run_dir / "robustness_report.json")
    promotion = read_json(run_dir / "promotion_decision.json")
    artifact_paths = _hbt_artifact_paths(run_dir)
    run_id = str(manifest.get("run_id") or stats.get("run_id") or promotion.get("run_id") or run_dir.name)
    recorder_exists = (run_dir / "recorder_result.npz").is_file()
    stats_exists = bool(stats)
    promotion_exists = bool(promotion)
    data_validation_status = str(
        data_validation.get("data_validation_status")
        or data_manifest.get("validation_status")
        or normalized_input.get("validation_status")
        or "missing"
    ).lower()
    data_passed = data_validation_status == "pass"
    results_observed = recorder_exists and stats_exists
    missing_artifacts = [
        name
        for name in ("run_manifest.json", "recorder_result.npz", "stats_summary.json", "promotion_decision.json")
        if not (run_dir / name).is_file()
    ]
    blocking_gates: list[dict[str, Any]] = []
    if str(manifest.get("active_path") or "") != "hftbacktest_only":
        blocking_gates.append(
            {
                "gate": "hbt_active_path",
                "status": "BLOCKING",
                "reason": "run_manifest.json does not declare active_path=hftbacktest_only.",
                "artifact": str(run_dir / "run_manifest.json"),
            }
        )
    if data_validation and not data_passed:
        blocking_gates.append(
            {
                "gate": "hbt_data_validation",
                "status": "BLOCKING",
                "reason": "HftBacktest data validation did not pass.",
                "fail_closed_reasons": data_validation.get("fail_closed_reasons", []),
                "artifact": str(run_dir / "data_validation.json"),
            }
        )
    if missing_artifacts:
        blocking_gates.append(
            {
                "gate": "hbt_required_artifacts",
                "status": "MISSING",
                "reason": "Required HftBacktest-only output artifacts are missing.",
                "missing_artifacts": missing_artifacts,
                "artifact_dir": str(run_dir),
            }
        )

    decision_text = str(promotion.get("decision") or ("observe" if results_observed else "blocked")).upper()
    state = "observed" if results_observed and not blocking_gates else "blocked"
    current_stage = "hbt_promotion_decision_observed" if promotion_exists else "hbt_outputs_missing"
    hbt_row = {
        "run_id": run_id,
        "artifact_dir": str(run_dir),
        "symbol": manifest.get("symbol"),
        "contract": manifest.get("contract"),
        "event_id": manifest.get("event_id"),
        "strategy_id": manifest.get("strategy_id") or strategy_config.get("strategy_id"),
        "mechanical_validity_status": stats.get("mechanical_validity_status"),
        "economic_result_status": stats.get("economic_result_status"),
        "orders_submitted": stats.get("orders_submitted"),
        "fills_count": stats.get("fills_count"),
        "fill_rate": stats.get("fill_rate"),
        "net_pnl": stats.get("net_pnl"),
        "decision": promotion.get("decision", ""),
        "promotion_allowed": promotion.get("promotion_allowed", False),
    }
    hbt_diagnostics_observed = (
        bool(latency_report)
        and bool(fill_quality)
        and fill_quality.get("status") != "not_run"
        and bool(queue_diagnostics)
        and queue_diagnostics.get("status") != "not_run"
    )
    coverage = [
        _coverage_row(
            "hbt_run_manifest",
            "OBSERVED",
            str(run_dir / "run_manifest.json"),
            "The selected active result is bound to a concrete HftBacktest run_id and artifact folder.",
            authority="HftBacktest-only pipeline",
            role="active_run_identity",
            artifact_contract="artifacts/hbt_runs/<run_id>/run_manifest.json",
        ),
        _coverage_row(
            "hbt_data_validation",
            "OBSERVED" if data_passed else "BLOCKING",
            str(run_dir / "data_validation.json"),
            "HftBacktest-compatible event data validation passed."
            if data_passed
            else "HftBacktest-compatible event data validation is missing or failed.",
            authority="HftBacktest validation",
            role="admissibility_gate",
            artifact_contract="data_validation.json with data_validation_status=pass",
        ),
        _coverage_row(
            "hbt_strategy_results",
            "OBSERVED" if results_observed else "BLOCKING",
            str(run_dir),
            "recorder_result.npz and stats_summary.json exist for this HftBacktest run."
            if results_observed
            else "recorder_result.npz and stats_summary.json must exist before active results are displayed as complete.",
            authority="HftBacktest runner",
            role="required_execution_replay",
            artifact_contract="recorder_result.npz plus stats_summary.json",
        ),
        _coverage_row(
            "hbt_latency_fill_queue",
            "OBSERVED" if hbt_diagnostics_observed else "BLOCKING",
            str(run_dir),
            "Latency, fill-quality, and queue diagnostics are attached to this HftBacktest run."
            if hbt_diagnostics_observed
            else "Latency, fill-quality, and queue diagnostics are incomplete.",
            authority="HftBacktest-only pipeline",
            role="microstructure_diagnostics",
            artifact_contract="latency_report.json, fill_quality_report.json, queue_diagnostics.json",
        ),
        _coverage_row(
            "hbt_promotion_decision",
            "OBSERVED" if promotion_exists else "BLOCKING",
            str(run_dir / "promotion_decision.json"),
            "Promotion decision was generated after HftBacktest outputs existed."
            if promotion_exists
            else "promotion_decision.json is not available yet.",
            authority="post-HftBacktest evaluator",
            role="post_hbt_decision_gate",
            artifact_contract="promotion_decision.json written only after recorder_result.npz and stats_summary.json",
        ),
    ]
    return RunEvidenceSnapshot(
        source="hbt_runs",
        run_id=run_id,
        state=state,
        current_stage=current_stage,
        started_at=str(manifest.get("created_at_utc") or ""),
        root=str(run_dir),
        stages=[
            {"name": "hbt_run_manifest", "status": "observed"},
            {"name": "hbt_data_validation", "status": "pass" if data_passed else data_validation_status},
            {"name": "hbt_strategy_results", "status": "observed" if results_observed else "missing"},
            {"name": "hbt_latency_fill_queue", "status": "observed" if hbt_diagnostics_observed else "missing"},
            {"name": "hbt_promotion_decision", "status": "observed" if promotion_exists else "missing"},
        ],
        artifacts=artifact_paths,
        registry={
            "selected_run_id": run_id,
            "artifact_dir": str(run_dir),
            "active_pipeline": "hftbacktest_only",
        },
        data={
            "symbol": manifest.get("symbol"),
            "contract": manifest.get("contract"),
            "event_id": manifest.get("event_id"),
            "normalized_npz": manifest.get("normalized_npz") or data_manifest.get("normalized_npz"),
            "initial_snapshot": manifest.get("initial_snapshot") or data_manifest.get("initial_snapshot"),
            "data_validation": data_validation,
            "data_manifest": data_manifest,
            "normalized_input_manifest": normalized_input,
            "data_files": [
                {
                    "artifact": "normalized_npz",
                    "status": "observed" if manifest.get("normalized_npz") else "missing",
                    "path": manifest.get("normalized_npz") or data_manifest.get("normalized_npz", ""),
                },
                {
                    "artifact": "initial_snapshot",
                    "status": "observed" if manifest.get("initial_snapshot") else "missing",
                    "path": manifest.get("initial_snapshot") or data_manifest.get("initial_snapshot", ""),
                },
            ],
        },
        backtest={
            "rows": [hbt_row],
            "hbt_run": hbt_row,
            "summary": stats,
            "hbt_config": hbt_config,
            "strategy_config": strategy_config,
            "promotion_decision": promotion,
        },
        latency={
            "hbt_latency_report": latency_report,
            "latency_model": latency_report.get("latency_model") or manifest.get("latency_model"),
            "entry_latency_ns": latency_report.get("order_entry_latency_ns") or manifest.get("entry_latency_ns"),
            "response_latency_ns": latency_report.get("order_response_latency_ns") or manifest.get("response_latency_ns"),
        },
        diagnostics={
            "feature_fabric": feature_fabric,
            "fill_quality": fill_quality,
            "queue_diagnostics": queue_diagnostics,
        },
        robustness={
            "status": robustness_report.get("status", "not_evaluated_minimal_slice"),
            "robustness_report": robustness_report,
            "pending": ["latency_sensitivity", "fill_model_sensitivity", "symbol_event_stability"]
            if not robustness_report or robustness_report.get("status") == "not_run"
            else [],
            "failed": [],
        },
        decision={
            "action": decision_text,
            "reason": "Active HftBacktest-only run artifacts are observed."
            if results_observed and not blocking_gates
            else "Active HftBacktest-only run is blocked until required artifacts and validation pass.",
            "live_registry_ready": False,
            "evidence_candidate_id": run_id,
            "blocking_gates": blocking_gates,
            "promotion_decision": promotion,
        },
        reports={
            "audit": str(run_dir / "audit.md") if (run_dir / "audit.md").is_file() else "",
            "stats_summary": str(run_dir / "stats_summary.json") if stats_exists else "",
            "promotion_decision": str(run_dir / "promotion_decision.json") if promotion_exists else "",
        },
        system={
            "active_pipeline": "hftbacktest_only",
            "pipeline_coverage": coverage,
            "feature_fabric": feature_fabric,
            "hbt_run_manifest": manifest,
            "hbt_artifact_dir": str(run_dir),
        },
    )


def _lane_source_snapshot(repo: Path, source: str) -> RunEvidenceSnapshot:
    lane = _source_lane(source)
    lane_registry = _lane_registry_snapshot(repo)
    lane_row = (lane_registry.get("by_lane") or {}).get(lane, {})
    config = lane_row.get("config") or {}
    is_cme = lane == "cme_futures"
    rithmic_endpoint = _rithmic_endpoint_status(repo, force_paper=is_cme) if is_cme else {}
    rithmic_trial = _latest_rithmic_trial_bundle(repo) if is_cme else {}
    latency_baseline = _latest_latency_baseline_summary(
        repo,
        broker="rithmic",
        environment="paper",
    ) if is_cme else {}
    endpoint_status = str(rithmic_endpoint.get("status") or "").upper()
    endpoint_ready = (not is_cme) or endpoint_status in {"READY_TO_CONNECT", "CONNECTED"}
    endpoint_blocked = is_cme and not endpoint_ready
    active_run_id, active_run_dir = _active_all_lanes_run_dir(repo)
    feature_root = active_run_dir / "feature_fabric" / lane if active_run_dir else None
    feature_fabric_root = _ensure_catalog_feature_fabric(
        repo,
        consumer_lane=lane,
        selected_root=feature_root,
        run_id=active_run_id,
    )
    feature_fabric = _feature_fabric_snapshot(
        repo,
        selected_root=feature_fabric_root,
        consumer_lane=lane,
        selected_run_id=active_run_id,
    )
    lane_registry_blocked = str(lane_registry.get("status") or "") == "BLOCKING" or not lane_row
    feature_fabric_blocked = str(feature_fabric.get("gate_status") or "") == "BLOCKING"
    has_rithmic_trial = bool(rithmic_trial)
    reports_bound = str(rithmic_trial.get("report_binding_status") or "") == "PASS"
    report_binding_blocked = has_rithmic_trial and not reports_bound
    baseline_order_ack_count = _latency_metric_count(latency_baseline, "send_to_ack_us")
    baseline_order_ack_measured = baseline_order_ack_count > 0
    order_ack_measured = (
        (bool(rithmic_trial.get("paired_count", 0)) and reports_bound if has_rithmic_trial else False)
        or baseline_order_ack_measured
    )
    state = (
        "observed_blocked"
        if has_rithmic_trial
        and (
            endpoint_blocked
            or lane_registry_blocked
            or feature_fabric_blocked
            or report_binding_blocked
            or not order_ack_measured
        )
        else "blocked"
        if endpoint_blocked or lane_registry_blocked or feature_fabric_blocked
        else "catalogued"
    )
    if report_binding_blocked:
        current_stage = "paper_report_binding_blocked"
        reason = (
            "Rithmic Paper capture is present, but its reports are not bound to the same raw checksum, "
            "normalized file, and replay artifact."
        )
    elif has_rithmic_trial and not order_ack_measured:
        current_stage = "paper_market_data_observed_order_ack_missing"
        reason = (
            "Rithmic Paper market data capture and trade-only replay are observed, "
            "but no tagged submit-to-ack order pairs have been captured yet."
        )
    elif baseline_order_ack_measured:
        current_stage = "paper_latency_baseline_observed"
        reason = (
            "Rithmic Paper submit-to-ack baseline is observed. Placement speed is separate from broker "
            "acknowledgment latency; remaining Workbench gates still control promotion."
        )
    elif endpoint_blocked:
        current_stage = "endpoint_not_ready"
        reason_code = str(rithmic_endpoint.get("reason_code") or endpoint_status or "RITHMIC_ENDPOINT_NOT_READY")
        if reason_code == "PAPER_ENDPOINT_PARAMS_MISSING":
            reason = "Rithmic Paper/Chicago endpoint parameters are missing."
        elif reason_code == "RITHMIC_CREDENTIALS_MISSING":
            reason = "Rithmic Paper credentials are not loaded into the runtime environment."
        elif reason_code == "GATEWAY_LIBRARY_NOT_FOUND":
            reason = "Rithmic C++ gateway library is not built or not pointed to by HFT3_RITHMIC_GATEWAY_SO."
        else:
            reason = f"Rithmic endpoint is not ready: {reason_code}."
    elif lane_registry_blocked:
        current_stage = "lane_registry_blocked"
        reason = "Lane registry failed to load cleanly."
    elif feature_fabric_blocked:
        current_stage = "feature_fabric_blocked"
        reason = "Cross-lane feature fabric evidence is missing or not point-in-time safe."
    else:
        current_stage = "lane_catalogued"
        reason = "Lane is registered; no active selected run has emitted execution evidence."
    blocking_gates = [
        {
            "gate": "rithmic_paper_endpoint"
            if endpoint_blocked
            else "rithmic_report_binding"
            if report_binding_blocked
            else "rithmic_order_ack"
            if has_rithmic_trial and not order_ack_measured
            else "observed_lane_run",
            "status": rithmic_endpoint.get("status", "PENDING")
            if endpoint_blocked
            else "BLOCKING"
            if report_binding_blocked
            else "INSUFFICIENT_ORDER_ACK_EVIDENCE"
            if has_rithmic_trial and not order_ack_measured
            else "PENDING",
            "reason": rithmic_endpoint.get("reason_code", reason)
            if endpoint_blocked
            else reason,
        }
    ]
    blocking_gates.extend(_shared_blocking_gates(lane_registry, feature_fabric))
    rithmic_reports = rithmic_trial.get("reports") or {}
    data_quality = rithmic_reports.get("data_quality") or {}
    conversion = rithmic_reports.get("hftbacktest_conversion") or {}
    book = rithmic_reports.get("book_reconstruction") or {}
    latency_profile = rithmic_reports.get("latency_profile") or {}
    paper_order_summary = rithmic_reports.get("paper_order_summary") or {}
    rithmic_paths = rithmic_trial.get("paths") or {}
    event_counts = rithmic_trial.get("event_type_counts") or {}
    data_files = []
    if has_rithmic_trial:
        data_files = [
            {
                "artifact": "raw_events",
                "status": "observed",
                "path": rithmic_paths.get("raw_events", ""),
            },
            {
                "artifact": "normalized_events",
                "status": "observed" if rithmic_trial.get("normalized_exists") else "missing",
                "path": rithmic_paths.get("normalized_events", ""),
            },
            {
                "artifact": "hftbacktest_npz",
                "status": "observed" if rithmic_trial.get("npz_exists") else "missing",
                "path": rithmic_paths.get("npz", ""),
            },
        ]
    backtest_rows = []
    if has_rithmic_trial:
        backtest_rows.append(
            {
                "candidate_id": rithmic_trial.get("run_id"),
                "target": "CME paper market-data replay",
                "pass_fail": "PASS"
                if reports_bound and data_quality.get("status") == "pass" and conversion.get("status") == "pass"
                else "BLOCKING",
                "rows": rithmic_trial.get("row_count", 0),
                "proxy_trades": rithmic_trial.get("trade_count", 0),
                "quotes": rithmic_trial.get("quote_count", 0),
                "depth_events": rithmic_trial.get("depth_count", 0),
                "mode": conversion.get("mode", ""),
                "npz_path": conversion.get("output_file", rithmic_paths.get("npz", "")),
            }
        )
    trial_artifacts = {
        "rithmic_trial_manifest": rithmic_paths.get("manifest", ""),
        "rithmic_raw_events": rithmic_paths.get("raw_events", ""),
        "rithmic_normalized_events": rithmic_paths.get("normalized_events", ""),
        "rithmic_hftbacktest_npz": rithmic_paths.get("npz", ""),
        "rithmic_data_quality_report": rithmic_paths.get("data_quality_report", ""),
        "rithmic_hftbacktest_conversion_report": rithmic_paths.get("hftbacktest_conversion_report", ""),
        "rithmic_latency_profile": rithmic_paths.get("latency_profile", ""),
        "rithmic_paper_order_summary": rithmic_paths.get("paper_order_summary", ""),
    }
    if not has_rithmic_trial:
        trial_artifacts = {}
    order_ack_status = (
        "MEASURED"
        if order_ack_measured
        else "REPORT_BINDING_BLOCKED"
        if report_binding_blocked
        else "INSUFFICIENT_ORDER_ACK_EVIDENCE"
        if has_rithmic_trial
        else rithmic_endpoint.get("status", "not_applicable")
    )
    return RunEvidenceSnapshot(
        source=source,
        run_id=str(rithmic_trial.get("run_id") or f"{lane}_lane"),
        state=state,
        current_stage=current_stage,
        started_at=str(rithmic_trial.get("started_at") or ""),
        finished_at=str(rithmic_trial.get("finished_at") or ""),
        root=str(rithmic_trial.get("root") or repo),
        stages=[
            {
                "name": "lane_registry",
                "status": "BLOCKING" if lane_registry_blocked else lane_row.get("load_status", "missing"),
            },
            {
                "name": "cross_lane_feature_fabric",
                "status": feature_fabric.get("status", "BLOCKING"),
            },
            {
                "name": "rithmic_paper_endpoint",
                "status": rithmic_endpoint.get("status", "not_applicable") if is_cme else "not_applicable",
            },
            {
                "name": "paper_market_data_capture",
                "status": "observed" if has_rithmic_trial else "missing",
            },
            {
                "name": "rithmic_report_binding",
                "status": rithmic_trial.get("report_binding_status", "missing") if has_rithmic_trial else "missing",
            },
            {
                "name": "data_quality",
                "status": rithmic_trial.get("quality_status", "not_applicable") if has_rithmic_trial else "missing",
            },
            {
                "name": "hftbacktest_trade_only_replay",
                "status": rithmic_trial.get("conversion_status", "not_applicable") if has_rithmic_trial else "missing",
            },
            {
                "name": "submit_to_ack_latency",
                "status": order_ack_status,
            },
            {"name": "trade_manager_session", "status": "loaded_by_live_monitor"},
        ],
        artifacts={
            "feature_fabric_manifest": feature_fabric.get("expected_artifacts", {}).get("feature_fabric_manifest.json", ""),
            "feature_lineage": feature_fabric.get("expected_artifacts", {}).get("feature_lineage.json", ""),
            "feature_pit_audit": feature_fabric.get("expected_artifacts", {}).get("feature_pit_audit.json", ""),
            "rejected_features": feature_fabric.get("expected_artifacts", {}).get("rejected_features.json", ""),
            "rithmic_api_config": rithmic_endpoint.get("config_path", "") if is_cme else "",
            "rithmic_endpoint_status": rithmic_endpoint.get("runtime_status_path", "") if is_cme else "",
            "rithmic_latency_baseline_summary": latency_baseline.get("_path", "") if is_cme else "",
            "rithmic_latency_baseline_samples": latency_baseline.get("_sample_path", "") if is_cme else "",
            **trial_artifacts,
        },
        registry={
            "selected_lane": lane,
            "lane_config": config,
            "lanes": lane_registry.get("rows", []),
            "lane_registry": lane_registry,
        },
        data={
            "symbols": config.get("symbols", []),
            "event_types": config.get("event_types", []),
            "test_paths": config.get("test_paths", []),
            "capability_profile": config.get("capability_profile", {}),
            "data_files": data_files,
            "rithmic_trial": {
                "observed": has_rithmic_trial,
                "capture_date": rithmic_trial.get("capture_date", ""),
                "symbol": rithmic_trial.get("symbol", ""),
                "exchange": rithmic_trial.get("exchange", ""),
                "capture_environment": rithmic_trial.get("capture_environment", ""),
                "row_count": rithmic_trial.get("row_count", 0),
                "event_type_counts": event_counts,
                "quality_checks": data_quality.get("checks", {}),
                "book_reconstruction": book,
                "report_binding_status": rithmic_trial.get("report_binding_status", "missing"),
                "report_binding_issues": rithmic_trial.get("report_binding_issues", []),
            },
        },
        backtest={
            "rows": backtest_rows,
            "summary": {
                "status": (
                    "observed_trade_only_replay"
                    if has_rithmic_trial and reports_bound
                    else "report_binding_blocked"
                    if report_binding_blocked
                    else "not_observed"
                ),
                "reason": (
                    "Rithmic Paper market data was normalized and converted into a trade-only HFT replay artifact."
                    if has_rithmic_trial and reports_bound
                    else "Rithmic Paper capture is present, but report binding failed; replay evidence is blocked until process regenerates matching artifacts."
                    if report_binding_blocked
                    else "No active lane run artifact has emitted backtest rows for this source."
                ),
                "report_binding_status": rithmic_trial.get("report_binding_status", "missing"),
                "report_binding_issues": rithmic_trial.get("report_binding_issues", []),
                "data_quality_status": data_quality.get("status"),
                "hftbacktest_conversion_status": conversion.get("status"),
                "mode": conversion.get("mode"),
                "event_count": data_quality.get("event_count"),
                "event_type_counts": event_counts,
                "limitations": list((book.get("limitations") or [])) + list((conversion.get("limitations") or [])),
            },
            "rithmic_trial": rithmic_trial,
        },
        latency={
            "rithmic_order_ack": {
                "status": order_ack_status if is_cme else "not_applicable",
                "scope": "cme_rithmic_submit_to_ack" if is_cme else "not_applicable",
                "reason_code": (
                    ""
                    if order_ack_measured
                    else "RITHMIC_REPORT_BINDING_BLOCKED"
                    if report_binding_blocked
                    else "NO_PAIRED_SUBMIT_ACK_ROWS"
                    if has_rithmic_trial
                    else rithmic_endpoint.get("reason_code", "")
                )
                if is_cme
                else "",
                "order_ack_measured": order_ack_measured,
                "endpoint_profile": rithmic_endpoint.get("profile", "") if is_cme else "",
                "paired_count": baseline_order_ack_count or rithmic_trial.get("paired_count", 0),
                "summary": latency_baseline or paper_order_summary,
                "source": "latency_baseline" if baseline_order_ack_measured else "rithmic_trial_capture",
            },
            "rithmic_endpoint": rithmic_endpoint,
            "rithmic_capture_endpoint": rithmic_trial.get("capture_endpoint", {}),
            "latency_baseline": latency_baseline,
            "latency_profile": latency_profile,
            "feed_latency_us": latency_profile.get("feed_latency_us", {}),
            "paper_order_summary": paper_order_summary,
        },
        diagnostics={
            "feature_fabric": feature_fabric,
            "feature_lineage": feature_fabric.get("lineage", {}),
        },
        robustness={
            "pending": ["strategy_robustness", "walk_forward", "submit_to_ack_latency"],
            "failed": [],
            "explanation": {
                "aggregate_status": "PENDING",
                "operator_explanation": (
                    "CME Paper data capture is observed. Model robustness is still pending because this selected "
                    "evidence is a data/replay lane artifact, not a promoted candidate robustness run."
                ),
            },
        },
        decision={
            "action": "QUARANTINE",
            "reason": reason,
            "live_registry_ready": False,
            "blocking_gates": blocking_gates,
        },
        reports={
            "rithmic_trial_manifest": rithmic_paths.get("manifest", ""),
            "data_quality_report": rithmic_paths.get("data_quality_report", ""),
            "book_reconstruction_report": rithmic_paths.get("book_reconstruction_report", ""),
            "latency_profile": rithmic_paths.get("latency_profile", ""),
            "paper_order_summary": rithmic_paths.get("paper_order_summary", ""),
            "hftbacktest_conversion_report": rithmic_paths.get("hftbacktest_conversion_report", ""),
            "hftbacktest_npz": rithmic_paths.get("npz", ""),
        }
        if has_rithmic_trial
        else {},
        system={
            "lane_registry": lane_registry,
            "rithmic_endpoint": rithmic_endpoint,
            "rithmic_trial": rithmic_trial,
            "latency_baseline": latency_baseline,
            "rithmic_report_binding": rithmic_trial.get("report_binding", {}),
            "feature_fabric": feature_fabric,
        },
    )


def _autonomous_snapshot(repo: Path) -> RunEvidenceSnapshot:
    return RunEvidenceSnapshot(
        source="autonomous",
        state="blocked",
        current_stage="active_run_required",
        root=str(repo / "artifacts" / "runs"),
        decision={
            "action": "BLOCKED",
            "reason": "Autonomous latest-run fallback is disabled for the fresh all-lane boundary.",
            "live_registry_ready": False,
            "blocking_gates": [
                {
                    "gate": "autonomous_latest_fallback",
                    "status": "DISABLED",
                    "reason": "Use active all-lane run evidence instead of latest autonomous artifacts.",
                }
            ],
        },
    )


def _stale_source_blocked_snapshot(repo: Path, source: str, active: dict[str, Any]) -> RunEvidenceSnapshot:
    run_id = str(active.get("run_id") or "")
    run_dir = repo / "runtime" / "workbench" / "all_lanes" / run_id if run_id else repo / "runtime" / "workbench"
    return RunEvidenceSnapshot(
        source=source,
        run_id=run_id,
        state="blocked",
        current_stage="stale_source_blocked",
        root=str(run_dir),
        stages=[
            {"name": "active_run_manifest", "status": "observed" if run_id else "missing"},
            {"name": source, "status": "stale_blocked"},
        ],
        artifacts={
            "active_run": str(_active_run_path(repo)),
            "active_run_root": str(run_dir),
        },
        registry={"lanes": _lane_registry_snapshot(repo).get("rows", [])},
        diagnostics={
            "leakage_boundary": {
                "artifact_reuse_policy": active.get("artifact_reuse_policy", "active_run_id_only"),
                "blocked_source": source,
                "active_run_id": run_id,
            }
        },
        decision={
            "action": "BLOCKED",
            "reason": (
                f"{source} artifacts are outside the active all-lane run boundary. "
                "They cannot be used as evidence for the fresh run."
            ),
            "live_registry_ready": False,
            "blocking_gates": [
                {
                    "gate": "stale_artifact_source",
                    "status": "STALE",
                    "source": source,
                    "reason": "Active all-lane run requires active_run_id_only evidence.",
                }
            ],
        },
        system={
            "active_run": active,
            "artifact_reuse_policy": active.get("artifact_reuse_policy", "active_run_id_only"),
            "stale_source_blocked": source,
        },
    )


def _legacy_source_disabled_snapshot(repo: Path, source: str) -> RunEvidenceSnapshot:
    return RunEvidenceSnapshot(
        source=source,
        state="blocked",
        current_stage="legacy_source_disabled",
        root=str(repo / "runtime" / "workbench"),
        decision={
            "action": "BLOCKED",
            "reason": f"{source} is not a production Workbench run source for the fresh all-lane boundary.",
            "live_registry_ready": False,
            "blocking_gates": [
                {
                    "gate": "legacy_source_disabled",
                    "status": "DISABLED",
                    "source": source,
                    "reason": "Use all_lanes or a lane view backed by the active run.",
                }
            ],
        },
    )


def _all_lanes_snapshot(repo: Path) -> RunEvidenceSnapshot:
    active = _active_run_manifest(repo)
    lane_registry = _lane_registry_snapshot(repo)
    if not active:
        return RunEvidenceSnapshot(
            source="all_lanes",
            state="idle",
            current_stage="fresh_start_required",
            root=str(repo / "runtime" / "workbench"),
            stages=[
                {"name": "fresh_start", "status": "missing"},
                {"name": "all_lane_execution", "status": "blocked"},
            ],
            registry={"lanes": lane_registry.get("rows", []), "lane_registry": lane_registry},
            decision={
                "action": "BLOCKED",
                "reason": "No active all-lane run manifest exists. Run fresh-start before all-model testing.",
                "live_registry_ready": False,
                "blocking_gates": [
                    {
                        "gate": "active_run_manifest",
                        "status": "MISSING",
                        "reason": "runtime/workbench/active_run.json is missing.",
                    }
                ],
            },
            system={
                "lane_registry": lane_registry,
                "active_run": {},
                "artifact_reuse_policy": "active_run_id_only",
            },
        )

    run_id = str(active.get("run_id") or "")
    run_dir = repo / "runtime" / "workbench" / "all_lanes" / run_id if run_id else repo / "runtime" / "workbench" / "all_lanes"
    plan = read_json(run_dir / "plan.json") or read_json(run_dir / "all_lanes_plan.json")
    summary = read_json(run_dir / "summary.json")
    leakage_detection = read_json(run_dir / "leakage_detection.json")
    model_rows = (
        plan.get("models")
        or plan.get("model_plan")
        or summary.get("models")
        or summary.get("model_states")
        or []
    )
    if isinstance(model_rows, dict):
        model_rows = list(model_rows.values())
    model_rows = [dict(row) for row in model_rows if isinstance(row, dict)]
    terminal_counts = {state: 0 for state in sorted(ALL_LANES_TERMINAL_STATES)}
    missing_state_rows: list[dict[str, Any]] = []
    invalid_state_rows: list[dict[str, Any]] = []
    planned_lane_counts = plan.get("lane_model_counts") if isinstance(plan.get("lane_model_counts"), dict) else {}
    lane_counts: dict[str, int] = {
        str(lane): int(count)
        for lane, count in planned_lane_counts.items()
        if isinstance(count, int) and not isinstance(count, bool)
    }
    has_planned_lane_counts = bool(lane_counts)
    for row in model_rows:
        state = str(row.get("terminal_state") or row.get("state") or row.get("status") or "")
        lane = str(row.get("lane") or row.get("resolved_lane") or "unknown")
        if not has_planned_lane_counts:
            lane_counts[lane] = lane_counts.get(lane, 0) + 1
        if not state:
            missing_state_rows.append(row)
        elif state not in ALL_LANES_TERMINAL_STATES:
            invalid_state_rows.append(row)
        else:
            terminal_counts[state] += 1

    blocking_gates: list[dict[str, Any]] = []
    if not run_id:
        blocking_gates.append(
            {
                "gate": "active_run_id",
                "status": "MISSING",
                "reason": "Active run manifest does not contain run_id.",
            }
        )
    if not model_rows:
        blocking_gates.append(
            {
                "gate": "all_lanes_model_plan",
                "status": "MISSING",
                "reason": "Active all-lane run has not emitted a model execution plan yet.",
            }
        )
    if missing_state_rows:
        blocking_gates.append(
            {
                "gate": "terminal_state_contract",
                "status": "BLOCKING",
                "reason": f"{len(missing_state_rows)} model rows are missing terminal_state.",
            }
        )
    if invalid_state_rows:
        blocking_gates.append(
            {
                "gate": "terminal_state_contract",
                "status": "BLOCKING",
                "reason": f"{len(invalid_state_rows)} model rows have invalid terminal_state.",
            }
        )
    for artifact_name, payload in (
        ("plan.json", plan),
        ("summary.json", summary),
        ("rejected_stale_artifacts.json", read_json(run_dir / "rejected_stale_artifacts.json")),
    ):
        if payload and run_id and str(payload.get("run_id") or "") != run_id:
            blocking_gates.append(
                {
                    "gate": "active_run_artifact_identity",
                    "status": "STALE",
                    "artifact": artifact_name,
                    "reason": f"{artifact_name} does not declare the active run id.",
                    "expected_run_id": run_id,
                    "observed_run_id": str(payload.get("run_id") or ""),
                }
            )

    plan_coverage_gates = [
        dict(gate)
        for gate in plan.get("lane_coverage_gates", [])
        if isinstance(gate, dict)
    ]
    summary_gates = list(summary.get("blocking_gates") or [])
    if not summary_gates:
        summary_gates = plan_coverage_gates

    state = str(summary.get("state") or active.get("state") or ("blocked" if blocking_gates else "planned"))
    planned = len(model_rows)
    executed = terminal_counts["EXECUTED"] + terminal_counts["PROMOTED"] + terminal_counts["QUARANTINED"]
    blocked = sum(
        count
        for key, count in terminal_counts.items()
        if key.startswith("BLOCKED_")
    )
    return RunEvidenceSnapshot(
        source="all_lanes",
        run_id=run_id,
        state=state,
        current_stage=str(summary.get("current_stage") or active.get("current_stage") or "awaiting_all_lane_execution"),
        started_at=str(active.get("created_at_utc") or active.get("started_at") or ""),
        finished_at=str(summary.get("finished_at") or ""),
        root=str(run_dir),
        stages=[
            {"name": "active_run_manifest", "status": "observed" if run_id else "missing"},
            {"name": "leakage_detection", "status": leakage_detection.get("status", summary.get("leakage_detection_status", "pending"))},
            {"name": "lane_registry", "status": lane_registry.get("status", "UNKNOWN")},
            {"name": "model_execution_plan", "status": "observed" if model_rows else "missing"},
            {"name": "pit_feature_audit", "status": summary.get("pit_feature_audit_status", "pending")},
            {"name": "backtest_replay", "status": summary.get("backtest_status", "pending")},
            {"name": "robustness_wfc", "status": summary.get("robustness_status", "pending")},
            {"name": "latency_operating_envelope", "status": summary.get("latency_status", "pending")},
            {"name": "decision", "status": summary.get("decision_status", "pending")},
        ],
        artifacts={
            "active_run": str(_active_run_path(repo)),
            "active_run_lock": str(repo / "runtime" / "workbench" / "active_run.lock"),
            "all_lanes_plan": str(run_dir / "plan.json"),
            "all_lanes_summary": str(run_dir / "summary.json"),
            "rejected_stale_artifacts": str(run_dir / "rejected_stale_artifacts.json"),
            "leakage_detection": str(run_dir / "leakage_detection.json"),
        },
        registry={
            "lanes": lane_registry.get("rows", []),
            "lane_registry": lane_registry,
            "planned_models": model_rows,
            "terminal_states": sorted(ALL_LANES_TERMINAL_STATES),
        },
        data={
            "planned_model_count": planned,
            "lane_counts": lane_counts,
            "lane_coverage_gates": plan_coverage_gates,
            "source_data_reused": active.get("source_data_reused", True),
            "previous_run_artifacts_reused": active.get("previous_run_artifacts_reused", False),
        },
        backtest={
            "rows": model_rows,
            "summary": {
                "planned": planned,
                "executed": executed,
                "blocked": blocked,
                "terminal_counts": terminal_counts,
            },
        },
        diagnostics={
            "leakage_boundary": {
                "artifact_reuse_policy": active.get("artifact_reuse_policy", "active_run_id_only"),
                "missing_terminal_state_count": len(missing_state_rows),
                "invalid_terminal_state_count": len(invalid_state_rows),
                "leakage_detection_status": leakage_detection.get("status", summary.get("leakage_detection_status", "")),
                "leakage_detection_blockers": leakage_detection.get("blocking", summary.get("leakage_detection_blockers", [])),
                "lane_coverage_gates": plan_coverage_gates,
            },
            "rejected_stale_artifacts": read_json(run_dir / "rejected_stale_artifacts.json"),
        },
        robustness={
            "status": summary.get("robustness_status", "pending"),
            "wfc": summary.get("wfc", {}),
            "pending": summary.get("robustness_pending_checks", []),
            "failed": summary.get("robustness_failed_checks", []),
        },
        decision={
            "action": summary.get("decision_action") or ("BLOCKED" if blocking_gates else "QUARANTINE"),
            "reason": summary.get("decision_reason") or (
                "Active all-lane run is waiting for model evidence."
                if blocking_gates
                else "All-lane model plan is present; promotion remains gated by observed evidence."
            ),
            "live_registry_ready": bool(summary.get("live_registry_ready", False)),
            "blocking_gates": blocking_gates + summary_gates,
            "terminal_counts": terminal_counts,
        },
        reports={
            "summary": str(run_dir / "summary.json"),
            "report": str(run_dir / "report.md"),
            "leakage_detection": str(run_dir / "leakage_detection.md"),
        },
        system={
            "active_run": active,
            "lane_registry": lane_registry,
            "all_lanes_summary": summary,
            "leakage_detection": leakage_detection,
            "artifact_reuse_policy": active.get("artifact_reuse_policy", "active_run_id_only"),
        },
    )


def load_run_evidence(repo: Path, source: str, *, campaign_id: str = "") -> RunEvidenceSnapshot:
    active = _active_run_manifest(repo)
    if active and source in STALE_WHEN_ACTIVE_SOURCES:
        return _stale_source_blocked_snapshot(repo, source, active)
    if source == "hbt_runs":
        snapshot = _hbt_runs_snapshot(repo)
    elif source == "all_lanes":
        snapshot = _all_lanes_snapshot(repo)
    elif source == "workbench_campaign":
        snapshot = _workbench_snapshot(repo, campaign_id)
    elif source == "autonomous":
        snapshot = _autonomous_snapshot(repo)
    elif source in LANE_RUN_SOURCES:
        snapshot = _lane_source_snapshot(repo, source)
    elif source == "crypto_lane":
        snapshot = _legacy_source_disabled_snapshot(repo, source)
    else:
        snapshot = _all_lanes_snapshot(repo)
    lane = _source_lane(snapshot.source)
    lane_registry = _lane_registry_snapshot(repo)
    selected_feature_root = Path(snapshot.root) if snapshot.root and Path(snapshot.root).is_dir() else None
    feature_fabric = (snapshot.diagnostics or {}).get("feature_fabric")
    if not feature_fabric:
        feature_fabric_root = _ensure_catalog_feature_fabric(
            repo,
            consumer_lane=lane,
            selected_root=selected_feature_root,
            run_id=snapshot.run_id,
        )
        feature_fabric = _feature_fabric_snapshot(
            repo,
            selected_root=feature_fabric_root,
            consumer_lane=lane,
            selected_run_id=snapshot.run_id,
        )
    is_cme_lane = lane == "cme_futures"
    rithmic_endpoint = (snapshot.system or {}).get("rithmic_endpoint") or (
        _rithmic_endpoint_status(repo, force_paper=True) if is_cme_lane else {}
    )
    event_universe = _event_universe_snapshot(repo)
    snapshot.registry = {
        **(snapshot.registry or {}),
        "lanes": lane_registry.get("rows", []),
        "lane_registry": lane_registry,
        "event_universe": event_universe,
    }
    snapshot.data = {
        **(snapshot.data or {}),
        "event_universe": event_universe,
    }
    snapshot.diagnostics = {
        **(snapshot.diagnostics or {}),
        "feature_fabric": feature_fabric,
    }
    latency_payload = {
        **(snapshot.latency or {}),
    }
    if is_cme_lane:
        latency_payload["rithmic_endpoint"] = rithmic_endpoint
        latency_payload["rithmic_order_ack"] = (snapshot.latency or {}).get(
            "rithmic_order_ack",
            {
                "status": "CONFIGURED_NOT_OBSERVED",
                "scope": "cme_rithmic_submit_to_ack",
                "reason_code": rithmic_endpoint.get("reason_code", ""),
                "order_ack_measured": False,
                "endpoint_profile": rithmic_endpoint.get("profile", ""),
            },
        )
    snapshot.latency = latency_payload
    snapshot.trade_manager = _trade_manager_snapshot(
        repo,
        selected_run_id=snapshot.run_id,
        selected_root=snapshot.root,
    )
    system_payload = {
        **(snapshot.system or {}),
        "lane_registry": lane_registry,
        "feature_fabric": feature_fabric,
        "trade_manager": snapshot.trade_manager,
        "event_universe": event_universe,
    }
    if is_cme_lane:
        system_payload["rithmic_endpoint"] = rithmic_endpoint
    snapshot.system = system_payload
    _append_decision_blocking_gates(
        snapshot,
        _shared_blocking_gates(lane_registry, feature_fabric)
        + list(event_universe.get("blocking_gates") or []),
    )
    return snapshot


def default_source(repo: Path) -> str:
    if _latest_hbt_run_dir(repo) is not None:
        return "hbt_runs"
    return "all_lanes"
