"""Institutional model scorecard generation."""

from __future__ import annotations

from typing import Any

from model_metrics.config import DEFAULT_CONFIG, thresholds_for
from model_metrics.registry import calculate_metric_values
from model_metrics.schemas import (
    CALCULATION_VERSION,
    CategoryScore,
    MetricValue,
    ModelScorecard,
    stable_id,
)


CATEGORY_WEIGHTS = {
    "net_alpha_quality": 0.20,
    "drawdown_loss_behavior": 0.20,
    "robustness_stability": 0.20,
    "execution_realism": 0.15,
    "portfolio_fit": 0.15,
    "prediction_calibration_quality": 0.10,
}


def _metric_map(metrics: list[MetricValue]) -> dict[str, MetricValue]:
    return {metric.metric_name: metric for metric in metrics}


def _value(metrics: dict[str, MetricValue], name: str) -> float | None:
    metric = metrics.get(name)
    return metric.metric_value if metric and metric.status == "ok" else None


def _score_ge(value: float | None, threshold: float, *, unavailable: float = 0.35) -> float:
    if value is None:
        return unavailable
    if threshold <= 0:
        return 1.0 if value > threshold else 0.0
    return max(0.0, min(1.0, value / threshold))


def _score_le_abs(value: float | None, threshold: float, *, unavailable: float = 0.35) -> float:
    if value is None:
        return unavailable
    magnitude = abs(value)
    if threshold <= 0:
        return 1.0 if magnitude == 0 else 0.0
    return max(0.0, min(1.0, 1.0 - (magnitude / threshold - 1.0)))


def _category_status(score: float, unavailable_count: int) -> str:
    if unavailable_count:
        return "warning" if score >= 0.55 else "fail"
    if score >= 0.75:
        return "pass"
    if score >= 0.55:
        return "warning"
    return "fail"


def _category(
    name: str,
    metric_names: tuple[str, ...],
    score_parts: list[float],
    metrics: dict[str, MetricValue],
) -> CategoryScore:
    unavailable = sum(1 for metric in metric_names if (metrics.get(metric) is None or metrics[metric].status != "ok"))
    score = sum(score_parts) / len(score_parts) if score_parts else 0.0
    sample_sizes = [metrics[m].sample_size for m in metric_names if m in metrics]
    sample_warning = "sample size below institutional minimum" if sample_sizes and max(sample_sizes) < 30 else ""
    return CategoryScore(
        category=name,
        weight=CATEGORY_WEIGHTS[name],
        normalized_score=round(score, 4),
        status=_category_status(score, unavailable),
        explanation=f"{name} score={score:.3f}; unavailable_metrics={unavailable}",
        metric_names=metric_names,
        sample_size_warning=sample_warning,
    )


def _grade(weighted_score: float, category_scores: list[CategoryScore], metrics: dict[str, MetricValue]) -> str:
    if any(score.status == "fail" for score in category_scores[:4]):
        return "D" if weighted_score >= 0.35 else "F"
    if _value(metrics, "sharpe") is None and _value(metrics, "net_return") is None:
        return "F"
    if weighted_score >= 0.85:
        return "A"
    if weighted_score >= 0.70:
        return "B"
    if weighted_score >= 0.55:
        return "C"
    if weighted_score >= 0.35:
        return "D"
    return "F"


