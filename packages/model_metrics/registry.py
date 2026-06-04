"""Deterministic metric calculators and registry definitions."""

from __future__ import annotations

import math
from statistics import mean, median, pstdev
from typing import Any, Iterable

from model_metrics.schemas import CALCULATION_VERSION, DEFAULT_CREATED_AT, MetricValue, finite_or_none


METRIC_GROUPS: dict[str, tuple[str, ...]] = {
    "net_alpha_quality": (
        "net_return",
        "gross_return",
        "cagr",
        "annualized_volatility",
        "sharpe",
        "sortino",
        "calmar",
        "profit_factor",
        "expectancy_per_trade",
        "hit_rate",
        "average_win",
        "average_loss",
        "average_win_loss_ratio",
        "median_trade_return",
        "return_skew",
        "return_kurtosis",
        "alpha_t_stat",
        "information_ratio",
    ),
    "drawdown_loss_behavior": (
        "max_drawdown",
        "average_drawdown",
        "median_drawdown",
        "drawdown_duration_max",
        "drawdown_duration_average",
        "time_under_water",
        "recovery_factor",
        "ulcer_index",
        "drawdown_velocity",
        "worst_day",
        "worst_week",
        "worst_month",
        "max_consecutive_losses",
        "loss_clustering_score",
        "VaR_95",
        "VaR_99",
        "CVaR_95",
        "CVaR_99",
        "tail_ratio",
        "downside_deviation",
    ),
    "robustness_stability": (
        "walk_forward_efficiency",
        "fold_to_fold_return_dispersion",
        "fold_to_fold_sharpe_dispersion",
        "fold_to_fold_drawdown_dispersion",
        "parameter_stability_score",
        "feature_stability_score",
        "regime_stability_score",
        "symbol_stability_score",
        "timeframe_stability_score",
        "cost_sensitivity_score",
        "slippage_sensitivity_score",
        "capacity_sensitivity_score",
        "turnover_sensitivity_score",
        "out_of_sample_decay",
        "in_sample_vs_out_of_sample_gap",
        "probabilistic_sharpe_ratio",
        "deflated_sharpe_ratio",
        "PBO",
    ),
    "execution_realism": (
        "average_slippage_per_trade",
        "median_slippage_per_trade",
        "slippage_bps",
        "spread_cost",
        "spread_capture",
        "fill_rate",
        "partial_fill_rate",
        "order_reject_rate",
        "cancel_replace_rate",
        "queue_position_decay",
        "latency_event_to_signal",
        "latency_signal_to_order",
        "tick_to_send_us",
        "decision_to_send_us",
        "cancel_to_send_us",
        "replace_to_send_us",
        "latency_order_to_ack",
        "send_to_ack_us",
        "cancel_to_ack_us",
        "replace_to_ack_us",
        "latency_ack_to_fill",
        "alpha_half_life",
        "execution_latency_vs_alpha_half_life",
        "turnover",
        "market_impact_estimate",
        "adverse_selection_rate",
        "capacity_estimate",
        "capacity_at_10pct_edge_decay",
        "capacity_at_25pct_edge_decay",
        "capacity_at_50pct_edge_decay",
    ),
    "portfolio_fit": (
        "correlation_to_existing_models",
        "drawdown_correlation_to_existing_models",
        "marginal_sharpe_contribution",
        "marginal_calmar_contribution",
        "marginal_drawdown_contribution",
        "marginal_CVaR_contribution",
        "risk_contribution",
        "gross_exposure_contribution",
        "net_exposure_contribution",
        "factor_exposures",
        "beta_to_benchmark",
        "liquidity_overlap_with_existing_models",
        "crowding_overlap_score",
        "regime_complementarity_score",
        "crisis_period_contribution",
    ),
    "prediction_calibration_quality": (
        "IC",
        "rank_IC",
        "ICIR",
        "Brier_score",
        "log_loss",
        "ROC_AUC",
        "PR_AUC",
        "precision",
        "recall",
        "F1",
        "false_positive_cost",
        "false_negative_cost",
        "prediction_decile_returns",
        "signal_bucket_monotonicity",
        "calibration_error",
        "expected_calibration_error",
        "confidence_vs_realized_accuracy",
        "high_confidence_trade_performance",
        "low_confidence_trade_performance",
    ),
}

