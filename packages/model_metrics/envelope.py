"""Generate runtime operating envelopes from scorecards and observed evidence."""

from __future__ import annotations

from statistics import median
from typing import Any

from model_metrics.config import DEFAULT_CONFIG, thresholds_for
from model_metrics.schemas import CALCULATION_VERSION, ModelBehaviorEnvelope, ModelScorecard, finite_or_none, stable_id


def _numbers(values: list[Any]) -> list[float]:
    out: list[float] = []
    for value in values:
        parsed = finite_or_none(value)
        if parsed is not None:
            out.append(parsed)
    return out


def _q(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * pct
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return float(ordered[lo])
    frac = idx - lo
    return float(ordered[lo] * (1 - frac) + ordered[hi] * frac)


def _range(values: list[float]) -> tuple[float | None, float | None]:
    return (_q(values, 0.05), _q(values, 0.95)) if values else (None, None)


def _metric(scorecard: ModelScorecard, name: str) -> float | None:
    for metric in scorecard.metrics:
        if metric.metric_name == name and metric.status == "ok":
            return metric.metric_value
    return None


def _latency_metric_bounds(value: Any) -> tuple[float | None, float | None]:
    parsed = finite_or_none(value)
    return (0.0, parsed) if parsed is not None else (None, None)


def _nested(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _latency_operating_bounds(envelope: dict[str, Any], legacy_ack_ms: float | None, max_latency_ms: float) -> dict[str, tuple[float | None, float | None]]:
    placement = _nested(envelope, "offensive", "placement") or {}
    defensive = _nested(envelope, "defensive", "placement") or {}
    confirmation = _nested(envelope, "external_confirmation", "confirmation") or {}
    defensive_confirmation = _nested(envelope, "defensive", "confirmation") or {}
    return {
        "latency_order_to_ack": (0.0, legacy_ack_ms if legacy_ack_ms is not None else max_latency_ms),
        "tick_to_send_trigger_us": _latency_metric_bounds(_nested(placement, "tick_to_send_trigger_us", "p99_9") or _nested(placement, "tick_to_send_trigger_us", "p99")),
        "decision_to_send_trigger_us": _latency_metric_bounds(_nested(placement, "decision_to_send_trigger_us", "p99_9") or _nested(placement, "decision_to_send_trigger_us", "p99")),
        "tick_to_send_us": _latency_metric_bounds(_nested(placement, "tick_to_send_us", "p99_9") or _nested(placement, "tick_to_send_us", "p99")),
        "decision_to_send_us": _latency_metric_bounds(_nested(placement, "decision_to_send_us", "p99_9") or _nested(placement, "decision_to_send_us", "p99")),
        "rithmic_send_call_us": _latency_metric_bounds(_nested(placement, "rithmic_send_call_us", "p99_9") or _nested(placement, "rithmic_send_call_us", "p99")),
        "cancel_to_send_us": _latency_metric_bounds(_nested(defensive, "cancel_to_send_us", "p99_9") or _nested(defensive, "cancel_to_send_us", "p99")),
        "replace_to_send_us": _latency_metric_bounds(_nested(defensive, "replace_to_send_us", "p99_9") or _nested(defensive, "replace_to_send_us", "p99")),
        "send_to_ack_us": _latency_metric_bounds(_nested(confirmation, "send_to_ack_us", "p99_9") or _nested(confirmation, "send_to_ack_us", "p99")),
        "cancel_to_ack_us": _latency_metric_bounds(_nested(defensive_confirmation, "cancel_to_ack_us", "p99_9") or _nested(defensive_confirmation, "cancel_to_ack_us", "p99")),
        "replace_to_ack_us": _latency_metric_bounds(_nested(defensive_confirmation, "replace_to_ack_us", "p99_9") or _nested(defensive_confirmation, "replace_to_ack_us", "p99")),
    }


def _first_compatible_window(envelope: dict[str, Any]) -> float | None:
    compat = _nested(envelope, "offensive", "opportunity_window_compatible")
    if isinstance(compat, dict):
        for key, value in sorted(compat.items(), key=lambda item: float(item[0])):
            if value is True:
                return float(key)
    windows = _nested(envelope, "competitor_speed_sensitivity", "opportunity_decay_windows_us")
    if isinstance(windows, list) and windows:
        return finite_or_none(windows[-1])
    return None


def generate_behavior_envelope(
    inputs: dict[str, Any],
    scorecard: ModelScorecard,
    *,
    config: dict[str, Any] | None = None,
) -> ModelBehaviorEnvelope:
    """Create a versioned rule envelope for any promoted or candidate model."""

    cfg = config or DEFAULT_CONFIG
    meta = inputs.get("context") or inputs
    threshold = thresholds_for(
        cfg,
        asset_class=str(meta.get("asset_class", "")),
        symbol=str(meta.get("symbol", meta.get("contract", meta.get("market_id", "")))),
        strategy_family=str(meta.get("strategy_family", "")),
        regime_id=str(meta.get("regime_id", "")),
        venue=str(meta.get("venue", "")),
        runtime_context=str(meta.get("runtime_context", "")),
    )
    returns = _numbers(inputs.get("returns") or [])
    trades = [row for row in inputs.get("trades", []) if isinstance(row, dict)]
    if not returns and trades:
        returns = _numbers([trade.get("realized_pnl", trade.get("net_pnl", trade.get("pnl"))) for trade in trades])
    slippage = _numbers([trade.get("slippage_bps") for trade in trades])
    spreads = _numbers([trade.get("spread_bps") for trade in trades])
    holding = _numbers([trade.get("holding_period_min") for trade in trades])
    feature_ranges = {
        str(name): tuple(bounds)
        for name, bounds in (inputs.get("feature_training_domain_bounds") or inputs.get("expected_feature_ranges") or {}).items()
    }
    max_dd = abs(_metric(scorecard, "max_drawdown") or float(threshold["maximum_drawdown"]))
    latency = _metric(scorecard, "latency_order_to_ack")
    alpha_half_life = _metric(scorecard, "alpha_half_life")
    latency_envelope = inputs.get("latency_operating_envelope") or {}
    if not isinstance(latency_envelope, dict):
        latency_envelope = {}
    execution_audit = latency_envelope.get("execution_path_audit") if isinstance(latency_envelope.get("execution_path_audit"), dict) else {}
    pending_state = latency_envelope.get("pending_state_risk") if isinstance(latency_envelope.get("pending_state_risk"), dict) else {}
    warning = max_dd * float(threshold.get("warning_drawdown_multiplier", 1.0))
    kill = max_dd * float(threshold.get("kill_drawdown_multiplier", 1.5))
    payload = {
        "model_id": scorecard.model_id,
        "model_version": scorecard.model_version,
        "robustness_run_id": scorecard.robustness_run_id,
        "grade": scorecard.grade,
        "weighted_score": scorecard.weighted_score,
    }
    warnings = []
    if scorecard.grade in {"D", "F"}:
        warnings.append("scorecard grade is not eligible for active operation")
    if not returns:
        warnings.append("return samples missing; PnL envelope ranges unavailable")
    audit_status = str(execution_audit.get("status") or "missing").lower()
    if audit_status in {"missing", "fail", "failed", "blocked"}:
        warnings.append("low-latency execution-path audit is missing, failed, or blocked")
    return ModelBehaviorEnvelope(
        envelope_id=stable_id("envelope", payload),
        model_id=scorecard.model_id,
        model_version=scorecard.model_version,
        robustness_run_id=scorecard.robustness_run_id,
        calculation_version=CALCULATION_VERSION,
        created_at=scorecard.created_at,
        approved_by_system_rule=scorecard.grade in {"A", "B"},
        active=scorecard.grade in {"A", "B"},
        expected_daily_pnl_range=_range(returns),
        expected_weekly_pnl_range=_range([sum(returns[i : i + 5]) for i in range(0, max(0, len(returns) - 4))]),
        expected_intraday_drawdown_range=(_metric(scorecard, "max_drawdown"), 0.0),
        warning_drawdown_threshold=warning,
        kill_drawdown_threshold=kill,
        expected_drawdown_velocity=(_metric(scorecard, "drawdown_velocity"), 0.0),
        max_allowed_drawdown_velocity=float(threshold["maximum_drawdown_velocity"]),
        expected_trade_count_per_session=(float(len(trades)), float(len(trades))) if trades else (None, None),
        min_trade_count_per_session=0.0,
        max_trade_count_per_session=float(max(len(trades) * 2, 1)) if trades else None,
        expected_holding_period_min=min(holding) if holding else None,
        expected_holding_period_median=median(holding) if holding else None,
        expected_holding_period_max=max(holding) if holding else None,
        expected_win_rate_range=(_metric(scorecard, "hit_rate"), _metric(scorecard, "hit_rate")),
        expected_payoff_ratio_range=(_metric(scorecard, "average_win_loss_ratio"), _metric(scorecard, "average_win_loss_ratio")),
        expected_expectancy_range=(_metric(scorecard, "expectancy_per_trade"), _metric(scorecard, "expectancy_per_trade")),
        expected_slippage_range=_range(slippage),
        max_allowed_slippage=float(threshold["maximum_slippage_bps"]),
        expected_spread_range=_range(spreads),
        max_allowed_spread=finite_or_none(threshold.get("maximum_spread_bps")),
        expected_fill_rate_range=(_metric(scorecard, "fill_rate"), 1.0),
        min_allowed_fill_rate=float(threshold["minimum_fill_rate"]),
        expected_signal_score_distribution=inputs.get("expected_signal_score_distribution") or {},
        abnormal_signal_score_thresholds=inputs.get("abnormal_signal_score_thresholds") or {},
        expected_model_confidence_distribution=inputs.get("expected_model_confidence_distribution") or {},
        expected_feature_ranges=feature_ranges,
        feature_training_domain_bounds=feature_ranges,
        approved_regime_ids=tuple(str(v) for v in inputs.get("approved_regime_ids", ()) if str(v)),
        blocked_regime_ids=tuple(str(v) for v in inputs.get("blocked_regime_ids", ()) if str(v)),
        expected_correlation_range_to_book=(-float(threshold["maximum_correlation_to_book"]), float(threshold["maximum_correlation_to_book"])),
        max_allowed_correlation_to_book=float(threshold["maximum_correlation_to_book"]),
        max_allowed_position_size=finite_or_none(threshold.get("max_allowed_position_size")),
        max_allowed_gross_exposure=finite_or_none(threshold.get("max_allowed_gross_exposure")),
        max_allowed_net_exposure=finite_or_none(threshold.get("max_allowed_net_exposure")),
        asset_class_specific_limits=(cfg.get("asset_class_overrides") or {}).get(str(scorecard.asset_class), {}),
        venue_specific_limits=(cfg.get("venue_overrides") or {}).get(str(meta.get("venue", "")), {}),
        symbol_specific_limits=(cfg.get("symbol_overrides") or {}).get(str(scorecard.symbol), {}),
        latency_bounds=_latency_operating_bounds(latency_envelope, latency, float(threshold["maximum_latency_ms"])),
        operating_band=str(_nested(latency_envelope, "offensive", "operating_band") or ""),
        async_state_model_required=bool(_nested(latency_envelope, "external_confirmation", "modeled_as_async_state_confirmation")),
        max_pending_orders=int(pending_state["max_pending_orders"]) if pending_state.get("max_pending_orders") is not None else None,
        max_pending_quantity=finite_or_none(pending_state.get("max_pending_quantity")),
        max_pending_notional=finite_or_none(pending_state.get("max_pending_notional")),
        stale_pending_timeout_policy={
            "stale_pending_timeout_us": pending_state.get("stale_pending_timeout_us"),
            "stale_state_risk": pending_state.get("stale_state_risk"),
        },
        competitor_speed_assumption_used=latency_envelope.get("competitor_speed_sensitivity") or {},
        opportunity_decay_window_used=_first_compatible_window(latency_envelope),
        low_latency_execution_path_status=execution_audit,
        alpha_half_life_bounds=(alpha_half_life, alpha_half_life),
        data_freshness_requirements={"max_age_ns": int(threshold["data_freshness_max_ns"])},
        warnings=tuple(warnings),
    )
