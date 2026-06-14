"""All-lane Workbench run planning and terminal-state artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hft3.validation.lanes.registration import register_all_lanes
from hft3.validation.lanes.lane_registry import LaneRegistry
from workbench.src.registry.model_catalog import load_catalog
from workbench.src.registry.unified_registry import build_models_config, list_models


TERMINAL_STATES = {
    "EXECUTED",
    "BLOCKED_MISSING_DATA",
    "BLOCKED_ENDPOINT",
    "BLOCKED_VALIDATION",
    "BLOCKED_ROBUSTNESS",
    "BLOCKED_LATENCY",
    "QUARANTINED",
    "PROMOTED",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _config_payload(registration: Any) -> tuple[dict[str, Any], str]:
    try:
        config = registration.config_loader()
        if hasattr(config, "to_dict"):
            return dict(config.to_dict()), ""
        return {}, ""
    except Exception as exc:
        return {}, str(exc)


def _model_config_payload(config: Any) -> dict[str, Any]:
    if config is None:
        return {
            "kind": "",
            "required_datasets": [],
            "min_history_years": None,
            "robustness_window": "",
            "latency_lane": "",
            "execution_assumptions": "",
            "parameter_bounds": {},
            "signal_field": "",
            "diagnostics_only": False,
            "hyp_id": None,
        }
    return {
        "kind": str(getattr(config, "kind", "") or ""),
        "required_datasets": list(getattr(config, "required_datasets", []) or []),
        "min_history_years": getattr(config, "min_history_years", None),
        "robustness_window": str(getattr(config, "robustness_window", "") or ""),
        "latency_lane": str(getattr(config, "latency_lane", "") or ""),
        "execution_assumptions": str(getattr(config, "execution_assumptions", "") or ""),
        "parameter_bounds": dict(getattr(config, "parameter_bounds", {}) or {}),
        "signal_field": str(getattr(config, "signal_field", "") or ""),
        "diagnostics_only": bool(getattr(config, "diagnostics_only", False)),
        "hyp_id": getattr(config, "hyp_id", None),
    }


def _lane_coverage_gates(lanes: list[dict[str, Any]], lane_model_counts: dict[str, int]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for lane in lanes:
        lane_name = str(lane.get("lane") or "")
        if not lane_name:
            continue
        if lane_model_counts.get(lane_name, 0) == 0:
            gates.append(
                {
                    "gate": "lane_model_universe",
                    "status": "BLOCKING",
                    "lane": lane_name,
                    "reason": "Registered lane has no model ids resolved from the Workbench model registry.",
                    "model_count": 0,
                }
            )
    return gates


def build_all_lanes_plan(repo: Path, run_id: str) -> dict[str, Any]:
    """Build a run-id-scoped plan with one explicit terminal state per model."""

    if not run_id:
        raise ValueError("run_id is required")
    register_all_lanes()
    registry = LaneRegistry.instance()
    catalog = load_catalog(repo)
    model_configs = build_models_config()
    registrations = {registration.lane.value: registration for registration in registry.all_registrations()}
    lanes: list[dict[str, Any]] = []
    lane_config_errors: dict[str, str] = {}
    for lane, registration in sorted(registrations.items()):
        config, error = _config_payload(registration)
        if error:
            lane_config_errors[lane] = error
        lanes.append(
            {
                "lane": lane,
                "load_status": "error" if error else "loaded",
                "load_error": error,
                "symbols": config.get("symbols", []),
                "event_types": config.get("event_types", []),
                "test_paths": list(registration.test_paths),
            }
        )

    models: list[dict[str, Any]] = []
    lane_model_counts = {lane: 0 for lane in sorted(registrations)}
    for model_id in list_models():
        lane = registry.resolve_lane(model_id).value
        lane_model_counts[lane] = lane_model_counts.get(lane, 0) + 1
        catalog_entry = catalog.get(model_id)
        config_payload = _model_config_payload(model_configs.get(model_id))
        reason = "All-lane dry-run planning emitted no execution evidence yet."
        terminal_state = "BLOCKED_VALIDATION"
        if lane in lane_config_errors:
            terminal_state = "BLOCKED_VALIDATION"
            reason = f"Lane config failed to load: {lane_config_errors[lane]}"
        models.append(
            {
                "run_id": run_id,
                "model_id": model_id,
                "lane": lane,
                "role": getattr(catalog_entry, "role", "") if catalog_entry else "",
                "display_name": getattr(catalog_entry, "display_name", model_id) if catalog_entry else model_id,
                **config_payload,
                "terminal_state": terminal_state,
                "reason": reason,
                "evidence_scope": "all_lanes_plan_no_previous_run_artifacts",
            }
        )

    terminal_counts = {state: 0 for state in sorted(TERMINAL_STATES)}
    for row in models:
        terminal_counts[row["terminal_state"]] += 1

    lane_coverage_gates = _lane_coverage_gates(lanes, lane_model_counts)
    return {
        "schema_version": "workbench_all_lanes_plan_v1",
        "run_id": run_id,
        "generated_at_utc": _utc_now(),
        "repo": str(repo),
        "artifact_reuse_policy": "active_run_id_only",
        "previous_run_artifacts_reused": False,
        "registered_lane_count": len(lanes),
        "lanes": lanes,
        "lane_model_counts": lane_model_counts,
        "lane_coverage_gates": lane_coverage_gates,
        "model_universe_status": "BLOCKING" if lane_coverage_gates else "PLANNED",
        "models": models,
        "model_count": len(models),
        "terminal_states": sorted(TERMINAL_STATES),
        "terminal_counts": terminal_counts,
    }


def run_all_lanes(repo: Path, run_id: str, *, execute: bool = False) -> dict[str, Any]:
    """Write all-lane run artifacts.

    v1 is intentionally conservative: it creates the no-leakage execution plan
    and terminal-state rows. Real model execution can fill the same run-scoped
    artifacts later without changing the Workbench evidence contract.
    """

    if execute:
        raise NotImplementedError("all-lane model execution is not wired in this conservative planning pass")
    plan = build_all_lanes_plan(repo, run_id)
    run_dir = repo / "runtime" / "workbench" / "all_lanes" / run_id
    rejected_stale = {}
    rejected_stale_path = run_dir / "rejected_stale_artifacts.json"
    if rejected_stale_path.is_file():
        try:
            rejected_stale = json.loads(rejected_stale_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rejected_stale = {}
    _write_json(run_dir / "plan.json", plan)
    summary = {
        "schema_version": "workbench_all_lanes_summary_v1",
        "run_id": run_id,
        "state": "planned",
        "current_stage": "model_execution_plan",
        "planned_model_count": plan["model_count"],
        "registered_lane_count": plan.get("registered_lane_count", len(plan.get("lanes", []))),
        "lane_model_counts": plan.get("lane_model_counts", {}),
        "lane_coverage_gates": plan.get("lane_coverage_gates", []),
        "model_universe_status": plan.get("model_universe_status", "PLANNED"),
        "terminal_counts": plan["terminal_counts"],
        "decision_action": "BLOCKED",
        "decision_reason": "All-lane run has a clean model plan; model execution evidence has not been emitted yet.",
        "blocking_gates": [
            *list(plan.get("lane_coverage_gates", [])),
            {
                "gate": "model_execution",
                "status": "PENDING",
                "reason": "No model backtest/replay evidence has been emitted for this active run.",
            }
        ],
    }
    _write_json(run_dir / "summary.json", summary)
    _write_json(
        rejected_stale_path,
        rejected_stale
        if rejected_stale and str(rejected_stale.get("run_id") or "") == run_id
        else {
            "schema_version": "rejected_stale_artifacts_v1",
            "run_id": run_id,
            "rows": [],
            "rejected_count": 0,
        },
    )
    from workbench.src.run.leakage_detector import run_leakage_detection

    leakage = run_leakage_detection(repo, run_id=run_id)
    summary["leakage_detection_status"] = leakage.get("status", "FAIL")
    summary["leakage_detection_path"] = (leakage.get("artifact_paths") or {}).get("json", "")
    summary["leakage_detection_blockers"] = leakage.get("blocking", [])
    if leakage.get("status") != "PASS":
        summary["blocking_gates"].append(
            {
                "gate": "leakage_detection",
                "status": "FAIL",
                "reason": "Leakage detector found stale, cross-run, or unquarantined evidence before model execution.",
                "blocker_count": len(leakage.get("blocking") or []),
            }
        )
    _write_json(run_dir / "summary.json", summary)
    status = "PASS" if leakage.get("status") == "PASS" else "FAIL"
    return {
        "status": status,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "planned_model_count": plan["model_count"],
        "terminal_counts": plan["terminal_counts"],
        "leakage_detection_status": leakage.get("status", "FAIL"),
        "leakage_detection_path": summary["leakage_detection_path"],
    }