METRIC_UNITS: dict[str, str] = {
    "hit_rate": "ratio",
    "fill_rate": "ratio",
    "partial_fill_rate": "ratio",
    "order_reject_rate": "ratio",
    "slippage_bps": "bps",
    "average_slippage_per_trade": "bps",
    "median_slippage_per_trade": "bps",
    "latency_order_to_ack": "ms",
    "latency_signal_to_order": "ms",
    "latency_event_to_signal": "ms",
    "latency_ack_to_fill": "ms",
    "tick_to_send_us": "us",
    "decision_to_send_us": "us",
    "cancel_to_send_us": "us",
    "replace_to_send_us": "us",
    "send_to_ack_us": "us",
    "cancel_to_ack_us": "us",
    "replace_to_ack_us": "us",
    "alpha_half_life": "ms",
}


def _numbers(values: Iterable[Any]) -> list[float]:
    parsed: list[float] = []
    for value in values:
        number = finite_or_none(value)
        if number is not None:
            parsed.append(number)
    return parsed


def _metric(
    name: str,
    value: float | None,
    *,
    group: str,
    sample_size: int,
    created_at: str,
    source_artifact_ids: tuple[str, ...],
    unit: str | None = None,
    warnings: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
) -> MetricValue:
    status = "ok" if value is not None and not errors else "unavailable"
    reliability = "high" if sample_size >= 30 else ("medium" if sample_size >= 5 else "low")
    return MetricValue(
        metric_name=name,
        metric_value=value,
        metric_unit=unit or METRIC_UNITS.get(name, "scalar"),
        metric_window="run",
        sample_size=sample_size,
        calculation_version=CALCULATION_VERSION,
        source_artifact_ids=source_artifact_ids,
        created_at=created_at,
        group=group,
        status=status,
        warnings=warnings,
        errors=errors,
        reliability=reliability,
    )


def _unavailable(
    name: str,
    *,
    group: str,
    reason: str,
    created_at: str,
    source_artifact_ids: tuple[str, ...],
) -> MetricValue:
    return _metric(
        name,
        None,
        group=group,
        sample_size=0,
        created_at=created_at,
        source_artifact_ids=source_artifact_ids,
        warnings=(reason,),
        errors=(reason,),
    )


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * pct
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(ordered[int(idx)])
    return float(ordered[lo] * (hi - idx) + ordered[hi] * (idx - lo))


