"""Rule-based model GREEN/YELLOW/RED behavior state engine."""

from __future__ import annotations

from typing import Any

from model_metrics.schemas import CALCULATION_VERSION, ModelBehaviorEnvelope, ModelLiveObservation, ModelStateDecision, finite_or_none


STATE_ACTIONS = {
    "GREEN": "allow normal operation",
    "YELLOW": "reduce size, raise signal threshold, reduce frequency, increase monitoring",
    "RED": "pause new trades; flatten or reduce according to risk policy; create incident report",
}


def _trigger(name: str, severity: str, observed: Any, expected: Any, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "severity": severity,
        "observed": observed,
        "expected": expected,
        "reason": reason,
    }


def _above(value: float | None, threshold: float | None) -> bool:
    return value is not None and threshold is not None and value > threshold


def _below(value: float | None, threshold: float | None) -> bool:
    return value is not None and threshold is not None and value < threshold


def _loss_magnitude_above(value: float | None, threshold: float | None) -> bool:
    return value is not None and threshold is not None and abs(value) > abs(threshold)


def classify_model_state(
    envelope: ModelBehaviorEnvelope | dict[str, Any],
    observation: ModelLiveObservation | dict[str, Any],
) -> ModelStateDecision:
    """Classify live/paper/shadow behavior using only replayable rules."""

    env = envelope if isinstance(envelope, ModelBehaviorEnvelope) else _envelope_from_dict(envelope)
    obs = observation if isinstance(observation, ModelLiveObservation) else ModelLiveObservation.from_dict(observation)
    triggers: list[dict[str, Any]] = []

    if not env.active:
        triggers.append(_trigger("envelope_inactive", "RED", env.active, True, "behavior envelope is not active"))
    audit_status = env.low_latency_execution_path_status if isinstance(env.low_latency_execution_path_status, dict) else {}
    audit_state = str(audit_status.get("status") or "missing").lower()
    if audit_state in {"missing", "fail", "failed", "blocked"}:
        triggers.append(
            _trigger(
                "low_latency_execution_path_audit",
                "RED",
                audit_state,
                "pass",
                "low-latency execution-path audit is missing, failed, or blocked",
            )
        )
    if obs.regime_id and obs.regime_id in env.blocked_regime_ids:
        triggers.append(_trigger("blocked_regime", "RED", obs.regime_id, env.blocked_regime_ids, "model is in a blocked regime"))
    if env.approved_regime_ids and obs.regime_id and obs.regime_id not in env.approved_regime_ids:
        triggers.append(_trigger("unapproved_regime", "YELLOW", obs.regime_id, env.approved_regime_ids, "regime is not explicitly approved"))

    if _loss_magnitude_above(obs.drawdown, env.kill_drawdown_threshold):
        triggers.append(_trigger("drawdown_kill_threshold", "RED", obs.drawdown, env.kill_drawdown_threshold, "drawdown breached kill threshold"))
    elif _loss_magnitude_above(obs.drawdown, env.warning_drawdown_threshold):
        triggers.append(_trigger("drawdown_warning_threshold", "YELLOW", obs.drawdown, env.warning_drawdown_threshold, "drawdown breached warning threshold"))
    if _loss_magnitude_above(obs.drawdown_velocity, env.max_allowed_drawdown_velocity):
        triggers.append(_trigger("drawdown_velocity", "RED", obs.drawdown_velocity, env.max_allowed_drawdown_velocity, "drawdown velocity is outside allowed bounds"))
    if _above(obs.slippage_bps, env.max_allowed_slippage):
        triggers.append(_trigger("slippage", "RED", obs.slippage_bps, env.max_allowed_slippage, "slippage exceeded max allowed slippage"))
    elif _above(obs.slippage_bps, env.expected_slippage_range[1]):
        triggers.append(_trigger("slippage_drift", "YELLOW", obs.slippage_bps, env.expected_slippage_range, "slippage above expected range"))
    if _below(obs.fill_rate, env.min_allowed_fill_rate):
        triggers.append(_trigger("fill_rate", "RED", obs.fill_rate, env.min_allowed_fill_rate, "fill rate below minimum allowed"))
    if _above(obs.spread_bps, env.max_allowed_spread):
        triggers.append(_trigger("spread", "RED", obs.spread_bps, env.max_allowed_spread, "spread exceeded max allowed spread"))
    if _above(obs.correlation_to_book, env.max_allowed_correlation_to_book):
        triggers.append(_trigger("book_correlation", "RED", obs.correlation_to_book, env.max_allowed_correlation_to_book, "correlation to book exceeded limit"))
    if obs.trade_count is not None:
        if env.max_trade_count_per_session is not None and obs.trade_count > env.max_trade_count_per_session:
            triggers.append(_trigger("trade_count_high", "RED", obs.trade_count, env.max_trade_count_per_session, "trade count exploded above expected range"))
        elif env.min_trade_count_per_session is not None and obs.trade_count < env.min_trade_count_per_session:
            triggers.append(_trigger("trade_count_low", "YELLOW", obs.trade_count, env.min_trade_count_per_session, "trade count below expected range"))
    latency_hi = (env.latency_bounds.get("latency_order_to_ack") or (None, None))[1]
    if _above(obs.latency_order_to_ack, obs.alpha_half_life):
        triggers.append(_trigger("latency_exceeds_alpha_half_life", "RED", obs.latency_order_to_ack, obs.alpha_half_life, "execution latency exceeds alpha half-life"))
    elif _above(obs.latency_order_to_ack, latency_hi):
        triggers.append(_trigger("latency_drift", "YELLOW", obs.latency_order_to_ack, latency_hi, "execution latency above expected range"))
    tick_to_send_hi = (env.latency_bounds.get("tick_to_send_us") or (None, None))[1]
    decision_to_send_hi = (env.latency_bounds.get("decision_to_send_us") or (None, None))[1]
    if _above(obs.tick_to_send_us, env.opportunity_decay_window_used):
        triggers.append(_trigger("placement_exceeds_opportunity_window", "RED", obs.tick_to_send_us, env.opportunity_decay_window_used, "tick-to-send exceeded the operating opportunity window"))
    elif _above(obs.tick_to_send_us, tick_to_send_hi):
        triggers.append(_trigger("placement_latency_drift", "YELLOW", obs.tick_to_send_us, tick_to_send_hi, "tick-to-send above expected operating envelope"))
    if _above(obs.decision_to_send_us, decision_to_send_hi):
        triggers.append(_trigger("decision_to_send_latency_drift", "YELLOW", obs.decision_to_send_us, decision_to_send_hi, "decision-to-send above expected operating envelope"))
    stale_timeout_us = finite_or_none(env.stale_pending_timeout_policy.get("stale_pending_timeout_us"))
    if env.async_state_model_required and _above(obs.send_to_ack_us, stale_timeout_us):
        triggers.append(_trigger("async_ack_stale_state", "RED", obs.send_to_ack_us, stale_timeout_us, "send-to-ack exceeded stale pending timeout"))
    max_age = finite_or_none(env.data_freshness_requirements.get("max_age_ns"))
    if obs.data_age_ns is not None and max_age is not None and obs.data_age_ns > max_age:
        triggers.append(_trigger("data_freshness", "RED", obs.data_age_ns, max_age, "data feed is stale"))
    for feature, value in obs.feature_values.items():
        bounds = env.feature_training_domain_bounds.get(feature)
        if not bounds:
            continue
        lo, hi = bounds
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            triggers.append(_trigger("feature_training_domain", "RED", {feature: value}, {feature: bounds}, "feature outside training support"))
    if obs.confidence is not None and obs.realized_accuracy is not None and obs.confidence >= 0.8 and obs.realized_accuracy < 0.5:
        triggers.append(_trigger("high_confidence_wrong", "RED", obs.realized_accuracy, obs.confidence, "high-confidence predictions are systematically wrong"))
    if obs.order_reject_rate is not None and obs.order_reject_rate > 0.05:
        triggers.append(_trigger("order_reject_rate", "RED", obs.order_reject_rate, 0.05, "order reject rate abnormal"))

    state = "RED" if any(t["severity"] == "RED" for t in triggers) else ("YELLOW" if triggers else "GREEN")
    alerts = tuple(
        {
            "model_id": obs.model_id,
            "state": state,
            "trigger": trigger["name"],
            "severity": trigger["severity"],
            "reason": trigger["reason"],
        }
        for trigger in triggers
    )
    return ModelStateDecision(
        state=state,
        action=STATE_ACTIONS[state],
        triggers=tuple(triggers),
        alerts=alerts,
        observation=obs,
        envelope_id=env.envelope_id,
        calculation_version=CALCULATION_VERSION,
    )


def _envelope_from_dict(raw: dict[str, Any]) -> ModelBehaviorEnvelope:
    fields = ModelBehaviorEnvelope.__dataclass_fields__
    values = {name: raw[name] for name in fields if name in raw}
    for name in (
        "expected_daily_pnl_range",
        "expected_weekly_pnl_range",
        "expected_intraday_drawdown_range",
        "expected_drawdown_velocity",
        "expected_trade_count_per_session",
        "expected_win_rate_range",
        "expected_payoff_ratio_range",
        "expected_expectancy_range",
        "expected_slippage_range",
        "expected_spread_range",
        "expected_fill_rate_range",
        "expected_correlation_range_to_book",
        "alpha_half_life_bounds",
    ):
        if name in values:
            values[name] = tuple(values[name])
    for name in ("approved_regime_ids", "blocked_regime_ids", "warnings"):
        if name in values:
            values[name] = tuple(values[name])
    return ModelBehaviorEnvelope(**values)
