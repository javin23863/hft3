"""Latency-aware operational capability modeling for Trade Manager research.

The module converts measured latency baselines into operational statements and
parameterized offensive/defensive/hybrid interaction tests. It does not route
orders and does not decide which model relationship is correct.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any


INTERNAL_LATENCY_FIELDS = (
    "tick_to_decision_us",
    "decision_to_send_trigger_us",
    "tick_to_send_trigger_us",
    "decision_to_send_us",
    "tick_to_send_us",
    "rithmic_send_call_us",
    "cancel_to_send_us",
    "replace_to_send_us",
    "feed_latency_us",
    "new_send_to_exchange_us",
    "new_exchange_to_ack_us",
    "cancel_send_to_exchange_us",
)
EXTERNAL_CONFIRMATION_FIELDS = (
    "send_to_ack_us",
    "cancel_to_ack_us",
    "replace_to_ack_us",
    "fill_received_latency_us",
    "cancel_exchange_to_ack_us",
)


class ModelInteractionMode(str, Enum):
    OFFENSIVE_ONLY = "offensive_only"
    DEFENSIVE_ALWAYS_ACTIVE = "defensive_always_active"
    DEFENSIVE_PRE_ACTION_ONLY = "defensive_pre_action_only"
    DEFENSIVE_DURING_ACTION = "defensive_during_action"
    DEFENSIVE_POST_ACTION = "defensive_post_action"
    CONCURRENT_OFFENSIVE_DEFENSIVE = "concurrent_offensive_defensive"
    HYBRID_CONFIGURATION = "hybrid_configuration"


MODEL_INTERACTION_MODES = tuple(mode.value for mode in ModelInteractionMode)


class LocalOrderState(str, Enum):
    PENDING_NEW = "PENDING_NEW"
    ACKED = "ACKED"
    WORKING = "WORKING"
    REJECTED = "REJECTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    PENDING_CANCEL = "PENDING_CANCEL"
    CANCELED = "CANCELED"
    CANCEL_REJECTED = "CANCEL_REJECTED"
    PENDING_REPLACE = "PENDING_REPLACE"
    REPLACED = "REPLACED"
    REPLACE_REJECTED = "REPLACE_REJECTED"


PENDING_STATES = frozenset(
    {
        LocalOrderState.PENDING_NEW,
        LocalOrderState.PENDING_CANCEL,
        LocalOrderState.PENDING_REPLACE,
    }
)


@dataclass(frozen=True)
class PendingExposureConfig:
    max_pending_orders: int = 1
    max_pending_quantity: float = 1.0
    max_pending_notional: float = 0.0
    stale_pending_timeout_us: float = 500_000.0
    cancel_replace_throttle_us: float = 50_000.0
    duplicate_order_protection: bool = True
    client_order_id_tracking: bool = True
    kill_switch_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityAssumptions:
    opportunity_decay_us: float = 1_000.0
    competitor_tick_to_send_us: float | None = None
    arbitration_latency_us: float = 0.0
    defensive_activation_latency_us: float = 0.0
    hybrid_coordination_latency_us: float = 0.0
    queue_position_penalty_us: float = 0.0
    pending_exposure: PendingExposureConfig = field(default_factory=PendingExposureConfig)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["pending_exposure"] = self.pending_exposure.to_dict()
        return payload


class LatencyCapabilityError(ValueError):
    """Raised when a latency capability input is malformed."""


COMPONENT_METRIC_FIELDS = (
    "feed_latency_us",
    "new_send_to_exchange_us",
    "new_exchange_to_ack_us",
    "cancel_send_to_exchange_us",
    "cancel_exchange_to_ack_us",
)


def _hftbacktest_component_view(
    internal: dict[str, float | None],
    external: dict[str, float | None],
) -> dict[str, Any]:
    tick = internal.get("tick_to_send_us")
    cancel_fire = internal.get("cancel_to_send_us")
    cancel_ack = external.get("cancel_to_ack_us")
    send_ack = external.get("send_to_ack_us")
    feed = internal.get("feed_latency_us") if "feed_latency_us" in internal else None
    return {
        "feed_latency_us": feed,
        "order_entry_local_us": tick,
        "order_response_round_trip_us": send_ack,
        "cancel_decision_to_send_us": cancel_fire,
        "cancel_exchange_to_ack_us": cancel_ack,
        "cancel_effective_time_us": None if feed is None or cancel_fire is None else feed + cancel_fire,
        "cancel_confirmed_time_us": None
        if feed is None or cancel_fire is None or cancel_ack is None
        else feed + cancel_fire + cancel_ack,
        "measurement_status": {
            name: ("MEASURED" if internal.get(name) is not None or external.get(name) is not None else "OPEN")
            for name in COMPONENT_METRIC_FIELDS
        },
        "note": "Exchange-segmented bands populated by probe v3 CC campaigns.",
    }


def classify_internal_speed(tick_to_send_us: float | None) -> str:
    if tick_to_send_us is None:
        return "unknown"
    if tick_to_send_us < 100.0:
        return "microsecond_loop"
    if tick_to_send_us < 1_000.0:
        return "sub_millisecond_loop"
    return "millisecond_loop"


def classify_ack_lag(value_us: float | None) -> str:
    if value_us is None:
        return "unknown"
    if value_us < 1_000.0:
        return "sub_millisecond_ack"
    if value_us < 10_000.0:
        return "single_digit_millisecond_ack"
    if value_us < 100_000.0:
        return "tens_of_milliseconds_ack"
    return "hundreds_of_milliseconds_or_slower_ack"


def local_state_after_send(action: str) -> LocalOrderState:
    normalized = str(action).lower()
    if normalized in {"new", "order", "send"}:
        return LocalOrderState.PENDING_NEW
    if normalized == "cancel":
        return LocalOrderState.PENDING_CANCEL
    if normalized == "replace":
        return LocalOrderState.PENDING_REPLACE
    raise LatencyCapabilityError(f"UNKNOWN_ORDER_ACTION: {action}")


def state_after_external_message(previous: LocalOrderState, message: str) -> LocalOrderState:
    normalized = str(message).lower()
    if previous == LocalOrderState.PENDING_NEW:
        if normalized in {"ack", "acknowledged"}:
            return LocalOrderState.ACKED
        if normalized == "working":
            return LocalOrderState.WORKING
        if normalized in {"reject", "rejected"}:
            return LocalOrderState.REJECTED
    if previous == LocalOrderState.WORKING:
        if normalized in {"partial_fill", "partially_filled"}:
            return LocalOrderState.PARTIALLY_FILLED
        if normalized == "fill":
            return LocalOrderState.FILLED
    if previous == LocalOrderState.PENDING_CANCEL:
        if normalized in {"cancel", "canceled", "cancelled"}:
            return LocalOrderState.CANCELED
        if normalized in {"cancel_reject", "cancel_rejected"}:
            return LocalOrderState.CANCEL_REJECTED
    if previous == LocalOrderState.PENDING_REPLACE:
        if normalized in {"replace", "replaced"}:
            return LocalOrderState.REPLACED
        if normalized in {"replace_reject", "replace_rejected"}:
            return LocalOrderState.REPLACE_REJECTED
    raise LatencyCapabilityError(f"INVALID_EXTERNAL_STATE_TRANSITION: {previous.value}->{message}")


def build_capability_report(
    latency_summary: dict[str, Any],
    *,
    mode: ModelInteractionMode | str = ModelInteractionMode.OFFENSIVE_ONLY,
    assumptions: CapabilityAssumptions | None = None,
) -> dict[str, Any]:
    """Convert a latency baseline summary into operational capability."""

    if not isinstance(latency_summary, dict):
        raise LatencyCapabilityError("LATENCY_SUMMARY_OBJECT_REQUIRED")
    assumptions = assumptions or CapabilityAssumptions()
    interaction_mode = _coerce_mode(mode)
    metrics = _metrics_from_summary(latency_summary)
    internal = {field: _extract_stat(metrics, field) for field in INTERNAL_LATENCY_FIELDS}
    external = {field: _extract_stat(metrics, field) for field in EXTERNAL_CONFIRMATION_FIELDS}
    tick_to_send = internal["tick_to_send_us"]
    trigger_tick_to_send = internal["tick_to_send_trigger_us"]
    effective_trigger = trigger_tick_to_send if trigger_tick_to_send is not None else tick_to_send
    operating_band = classify_internal_speed(effective_trigger)
    sdk_return_band = classify_internal_speed(tick_to_send)
    ack_lag = classify_ack_lag(external["send_to_ack_us"])
    hybrid = _hybrid_capability(interaction_mode, internal, external, assumptions)
    risk = _risk_control_report(internal, external, assumptions)
    blockers = _capability_blockers(internal, risk)
    evidence_status = "blocked" if blockers else "observed"
    feasibility = _feasibility_statement(
        operating_band=operating_band,
        ack_lag=ack_lag,
        internal=internal,
        external=external,
        hybrid=hybrid,
        risk=risk,
        assumptions=assumptions,
    )
    return {
        "schema_version": "latency_capability_report_v1",
        "run_id": str(latency_summary.get("run_id") or ""),
        "latency_baseline_schema_version": str(latency_summary.get("schema_version") or ""),
        "evidence_status": evidence_status,
        "blocking_reasons": blockers,
        "model_interaction_mode": interaction_mode.value,
        "assumptions": assumptions.to_dict(),
        "hftbacktest_components": _hftbacktest_component_view(internal, external),
        "internal_operating_speed": internal,
        "external_confirmation_speed": external,
        "offensive_capability": {
            "tick_to_decision_us": internal["tick_to_decision_us"],
            "decision_to_send_trigger_us": internal["decision_to_send_trigger_us"],
            "tick_to_send_trigger_us": trigger_tick_to_send,
            "decision_to_send_us": internal["decision_to_send_us"],
            "tick_to_send_us": tick_to_send,
            "rithmic_send_call_us": internal["rithmic_send_call_us"],
            "placement_trigger_kpi": "tick_to_send_trigger_us" if trigger_tick_to_send is not None else "tick_to_send_us",
            "operating_band": operating_band,
            "sdk_return_operating_band": sdk_return_band,
            "opportunity_window_compatible": _lte(tick_to_send, assumptions.opportunity_decay_us),
            "trigger_opportunity_window_compatible": _lte(effective_trigger, assumptions.opportunity_decay_us),
            "sdk_return_opportunity_window_compatible": _lte(tick_to_send, assumptions.opportunity_decay_us),
            "competitor_relation": _competitor_relation(tick_to_send, assumptions.competitor_tick_to_send_us),
            "trigger_competitor_relation": _competitor_relation(effective_trigger, assumptions.competitor_tick_to_send_us),
        },
        "defensive_capability": {
            "cancel_to_send_us": internal["cancel_to_send_us"],
            "replace_to_send_us": internal["replace_to_send_us"],
            "cancel_to_ack_us": external["cancel_to_ack_us"],
            "replace_to_ack_us": external["replace_to_ack_us"],
            "stale_state_risk": risk["stale_state_risk"],
        },
        "hybrid_configuration_capability": hybrid,
        "external_confirmation_behavior": {
            "send_to_ack_us": external["send_to_ack_us"],
            "cancel_to_ack_us": external["cancel_to_ack_us"],
            "replace_to_ack_us": external["replace_to_ack_us"],
            "fill_received_latency_us": external["fill_received_latency_us"],
            "acknowledgment_lag_classification": ack_lag,
        },
        "pending_state_model": {
            "nonblocking_ack_default": True,
            "send_transitions": {
                "new": LocalOrderState.PENDING_NEW.value,
                "cancel": LocalOrderState.PENDING_CANCEL.value,
                "replace": LocalOrderState.PENDING_REPLACE.value,
            },
            "ack_reconciliation": {
                "PENDING_NEW": ["ACKED", "WORKING", "REJECTED"],
                "WORKING": ["PARTIALLY_FILLED", "FILLED"],
                "PENDING_CANCEL": ["CANCELED", "CANCEL_REJECTED"],
                "PENDING_REPLACE": ["REPLACED", "REPLACE_REJECTED"],
            },
        },
        "risk_controls": risk,
        "feasibility_statement": feasibility,
        "blocking_behavior": {
            "blocks_on_ack": False,
            "ack_required_for_reconciliation": True,
            "note": "Acknowledgments reconcile official state but do not block the next decision cycle unless a test mode explicitly requires blocking.",
        },
    }


def write_capability_reports(report: dict[str, Any], *, reports_root: Path | str) -> tuple[Path, Path]:
    root = Path(reports_root)
    root.mkdir(parents=True, exist_ok=True)
    run_id = str(report.get("run_id") or "unknown")
    json_path = root / f"{run_id}_capability.json"
    md_path = root / f"{run_id}_capability.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    md_path.write_text(render_capability_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_capability_markdown(report: dict[str, Any]) -> str:
    offensive = report.get("offensive_capability") or {}
    defensive = report.get("defensive_capability") or {}
    hybrid = report.get("hybrid_configuration_capability") or {}
    external = report.get("external_confirmation_behavior") or {}
    risk = report.get("risk_controls") or {}
    lines = [
        "# Latency Capability Report",
        "",
        f"Run ID: `{report.get('run_id', '')}`",
        f"Evidence status: `{report.get('evidence_status', 'unknown')}`",
        f"Interaction mode: `{report.get('model_interaction_mode', '')}`",
        "",
        "## Offensive Capability",
        f"- Operating band: `{offensive.get('operating_band', 'unknown')}`",
        f"- Tick-to-decision: `{_fmt_us(offensive.get('tick_to_decision_us'))}`",
        f"- Decision-to-trigger: `{_fmt_us(offensive.get('decision_to_send_trigger_us'))}`",
        f"- Tick-to-trigger: `{_fmt_us(offensive.get('tick_to_send_trigger_us'))}`",
        f"- Decision-to-send: `{_fmt_us(offensive.get('decision_to_send_us'))}`",
        f"- Tick-to-SDK-return: `{_fmt_us(offensive.get('tick_to_send_us'))}`",
        f"- Rithmic send call: `{_fmt_us(offensive.get('rithmic_send_call_us'))}`",
        f"- Opportunity window compatible: `{offensive.get('opportunity_window_compatible')}`",
        f"- Competitor relation: `{offensive.get('competitor_relation', 'unknown')}`",
        "",
        "## Defensive Capability",
        f"- Cancel-to-send: `{_fmt_us(defensive.get('cancel_to_send_us'))}`",
        f"- Replace-to-send: `{_fmt_us(defensive.get('replace_to_send_us'))}`",
        f"- Cancel-to-ack: `{_fmt_us(defensive.get('cancel_to_ack_us'))}`",
        f"- Replace-to-ack: `{_fmt_us(defensive.get('replace_to_ack_us'))}`",
        f"- Stale-state risk: `{defensive.get('stale_state_risk', 'unknown')}`",
        "",
        "## Hybrid Configuration Capability",
        f"- Arbitration/sequencing latency: `{_fmt_us(hybrid.get('arbitration_sequencing_latency_us'))}`",
        f"- Total decision-to-action latency: `{_fmt_us(hybrid.get('total_decision_to_action_latency_us'))}`",
        f"- Pending exposure behavior: `{hybrid.get('pending_exposure_behavior', 'unknown')}`",
        f"- Outcome quality effect: `{hybrid.get('outcome_quality_effect', 'unknown')}`",
        "",
        "## External Confirmation",
        f"- Send-to-ack: `{_fmt_us(external.get('send_to_ack_us'))}`",
        f"- Cancel-to-ack: `{_fmt_us(external.get('cancel_to_ack_us'))}`",
        f"- Replace-to-ack: `{_fmt_us(external.get('replace_to_ack_us'))}`",
        f"- Ack lag class: `{external.get('acknowledgment_lag_classification', 'unknown')}`",
        "",
        "## HftBacktest Components",
        f"- Feed latency: `{_fmt_us((report.get('hftbacktest_components') or {}).get('feed_latency_us'))}`",
        f"- Cancel effective (est): `{_fmt_us((report.get('hftbacktest_components') or {}).get('cancel_effective_time_us'))}`",
        f"- Cancel confirmed (est): `{_fmt_us((report.get('hftbacktest_components') or {}).get('cancel_confirmed_time_us'))}`",
        "",
        "## Risk Controls",
        f"- Status: `{risk.get('status', 'unknown')}`",
        f"- Pending orders within limit: `{risk.get('pending_orders_within_limit')}`",
        f"- Pending quantity within limit: `{risk.get('pending_quantity_within_limit')}`",
        f"- Duplicate protection: `{risk.get('duplicate_order_protection')}`",
        f"- Client order ID tracking: `{risk.get('client_order_id_tracking')}`",
        f"- Kill switch required: `{risk.get('kill_switch_required')}`",
        "",
        "## Feasibility",
        str(report.get("feasibility_statement") or ""),
        "",
    ]
    blockers = report.get("blocking_reasons") or []
    if blockers:
        lines.extend(["## Blocking Reasons", ""])
        lines.extend(f"- `{reason}`" for reason in blockers)
        lines.append("")
    return "\n".join(lines)


def _metrics_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        raise LatencyCapabilityError("LATENCY_SUMMARY_METRICS_REQUIRED")
    return metrics


def _extract_stat(metrics: dict[str, Any], field: str, stat: str = "p99_9_us") -> float | None:
    value = metrics.get(field)
    if not isinstance(value, dict):
        return None
    selected = value.get(stat)
    if selected is None:
        selected = value.get("p99_us")
    if selected is None:
        selected = value.get("p50_us")
    if selected is None:
        return None
    number = float(selected)
    if not math.isfinite(number):
        raise LatencyCapabilityError(f"NON_FINITE_LATENCY_METRIC: {field}.{stat}")
    return number


def _coerce_mode(mode: ModelInteractionMode | str) -> ModelInteractionMode:
    if isinstance(mode, ModelInteractionMode):
        return mode
    try:
        return ModelInteractionMode(str(mode))
    except ValueError as exc:
        raise LatencyCapabilityError(f"UNKNOWN_MODEL_INTERACTION_MODE: {mode}") from exc


def _hybrid_capability(
    mode: ModelInteractionMode,
    internal: dict[str, float | None],
    external: dict[str, float | None],
    assumptions: CapabilityAssumptions,
) -> dict[str, Any]:
    decision_to_send = internal.get("decision_to_send_us")
    trigger_base = internal.get("decision_to_send_trigger_us")
    base = 0.0 if decision_to_send is None and trigger_base is None else float(decision_to_send if decision_to_send is not None else trigger_base)
    arbitration = _mode_arbitration_latency(mode, assumptions)
    total = base + arbitration + assumptions.queue_position_penalty_us
    send_to_ack = external.get("send_to_ack_us")
    stale_ack = send_to_ack is not None and send_to_ack > assumptions.pending_exposure.stale_pending_timeout_us
    return {
        "selected_model_interaction_mode": mode.value,
        "arbitration_sequencing_latency_us": arbitration,
        "total_decision_to_trigger_latency_us": (
            None if trigger_base is None else trigger_base + arbitration + assumptions.queue_position_penalty_us
        ),
        "total_decision_to_action_latency_us": total,
        "pending_exposure_behavior": "continues_processing_with_pending_state",
        "ack_delay_creates_stale_state_risk": stale_ack,
        "outcome_quality_effect": _outcome_effect(total, assumptions.opportunity_decay_us),
        "requires_ack_blocking": False,
    }


def _mode_arbitration_latency(mode: ModelInteractionMode, assumptions: CapabilityAssumptions) -> float:
    if mode == ModelInteractionMode.OFFENSIVE_ONLY:
        return 0.0
    if mode == ModelInteractionMode.DEFENSIVE_PRE_ACTION_ONLY:
        return assumptions.defensive_activation_latency_us
    if mode == ModelInteractionMode.DEFENSIVE_ALWAYS_ACTIVE:
        return assumptions.defensive_activation_latency_us
    if mode == ModelInteractionMode.DEFENSIVE_DURING_ACTION:
        return assumptions.defensive_activation_latency_us
    if mode == ModelInteractionMode.DEFENSIVE_POST_ACTION:
        return 0.0
    if mode == ModelInteractionMode.CONCURRENT_OFFENSIVE_DEFENSIVE:
        return assumptions.arbitration_latency_us
    return assumptions.hybrid_coordination_latency_us + assumptions.arbitration_latency_us


def _risk_control_report(
    internal: dict[str, float | None],
    external: dict[str, float | None],
    assumptions: CapabilityAssumptions,
) -> dict[str, Any]:
    pending = assumptions.pending_exposure
    send_to_ack = external.get("send_to_ack_us")
    stale_state_risk = "unknown"
    if send_to_ack is not None:
        stale_state_risk = "high" if send_to_ack > pending.stale_pending_timeout_us else "managed"
    blocking_reasons: list[str] = []
    if pending.max_pending_orders < 1:
        blocking_reasons.append("MAX_PENDING_ORDERS_LT_ONE")
    if pending.max_pending_quantity <= 0:
        blocking_reasons.append("MAX_PENDING_QUANTITY_NOT_POSITIVE")
    if pending.max_pending_notional < 0:
        blocking_reasons.append("MAX_PENDING_NOTIONAL_NEGATIVE")
    if pending.stale_pending_timeout_us <= 0:
        blocking_reasons.append("STALE_PENDING_TIMEOUT_NOT_POSITIVE")
    if pending.cancel_replace_throttle_us < 0:
        blocking_reasons.append("CANCEL_REPLACE_THROTTLE_NEGATIVE")
    if not pending.duplicate_order_protection:
        blocking_reasons.append("DUPLICATE_ORDER_PROTECTION_DISABLED")
    if not pending.client_order_id_tracking:
        blocking_reasons.append("CLIENT_ORDER_ID_TRACKING_DISABLED")
    if not pending.kill_switch_required:
        blocking_reasons.append("KILL_SWITCH_NOT_REQUIRED")
    return {
        "status": "blocked" if blocking_reasons else "configured",
        "blocking_reasons": blocking_reasons,
        "max_pending_orders": pending.max_pending_orders,
        "max_pending_quantity": pending.max_pending_quantity,
        "max_pending_notional": pending.max_pending_notional,
        "stale_pending_timeout_us": pending.stale_pending_timeout_us,
        "cancel_replace_throttle_us": pending.cancel_replace_throttle_us,
        "duplicate_order_protection": pending.duplicate_order_protection,
        "client_order_id_tracking": pending.client_order_id_tracking,
        "kill_switch_required": pending.kill_switch_required,
        "pending_orders_within_limit": pending.max_pending_orders >= 1,
        "pending_quantity_within_limit": pending.max_pending_quantity >= 1,
        "stale_state_risk": stale_state_risk,
        "cancel_replace_throttle_compatible": _lte(
            max(_none_to_zero(internal.get("cancel_to_send_us")), _none_to_zero(internal.get("replace_to_send_us"))),
            pending.cancel_replace_throttle_us,
        ),
        "required_controls": [
            "max_pending_orders",
            "max_pending_quantity",
            "max_pending_notional",
            "duplicate_order_protection",
            "client_order_id_tracking",
            "stale_pending_order_timeout",
            "cancel_replace_throttles",
            "reject_handling",
            "state_reconciliation",
            "kill_switch",
        ],
    }


def _capability_blockers(internal: dict[str, float | None], risk: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if internal.get("tick_to_send_us") is None and internal.get("tick_to_send_trigger_us") is None:
        blockers.append("TICK_TO_SEND_MISSING")
    if internal.get("tick_to_decision_us") is None:
        blockers.append("TICK_TO_DECISION_MISSING")
    if internal.get("decision_to_send_us") is None and internal.get("decision_to_send_trigger_us") is None:
        blockers.append("DECISION_TO_SEND_MISSING")
    blockers.extend(str(reason) for reason in risk.get("blocking_reasons", []))
    return blockers


def _feasibility_statement(
    *,
    operating_band: str,
    ack_lag: str,
    internal: dict[str, float | None],
    external: dict[str, float | None],
    hybrid: dict[str, Any],
    risk: dict[str, Any],
    assumptions: CapabilityAssumptions,
) -> str:
    tick = internal.get("tick_to_send_us")
    trigger = internal.get("tick_to_send_trigger_us")
    effective_trigger = trigger if trigger is not None else tick
    total = hybrid.get("total_decision_to_action_latency_us")
    window_ok = _lte(tick, assumptions.opportunity_decay_us)
    stale = risk.get("stale_state_risk")
    parts = [
        f"The system is operating in the {operating_band.replace('_', ' ')} band for placement speed.",
    ]
    if tick is not None:
        parts.append(f"Measured tick-to-SDK-return is {tick:.1f} us against a {assumptions.opportunity_decay_us:.1f} us opportunity decay assumption.")
    if trigger is not None:
        parts.append(f"Measured tick-to-trigger is {trigger:.1f} us; this is call-entry, not broker acknowledgment.")
    parts.append("Offensive behavior is feasible inside the configured opportunity window." if window_ok else "Offensive behavior is too slow for the configured opportunity window.")
    if total is not None:
        parts.append(f"The selected interaction mode has {float(total):.1f} us total decision-to-action latency.")
    parts.append(f"External confirmation is classified as {ack_lag.replace('_', ' ')} and must be handled asynchronously.")
    if stale == "high":
        parts.append("Acknowledgment delay creates high stale-state risk; pending exposure limits and reconciliation are mandatory bottlenecks.")
    elif stale == "managed":
        parts.append("Acknowledgment delay is within the configured stale-state timeout, but pending state still must be reconciled.")
    else:
        parts.append("Acknowledgment risk is unknown because confirmation latency is missing.")
    competitor = _competitor_relation(effective_trigger, assumptions.competitor_tick_to_send_us)
    if competitor != "unknown":
        parts.append(f"Against the assumed competitor speed, our placement speed is {competitor.replace('_', ' ')}.")
    return " ".join(parts)


def _competitor_relation(ours: float | None, competitor: float | None) -> str:
    if ours is None or competitor is None:
        return "unknown"
    if ours < competitor:
        return "faster_than_assumed_competitor"
    if ours > competitor:
        return "slower_than_assumed_competitor"
    return "same_as_assumed_competitor"


def _outcome_effect(total_latency_us: float, opportunity_decay_us: float) -> str:
    if total_latency_us <= opportunity_decay_us:
        return "timing_feasible"
    return "timing_degraded"


def _lte(lhs: float | None, rhs: float | None) -> bool | None:
    if lhs is None or rhs is None:
        return None
    return lhs <= rhs


def _none_to_zero(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def _fmt_us(value: Any) -> str:
    if value is None:
        return "missing"
    return f"{float(value):.3f} us"