def _sharpe(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    sd = pstdev(values)
    if sd <= 1e-12:
        return None
    return float(mean(values) / sd * math.sqrt(len(values)))


def _sortino(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    downside = [min(0.0, value) for value in values]
    downside_sd = pstdev(downside)
    if downside_sd <= 1e-12:
        return None
    return float(mean(values) / downside_sd * math.sqrt(len(values)))


def _profit_factor(values: list[float]) -> float | None:
    wins = sum(value for value in values if value > 0.0)
    losses = abs(sum(value for value in values if value < 0.0))
    if losses <= 1e-12:
        return wins if wins > 0.0 else None
    return float(wins / losses)


def _drawdowns(values: list[float]) -> list[float]:
    cumulative = []
    total = 0.0
    for value in values:
        total += value
        cumulative.append(total)
    if not cumulative:
        return []
    peak = cumulative[0]
    dds: list[float] = []
    for value in cumulative:
        peak = max(peak, value)
        dds.append(value - peak)
    return dds


def _loss_streak(values: list[float]) -> int:
    current = 0
    worst = 0
    for value in values:
        if value < 0:
            current += 1
            worst = max(worst, current)
        else:
            current = 0
    return worst


def _skew(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    mu = mean(values)
    sd = pstdev(values)
    if sd <= 1e-12:
        return None
    return float(sum(((value - mu) / sd) ** 3 for value in values) / len(values))


def _kurtosis(values: list[float]) -> float | None:
    if len(values) < 4:
        return None
    mu = mean(values)
    sd = pstdev(values)
    if sd <= 1e-12:
        return None
    return float(sum(((value - mu) / sd) ** 4 for value in values) / len(values) - 3.0)


def _extract_returns(inputs: dict[str, Any]) -> list[float]:
    returns = _numbers(inputs.get("returns") or [])
    if returns:
        return returns
    trades = inputs.get("trades") or []
    if isinstance(trades, list):
        returns = _numbers(
            trade.get("realized_pnl", trade.get("net_pnl", trade.get("pnl", trade.get("return"))))
            for trade in trades
            if isinstance(trade, dict)
        )
    return returns


def calculate_metric_values(inputs: dict[str, Any]) -> list[MetricValue]:
    """Calculate the full registry surface, marking impossible metrics explicitly."""

    created_at = str(inputs.get("created_at") or inputs.get("run_finished_at") or DEFAULT_CREATED_AT)
    source_artifact_ids = tuple(str(v) for v in inputs.get("source_artifact_ids", ()) if str(v))
    returns = _extract_returns(inputs)
    trades = [row for row in inputs.get("trades", []) if isinstance(row, dict)]
    execution = inputs.get("execution") or {}
    robustness = inputs.get("robustness") or {}
    portfolio = inputs.get("portfolio") or {}
    prediction = inputs.get("prediction") or {}
    folds = [row for row in robustness.get("folds", []) if isinstance(row, dict)]
    metrics: dict[str, float | None] = {}

    wins = [value for value in returns if value > 0.0]
    losses = [value for value in returns if value < 0.0]
    dds = _drawdowns(returns)
    slippages = _numbers([trade.get("slippage_bps") for trade in trades] + list(execution.get("slippage_bps_samples") or []))
    spreads = _numbers([trade.get("spread_bps") for trade in trades] + list(execution.get("spread_bps_samples") or []))
    fills = [trade for trade in trades if str(trade.get("fill_status", "")).upper() in {"FILLED", "PARTIAL"}]
    partials = [trade for trade in trades if str(trade.get("fill_status", "")).upper() == "PARTIAL"]
    rejects = [trade for trade in trades if str(trade.get("fill_status", "")).upper() == "REJECTED"]
    gross_values = _numbers(trade.get("gross_pnl") for trade in trades)
    holding = _numbers(trade.get("holding_period_min") for trade in trades)

    metrics.update(
        {
            "net_return": sum(returns) if returns else None,
            "gross_return": sum(gross_values) if gross_values else (sum(abs(v) for v in returns) if returns else None),
            "annualized_volatility": pstdev(returns) * math.sqrt(len(returns)) if len(returns) >= 2 else None,
            "sharpe": _sharpe(returns),
            "sortino": _sortino(returns),
            "profit_factor": _profit_factor(returns),
            "expectancy_per_trade": mean(returns) if returns else None,
            "hit_rate": len(wins) / len(returns) if returns else None,
            "average_win": mean(wins) if wins else None,
            "average_loss": mean(losses) if losses else None,
            "average_win_loss_ratio": (mean(wins) / abs(mean(losses))) if wins and losses else None,
            "median_trade_return": median(returns) if returns else None,
            "return_skew": _skew(returns),
            "return_kurtosis": _kurtosis(returns),
            "alpha_t_stat": (mean(returns) / (pstdev(returns) / math.sqrt(len(returns)))) if len(returns) >= 2 and pstdev(returns) > 1e-12 else None,
            "max_drawdown": min(dds) if dds else None,
            "average_drawdown": mean([dd for dd in dds if dd < 0.0]) if any(dd < 0.0 for dd in dds) else None,
            "median_drawdown": median([dd for dd in dds if dd < 0.0]) if any(dd < 0.0 for dd in dds) else None,
            "ulcer_index": math.sqrt(mean([dd * dd for dd in dds])) if dds else None,
            "drawdown_velocity": min((dds[i] - dds[i - 1] for i in range(1, len(dds))), default=None) if dds else None,
            "worst_day": min(returns) if returns else None,
            "worst_week": min(sum(returns[i : i + 5]) for i in range(0, max(1, len(returns) - 4))) if len(returns) >= 5 else None,
            "worst_month": min(sum(returns[i : i + 21]) for i in range(0, max(1, len(returns) - 20))) if len(returns) >= 21 else None,
            "max_consecutive_losses": float(_loss_streak(returns)) if returns else None,
            "loss_clustering_score": (_loss_streak(returns) / len(returns)) if returns else None,
            "VaR_95": _percentile(returns, 0.05),
            "VaR_99": _percentile(returns, 0.01),
            "CVaR_95": mean([v for v in returns if v <= (_percentile(returns, 0.05) or v)]) if returns else None,
            "CVaR_99": mean([v for v in returns if v <= (_percentile(returns, 0.01) or v)]) if returns else None,
            "tail_ratio": (abs((_percentile(returns, 0.95) or 0.0) / (_percentile(returns, 0.05) or 0.0))) if returns and (_percentile(returns, 0.05) or 0.0) != 0.0 else None,
            "downside_deviation": pstdev([min(0.0, v) for v in returns]) if len(returns) >= 2 else None,
        }
    )
    max_dd = metrics.get("max_drawdown")
    metrics["calmar"] = (metrics["net_return"] / abs(max_dd)) if metrics.get("net_return") is not None and max_dd else None
    metrics["cagr"] = metrics.get("net_return")
    metrics["recovery_factor"] = (metrics["net_return"] / abs(max_dd)) if metrics.get("net_return") is not None and max_dd else None
    metrics["drawdown_duration_max"] = float(_loss_streak(dds)) if dds else None
    metrics["drawdown_duration_average"] = metrics["time_under_water"] = float(sum(1 for dd in dds if dd < 0.0)) if dds else None

    fold_returns = _numbers(fold.get("return", fold.get("net_return", fold.get("proxy_net_pnl_bps"))) for fold in folds)
    fold_sharpes = _numbers(fold.get("sharpe") for fold in folds)
    fold_dds = _numbers(fold.get("max_drawdown") for fold in folds)
    metrics.update(
        {
            "walk_forward_efficiency": finite_or_none(robustness.get("walk_forward_efficiency")),
            "fold_to_fold_return_dispersion": pstdev(fold_returns) if len(fold_returns) >= 2 else None,
            "fold_to_fold_sharpe_dispersion": pstdev(fold_sharpes) if len(fold_sharpes) >= 2 else None,
            "fold_to_fold_drawdown_dispersion": pstdev(fold_dds) if len(fold_dds) >= 2 else None,
            "parameter_stability_score": finite_or_none(robustness.get("parameter_stability_score")),
            "feature_stability_score": finite_or_none(robustness.get("feature_stability_score")),
            "regime_stability_score": finite_or_none(robustness.get("regime_stability_score")),
            "symbol_stability_score": finite_or_none(robustness.get("symbol_stability_score")),
            "timeframe_stability_score": finite_or_none(robustness.get("timeframe_stability_score")),
            "cost_sensitivity_score": finite_or_none(robustness.get("cost_sensitivity_score")),
            "slippage_sensitivity_score": finite_or_none(robustness.get("slippage_sensitivity_score")),
            "capacity_sensitivity_score": finite_or_none(robustness.get("capacity_sensitivity_score")),
            "turnover_sensitivity_score": finite_or_none(robustness.get("turnover_sensitivity_score")),
            "out_of_sample_decay": finite_or_none(robustness.get("out_of_sample_decay")),
            "in_sample_vs_out_of_sample_gap": finite_or_none(robustness.get("in_sample_vs_out_of_sample_gap")),
            "probabilistic_sharpe_ratio": finite_or_none(robustness.get("probabilistic_sharpe_ratio")),
            "deflated_sharpe_ratio": finite_or_none(robustness.get("deflated_sharpe_ratio")),
            "PBO": finite_or_none(robustness.get("PBO")),
        }
    )

    fill_rate = finite_or_none(execution.get("fill_rate"))
    if fill_rate is None and trades:
        fill_rate = len(fills) / len(trades)
    metrics.update(
        {
            "average_slippage_per_trade": mean(slippages) if slippages else None,
            "median_slippage_per_trade": median(slippages) if slippages else None,
            "slippage_bps": mean(slippages) if slippages else finite_or_none(execution.get("slippage_bps")),
            "spread_cost": mean(spreads) if spreads else finite_or_none(execution.get("spread_cost")),
            "spread_capture": finite_or_none(execution.get("spread_capture")),
            "fill_rate": fill_rate,
            "partial_fill_rate": len(partials) / len(trades) if trades else finite_or_none(execution.get("partial_fill_rate")),
            "order_reject_rate": len(rejects) / len(trades) if trades else finite_or_none(execution.get("order_reject_rate")),
            "cancel_replace_rate": finite_or_none(execution.get("cancel_replace_rate")),
            "queue_position_decay": finite_or_none(execution.get("queue_position_decay")),
            "latency_event_to_signal": finite_or_none(execution.get("latency_event_to_signal")),
            "latency_signal_to_order": finite_or_none(execution.get("latency_signal_to_order")),
            "tick_to_send_us": finite_or_none(execution.get("tick_to_send_us")),
            "decision_to_send_us": finite_or_none(execution.get("decision_to_send_us")),
            "cancel_to_send_us": finite_or_none(execution.get("cancel_to_send_us")),
            "replace_to_send_us": finite_or_none(execution.get("replace_to_send_us")),
            "latency_order_to_ack": finite_or_none(execution.get("latency_order_to_ack")),
            "send_to_ack_us": finite_or_none(execution.get("send_to_ack_us")),
            "cancel_to_ack_us": finite_or_none(execution.get("cancel_to_ack_us")),
            "replace_to_ack_us": finite_or_none(execution.get("replace_to_ack_us")),
            "latency_ack_to_fill": finite_or_none(execution.get("latency_ack_to_fill")),
            "alpha_half_life": finite_or_none(execution.get("alpha_half_life")),
            "turnover": float(len(trades)) if trades else finite_or_none(execution.get("turnover")),
            "market_impact_estimate": finite_or_none(execution.get("market_impact_estimate")),
            "adverse_selection_rate": finite_or_none(execution.get("adverse_selection_rate")),
            "capacity_estimate": finite_or_none(execution.get("capacity_estimate")),
            "capacity_at_10pct_edge_decay": finite_or_none(execution.get("capacity_at_10pct_edge_decay")),
            "capacity_at_25pct_edge_decay": finite_or_none(execution.get("capacity_at_25pct_edge_decay")),
            "capacity_at_50pct_edge_decay": finite_or_none(execution.get("capacity_at_50pct_edge_decay")),
        }
    )
    latency = metrics.get("latency_order_to_ack")
    half_life = metrics.get("alpha_half_life")
    metrics["execution_latency_vs_alpha_half_life"] = (latency / half_life) if latency is not None and half_life else None

    for name in METRIC_GROUPS["portfolio_fit"]:
        metrics[name] = finite_or_none(portfolio.get(name))
    for name in METRIC_GROUPS["prediction_calibration_quality"]:
        metrics[name] = finite_or_none(prediction.get(name))

    out: list[MetricValue] = []
    for group, names in METRIC_GROUPS.items():
        for name in names:
            value = metrics.get(name)
            if value is None:
                out.append(
                    _unavailable(
                        name,
                        group=group,
                        reason=f"{name} unavailable: required input not observed",
                        created_at=created_at,
                        source_artifact_ids=source_artifact_ids,
                    )
                )
            else:
                out.append(
                    _metric(
                        name,
                        value,
                        group=group,
                        sample_size=len(returns) or len(trades) or len(folds),
                        created_at=created_at,
                        source_artifact_ids=source_artifact_ids,
                    )
                )
    return out