def generate_model_scorecard(
    inputs: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    metrics: list[MetricValue] | None = None,
) -> ModelScorecard:
    """Generate a reproducible institutional scorecard for any model lane."""

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
    metric_values = metrics or calculate_metric_values(inputs)
    mmap = _metric_map(metric_values)
    categories = [
        _category(
            "net_alpha_quality",
            ("net_return", "sharpe", "sortino", "profit_factor", "expectancy_per_trade"),
            [
                _score_ge(_value(mmap, "net_return"), 0.0, unavailable=0.2),
                _score_ge(_value(mmap, "sharpe"), float(threshold["minimum_sharpe"])),
                _score_ge(_value(mmap, "sortino"), float(threshold["minimum_sortino"])),
                _score_ge(_value(mmap, "profit_factor"), float(threshold["minimum_profit_factor"])),
                _score_ge(_value(mmap, "expectancy_per_trade"), float(threshold["minimum_expectancy"]), unavailable=0.2),
            ],
            mmap,
        ),
        _category(
            "drawdown_loss_behavior",
            ("max_drawdown", "CVaR_95", "drawdown_velocity", "max_consecutive_losses"),
            [
                _score_le_abs(_value(mmap, "max_drawdown"), float(threshold["maximum_drawdown"])),
                _score_le_abs(_value(mmap, "CVaR_95"), float(threshold["maximum_drawdown"])),
                _score_le_abs(_value(mmap, "drawdown_velocity"), float(threshold["maximum_drawdown_velocity"])),
            ],
            mmap,
        ),
        _category(
            "robustness_stability",
            ("walk_forward_efficiency", "fold_to_fold_return_dispersion", "deflated_sharpe_ratio", "PBO"),
            [
                _score_ge(_value(mmap, "walk_forward_efficiency"), float(threshold["minimum_walk_forward_efficiency"])),
                _score_le_abs(_value(mmap, "fold_to_fold_return_dispersion"), float(threshold["maximum_fold_dispersion"])),
                _score_ge(_value(mmap, "deflated_sharpe_ratio"), float(threshold["minimum_deflated_sharpe"])),
                _score_le_abs(_value(mmap, "PBO"), float(threshold["maximum_pbo"])),
            ],
            mmap,
        ),
        _category(
            "execution_realism",
            ("slippage_bps", "fill_rate", "latency_order_to_ack", "execution_latency_vs_alpha_half_life"),
            [
                _score_le_abs(_value(mmap, "slippage_bps"), float(threshold["maximum_slippage_bps"])),
                _score_ge(_value(mmap, "fill_rate"), float(threshold["minimum_fill_rate"])),
                _score_le_abs(_value(mmap, "latency_order_to_ack"), float(threshold["maximum_latency_ms"])),
                _score_le_abs(_value(mmap, "execution_latency_vs_alpha_half_life"), 1.0),
            ],
            mmap,
        ),
        _category(
            "portfolio_fit",
            ("correlation_to_existing_models", "marginal_sharpe_contribution", "risk_contribution"),
            [
                _score_le_abs(_value(mmap, "correlation_to_existing_models"), float(threshold["maximum_correlation_to_book"])),
                _score_ge(_value(mmap, "marginal_sharpe_contribution"), 0.0),
            ],
            mmap,
        ),
        _category(
            "prediction_calibration_quality",
            ("IC", "Brier_score", "expected_calibration_error", "high_confidence_trade_performance"),
            [
                _score_ge(_value(mmap, "IC"), 0.0),
                _score_le_abs(_value(mmap, "Brier_score"), 0.25),
                _score_le_abs(_value(mmap, "expected_calibration_error"), 0.10),
            ],
            mmap,
        ),
    ]
    weighted = round(sum(score.normalized_score * score.weight for score in categories), 4)
    warnings = tuple(score.sample_size_warning for score in categories if score.sample_size_warning)
    payload = {
        "model_id": str(meta.get("model_id", meta.get("candidate_id", ""))),
        "model_version": str(meta.get("model_version", "")),
        "run_id": str(meta.get("run_id", "")),
        "campaign_id": str(meta.get("campaign_id", "")),
        "robustness_run_id": str(meta.get("robustness_run_id", meta.get("run_id", ""))),
        "weighted_score": weighted,
        "metric_count": len(metric_values),
    }
    return ModelScorecard(
        scorecard_id=stable_id("scorecard", payload),
        model_id=payload["model_id"],
        model_version=payload["model_version"],
        run_id=payload["run_id"],
        campaign_id=payload["campaign_id"],
        asset_class=str(meta.get("asset_class", "")),
        symbol=str(meta.get("symbol", meta.get("contract", meta.get("market_id", "")))),
        robustness_run_id=payload["robustness_run_id"],
        calculation_version=CALCULATION_VERSION,
        created_at=str(inputs.get("created_at") or inputs.get("run_finished_at") or "1970-01-01T00:00:00+00:00"),
        grade=_grade(weighted, categories, mmap),
        weighted_score=weighted,
        category_scores=tuple(categories),
        metrics=tuple(metric_values),
        warnings=warnings,
        errors=tuple(metric.metric_name for metric in metric_values if metric.status == "unavailable"),
    )
