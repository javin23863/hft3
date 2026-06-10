"""All-lane Workbench run planning and terminal-state artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hft3.validation.lanes.registration import register_all_lanes
from hft3.validation.lanes.lane_registry import LaneRegistry
from workbench.src.registry.model_catalog import load_catalog
from workbench.src.registry.unified_registry import list_models


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


def build_all_lanes_plan(repo: Path, run_id: str) -> dict[str, Any]:
    """Build a run-id-scoped plan with one explicit terminal state per model."""

    if not run_id:
        raise ValueError("run_id is required")
    register_all_lanes()
    registry = LaneRegistry.instance()
    catalog = load_catalog(repo)
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

    # Check IBKR endpoint readiness once for the equities lane.
    _ibkr_status_path = repo / "runtime" / "equities_lane" / "ibkr_endpoint_status.json"
    _ibkr_ready: bool = False
    try:
        _ibkr_payload = json.loads(_ibkr_status_path.read_text(encoding="utf-8"))
        _ibkr_ready = bool(_ibkr_payload.get("ready"))
    except (FileNotFoundError, json.JSONDecodeError):
        _ibkr_ready = False

    models: list[dict[str, Any]] = []
    for model_id in list_models():
        lane = registry.resolve_lane(model_id).value
        catalog_entry = catalog.get(model_id)
        reason = "All-lane dry-run planning emitted no execution evidence yet."
        terminal_state = "BLOCKED_VALIDATION"
        if lane in lane_config_errors:
            terminal_state = "BLOCKED_VALIDATION"
            reason = f"Lane config failed to load: {lane_config_errors[lane]}"
        elif lane == "equities" and not _ibkr_ready:
            terminal_state = "BLOCKED_ENDPOINT"
            reason = (
                "IBKR Web API endpoint not ready (no-GUI Web API lane); "
                "run the equities endpoint readiness check."
            )
        models.append(
            {
                "run_id": run_id,
                "model_id": model_id,
                "lane": lane,
                "role": getattr(catalog_entry, "role", "") if catalog_entry else "",
                "display_name": getattr(catalog_entry, "display_name", model_id) if catalog_entry else model_id,
                "terminal_state": terminal_state,
                "reason": reason,
                "evidence_scope": "all_lanes_plan_no_previous_run_artifacts",
            }
        )

    terminal_counts = {state: 0 for state in sorted(TERMINAL_STATES)}
    for row in models:
        terminal_counts[row["terminal_state"]] += 1

    return {
        "schema_version": "workbench_all_lanes_plan_v1",
        "run_id": run_id,
        "generated_at_utc": _utc_now(),
        "repo": str(repo),
        "artifact_reuse_policy": "active_run_id_only",
        "previous_run_artifacts_reused": False,
        "lanes": lanes,
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
        "terminal_counts": plan["terminal_counts"],
        "decision_action": "BLOCKED",
        "decision_reason": "All-lane run has a clean model plan; model execution evidence has not been emitted yet.",
        "blocking_gates": [
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
