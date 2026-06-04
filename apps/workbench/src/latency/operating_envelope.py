"""Workbench latency operating envelope.

This module turns existing C++/CHI404 latency evidence into promotion-gating
operating limits for Workbench runs and campaigns.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from trade_manager.latency_capability import (
    CapabilityAssumptions,
    PendingExposureConfig,
    classify_ack_lag,
    classify_internal_speed,
)
from workbench.src.core.composition import CompositionTrace, ModelComposition
from workbench.src.core.trade_audit import TradeAuditRecord
from workbench.src.latency.viability import LatencyViability
from workbench.src.registry.model_catalog import get_catalog_entry
from workbench.src.sim.cpp_latency_profile import CppLatencyProfile

DEFAULT_OPPORTUNITY_DECAY_WINDOWS_US = (100.0, 250.0, 500.0, 1_000.0, 2_000.0, 5_000.0)
DEFAULT_COMPETITOR_MULTIPLIERS = {"faster": 0.5, "equal": 1.0, "slower": 2.0}


def build_latency_operating_envelope(
    *,
    run_id: str,
    model_id: str,
    event_id: str,
    viability: LatencyViability,
    cpp_profile: CppLatencyProfile,
    phase5_timestamp_schema: Mapping[str, Any],
    audit_records: Sequence[TradeAuditRecord],
    composition: ModelComposition | None = None,
    composition_trace: CompositionTrace | Mapping[str, Any] | None = None,
    chi404_observed: bool = False,
    wfc_status: str | None = None,
    assumptions: CapabilityAssumptions | None = None,
    robustness: Any = None,
    execution_path_audit_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    assumptions = assumptions or CapabilityAssumptions(
        opportunity_decay_us=1_000.0,
        pending_exposure=PendingExposureConfig(),
    )
    placement = _placement_percentiles(audit_records, cpp_profile)
    confirmation = _confirmation_percentiles(audit_records, cpp_profile)
    tick_to_send = _p(placement, "tick_to_send_us", "p99")
    tick_to_trigger = _p(placement, "tick_to_send_trigger_us", "p99")
    effective_trigger = tick_to_trigger if tick_to_trigger is not None else tick_to_send
    send_to_ack = _p(confirmation, "send_to_ack_us", "p99")
    latency_authority = _latency_authority(cpp_profile, chi404_observed)
    role = _catalog_role(model_id)
    composition_payload = _composition_payload(composition, composition_trace)
    defensive_timing_required = bool(
        role.get("role") in {"defensive", "hybrid"}
        or (composition is not None and composition.defensive_stubs)
    )
    competitor = _competitor_sensitivity(
        tick_to_send=effective_trigger,
        viability=viability,
    )
    pending_controls = _pending_controls(assumptions.pending_exposure, send_to_ack, latency_authority)
    checks = _checks(
        latency_authority=latency_authority,
        phase5_timestamp_schema=phase5_timestamp_schema,
        tick_to_send=tick_to_send,
        viability=viability,
        pending_controls=pending_controls,
        composition_payload=composition_payload,
        competitor=competitor,
        defensive_timing_required=defensive_timing_required,
        execution_path_audit=_execution_path_audit_payload(execution_path_audit_status),
    )
    promotion_blockers = [
        {
            "gate": name,
            "status": "FAIL",
            "reason": check["reason"],
        }
        for name, check in checks.items()
        if not check["passed"]
    ]
    status = "PASS" if not promotion_blockers else "FAIL"
    robustness_payload = _robustness_payload(robustness)
    if robustness_payload:
        checks["robustness_attached"] = {
            "passed": bool(robustness_payload.get("passed")),
            "reason": "robustness result attached to final envelope",
        }
    return {
        "schema_version": "latency_operating_envelope.v1",
        "run_id": run_id,
        "model_id": model_id,
        "event_id": event_id,
        "status": status,
        "source_authority": "cpp_chi404" if chi404_observed else "cpp_yaml_default",
        "source_authoritative": bool(latency_authority["authoritative"]),
        "source_authority_detail": latency_authority,
        "python_runtime_authoritative": False,
        "wfc_status": wfc_status,
        "model_role": role.get("role", "unknown"),
        "model_default_phase": role.get("default_phase", "unknown"),
        "latency_lane_required": viability.lane_required,
        "latency_lane_measured": viability.lane_measured,
        "offensive": {
            "placement": placement,
            "operating_band": classify_internal_speed(effective_trigger),
            "sdk_return_operating_band": classify_internal_speed(tick_to_send),
            "opportunity_decay_windows_us": list(DEFAULT_OPPORTUNITY_DECAY_WINDOWS_US),
            "opportunity_window_compatible": _compatible_windows(tick_to_send),
            "trigger_opportunity_window_compatible": _compatible_windows(effective_trigger),
            "latency_adjusted_pnl": viability.simulated_latency_adjusted_pnl,
            "latency_profitability_buffer_us": viability.latency_profitability_buffer_us,
        },
        "defensive": {
            "timing_required": defensive_timing_required,
            "placement": {
                "cancel_to_send_us": _missing_metric(),
                "replace_to_send_us": _missing_metric(),
            },
            "confirmation": {
                "cancel_to_ack_us": _missing_metric(),
                "replace_to_ack_us": _missing_metric(),
            },
            "stale_pending_order_risk": pending_controls["stale_state_risk"],
            "response_feasibility": "cancel_replace_not_observed",
        },
        "hybrid_composition": composition_payload,
        "external_confirmation": {
            "confirmation": confirmation,
            "ack_lag_classification": classify_ack_lag(send_to_ack),
            "modeled_as_async_state_confirmation": True,
            "blocks_on_ack": False,
        },
        "competitor_speed_sensitivity": competitor,
        "pending_state_risk": pending_controls,
        "execution_path_audit": _execution_path_audit_payload(execution_path_audit_status),
        "checks": checks,
        "promotion_blockers": promotion_blockers,
        "robustness": robustness_payload,
    }


def compact_envelope_fields(envelope: Mapping[str, Any]) -> dict[str, Any]:
    offensive = envelope.get("offensive") if isinstance(envelope.get("offensive"), Mapping) else {}
    external = envelope.get("external_confirmation") if isinstance(envelope.get("external_confirmation"), Mapping) else {}
    placement = offensive.get("placement") if isinstance(offensive.get("placement"), Mapping) else {}
    confirmation = external.get("confirmation") if isinstance(external.get("confirmation"), Mapping) else {}
    send_to_ack = confirmation.get("send_to_ack_us") if isinstance(confirmation.get("send_to_ack_us"), Mapping) else {}
    tick_to_trigger = placement.get("tick_to_send_trigger_us") if isinstance(placement.get("tick_to_send_trigger_us"), Mapping) else {}
    tick_to_send = placement.get("tick_to_send_us") if isinstance(placement.get("tick_to_send_us"), Mapping) else {}
    return {
        "latency_operating_envelope_status": envelope.get("status"),
        "latency_operating_envelope_source": envelope.get("source_authority"),
        "offensive_operating_band": offensive.get("operating_band"),
        "async_ack_risk": (envelope.get("pending_state_risk") or {}).get("stale_state_risk"),
        "placement_trigger_p50_us": tick_to_trigger.get("p50"),
        "placement_trigger_p99_us": tick_to_trigger.get("p99"),
        "placement_trigger_p99_9_us": tick_to_trigger.get("p99_9"),
        "placement_speed_p50_us": tick_to_send.get("p50"),
        "placement_speed_p99_us": tick_to_send.get("p99"),
        "placement_speed_p99_9_us": tick_to_send.get("p99_9"),
        "send_to_ack_p99_us": send_to_ack.get("p99"),
        "execution_path_audit_status": (envelope.get("execution_path_audit") or {}).get("status"),
        "execution_path_audit_run_id": (envelope.get("execution_path_audit") or {}).get("run_id"),
        "promotion_blockers": envelope.get("promotion_blockers", []),
    }


def aggregate_campaign_latency_envelopes(
    *,
    campaign_id: str,
    model_id: str,
    symbol: str,
    period_results: Sequence[Any],
    event_envelopes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [dict(row) for row in event_envelopes if isinstance(row, Mapping)]
    blockers = [
        blocker
        for row in rows
        for blocker in (row.get("promotion_blockers") or [])
        if isinstance(blocker, Mapping)
    ]
    expected_events = _expected_event_count(period_results)
    if expected_events and len(rows) < expected_events:
        blockers.append(
            {
                "gate": "campaign_latency_operating_envelope",
                "status": "MISSING_EVENT_ENVELOPE",
                "reason": f"expected {expected_events} event latency envelopes but observed {len(rows)}",
            }
        )
    statuses = [str(row.get("status", "")).upper() for row in rows]
    placement_p99s = [
        float(v)
        for row in rows
        for v in [_nested(row, ("offensive", "placement", "tick_to_send_us", "p99"))]
        if isinstance(v, (int, float))
    ]
    trigger_p99s = [
        float(v)
        for row in rows
        for v in [_nested(row, ("offensive", "placement", "tick_to_send_trigger_us", "p99"))]
        if isinstance(v, (int, float))
    ]
    status = "PASS" if rows and not blockers and all(s == "PASS" for s in statuses) else "FAIL"
    if not rows:
        blockers.append(
            {
                "gate": "campaign_latency_operating_envelope",
                "status": "MISSING",
                "reason": "no event latency operating envelopes were generated",
            }
        )
    return {
        "schema_version": "campaign_latency_operating_envelope.v1",
        "campaign_id": campaign_id,
        "model_id": model_id,
        "symbol": symbol,
        "status": status,
        "events_observed": len(rows),
        "periods": [_period_latency_summary(period) for period in period_results],
        "event_envelopes": [
            {
                "run_id": row.get("run_id"),
                "event_id": row.get("event_id"),
                **compact_envelope_fields(row),
            }
            for row in rows
        ],
        "placement_trigger_p99_us": max(trigger_p99s) if trigger_p99s else None,
        "placement_speed_p99_us": max(placement_p99s) if placement_p99s else None,
        "promotion_blockers": blockers,
    }


def write_latency_operating_envelope(artifact_dir: Path, envelope: Mapping[str, Any]) -> tuple[Path, Path]:
    json_path = artifact_dir / "latency_operating_envelope.json"
    md_path = artifact_dir / "latency_operating_envelope.md"
    json_path.write_text(json.dumps(envelope, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    md_path.write_text(render_latency_operating_envelope_markdown(envelope), encoding="utf-8")
    return json_path, md_path


def write_campaign_latency_operating_envelope(artifact_dir: Path, envelope: Mapping[str, Any]) -> tuple[Path, Path]:
    json_path = artifact_dir / "campaign_latency_operating_envelope.json"
    md_path = artifact_dir / "campaign_latency_operating_envelope.md"
    json_path.write_text(json.dumps(envelope, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    md_path.write_text(render_campaign_latency_operating_envelope_markdown(envelope), encoding="utf-8")
    return json_path, md_path


def render_latency_operating_envelope_markdown(envelope: Mapping[str, Any]) -> str:
    compact = compact_envelope_fields(envelope)
    lines = [
        "# Latency Operating Envelope",
        "",
        f"Run ID: `{envelope.get('run_id', '')}`",
        f"Status: `{envelope.get('status', 'unknown')}`",
        f"Source authority: `{envelope.get('source_authority', 'unknown')}`",
        f"Placement band: `{compact.get('offensive_operating_band', 'unknown')}`",
        f"Tick-to-trigger p99: `{_fmt(compact.get('placement_trigger_p99_us'))}`",
        f"Tick-to-SDK-return p99: `{_fmt(compact.get('placement_speed_p99_us'))}`",
        f"Send-to-ack p99: `{_fmt(compact.get('send_to_ack_p99_us'))}`",
        f"Async ack risk: `{compact.get('async_ack_risk', 'unknown')}`",
        f"Execution-path audit: `{compact.get('execution_path_audit_status') or 'missing'}`",
        "",
        "## Interpretation",
        "Placement speed is measured separately from acknowledgment latency. Acknowledgments reconcile official state asynchronously and are not treated as placement speed.",
        "",
    ]
    blockers = envelope.get("promotion_blockers") or []
    if blockers:
        lines.extend(["## Promotion Blockers", ""])
        lines.extend(f"- `{b.get('gate')}`: {b.get('reason')}" for b in blockers if isinstance(b, Mapping))
        lines.append("")
    return "\n".join(lines)


def render_campaign_latency_operating_envelope_markdown(envelope: Mapping[str, Any]) -> str:
    lines = [
        "# Campaign Latency Operating Envelope",
        "",
        f"Campaign ID: `{envelope.get('campaign_id', '')}`",
        f"Status: `{envelope.get('status', 'unknown')}`",
        f"Events observed: `{envelope.get('events_observed', 0)}`",
        f"Worst tick-to-trigger p99: `{_fmt(envelope.get('placement_trigger_p99_us'))}`",
        f"Worst tick-to-SDK-return p99: `{_fmt(envelope.get('placement_speed_p99_us'))}`",
        "",
    ]
    blockers = envelope.get("promotion_blockers") or []
    if blockers:
        lines.extend(["## Promotion Blockers", ""])
        lines.extend(f"- `{b.get('gate')}`: {b.get('reason')}" for b in blockers if isinstance(b, Mapping))
        lines.append("")
    return "\n".join(lines)


def _placement_percentiles(records: Sequence[TradeAuditRecord], profile: CppLatencyProfile) -> dict[str, dict[str, float]]:
    if records:
        feed = [float(r.feed_delay_us) for r in records]
        decision = [float(r.feed_delay_us + r.decision_compute_us) for r in records]
        send = [float(r.decision_to_send_us) for r in records]
        tick_to_send = [float(r.feed_delay_us + r.decision_compute_us + r.decision_to_send_us) for r in records]
        decision_to_trigger = _optional_record_values(records, "decision_to_send_trigger_us")
        tick_to_trigger = _optional_record_values(records, "tick_to_send_trigger_us")
        rithmic_send_call = _optional_record_values(records, "rithmic_send_call_us")
        return {
            "feed_delay_us": _metric(feed),
            "tick_to_decision_us": _metric(decision),
            "decision_to_send_trigger_us": _metric(decision_to_trigger) if decision_to_trigger else _missing_metric(),
            "tick_to_send_trigger_us": _metric(tick_to_trigger) if tick_to_trigger else _missing_metric(),
            "decision_to_send_us": _metric(send),
            "tick_to_send_us": _metric(tick_to_send),
            "rithmic_send_call_us": _metric(rithmic_send_call) if rithmic_send_call else _missing_metric(),
        }
    return {
        "feed_delay_us": _metric_from_percentiles(profile.feed_delay),
        "tick_to_decision_us": _sum_percentiles(profile.feed_delay, profile.cpp_decision_compute),
        "decision_to_send_trigger_us": _missing_metric(),
        "tick_to_send_trigger_us": _missing_metric(),
        "decision_to_send_us": _metric_from_percentiles(profile.order_send),
        "tick_to_send_us": _sum_percentiles(profile.feed_delay, profile.cpp_decision_compute, profile.order_send),
        "rithmic_send_call_us": _metric_from_percentiles(profile.order_send),
    }


def _confirmation_percentiles(records: Sequence[TradeAuditRecord], profile: CppLatencyProfile) -> dict[str, dict[str, float]]:
    if records:
        return {"send_to_ack_us": _metric([float(r.send_to_ack_us) for r in records])}
    return {"send_to_ack_us": _metric_from_percentiles(profile.gateway_ack)}


def _checks(
    *,
    latency_authority: Mapping[str, Any],
    phase5_timestamp_schema: Mapping[str, Any],
    tick_to_send: float | None,
    viability: LatencyViability,
    pending_controls: Mapping[str, Any],
    composition_payload: Mapping[str, Any],
    competitor: Mapping[str, Any],
    defensive_timing_required: bool,
    execution_path_audit: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    checks = {
        "operating_envelope_generated": _check(
            tick_to_send is not None and bool(latency_authority.get("authoritative")),
            "envelope generated from CHI404/C++ authority",
            str(latency_authority.get("reason") or "missing CHI404/C++ authoritative placement evidence"),
        ),
        "placement_speed_sensitivity": _check(
            bool(viability.survives_cpp_execution_delay and viability.simulated_latency_adjusted_pnl > 0),
            "latency-adjusted PnL remains positive at measured speed",
            "latency-adjusted PnL or C++ delay survival failed",
        ),
        "async_ack_state_risk": _check(
            bool(pending_controls.get("async_ack_controlled")),
            "acknowledgment handled as controlled async state confirmation",
            "async acknowledgment state risk is not controlled",
        ),
        "pending_exposure_guardrails": _check(
            bool(pending_controls.get("guardrails_configured")),
            "pending exposure guardrails are configured",
            "pending exposure guardrails are missing or invalid",
        ),
        "composition_latency_feasibility": _check(
            bool(
                composition_payload.get("inside_latency_budget")
                and (not defensive_timing_required or composition_payload.get("defensive_timing_observed"))
            ),
            "composition timing is inside configured phase budgets",
            "composition timing exceeds configured phase budgets or defensive timing is not observed",
        ),
        "competitor_speed_sensitivity": _check(
            bool(competitor.get("tested") and competitor.get("equal_speed_viable")),
            "competitor-speed sensitivity was tested and equal-speed case is viable",
            "competitor-speed sensitivity was missing or equal-speed case failed",
        ),
        "phase5_timestamp_schema": _check(
            bool(phase5_timestamp_schema.get("complete") and phase5_timestamp_schema.get("monotonic_non_decreasing")),
            "Phase 5 timestamp chain is complete and monotonic",
            "Phase 5 timestamp chain is incomplete or non-monotonic",
        ),
    }
    status = str(execution_path_audit.get("status") or "").lower()
    checks["low_latency_execution_path_audit"] = _check(
        bool(execution_path_audit.get("observed")) and status not in {"fail", "failed", "blocked", "missing"},
        "latest low-latency execution-path audit is present and not failing",
        str(execution_path_audit.get("reason") or "latest low-latency execution-path audit is missing, failing, or blocked"),
    )
    return checks


def _pending_controls(
    pending: PendingExposureConfig,
    send_to_ack_us: float | None,
    latency_authority: Mapping[str, Any],
) -> dict[str, Any]:
    guardrails = (
        pending.max_pending_orders >= 1
        and pending.max_pending_quantity > 0
        and pending.max_pending_notional >= 0
        and pending.stale_pending_timeout_us > 0
        and pending.cancel_replace_throttle_us >= 0
        and pending.duplicate_order_protection
        and pending.client_order_id_tracking
        and pending.kill_switch_required
    )
    stale = "unknown"
    ack_measured = bool(latency_authority.get("ack_measured"))
    if not ack_measured:
        stale = "not_observed"
    elif send_to_ack_us is not None:
        stale = "high" if send_to_ack_us > pending.stale_pending_timeout_us else "managed"
    return {
        "guardrails_configured": guardrails,
        "async_ack_controlled": guardrails and ack_measured,
        "stale_state_risk": stale,
        "max_pending_orders": pending.max_pending_orders,
        "max_pending_quantity": pending.max_pending_quantity,
        "max_pending_notional": pending.max_pending_notional,
        "stale_pending_timeout_us": pending.stale_pending_timeout_us,
        "duplicate_order_protection": pending.duplicate_order_protection,
        "client_order_id_tracking": pending.client_order_id_tracking,
        "kill_switch_required": pending.kill_switch_required,
        "ack_measured": ack_measured,
    }


def _latency_authority(profile: CppLatencyProfile, chi404_observed: bool) -> dict[str, Any]:
    ack_measured = not bool(profile.order_ack_blocked)
    send_source = str(getattr(profile.order_send, "source", ""))
    ack_source = str(getattr(profile.gateway_ack, "source", ""))
    measured_components = [
        source
        for source in (send_source, ack_source, str(getattr(profile.cpp_decision_compute, "source", "")))
        if source and "yaml" not in source.lower() and "fallback" not in source.lower()
    ]
    authoritative = bool(chi404_observed and ack_measured and measured_components)
    if not chi404_observed:
        reason = "CHI404 latency summary is missing; YAML defaults are not promotion authority"
    elif not ack_measured:
        reason = "CHI404 latency summary reports order_ack_blocked; submit-to-ack evidence is not measured"
    elif not measured_components:
        reason = "CHI404 latency summary did not provide measured C++ latency components"
    else:
        reason = "CHI404/C++ latency evidence is authoritative"
    return {
        "authoritative": authoritative,
        "chi404_observed": bool(chi404_observed),
        "ack_measured": ack_measured,
        "order_ack_blocked": bool(profile.order_ack_blocked),
        "order_send_source": send_source,
        "gateway_ack_source": ack_source,
        "reason": reason,
    }


def _execution_path_audit_payload(status: Mapping[str, Any] | None) -> dict[str, Any]:
    if not status:
        return {
            "observed": False,
            "status": "missing",
            "reason": "current_low_latency_status.json not found",
        }
    failures = status.get("failures") if isinstance(status.get("failures"), list) else []
    warnings = status.get("warnings") if isinstance(status.get("warnings"), list) else []
    failure_reasons = [
        str(row.get("reason") or row.get("gate") or "")
        for row in failures
        if isinstance(row, Mapping)
    ]
    return {
        "observed": True,
        "run_id": status.get("run_id"),
        "status": status.get("status"),
        "mode": status.get("mode"),
        "primary_kpi": status.get("primary_kpi"),
        "tick_to_send_p50_us": status.get("tick_to_send_p50_us"),
        "tick_to_send_p99_us": status.get("tick_to_send_p99_us"),
        "tick_to_send_p99_9_us": status.get("tick_to_send_p99_9_us"),
        "summary_json": status.get("summary_json"),
        "summary_md": status.get("summary_md"),
        "spans_path": status.get("spans_path"),
        "runtime_env_path": status.get("runtime_env_path"),
        "optimization_status": status.get("optimization_status") or {},
        "failure_count": len(failures),
        "warning_count": len(warnings),
        "reason": "; ".join(reason for reason in failure_reasons if reason) or status.get("blocked_reason") or "",
    }


def _competitor_sensitivity(
    *,
    tick_to_send: float | None,
    viability: LatencyViability,
) -> dict[str, Any]:
    if tick_to_send is None:
        return {"tested": False, "reason": "tick_to_send missing"}
    scenarios: dict[str, Any] = {}
    for name, multiplier in DEFAULT_COMPETITOR_MULTIPLIERS.items():
        competitor_us = tick_to_send * multiplier
        window_results = []
        for window_us in DEFAULT_OPPORTUNITY_DECAY_WINDOWS_US:
            competitor_penalty_us = max(0.0, tick_to_send - competitor_us)
            window_penalty_us = max(0.0, tick_to_send - window_us)
            total_penalty_us = competitor_penalty_us + window_penalty_us
            adjusted_pnl = _pnl_at_penalty(viability, total_penalty_us)
            window_results.append(
                {
                    "opportunity_decay_window_us": window_us,
                    "latency_penalty_us": total_penalty_us,
                    "latency_adjusted_pnl": adjusted_pnl,
                    "viable": bool(
                        adjusted_pnl > 0
                        and viability.latency_profitability_buffer_us > total_penalty_us
                    ),
                }
            )
        scenarios[name] = {
            "competitor_tick_to_send_us": competitor_us,
            "ours_relation": "faster" if tick_to_send < competitor_us else ("slower" if tick_to_send > competitor_us else "equal"),
            "window_results": window_results,
            "viable": any(row["viable"] for row in window_results),
            "worst_case_viable": all(row["viable"] for row in window_results),
        }
    return {
        "tested": True,
        "scenarios": scenarios,
        "equal_speed_viable": bool(scenarios["equal"]["viable"]),
        "faster_competitor_viable": bool(scenarios["faster"]["viable"]),
        "opportunity_decay_windows_us": list(DEFAULT_OPPORTUNITY_DECAY_WINDOWS_US),
    }


def _pnl_at_penalty(viability: LatencyViability, penalty_us: float) -> float:
    points = viability.pnl_by_injection_us or {}
    if not points:
        return float(viability.simulated_latency_adjusted_pnl)
    nearest = min(points.keys(), key=lambda key: abs(float(key) - float(penalty_us)))
    return float(points[nearest])


def _composition_payload(
    composition: ModelComposition | None,
    trace: CompositionTrace | Mapping[str, Any] | None,
) -> dict[str, Any]:
    if trace is None:
        return {
            "composition_mode": "alpha_only",
            "active_model_roles": ["alpha"],
            "phase_budgets_us": {},
            "actual_phase_timing_us": {},
            "arbitration_delay_us": 0.0,
            "inside_latency_budget": True,
            "defensive_timing_observed": True,
            "budget_violations": [],
        }
    payload = trace.to_dict() if hasattr(trace, "to_dict") else dict(trace)
    steps = [row for row in payload.get("steps", []) if isinstance(row, Mapping)]
    violations = [
        {
            "model_id": row.get("model_id"),
            "phase": row.get("phase"),
            "actual_us": row.get("actual_us"),
            "budget_us": row.get("budget_us"),
        }
        for row in steps
        if _num(row.get("actual_us")) is not None
        and _num(row.get("budget_us")) is not None
        and float(row.get("actual_us")) > float(row.get("budget_us"))
    ]
    actual_by_phase: dict[str, float] = {}
    for row in steps:
        phase = str(row.get("phase") or "unknown")
        actual_by_phase[phase] = actual_by_phase.get(phase, 0.0) + float(row.get("actual_us") or 0.0)
    mode = "alpha_only"
    if composition and composition.defensive_stubs:
        mode = "hybrid_composition"
    return {
        "composition_mode": mode,
        "active_model_roles": ["alpha", "defensive"] if steps else ["alpha"],
        "phase_budgets_us": payload.get("phase_budgets_us") or {},
        "actual_phase_timing_us": actual_by_phase,
        "arbitration_delay_us": sum(actual_by_phase.values()),
        "inside_latency_budget": not violations,
        "defensive_timing_observed": False,
        "budget_violations": violations,
        "trades_vetoed": payload.get("trades_vetoed", 0),
    }


def _catalog_role(model_id: str) -> dict[str, Any]:
    try:
        entry = get_catalog_entry(model_id)
    except Exception:
        return {"role": "unknown", "default_phase": "unknown"}
    return {"role": entry.role, "default_phase": entry.default_phase, "budget_us": entry.budget_us}


def _robustness_payload(robustness: Any) -> dict[str, Any]:
    if robustness is None:
        return {}
    return {
        "passed": bool(getattr(robustness, "passed", False)),
        "failed_checks": [c.name for c in getattr(robustness, "checks", []) if getattr(c, "status", "") == "FAIL"],
        "pending_checks": [c.name for c in getattr(robustness, "checks", []) if getattr(c, "status", "") == "PENDING"],
    }


def _period_latency_summary(period: Any) -> dict[str, Any]:
    payload = asdict(period) if is_dataclass(period) else dict(period)
    events = payload.get("event_results") or []
    statuses = [e.get("latency_operating_envelope_status") for e in events if isinstance(e, Mapping)]
    return {
        "name": payload.get("name"),
        "events_run": payload.get("events_run"),
        "latency_operating_envelope_status": "PASS" if statuses and all(s == "PASS" for s in statuses) else "FAIL",
    }


def _expected_event_count(period_results: Sequence[Any]) -> int:
    expected = 0
    for period in period_results:
        payload = asdict(period) if is_dataclass(period) else dict(period)
        events = payload.get("event_results") or []
        if isinstance(events, Sequence) and not isinstance(events, (str, bytes)):
            expected += len([event for event in events if isinstance(event, Mapping)])
        else:
            try:
                expected += int(payload.get("events_run") or 0)
            except (TypeError, ValueError):
                continue
    return expected


def _check(condition: bool, pass_reason: str, fail_reason: str) -> dict[str, Any]:
    return {"passed": bool(condition), "reason": pass_reason if condition else fail_reason}


def _metric(values: Iterable[float]) -> dict[str, float]:
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return _empty_metric()
    return {
        "count": float(len(ordered)),
        "p50": _quantile(ordered, 0.50),
        "p95": _quantile(ordered, 0.95),
        "p99": _quantile(ordered, 0.99),
        "p99_9": _quantile(ordered, 0.999),
        "max": float(max(ordered)),
    }


def _optional_record_values(records: Sequence[TradeAuditRecord], attr: str) -> list[float]:
    values: list[float] = []
    for record in records:
        value = getattr(record, attr, None)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _empty_metric() -> dict[str, float]:
    return {"count": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "p99_9": 0.0, "max": 0.0}


def _missing_metric() -> dict[str, Any]:
    return {
        "observed": False,
        "status": "NOT_OBSERVED",
        "count": 0.0,
        "p50": None,
        "p95": None,
        "p99": None,
        "p99_9": None,
        "max": None,
    }


def _metric_from_percentiles(block: Any) -> dict[str, float]:
    return {
        "count": 0.0,
        "p50": float(block.p50_us),
        "p95": float(block.p95_us),
        "p99": float(block.p99_us),
        "p99_9": float(block.p99_us),
        "max": float(block.p99_us),
    }


def _sum_percentiles(*blocks: Any) -> dict[str, float]:
    return {
        "count": 0.0,
        "p50": sum(float(b.p50_us) for b in blocks),
        "p95": sum(float(b.p95_us) for b in blocks),
        "p99": sum(float(b.p99_us) for b in blocks),
        "p99_9": sum(float(b.p99_us) for b in blocks),
        "max": sum(float(b.p99_us) for b in blocks),
    }


def _quantile(ordered: Sequence[float], pct: float) -> float:
    if not ordered:
        return 0.0
    idx = (len(ordered) - 1) * pct
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return float(ordered[lo])
    frac = idx - lo
    return float(ordered[lo] * (1 - frac) + ordered[hi] * frac)


def _compatible_windows(tick_to_send: float | None) -> dict[str, bool]:
    if tick_to_send is None:
        return {str(int(w)): False for w in DEFAULT_OPPORTUNITY_DECAY_WINDOWS_US}
    return {str(int(w)): tick_to_send <= w for w in DEFAULT_OPPORTUNITY_DECAY_WINDOWS_US}


def _p(section: Mapping[str, Any], name: str, pct: str) -> float | None:
    metric = section.get(name)
    if isinstance(metric, Mapping):
        return _num(metric.get(pct))
    return None


def _nested(payload: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _fmt(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "missing"
    return f"{number:.1f} us"
