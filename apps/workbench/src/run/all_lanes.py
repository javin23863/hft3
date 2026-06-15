"""All-lane Workbench run planning and terminal-state artifacts."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from hft3.validation.lanes.registration import register_all_lanes
from hft3.validation.lanes.lane_registry import LaneRegistry
from hft3.validation.lanes.lane import Lane
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

_MODEL_EVENT_BINDING_PATH = Path("apps/workbench/config/model_event_binding.yaml")
_Q001_OWNER_DECISION_PATH = Path("docs/project/q001_owner_decision.json")
_Q001_AUTHORITY_REFS = [
    "docs/project/q001_owner_decision.json",
    "docs/project/Q001_OWNER_DECISION_PACKET.md",
    "docs/project/Q001_DATA_INVENTORY_STATUS.md",
]
_Q001_ACCEPTED_EVIDENCE = {
    "missing_or_unavailable_slots": 211,
    "strict_mbo_gap_count": 507,
    "strict_mbo_stale_gap_count": 503,
}
_Q001_ACCEPTED_LEDGER_STATUS = "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE"
_Q001_REQUIRED_MODEL_GAP_POLICY = {
    "missing_mbo_required_models": "SIDELINE_UNTIL_DATA_FILLED",
    "strict_options_quote_required_models": "SIDELINE_UNTIL_DATA_FILLED",
    "available_data_models": "RUN_WITH_EXPLICIT_COVERAGE",
    "must_emit_skip_or_rejection_reasons": True,
}
_Q001_ACCEPTED_OPTIONS_WARN_CHECKS = {"options-fixing-mbo-coverage"}
_STRICT_OPTIONS_DATASETS = {
    "options_chain",
    "strict_options_quotes",
    "strict_mbo_quotes",
    "options_order_book",
    "options_quote_mbo",
}
_CME_OPTIONS_STRUCTURAL_MODEL_ID = "FOPT_ES_CALL"
_CME_OPTIONS_STRUCTURAL_MODEL_CONFIG = {
    "kind": "lane_structural",
    "required_datasets": ["options_chain", "strict_options_quotes", "options_quote_mbo"],
    "min_history_years": 10,
    "robustness_window": "discovery",
    "latency_lane": "10_250ms",
    "execution_assumptions": "cme_options_research_only_structural_adapter",
    "parameter_bounds": {},
    "signal_field": "",
    "diagnostics_only": True,
    "hyp_id": None,
    "display_name": "CME options structural governance model",
    "role": "options_standalone",
    "model_source": "cme_options_lane_registration_structural_fopt",
}
_EXECUTION_ELIGIBLE_STATE = "BLOCKED_VALIDATION"
_EXECUTION_ELIGIBLE_POLICY = "RUN_WITH_EXPLICIT_COVERAGE"


def _load_model_event_bindings(repo: Path) -> dict[str, dict[str, Any]]:
    path = repo / _MODEL_EVENT_BINDING_PATH
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    if not isinstance(payload, dict):
        return {}
    bindings: dict[str, dict[str, Any]] = {}
    for section in ("pdf", "hypothesis"):
        rows = payload.get(section) or {}
        if not isinstance(rows, dict):
            continue
        for model_id, binding in rows.items():
            if isinstance(binding, dict):
                bindings[str(model_id)] = dict(binding)
    return bindings


def _resolve_plan_lane(registry: LaneRegistry, model_id: str, binding: dict[str, Any]) -> str:
    campaign_mode = str(binding.get("campaign_mode") or "")
    if campaign_mode == "options_lane":
        return Lane.EQUITIES.value
    return registry.resolve_lane(model_id).value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _stable_id_part(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-") or "unknown"


def _is_workbench_symbol(symbol: str) -> bool:
    return ".v." in symbol


def _binding_execution_symbol(binding: dict[str, Any]) -> str:
    symbol = str(binding.get("symbol") or binding.get("research_symbol") or "")
    if _is_workbench_symbol(symbol):
        return symbol
    symbols = binding.get("symbols") or binding.get("symbol_universe") or []
    if not isinstance(symbols, list):
        return ""
    for candidate in symbols:
        candidate_symbol = str(candidate or "")
        if _is_workbench_symbol(candidate_symbol):
            return candidate_symbol
    return ""


def _execution_symbol(row: dict[str, Any]) -> str:
    row_symbol = str(row.get("symbol") or "")
    if row_symbol and _is_workbench_symbol(row_symbol):
        return row_symbol
    return ""


def _execution_campaign_id(run_id: str, model_id: str, symbol: str) -> str:
    return "all_lanes_{run_id}_{model_id}_{symbol}".format(
        run_id=_stable_id_part(run_id),
        model_id=_stable_id_part(model_id),
        symbol=_stable_id_part(symbol),
    )


def _is_execution_eligible(row: dict[str, Any]) -> bool:
    return (
        row.get("execution_eligible") is True
        and row.get("terminal_state") == _EXECUTION_ELIGIBLE_STATE
        and row.get("available_data_policy") == _EXECUTION_ELIGIBLE_POLICY
        and bool(_execution_symbol(row))
    )


def _run_research_campaign(
    repo: Path,
    model_id: str,
    symbol: str,
    *,
    campaign_id: str,
) -> Any:
    from workbench.src.run.campaign_runner import run_campaign

    return run_campaign(
        repo,
        model_id,
        symbol,
        dry_run=False,
        download_missing=False,
        allow_partial=True,
        trial_mode=False,
        campaign_id=campaign_id,
    )


def _period_events_run(period: Any) -> int:
    if isinstance(period, dict):
        value = period.get("events_run", 0)
    else:
        value = getattr(period, "events_run", 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _campaign_events_ran(result: Any, artifact_dir: str) -> int:
    periods = getattr(result, "periods", []) or []
    total = sum(_period_events_run(period) for period in periods)
    if total > 0:
        return total
    summary_path = Path(artifact_dir) / "summary.json" if artifact_dir else Path()
    if not summary_path.is_file():
        return 0
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    try:
        events_ran = int(summary.get("events_ran", 0) or 0)
    except (TypeError, ValueError):
        events_ran = 0
    if events_ran > 0:
        return events_ran
    return sum(_period_events_run(period) for period in summary.get("periods", []) or [])


def _execute_available_data_models(repo: Path, plan: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    execution_results: list[dict[str, Any]] = []
    for row in plan.get("models", []):
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("model_id") or "")
        symbol = _execution_symbol(row)
        if not _is_execution_eligible(row):
            execution_results.append(
                {
                    "model_id": model_id,
                    "symbol": symbol,
                    "status": "SKIPPED",
                    "reason": str(
                        row.get("execution_block_reason")
                        or "model is not eligible for available-data research execution"
                    ),
                    "terminal_state": row.get("terminal_state", ""),
                    "available_data_policy": row.get("available_data_policy", ""),
                }
            )
            continue
        campaign_id = _execution_campaign_id(run_id, model_id, symbol)
        try:
            result = _run_research_campaign(repo, model_id, symbol, campaign_id=campaign_id)
        except Exception as exc:
            row["terminal_state"] = "BLOCKED_VALIDATION"
            row["reason"] = f"Research-only campaign execution failed: {exc}"
            row["execution_status"] = "ERROR"
            row["campaign_id"] = campaign_id
            row["campaign_status"] = "ERROR"
            row["artifact_dir"] = ""
            execution_results.append(
                {
                    "model_id": model_id,
                    "symbol": symbol,
                    "status": "ERROR",
                    "reason": str(exc),
                    "campaign_id": campaign_id,
                    "artifact_dir": "",
                }
            )
            continue
        result_campaign_id = str(getattr(result, "campaign_id", campaign_id) or campaign_id)
        result_status = str(getattr(result, "status", "") or "")
        artifact_dir = str(getattr(result, "artifact_dir", "") or "")
        events_ran = _campaign_events_ran(result, artifact_dir)
        executed = result_status in {"PASS", "FAIL"} and events_ran > 0
        row["terminal_state"] = "EXECUTED" if executed else "BLOCKED_VALIDATION"
        row["reason"] = (
            "Research-only Workbench campaign emitted backtest evidence with explicit available-data coverage; "
            f"campaign_status={result_status}; events_ran={events_ran}."
            if executed
            else (
                "Research-only Workbench campaign did not emit accepted backtest evidence; "
                f"campaign_status={result_status}; events_ran={events_ran}."
            )
        )
        row["execution_status"] = "EXECUTED" if executed else "BLOCKED"
        row["campaign_id"] = result_campaign_id
        row["campaign_status"] = result_status
        row["artifact_dir"] = artifact_dir
        execution_results.append(
            {
                "model_id": model_id,
                "symbol": symbol,
                "status": "EXECUTED" if executed else "BLOCKED",
                "campaign_id": result_campaign_id,
                "campaign_status": result_status,
                "artifact_dir": artifact_dir,
                "events_ran": events_ran,
            }
        )
    plan["terminal_counts"] = {state: 0 for state in sorted(TERMINAL_STATES)}
    for row in plan.get("models", []):
        if isinstance(row, dict) and row.get("terminal_state") in plan["terminal_counts"]:
            plan["terminal_counts"][row["terminal_state"]] += 1
    plan["execution_results"] = execution_results
    return execution_results


def _execution_result_counts(execution_results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "executed_count": sum(1 for result in execution_results if result.get("status") == "EXECUTED"),
        "skipped_count": sum(1 for result in execution_results if result.get("status") == "SKIPPED"),
        "blocked_count": sum(1 for result in execution_results if result.get("status") == "BLOCKED"),
        "error_count": sum(1 for result in execution_results if result.get("status") == "ERROR"),
    }


def _execution_state(
    *,
    execute: bool,
    counts: dict[str, int],
    execution_blocked_reason: str,
) -> str:
    if not execute:
        return "planned"
    if execution_blocked_reason:
        return "blocked"
    if counts["executed_count"] > 0 and counts["blocked_count"] == 0 and counts["error_count"] == 0:
        return "executed"
    if counts["executed_count"] > 0:
        return "partial"
    if counts["blocked_count"] or counts["error_count"]:
        return "blocked"
    return "skipped"


def _model_execution_gate(
    *,
    execute: bool,
    counts: dict[str, int],
    execution_blocked_reason: str,
) -> dict[str, Any]:
    if not execute:
        status = "PENDING"
        reason = "No model backtest/replay evidence has been emitted for this active run."
    elif execution_blocked_reason:
        status = "BLOCKED"
        reason = execution_blocked_reason
    elif counts["executed_count"] > 0 and counts["blocked_count"] == 0 and counts["error_count"] == 0:
        status = "RESEARCH_ONLY_EXECUTED"
        reason = "Eligible available-data models emitted Workbench campaign evidence with safe research-only kwargs."
    elif counts["executed_count"] > 0:
        status = "PARTIAL_RESEARCH_EXECUTION"
        reason = "Some eligible available-data models emitted campaign evidence; others blocked or errored."
    elif counts["blocked_count"] or counts["error_count"]:
        status = "BLOCKED"
        reason = "No eligible available-data model emitted accepted campaign evidence."
    else:
        status = "SKIPPED"
        reason = "No plan row was eligible for available-data research execution."
    return {
        "gate": "model_execution",
        "status": status,
        "reason": reason,
        **counts,
    }


def _build_summary(
    plan: dict[str, Any],
    run_id: str,
    *,
    execute: bool,
    execution_results: list[dict[str, Any]],
    execution_blocked_reason: str = "",
) -> dict[str, Any]:
    counts = _execution_result_counts(execution_results)
    state = _execution_state(
        execute=execute,
        counts=counts,
        execution_blocked_reason=execution_blocked_reason,
    )
    decision_reason = (
        "All-lane run attempted eligible available-data research campaigns only; promotion remains blocked."
        if execute
        else "All-lane run has a clean model plan; model execution evidence has not been emitted yet."
    )
    if execution_blocked_reason:
        decision_reason = execution_blocked_reason
    return {
        "schema_version": "workbench_all_lanes_summary_v1",
        "run_id": run_id,
        "state": state,
        "current_stage": "model_execution_campaigns" if execute else "model_execution_plan",
        "planned_model_count": plan["model_count"],
        "registered_lane_count": plan.get("registered_lane_count", len(plan.get("lanes", []))),
        "lane_model_counts": plan.get("lane_model_counts", {}),
        "lane_coverage_gates": plan.get("lane_coverage_gates", []),
        "model_gap_gates": plan.get("model_gap_gates", []),
        "model_universe_status": plan.get("model_universe_status", "PLANNED"),
        "terminal_counts": plan["terminal_counts"],
        "execution_results": execution_results,
        "decision_action": "BLOCKED",
        "decision_reason": decision_reason,
        "blocking_gates": [
            *list(plan.get("lane_coverage_gates", [])),
            *list(plan.get("model_gap_gates", [])),
            _model_execution_gate(
                execute=execute,
                counts=counts,
                execution_blocked_reason=execution_blocked_reason,
            ),
        ],
    }


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


def _structural_cme_options_models(
    registrations: dict[str, Any],
    model_ids: list[str],
) -> dict[str, dict[str, Any]]:
    registration = registrations.get(Lane.CME_OPTIONS.value)
    prefixes = {str(prefix).upper() for prefix in getattr(registration, "model_id_prefixes", ()) or ()}
    if "FOPT_" not in prefixes:
        return {}
    if any(str(model_id).upper().startswith("FOPT_") for model_id in model_ids):
        return {}
    return {_CME_OPTIONS_STRUCTURAL_MODEL_ID: dict(_CME_OPTIONS_STRUCTURAL_MODEL_CONFIG)}


def _load_q001_owner_decision(repo: Path) -> tuple[dict[str, Any], str]:
    path = repo / _Q001_OWNER_DECISION_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "q001_owner_decision_missing"
    except json.JSONDecodeError:
        return {}, "q001_owner_decision_invalid_json"
    if not isinstance(payload, dict):
        return {}, "q001_owner_decision_invalid_shape"
    policy = payload.get("model_gap_policy")
    if not isinstance(policy, dict):
        return payload, "q001_owner_decision_invalid_model_gap_policy"
    accepted_evidence = payload.get("accepted_evidence")
    if not isinstance(accepted_evidence, dict):
        return payload, "q001_owner_decision_invalid_accepted_evidence"
    for key, expected in _Q001_ACCEPTED_EVIDENCE.items():
        if accepted_evidence.get(key) != expected:
            return payload, "q001_owner_decision_invalid_accepted_evidence"
    warn_checks = accepted_evidence.get("options_warn_checks")
    if (
        not isinstance(warn_checks, list)
        or {str(check) for check in warn_checks} != _Q001_ACCEPTED_OPTIONS_WARN_CHECKS
    ):
        return payload, "q001_owner_decision_invalid_accepted_evidence"
    if payload.get("question_id") != "Q001" or payload.get("status") != "ACCEPTED_AVAILABLE_DATA_SCOPE":
        return payload, "q001_owner_decision_invalid_status"
    if payload.get("mbo_gap_ledger") != _Q001_ACCEPTED_LEDGER_STATUS:
        return payload, "q001_owner_decision_invalid_mbo_gap_ledger"
    if payload.get("options_strict_mbo_warning_ledger") != _Q001_ACCEPTED_LEDGER_STATUS:
        return payload, "q001_owner_decision_invalid_options_ledger"
    if payload.get("available_data_research_allowed") is not True:
        return payload, "q001_owner_decision_invalid_available_data_permission"
    for key, expected in _Q001_REQUIRED_MODEL_GAP_POLICY.items():
        if policy.get(key) != expected:
            return payload, "q001_owner_decision_invalid_model_gap_policy"
    return payload, ""


def _has_strict_missing_data_dependency(
    *,
    model_id: str,
    lane: str,
    display_name: str,
    required_datasets: list[Any],
) -> bool:
    datasets = {str(dataset).lower() for dataset in required_datasets}
    if datasets & _STRICT_OPTIONS_DATASETS:
        return True
    if "l2_order_book" not in datasets:
        return False
    context = " ".join([model_id, lane, display_name, *sorted(datasets)]).lower()
    return any(term in context for term in ("option", "options", "fopt"))


def _available_data_scope_fields(q001_policy: dict[str, Any], q001_error: str) -> dict[str, Any]:
    if q001_error:
        return {
            "available_data_policy": "VERIFY_Q001_OWNER_DECISION_BEFORE_EXECUTION",
            "q001_policy_warning": q001_error,
            "authority_refs": list(_Q001_AUTHORITY_REFS),
            "skip_or_rejection_required": True,
        }
    return {
        "available_data_policy": str(q001_policy.get("available_data_models") or "RUN_WITH_EXPLICIT_COVERAGE"),
        "authority_refs": list(_Q001_AUTHORITY_REFS),
        "skip_or_rejection_required": True,
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
    model_event_bindings = _load_model_event_bindings(repo)
    q001_owner_decision, q001_error = _load_q001_owner_decision(repo)
    q001_policy = q001_owner_decision.get("model_gap_policy") if not q001_error else {}
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
    registry_model_ids = list_models()
    structural_lane_models = _structural_cme_options_models(registrations, registry_model_ids)
    for model_id in sorted([*registry_model_ids, *structural_lane_models]):
        binding = model_event_bindings.get(model_id, {})
        lane = _resolve_plan_lane(registry, model_id, binding)
        lane_model_counts[lane] = lane_model_counts.get(lane, 0) + 1
        catalog_entry = catalog.get(model_id)
        structural_config = structural_lane_models.get(model_id)
        config_payload = (
            dict(structural_config)
            if structural_config
            else _model_config_payload(model_configs.get(model_id))
        )
        display_name = (
            str(structural_config.get("display_name"))
            if structural_config
            else getattr(catalog_entry, "display_name", model_id)
            if catalog_entry
            else model_id
        )
        reason = "All-lane dry-run planning emitted no execution evidence yet."
        terminal_state = "BLOCKED_VALIDATION"
        data_scope_fields = _available_data_scope_fields(q001_policy, q001_error)
        if lane in lane_config_errors:
            terminal_state = "BLOCKED_VALIDATION"
            reason = f"Lane config failed to load: {lane_config_errors[lane]}"
        if _has_strict_missing_data_dependency(
            model_id=model_id,
            lane=lane,
            display_name=display_name,
            required_datasets=config_payload["required_datasets"],
        ):
            terminal_state = "BLOCKED_MISSING_DATA"
            if q001_error:
                reason_code = q001_error
                missing_data_policy = "SIDELINE_UNTIL_Q001_OWNER_DECISION_VALID"
                reason = "Q001 owner decision is missing or invalid; strict options missing-data model is fail-closed."
            else:
                reason_code = "q001_strict_options_missing_data_sidelined"
                missing_data_policy = str(
                    q001_policy.get("strict_options_quote_required_models") or "SIDELINE_UNTIL_DATA_FILLED"
                )
                reason = (
                    "Q001 accepts available-data inventory scope only; strict options quote/chain/order-book "
                    "models stay sidelined until data is filled or separately scoped out."
                )
            data_scope_fields = {
                "reason_code": reason_code,
                "missing_data_policy": missing_data_policy,
                "authority_refs": list(_Q001_AUTHORITY_REFS),
                "skip_or_rejection_required": True,
            }
        execution_symbol = _binding_execution_symbol(binding)
        execution_block_reason = ""
        if lane in lane_config_errors:
            execution_block_reason = f"lane_config_failed:{lane_config_errors[lane]}"
        elif terminal_state != _EXECUTION_ELIGIBLE_STATE:
            execution_block_reason = str(data_scope_fields.get("reason_code") or terminal_state)
        elif data_scope_fields.get("available_data_policy") != _EXECUTION_ELIGIBLE_POLICY:
            execution_block_reason = str(
                data_scope_fields.get("q001_policy_warning") or "available_data_policy_not_accepted"
            )
        elif not execution_symbol:
            execution_block_reason = "missing_explicit_workbench_symbol"
        row = {
            "run_id": run_id,
            "model_id": model_id,
            "lane": lane,
            "campaign_mode": str(binding.get("campaign_mode") or ""),
            "role": (
                str(structural_config.get("role"))
                if structural_config
                else getattr(catalog_entry, "role", "")
                if catalog_entry
                else ""
            ),
            "display_name": display_name,
            **config_payload,
            "terminal_state": terminal_state,
            "reason": reason,
            "evidence_scope": "all_lanes_plan_no_previous_run_artifacts",
            "execution_eligible": execution_block_reason == "",
            "execution_block_reason": execution_block_reason,
            **data_scope_fields,
        }
        if execution_symbol:
            row["symbol"] = execution_symbol
        models.append(row)

    terminal_counts = {state: 0 for state in sorted(TERMINAL_STATES)}
    for row in models:
        terminal_counts[row["terminal_state"]] += 1

    lane_coverage_gates = _lane_coverage_gates(lanes, lane_model_counts)
    model_gap_gates = []
    if q001_error and terminal_counts.get("BLOCKED_MISSING_DATA", 0) > 0:
        model_gap_gates.append(
            {
                "gate": "q001_owner_decision",
                "status": "BLOCKING",
                "reason": "Strict missing-data models require a valid Q001 owner decision before available-data scope can proceed.",
                "reason_code": q001_error,
            }
        )
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
        "model_gap_gates": model_gap_gates,
        "model_universe_status": "BLOCKING" if lane_coverage_gates or model_gap_gates else "PLANNED",
        "models": models,
        "model_count": len(models),
        "terminal_states": sorted(TERMINAL_STATES),
        "terminal_counts": terminal_counts,
    }


def run_all_lanes(repo: Path, run_id: str, *, execute: bool = False) -> dict[str, Any]:
    """Write all-lane run artifacts.

    Planning mode creates the no-leakage execution plan and terminal-state rows.
    Execution mode hands eligible available-data rows to the existing Workbench
    campaign runner without enabling downloads, trial mode, paper, or live routing.
    """

    plan = build_all_lanes_plan(repo, run_id)
    execution_results: list[dict[str, Any]] = []
    run_dir = repo / "runtime" / "workbench" / "all_lanes" / run_id
    rejected_stale = {}
    rejected_stale_path = run_dir / "rejected_stale_artifacts.json"
    if rejected_stale_path.is_file():
        try:
            rejected_stale = json.loads(rejected_stale_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rejected_stale = {}
    _write_json(run_dir / "plan.json", plan)
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
    summary = _build_summary(plan, run_id, execute=False, execution_results=[])
    _write_json(run_dir / "summary.json", summary)
    from workbench.src.run.leakage_detector import run_leakage_detection

    leakage = run_leakage_detection(repo, run_id=run_id)
    execution_blocked_reason = ""
    if execute:
        if leakage.get("status") != "PASS":
            execution_blocked_reason = "Leakage detector failed before model execution; campaigns were not started."
        elif plan.get("lane_coverage_gates") or plan.get("model_gap_gates"):
            execution_blocked_reason = "All-lane plan has blocking lane or model-gap gates; campaigns were not started."
        else:
            execution_results = _execute_available_data_models(repo, plan, run_id)
            _write_json(run_dir / "plan.json", plan)
    summary = _build_summary(
        plan,
        run_id,
        execute=execute,
        execution_results=execution_results,
        execution_blocked_reason=execution_blocked_reason,
    )
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
