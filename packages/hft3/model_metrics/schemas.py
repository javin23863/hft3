"""Model metrics computation and scorecard generation.

Computes a full metric suite from backtest/replay outputs and generates
a categorized scorecard with letter grades.

Metric groups:
1. net_alpha_quality (~18 metrics)
2. drawdown_loss_behavior (~20 metrics)
3. robustness_stability (~18 metrics)
4. execution_realism (~30 metrics)
5. portfolio_fit (~15 metrics)
6. prediction_calibration_quality (~18 metrics)

Each metric includes: value, unit, status, sample_size, source_artifact, missing_reason
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MetricEntry:
    """Single metric with full metadata."""
    value: Optional[float] = None
    unit: str = ""
    status: str = "computed"  # computed, missing, not_applicable
    sample_size: int = 0
    source_artifact: str = ""
    missing_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MetricGroup:
    """Group of related metrics."""
    group_name: str
    metrics: Dict[str, MetricEntry] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_name": self.group_name,
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
        }


@dataclass
class MetricValues:
    """Full metric surface organized into 6 groups."""
    # Group 1: Net Alpha Quality
    net_return: Optional[float] = None
    gross_return: Optional[float] = None
    cagr: Optional[float] = None
    annualized_volatility: Optional[float] = None
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    calmar: Optional[float] = None
    profit_factor: Optional[float] = None
    expectancy_per_trade: Optional[float] = None
    hit_rate: Optional[float] = None
    average_win: Optional[float] = None
    average_loss: Optional[float] = None
    win_loss_ratio: Optional[float] = None
    median_trade_return: Optional[float] = None
    return_skew: Optional[float] = None
    return_kurtosis: Optional[float] = None
    alpha_t_stat: Optional[float] = None
    information_ratio: Optional[float] = None
    num_trades: int = 0

    # Group 2: Drawdown/Loss Behavior
    max_drawdown: Optional[float] = None
    average_drawdown: Optional[float] = None
    median_drawdown: Optional[float] = None
    drawdown_duration_max: Optional[int] = None
    drawdown_duration_average: Optional[float] = None
    time_under_water: Optional[float] = None
    recovery_factor: Optional[float] = None
    ulcer_index: Optional[float] = None
    drawdown_velocity: Optional[float] = None
    worst_day: Optional[float] = None
    worst_week: Optional[float] = None
    worst_month: Optional[float] = None
    max_consecutive_losses: Optional[int] = None
    loss_clustering_score: Optional[float] = None
    var_95: Optional[float] = None
    var_99: Optional[float] = None
    cvar_95: Optional[float] = None
    cvar_99: Optional[float] = None
    tail_ratio: Optional[float] = None
    downside_deviation: Optional[float] = None

    # Group 3: Robustness/Stability
    walk_forward_efficiency: Optional[float] = None
    fold_to_fold_return_dispersion: Optional[float] = None
    fold_to_fold_sharpe_dispersion: Optional[float] = None
    fold_to_fold_drawdown_dispersion: Optional[float] = None
    parameter_stability: Optional[float] = None
    feature_stability: Optional[float] = None
    regime_stability: Optional[float] = None
    symbol_stability: Optional[float] = None
    timeframe_stability: Optional[float] = None
    cost_sensitivity_score: Optional[float] = None
    slippage_sensitivity_score: Optional[float] = None
    capacity_sensitivity_score: Optional[float] = None
    turnover_sensitivity_score: Optional[float] = None
    out_of_sample_decay: Optional[float] = None
    in_sample_vs_out_of_sample_gap: Optional[float] = None
    probabilistic_sharpe_ratio: Optional[float] = None
    deflated_sharpe_ratio: Optional[float] = None
    pbo: Optional[float] = None

    # Group 4: Execution Realism
    average_slippage_per_trade: Optional[float] = None
    median_slippage_per_trade: Optional[float] = None
    slippage_bps: Optional[float] = None
    spread_cost: Optional[float] = None
    spread_capture: Optional[float] = None
    fill_rate: Optional[float] = None
    partial_fill_rate: Optional[float] = None
    order_reject_rate: Optional[float] = None
    cancel_replace_rate: Optional[float] = None
    queue_position_decay: Optional[float] = None
    latency_event_to_signal: Optional[float] = None
    latency_signal_to_order: Optional[float] = None
    tick_to_send_trigger_us: Optional[float] = None
    decision_to_send_trigger_us: Optional[float] = None
    tick_to_send_us: Optional[float] = None
    decision_to_send_us: Optional[float] = None
    rithmic_send_call_us: Optional[float] = None
    cancel_to_send_us: Optional[float] = None
    replace_to_send_us: Optional[float] = None
    latency_order_to_ack: Optional[float] = None
    send_to_ack_us: Optional[float] = None
    cancel_to_ack_us: Optional[float] = None
    replace_to_ack_us: Optional[float] = None
    latency_ack_to_fill: Optional[float] = None
    alpha_half_life: Optional[float] = None
    execution_latency_vs_alpha_half_life: Optional[float] = None
    turnover: Optional[float] = None
    market_impact_estimate: Optional[float] = None
    adverse_selection_rate: Optional[float] = None
    capacity_estimate: Optional[float] = None
    capacity_at_10pct_edge_decay: Optional[float] = None
    capacity_at_25pct_edge_decay: Optional[float] = None
    capacity_at_50pct_edge_decay: Optional[float] = None

    # Group 5: Portfolio Fit
    correlation_to_existing_models: Optional[float] = None
    drawdown_correlation_to_existing_models: Optional[float] = None
    marginal_sharpe_contribution: Optional[float] = None
    marginal_calmar_contribution: Optional[float] = None
    marginal_drawdown_contribution: Optional[float] = None
    marginal_cvar_contribution: Optional[float] = None
    risk_contribution: Optional[float] = None
    gross_exposure_contribution: Optional[float] = None
    net_exposure_contribution: Optional[float] = None
    factor_exposures: Optional[Dict[str, float]] = None
    beta_to_benchmark: Optional[float] = None
    liquidity_overlap_with_existing_models: Optional[float] = None
    crowding_overlap_score: Optional[float] = None
    regime_complementarity_score: Optional[float] = None
    crisis_period_contribution: Optional[float] = None

    # Group 6: Prediction Calibration Quality
    ic: Optional[float] = None
    rank_ic: Optional[float] = None
    icir: Optional[float] = None
    brier_score: Optional[float] = None
    log_loss: Optional[float] = None
    roc_auc: Optional[float] = None
    pr_auc: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1: Optional[float] = None
    false_positive_cost: Optional[float] = None
    false_negative_cost: Optional[float] = None
    prediction_decile_returns: Optional[List[float]] = None
    signal_bucket_monotonicity: Optional[float] = None
    calibration_error: Optional[float] = None
    expected_calibration_error: Optional[float] = None
    confidence_vs_realized_accuracy: Optional[float] = None
    high_confident_trade_performance: Optional[float] = None
    low_confident_trade_performance: Optional[float] = None

    # Metadata
    missing_reasons: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_groups(self) -> List[MetricGroup]:
        """Organize metrics into 6 groups."""
        groups = []

        # Group 1: Net Alpha Quality
        g1 = MetricGroup(group_name="net_alpha_quality")
        g1.metrics = {
            "net_return": MetricEntry(self.net_return, "USD", "computed" if self.net_return is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("net_return", "")),
            "gross_return": MetricEntry(self.gross_return, "USD", "computed" if self.gross_return is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("gross_return", "")),
            "cagr": MetricEntry(self.cagr, "%", "computed" if self.cagr is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("cagr", "")),
            "annualized_volatility": MetricEntry(self.annualized_volatility, "%", "computed" if self.annualized_volatility is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("annualized_volatility", "")),
            "sharpe": MetricEntry(self.sharpe, "ratio", "computed" if self.sharpe is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("sharpe", "")),
            "sortino": MetricEntry(self.sortino, "ratio", "computed" if self.sortino is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("sortino", "")),
            "calmar": MetricEntry(self.calmar, "ratio", "computed" if self.calmar is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("calmar", "")),
            "profit_factor": MetricEntry(self.profit_factor, "ratio", "computed" if self.profit_factor is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("profit_factor", "")),
            "expectancy_per_trade": MetricEntry(self.expectancy_per_trade, "USD", "computed" if self.expectancy_per_trade is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("expectancy_per_trade", "")),
            "hit_rate": MetricEntry(self.hit_rate, "%", "computed" if self.hit_rate is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("hit_rate", "")),
            "average_win": MetricEntry(self.average_win, "USD", "computed" if self.average_win is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("average_win", "")),
            "average_loss": MetricEntry(self.average_loss, "USD", "computed" if self.average_loss is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("average_loss", "")),
            "win_loss_ratio": MetricEntry(self.win_loss_ratio, "ratio", "computed" if self.win_loss_ratio is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("win_loss_ratio", "")),
            "median_trade_return": MetricEntry(self.median_trade_return, "USD", "computed" if self.median_trade_return is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("median_trade_return", "")),
            "return_skew": MetricEntry(self.return_skew, "skewness", "computed" if self.return_skew is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("return_skew", "")),
            "return_kurtosis": MetricEntry(self.return_kurtosis, "kurtosis", "computed" if self.return_kurtosis is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("return_kurtosis", "")),
            "alpha_t_stat": MetricEntry(self.alpha_t_stat, "t-stat", "computed" if self.alpha_t_stat is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("alpha_t_stat", "")),
            "information_ratio": MetricEntry(self.information_ratio, "ratio", "computed" if self.information_ratio is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("information_ratio", "")),
        }
        groups.append(g1)

        # Group 2: Drawdown/Loss Behavior
        g2 = MetricGroup(group_name="drawdown_loss_behavior")
        g2.metrics = {
            "max_drawdown": MetricEntry(self.max_drawdown, "USD", "computed" if self.max_drawdown is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("max_drawdown", "")),
            "average_drawdown": MetricEntry(self.average_drawdown, "USD", "computed" if self.average_drawdown is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("average_drawdown", "")),
            "median_drawdown": MetricEntry(self.median_drawdown, "USD", "computed" if self.median_drawdown is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("median_drawdown", "")),
            "drawdown_duration_max": MetricEntry(float(self.drawdown_duration_max) if self.drawdown_duration_max is not None else None, "trades", "computed" if self.drawdown_duration_max is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("drawdown_duration_max", "")),
            "drawdown_duration_average": MetricEntry(self.drawdown_duration_average, "trades", "computed" if self.drawdown_duration_average is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("drawdown_duration_average", "")),
            "time_under_water": MetricEntry(self.time_under_water, "%", "computed" if self.time_under_water is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("time_under_water", "")),
            "recovery_factor": MetricEntry(self.recovery_factor, "ratio", "computed" if self.recovery_factor is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("recovery_factor", "")),
            "ulcer_index": MetricEntry(self.ulcer_index, "index", "computed" if self.ulcer_index is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("ulcer_index", "")),
            "drawdown_velocity": MetricEntry(self.drawdown_velocity, "USD/trade", "computed" if self.drawdown_velocity is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("drawdown_velocity", "")),
            "worst_day": MetricEntry(self.worst_day, "USD", "computed" if self.worst_day is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("worst_day", "")),
            "worst_week": MetricEntry(self.worst_week, "USD", "computed" if self.worst_week is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("worst_week", "")),
            "worst_month": MetricEntry(self.worst_month, "USD", "computed" if self.worst_month is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("worst_month", "")),
            "max_consecutive_losses": MetricEntry(float(self.max_consecutive_losses) if self.max_consecutive_losses is not None else None, "trades", "computed" if self.max_consecutive_losses is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("max_consecutive_losses", "")),
            "loss_clustering_score": MetricEntry(self.loss_clustering_score, "score", "computed" if self.loss_clustering_score is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("loss_clustering_score", "")),
            "var_95": MetricEntry(self.var_95, "USD", "computed" if self.var_95 is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("var_95", "")),
            "var_99": MetricEntry(self.var_99, "USD", "computed" if self.var_99 is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("var_99", "")),
            "cvar_95": MetricEntry(self.cvar_95, "USD", "computed" if self.cvar_95 is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("cvar_95", "")),
            "cvar_99": MetricEntry(self.cvar_99, "USD", "computed" if self.cvar_99 is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("cvar_99", "")),
            "tail_ratio": MetricEntry(self.tail_ratio, "ratio", "computed" if self.tail_ratio is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("tail_ratio", "")),
            "downside_deviation": MetricEntry(self.downside_deviation, "USD", "computed" if self.downside_deviation is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("downside_deviation", "")),
        }
        groups.append(g2)

        # Group 3: Robustness/Stability
        g3 = MetricGroup(group_name="robustness_stability")
        g3.metrics = {
            "walk_forward_efficiency": MetricEntry(self.walk_forward_efficiency, "ratio", "computed" if self.walk_forward_efficiency is not None else "missing", 0, "wfc", self.missing_reasons.get("walk_forward_efficiency", "requires multi-period campaign")),
            "fold_to_fold_return_dispersion": MetricEntry(self.fold_to_fold_return_dispersion, "std", "computed" if self.fold_to_fold_return_dispersion is not None else "missing", 0, "wfc", self.missing_reasons.get("fold_to_fold_return_dispersion", "requires multi-period campaign")),
            "fold_to_fold_sharpe_dispersion": MetricEntry(self.fold_to_fold_sharpe_dispersion, "std", "computed" if self.fold_to_fold_sharpe_dispersion is not None else "missing", 0, "wfc", self.missing_reasons.get("fold_to_fold_sharpe_dispersion", "requires multi-period campaign")),
            "fold_to_fold_drawdown_dispersion": MetricEntry(self.fold_to_fold_drawdown_dispersion, "std", "computed" if self.fold_to_fold_drawdown_dispersion is not None else "missing", 0, "wfc", self.missing_reasons.get("fold_to_fold_drawdown_dispersion", "requires multi-period campaign")),
            "parameter_stability": MetricEntry(self.parameter_stability, "score", "computed" if self.parameter_stability is not None else "missing", 0, "wfc", self.missing_reasons.get("parameter_stability", "requires multi-period campaign")),
            "feature_stability": MetricEntry(self.feature_stability, "score", "computed" if self.feature_stability is not None else "missing", 0, "wfc", self.missing_reasons.get("feature_stability", "requires multi-period campaign")),
            "regime_stability": MetricEntry(self.regime_stability, "score", "computed" if self.regime_stability is not None else "missing", 0, "wfc", self.missing_reasons.get("regime_stability", "requires multi-period campaign")),
            "symbol_stability": MetricEntry(self.symbol_stability, "score", "computed" if self.symbol_stability is not None else "missing", 0, "wfc", self.missing_reasons.get("symbol_stability", "requires multi-symbol campaign")),
            "timeframe_stability": MetricEntry(self.timeframe_stability, "score", "computed" if self.timeframe_stability is not None else "missing", 0, "wfc", self.missing_reasons.get("timeframe_stability", "requires multi-timeframe campaign")),
            "cost_sensitivity_score": MetricEntry(self.cost_sensitivity_score, "score", "computed" if self.cost_sensitivity_score is not None else "missing", 0, "wfc", self.missing_reasons.get("cost_sensitivity_score", "requires cost sweep")),
            "slippage_sensitivity_score": MetricEntry(self.slippage_sensitivity_score, "score", "computed" if self.slippage_sensitivity_score is not None else "missing", 0, "wfc", self.missing_reasons.get("slippage_sensitivity_score", "requires slippage sweep")),
            "capacity_sensitivity_score": MetricEntry(self.capacity_sensitivity_score, "score", "computed" if self.capacity_sensitivity_score is not None else "missing", 0, "wfc", self.missing_reasons.get("capacity_sensitivity_score", "requires capacity sweep")),
            "turnover_sensitivity_score": MetricEntry(self.turnover_sensitivity_score, "score", "computed" if self.turnover_sensitivity_score is not None else "missing", 0, "wfc", self.missing_reasons.get("turnover_sensitivity_score", "requires turnover sweep")),
            "out_of_sample_decay": MetricEntry(self.out_of_sample_decay, "ratio", "computed" if self.out_of_sample_decay is not None else "missing", 0, "wfc", self.missing_reasons.get("out_of_sample_decay", "requires OOS data")),
            "in_sample_vs_out_of_sample_gap": MetricEntry(self.in_sample_vs_out_of_sample_gap, "ratio", "computed" if self.in_sample_vs_out_of_sample_gap is not None else "missing", 0, "wfc", self.missing_reasons.get("in_sample_vs_out_of_sample_gap", "requires OOS data")),
            "probabilistic_sharpe_ratio": MetricEntry(self.probabilistic_sharpe_ratio, "probability", "computed" if self.probabilistic_sharpe_ratio is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("probabilistic_sharpe_ratio", "")),
            "deflated_sharpe_ratio": MetricEntry(self.deflated_sharpe_ratio, "ratio", "computed" if self.deflated_sharpe_ratio is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("deflated_sharpe_ratio", "")),
            "pbo": MetricEntry(self.pbo, "probability", "computed" if self.pbo is not None else "missing", 0, "wfc", self.missing_reasons.get("pbo", "requires walk-forward analysis")),
        }
        groups.append(g3)

        # Group 4: Execution Realism
        g4 = MetricGroup(group_name="execution_realism")
        g4.metrics = {
            "average_slippage_per_trade": MetricEntry(self.average_slippage_per_trade, "ticks", "computed" if self.average_slippage_per_trade is not None else "missing", self.num_trades, "replay", self.missing_reasons.get("average_slippage_per_trade", "")),
            "median_slippage_per_trade": MetricEntry(self.median_slippage_per_trade, "ticks", "computed" if self.median_slippage_per_trade is not None else "missing", self.num_trades, "replay", self.missing_reasons.get("median_slippage_per_trade", "")),
            "slippage_bps": MetricEntry(self.slippage_bps, "bps", "computed" if self.slippage_bps is not None else "missing", self.num_trades, "replay", self.missing_reasons.get("slippage_bps", "")),
            "spread_cost": MetricEntry(self.spread_cost, "USD", "computed" if self.spread_cost is not None else "missing", self.num_trades, "replay", self.missing_reasons.get("spread_cost", "")),
            "spread_capture": MetricEntry(self.spread_capture, "%", "computed" if self.spread_capture is not None else "missing", self.num_trades, "replay", self.missing_reasons.get("spread_capture", "")),
            "fill_rate": MetricEntry(self.fill_rate, "%", "computed" if self.fill_rate is not None else "missing", self.num_trades, "replay", self.missing_reasons.get("fill_rate", "no replay lifecycle data")),
            "partial_fill_rate": MetricEntry(self.partial_fill_rate, "%", "computed" if self.partial_fill_rate is not None else "missing", self.num_trades, "replay", self.missing_reasons.get("partial_fill_rate", "")),
            "order_reject_rate": MetricEntry(self.order_reject_rate, "%", "computed" if self.order_reject_rate is not None else "missing", self.num_trades, "replay", self.missing_reasons.get("order_reject_rate", "")),
            "cancel_replace_rate": MetricEntry(self.cancel_replace_rate, "%", "computed" if self.cancel_replace_rate is not None else "missing", self.num_trades, "replay", self.missing_reasons.get("cancel_replace_rate", "")),
            "queue_position_decay": MetricEntry(self.queue_position_decay, "positions/sec", "computed" if self.queue_position_decay is not None else "missing", self.num_trades, "replay", self.missing_reasons.get("queue_position_decay", "")),
            "latency_event_to_signal": MetricEntry(self.latency_event_to_signal, "us", "computed" if self.latency_event_to_signal is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("latency_event_to_signal", "")),
            "latency_signal_to_order": MetricEntry(self.latency_signal_to_order, "us", "computed" if self.latency_signal_to_order is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("latency_signal_to_order", "")),
            "tick_to_send_trigger_us": MetricEntry(self.tick_to_send_trigger_us, "us", "computed" if self.tick_to_send_trigger_us is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("tick_to_send_trigger_us", "requires CHI404 measurement")),
            "decision_to_send_trigger_us": MetricEntry(self.decision_to_send_trigger_us, "us", "computed" if self.decision_to_send_trigger_us is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("decision_to_send_trigger_us", "requires CHI404 measurement")),
            "tick_to_send_us": MetricEntry(self.tick_to_send_us, "us", "computed" if self.tick_to_send_us is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("tick_to_send_us", "requires CHI404 measurement")),
            "decision_to_send_us": MetricEntry(self.decision_to_send_us, "us", "computed" if self.decision_to_send_us is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("decision_to_send_us", "requires CHI404 measurement")),
            "rithmic_send_call_us": MetricEntry(self.rithmic_send_call_us, "us", "computed" if self.rithmic_send_call_us is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("rithmic_send_call_us", "requires Rithmic measurement")),
            "cancel_to_send_us": MetricEntry(self.cancel_to_send_us, "us", "computed" if self.cancel_to_send_us is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("cancel_to_send_us", "requires CHI404 measurement")),
            "replace_to_send_us": MetricEntry(self.replace_to_send_us, "us", "computed" if self.replace_to_send_us is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("replace_to_send_us", "requires CHI404 measurement")),
            "latency_order_to_ack": MetricEntry(self.latency_order_to_ack, "us", "computed" if self.latency_order_to_ack is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("latency_order_to_ack", "")),
            "send_to_ack_us": MetricEntry(self.send_to_ack_us, "us", "computed" if self.send_to_ack_us is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("send_to_ack_us", "requires CHI404 measurement")),
            "cancel_to_ack_us": MetricEntry(self.cancel_to_ack_us, "us", "computed" if self.cancel_to_ack_us is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("cancel_to_ack_us", "requires CHI404 measurement")),
            "replace_to_ack_us": MetricEntry(self.replace_to_ack_us, "us", "computed" if self.replace_to_ack_us is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("replace_to_ack_us", "requires CHI404 measurement")),
            "latency_ack_to_fill": MetricEntry(self.latency_ack_to_fill, "us", "computed" if self.latency_ack_to_fill is not None else "missing", self.num_trades, "latency", self.missing_reasons.get("latency_ack_to_fill", "")),
            "alpha_half_life": MetricEntry(self.alpha_half_life, "seconds", "computed" if self.alpha_half_life is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("alpha_half_life", "")),
            "execution_latency_vs_alpha_half_life": MetricEntry(self.execution_latency_vs_alpha_half_life, "ratio", "computed" if self.execution_latency_vs_alpha_half_life is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("execution_latency_vs_alpha_half_life", "")),
            "turnover": MetricEntry(self.turnover, "contracts", "computed" if self.turnover is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("turnover", "")),
            "market_impact_estimate": MetricEntry(self.market_impact_estimate, "bps", "computed" if self.market_impact_estimate is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("market_impact_estimate", "")),
            "adverse_selection_rate": MetricEntry(self.adverse_selection_rate, "ticks", "computed" if self.adverse_selection_rate is not None else "missing", self.num_trades, "replay", self.missing_reasons.get("adverse_selection_rate", "no replay lifecycle data")),
            "capacity_estimate": MetricEntry(self.capacity_estimate, "contracts", "computed" if self.capacity_estimate is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("capacity_estimate", "extrapolated from single-event PnL")),
            "capacity_at_10pct_edge_decay": MetricEntry(self.capacity_at_10pct_edge_decay, "contracts", "computed" if self.capacity_at_10pct_edge_decay is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("capacity_at_10pct_edge_decay", "requires capacity sweep")),
            "capacity_at_25pct_edge_decay": MetricEntry(self.capacity_at_25pct_edge_decay, "contracts", "computed" if self.capacity_at_25pct_edge_decay is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("capacity_at_25pct_edge_decay", "requires capacity sweep")),
            "capacity_at_50pct_edge_decay": MetricEntry(self.capacity_at_50pct_edge_decay, "contracts", "computed" if self.capacity_at_50pct_edge_decay is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("capacity_at_50pct_edge_decay", "requires capacity sweep")),
        }
        groups.append(g4)

        # Group 5: Portfolio Fit
        g5 = MetricGroup(group_name="portfolio_fit")
        g5.metrics = {
            "correlation_to_existing_models": MetricEntry(self.correlation_to_existing_models, "correlation", "computed" if self.correlation_to_existing_models is not None else "missing", 0, "portfolio", self.missing_reasons.get("correlation_to_existing_models", "requires multi-model campaign")),
            "drawdown_correlation_to_existing_models": MetricEntry(self.drawdown_correlation_to_existing_models, "correlation", "computed" if self.drawdown_correlation_to_existing_models is not None else "missing", 0, "portfolio", self.missing_reasons.get("drawdown_correlation_to_existing_models", "requires multi-model campaign")),
            "marginal_sharpe_contribution": MetricEntry(self.marginal_sharpe_contribution, "sharpe", "computed" if self.marginal_sharpe_contribution is not None else "missing", 0, "portfolio", self.missing_reasons.get("marginal_sharpe_contribution", "requires portfolio context")),
            "marginal_calmar_contribution": MetricEntry(self.marginal_calmar_contribution, "calmar", "computed" if self.marginal_calmar_contribution is not None else "missing", 0, "portfolio", self.missing_reasons.get("marginal_calmar_contribution", "requires portfolio context")),
            "marginal_drawdown_contribution": MetricEntry(self.marginal_drawdown_contribution, "USD", "computed" if self.marginal_drawdown_contribution is not None else "missing", 0, "portfolio", self.missing_reasons.get("marginal_drawdown_contribution", "requires portfolio context")),
            "marginal_cvar_contribution": MetricEntry(self.marginal_cvar_contribution, "USD", "computed" if self.marginal_cvar_contribution is not None else "missing", 0, "portfolio", self.missing_reasons.get("marginal_cvar_contribution", "requires portfolio context")),
            "risk_contribution": MetricEntry(self.risk_contribution, "%", "computed" if self.risk_contribution is not None else "missing", 0, "portfolio", self.missing_reasons.get("risk_contribution", "requires portfolio context")),
            "gross_exposure_contribution": MetricEntry(self.gross_exposure_contribution, "USD", "computed" if self.gross_exposure_contribution is not None else "missing", 0, "portfolio", self.missing_reasons.get("gross_exposure_contribution", "requires portfolio context")),
            "net_exposure_contribution": MetricEntry(self.net_exposure_contribution, "USD", "computed" if self.net_exposure_contribution is not None else "missing", 0, "portfolio", self.missing_reasons.get("net_exposure_contribution", "requires portfolio context")),
            "factor_exposures": MetricEntry(None, "dict", "missing", 0, "portfolio", self.missing_reasons.get("factor_exposures", "requires factor analysis")),
            "beta_to_benchmark": MetricEntry(self.beta_to_benchmark, "beta", "computed" if self.beta_to_benchmark is not None else "missing", 0, "portfolio", self.missing_reasons.get("beta_to_benchmark", "requires benchmark data")),
            "liquidity_overlap_with_existing_models": MetricEntry(self.liquidity_overlap_with_existing_models, "%", "computed" if self.liquidity_overlap_with_existing_models is not None else "missing", 0, "portfolio", self.missing_reasons.get("liquidity_overlap_with_existing_models", "requires multi-model campaign")),
            "crowding_overlap_score": MetricEntry(self.crowding_overlap_score, "score", "computed" if self.crowding_overlap_score is not None else "missing", 0, "portfolio", self.missing_reasons.get("crowding_overlap_score", "requires multi-model campaign")),
            "regime_complementarity_score": MetricEntry(self.regime_complementarity_score, "score", "computed" if self.regime_complementarity_score is not None else "missing", 0, "portfolio", self.missing_reasons.get("regime_complementarity_score", "requires multi-model campaign")),
            "crisis_period_contribution": MetricEntry(self.crisis_period_contribution, "USD", "computed" if self.crisis_period_contribution is not None else "missing", 0, "portfolio", self.missing_reasons.get("crisis_period_contribution", "requires crisis period data")),
        }
        groups.append(g5)

        # Group 6: Prediction Calibration Quality
        g6 = MetricGroup(group_name="prediction_calibration_quality")
        g6.metrics = {
            "ic": MetricEntry(self.ic, "correlation", "computed" if self.ic is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("ic", "requires signal/return pairs")),
            "rank_ic": MetricEntry(self.rank_ic, "correlation", "computed" if self.rank_ic is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("rank_ic", "requires signal/return pairs")),
            "icir": MetricEntry(self.icir, "ratio", "computed" if self.icir is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("icir", "requires signal/return pairs")),
            "brier_score": MetricEntry(self.brier_score, "score", "computed" if self.brier_score is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("brier_score", "requires signal/return pairs")),
            "log_loss": MetricEntry(self.log_loss, "loss", "computed" if self.log_loss is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("log_loss", "requires signal/return pairs")),
            "roc_auc": MetricEntry(self.roc_auc, "auc", "computed" if self.roc_auc is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("roc_auc", "requires signal/return pairs")),
            "pr_auc": MetricEntry(self.pr_auc, "auc", "computed" if self.pr_auc is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("pr_auc", "requires signal/return pairs")),
            "precision": MetricEntry(self.precision, "%", "computed" if self.precision is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("precision", "requires signal/return pairs")),
            "recall": MetricEntry(self.recall, "%", "computed" if self.recall is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("recall", "requires signal/return pairs")),
            "f1": MetricEntry(self.f1, "score", "computed" if self.f1 is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("f1", "requires signal/return pairs")),
            "false_positive_cost": MetricEntry(self.false_positive_cost, "USD", "computed" if self.false_positive_cost is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("false_positive_cost", "requires cost model")),
            "false_negative_cost": MetricEntry(self.false_negative_cost, "USD", "computed" if self.false_negative_cost is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("false_negative_cost", "requires cost model")),
            "prediction_decile_returns": MetricEntry(None, "list", "missing", self.num_trades, "backtest", self.missing_reasons.get("prediction_decile_returns", "requires signal/return pairs")),
            "signal_bucket_monotonicity": MetricEntry(self.signal_bucket_monotonicity, "score", "computed" if self.signal_bucket_monotonicity is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("signal_bucket_monotonicity", "requires signal/return pairs")),
            "calibration_error": MetricEntry(self.calibration_error, "error", "computed" if self.calibration_error is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("calibration_error", "requires signal/return pairs")),
            "expected_calibration_error": MetricEntry(self.expected_calibration_error, "error", "computed" if self.expected_calibration_error is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("expected_calibration_error", "requires signal/return pairs")),
            "confidence_vs_realized_accuracy": MetricEntry(self.confidence_vs_realized_accuracy, "correlation", "computed" if self.confidence_vs_realized_accuracy is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("confidence_vs_realized_accuracy", "requires confidence estimates")),
            "high_confident_trade_performance": MetricEntry(self.high_confident_trade_performance, "USD", "computed" if self.high_confident_trade_performance is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("high_confident_trade_performance", "requires confidence-tagged trades")),
            "low_confident_trade_performance": MetricEntry(self.low_confident_trade_performance, "USD", "computed" if self.low_confident_trade_performance is not None else "missing", self.num_trades, "backtest", self.missing_reasons.get("low_confident_trade_performance", "requires confidence-tagged trades")),
        }
        groups.append(g6)

        return groups


@dataclass
class CategoryScore:
    category: str
    score: float
    grade: str
    metrics_used: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ModelScorecard:
    model_id: str
    run_id: str
    overall_grade: str
    overall_score: float
    category_scores: List[CategoryScore] = field(default_factory=list)
    metrics: MetricValues = field(default_factory=MetricValues)
    metric_groups: List[MetricGroup] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


def _safe_std(values: List[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    variance = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def _compute_sharpe(returns: List[float], annualize: float = 252.0) -> Optional[float]:
    if len(returns) < 2:
        return None
    m = sum(returns) / len(returns)
    std = _safe_std(returns)
    if std is None or std == 0:
        return None
    result = m / std * math.sqrt(annualize)
    # Cap at reasonable values to avoid numerical artifacts
    if abs(result) > 100:
        return None
    return result


def _compute_sortino(returns: List[float], annualize: float = 252.0) -> Optional[float]:
    if len(returns) < 2:
        return None
    m = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if len(downside) < 1:
        return None
    downside_std = math.sqrt(sum(d ** 2 for d in downside) / len(downside))
    if downside_std == 0:
        return None
    result = m / downside_std * math.sqrt(annualize)
    if abs(result) > 100:
        return None
    return result


def _compute_max_drawdown(equity: List[float]) -> Optional[float]:
    if len(equity) < 2:
        return None
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = v - peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _compute_cvar(returns: List[float], alpha: float = 0.05) -> Optional[float]:
    if len(returns) < 20:
        return None
    sorted_returns = sorted(returns)
    cutoff = max(1, int(len(sorted_returns) * alpha))
    tail = sorted_returns[:cutoff]
    return sum(tail) / len(tail)


def _grade_from_score(score: float) -> str:
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B+"
    elif score >= 70:
        return "B"
    elif score >= 60:
        return "C"
    elif score >= 50:
        return "D"
    else:
        return "F"


def _score_metric(value, thresholds, higher_is_better=True):
    if value is None:
        return 50.0
    if higher_is_better:
        if value >= thresholds.get("excellent", float("inf")):
            return 95.0
        elif value >= thresholds.get("good", float("inf")):
            return 80.0
        elif value >= thresholds.get("acceptable", 0.0):
            return 65.0
        elif value >= thresholds.get("poor", float("-inf")):
            return 40.0
        else:
            return 20.0
    else:
        if value <= thresholds.get("excellent", float("-inf")):
            return 95.0
        elif value <= thresholds.get("good", float("-inf")):
            return 80.0
        elif value <= thresholds.get("acceptable", 0.0):
            return 65.0
        elif value <= thresholds.get("poor", float("inf")):
            return 40.0
        else:
            return 20.0


def calculate_metric_values(
    backtest_report,
    replay_result=None,
    event_outcomes=None,
    per_trade_pnls=None,
) -> MetricValues:
    m = MetricValues()
    m.net_return = backtest_report.get("net_pnl")
    m.gross_return = backtest_report.get("net_pnl")  # Simplified
    m.num_trades = backtest_report.get("num_trades", 0)

    if per_trade_pnls and len(per_trade_pnls) > 0:
        wins = [p for p in per_trade_pnls if p > 0]
        losses = [p for p in per_trade_pnls if p < 0]
        m.hit_rate = len(wins) / len(per_trade_pnls) if per_trade_pnls else None
        m.expectancy_per_trade = sum(per_trade_pnls) / len(per_trade_pnls)
        m.average_win = sum(wins) / len(wins) if wins else None
        m.average_loss = sum(losses) / len(losses) if losses else None
        m.win_loss_ratio = abs(m.average_win / m.average_loss) if m.average_loss and m.average_loss != 0 else None
        m.median_trade_return = sorted(per_trade_pnls)[len(per_trade_pnls) // 2] if per_trade_pnls else None

        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        m.profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

        equity = []
        running = 0.0
        for p in per_trade_pnls:
            running += p
            equity.append(running)
        m.sharpe = _compute_sharpe(per_trade_pnls)
        m.sortino = _compute_sortino(per_trade_pnls)
        m.max_drawdown = _compute_max_drawdown(equity)
        m.cvar_95 = _compute_cvar(per_trade_pnls)

        # Add missing reasons for computed metrics that returned None
        if m.sharpe is None:
            m.missing_reasons["sharpe"] = "insufficient data or zero variance"
        if m.sortino is None:
            m.missing_reasons["sortino"] = "insufficient downside data"
        if m.max_drawdown is None:
            m.missing_reasons["max_drawdown"] = "insufficient equity curve data"
        if m.cvar_95 is None:
            m.missing_reasons["cvar_95"] = "insufficient data for CVaR (need >= 20 trades)"

        # Compute additional drawdown metrics
        if equity:
            peak = equity[0]
            drawdowns = []
            for v in equity:
                if v > peak:
                    peak = v
                dd = v - peak
                drawdowns.append(dd)
            m.average_drawdown = sum(drawdowns) / len(drawdowns) if drawdowns else None
            m.median_drawdown = sorted(drawdowns)[len(drawdowns) // 2] if drawdowns else None
            m.ulcer_index = math.sqrt(sum(d ** 2 for d in drawdowns) / len(drawdowns)) if drawdowns else None
            
            # Add missing reasons for drawdown metrics that returned None
            if m.average_drawdown is None:
                m.missing_reasons["average_drawdown"] = "no drawdown data"
            if m.median_drawdown is None:
                m.missing_reasons["median_drawdown"] = "no drawdown data"
            if m.ulcer_index is None:
                m.missing_reasons["ulcer_index"] = "no drawdown data"

        # Consecutive losses
        max_consec = 0
        current_consec = 0
        for p in per_trade_pnls:
            if p < 0:
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            else:
                current_consec = 0
        m.max_consecutive_losses = max_consec

        # Downside deviation
        downside = [p for p in per_trade_pnls if p < 0]
        if downside:
            m.downside_deviation = math.sqrt(sum(d ** 2 for d in downside) / len(downside))
    else:
        expectancy = backtest_report.get("expectancy", 0.0)
        num_trades = max(m.num_trades, 1)
        synthetic_pnls = [expectancy] * num_trades
        m.sharpe = _compute_sharpe(synthetic_pnls)
        m.sortino = _compute_sortino(synthetic_pnls)
        m.max_drawdown = _compute_max_drawdown([sum(synthetic_pnls[:i+1]) for i in range(len(synthetic_pnls))])
        m.cvar_95 = _compute_cvar(synthetic_pnls)
        m.hit_rate = backtest_report.get("win_rate")
        m.expectancy_per_trade = expectancy
        m.profit_factor = None
        m.missing_reasons["profit_factor"] = "no per-trade ledger"
        m.missing_reasons["hit_rate"] = "estimated from backtest summary"
        
        # Add missing reasons for computed metrics that returned None
        if m.sharpe is None:
            m.missing_reasons["sharpe"] = "insufficient data or zero variance"
        if m.sortino is None:
            m.missing_reasons["sortino"] = "insufficient downside data"
        if m.max_drawdown is None:
            m.missing_reasons["max_drawdown"] = "insufficient equity curve data"
        if m.cvar_95 is None:
            m.missing_reasons["cvar_95"] = "insufficient data for CVaR (need >= 20 trades)"
        
        # Mark drawdown metrics as missing (not computed in synthetic path)
        for metric in ["average_drawdown", "median_drawdown", "ulcer_index", "max_consecutive_losses", "downside_deviation"]:
            if metric not in m.missing_reasons:
                m.missing_reasons[metric] = "requires per-trade ledger"

    latency_ms = backtest_report.get("measured_p99_ms") or backtest_report.get("backtest_latency_ms")
    if latency_ms and latency_ms > 0:
        m.slippage_bps = latency_ms * 0.5
        m.latency_order_to_ack = latency_ms * 1000  # Convert to microseconds
    else:
        m.slippage_bps = None
        m.missing_reasons["slippage_bps"] = "latency not available"

    if replay_result:
        lifecycle = replay_result.get("order_lifecycle_summary", {})
        submitted = lifecycle.get("submitted", 0)
        filled = lifecycle.get("filled", 0)
        m.fill_rate = (filled / submitted) if submitted > 0 else None
        m.adverse_selection_rate = replay_result.get("adverse_selection_ticks")
    else:
        m.fill_rate = None
        m.missing_reasons["fill_rate"] = "no replay lifecycle data"
        m.adverse_selection_rate = None
        m.missing_reasons["adverse_selection_rate"] = "no replay lifecycle data"

    expectancy = backtest_report.get("expectancy", 0.0)
    if latency_ms and expectancy and expectancy > 0:
        alpha_half_life_est = expectancy / max(latency_ms, 0.001)
        m.execution_latency_vs_alpha_half_life = latency_ms / alpha_half_life_est if alpha_half_life_est > 0 else None
        m.alpha_half_life = alpha_half_life_est
    else:
        m.execution_latency_vs_alpha_half_life = None
        m.missing_reasons["execution_latency_vs_alpha_half_life"] = "insufficient data"

    m.capacity_estimate = (m.net_return or 0.0) * 100 if m.net_return else None
    m.missing_reasons["capacity_estimate"] = "extrapolated from single-event PnL"

    m.walk_forward_efficiency = backtest_report.get("wfc_correlation")
    if m.walk_forward_efficiency is None:
        m.missing_reasons["walk_forward_efficiency"] = "WFC not run for single event"

    # Mark all robustness metrics as missing with reasons
    for metric in ["parameter_stability", "feature_stability", "regime_stability",
                   "fold_to_fold_return_dispersion", "fold_to_fold_sharpe_dispersion",
                   "fold_to_fold_drawdown_dispersion", "symbol_stability", "timeframe_stability",
                   "cost_sensitivity_score", "slippage_sensitivity_score", "capacity_sensitivity_score",
                   "turnover_sensitivity_score", "out_of_sample_decay", "in_sample_vs_out_of_sample_gap",
                   "probabilistic_sharpe_ratio", "deflated_sharpe_ratio", "pbo"]:
        m.missing_reasons[metric] = "requires multi-period campaign"

    # Mark all portfolio fit metrics as missing
    for metric in ["correlation_to_existing_models", "drawdown_correlation_to_existing_models",
                   "marginal_sharpe_contribution", "marginal_calmar_contribution",
                   "marginal_drawdown_contribution", "marginal_cvar_contribution",
                   "risk_contribution", "gross_exposure_contribution", "net_exposure_contribution",
                   "beta_to_benchmark", "liquidity_overlap_with_existing_models",
                   "crowding_overlap_score", "regime_complementarity_score", "crisis_period_contribution"]:
        m.missing_reasons[metric] = "requires multi-model campaign"

    # Mark all prediction calibration metrics as missing
    for metric in ["ic", "rank_ic", "icir", "brier_score", "log_loss", "roc_auc", "pr_auc",
                   "precision", "recall", "f1", "false_positive_cost", "false_negative_cost",
                   "signal_bucket_monotonicity", "calibration_error", "expected_calibration_error",
                   "confidence_vs_realized_accuracy", "high_confident_trade_performance",
                   "low_confident_trade_performance"]:
        m.missing_reasons[metric] = "requires signal/return pairs"

    # Mark latency metrics as missing (require CHI404 measurement)
    for metric in ["tick_to_send_trigger_us", "decision_to_send_trigger_us", "tick_to_send_us",
                   "decision_to_send_us", "rithmic_send_call_us", "cancel_to_send_us",
                   "replace_to_send_us", "send_to_ack_us", "cancel_to_ack_us", "replace_to_ack_us"]:
        m.missing_reasons[metric] = "requires CHI404 measurement"

    # Mark remaining net_alpha_quality metrics as missing
    for metric in ["cagr", "annualized_volatility", "calmar", "average_win", "average_loss",
                   "win_loss_ratio", "median_trade_return", "return_skew", "return_kurtosis",
                   "alpha_t_stat", "information_ratio"]:
        if metric not in m.missing_reasons:
            m.missing_reasons[metric] = "requires per-trade ledger with timestamps"

    # Mark remaining drawdown metrics as missing
    for metric in ["average_drawdown", "median_drawdown", "drawdown_duration_max",
                   "drawdown_duration_average", "time_under_water", "recovery_factor",
                   "drawdown_velocity", "worst_day", "worst_week", "worst_month",
                   "loss_clustering_score", "var_95", "var_99", "cvar_99", "tail_ratio"]:
        if metric not in m.missing_reasons:
            m.missing_reasons[metric] = "requires equity curve with timestamps"

    # Mark remaining execution realism metrics as missing
    for metric in ["average_slippage_per_trade", "median_slippage_per_trade", "spread_cost",
                   "spread_capture", "partial_fill_rate", "order_reject_rate", "cancel_replace_rate",
                   "queue_position_decay", "latency_event_to_signal", "latency_signal_to_order",
                   "latency_ack_to_fill", "turnover", "market_impact_estimate",
                   "capacity_at_10pct_edge_decay", "capacity_at_25pct_edge_decay", "capacity_at_50pct_edge_decay"]:
        if metric not in m.missing_reasons:
            m.missing_reasons[metric] = "requires replay lifecycle data"

    return m


def generate_model_scorecard(model_id, run_id, metrics):
    categories = []

    # Performance
    perf_scores = []
    perf_metrics = []
    for name, val, thresh, hib in [
        ("net_return", metrics.net_return, {"excellent": 100, "good": 10, "acceptable": 0, "poor": -50}, True),
        ("sharpe", metrics.sharpe, {"excellent": 3.0, "good": 1.5, "acceptable": 0.5, "poor": 0.0}, True),
        ("sortino", metrics.sortino, {"excellent": 4.0, "good": 2.0, "acceptable": 0.5, "poor": 0.0}, True),
        ("profit_factor", metrics.profit_factor, {"excellent": 3.0, "good": 1.5, "acceptable": 1.0, "poor": 0.5}, True),
        ("hit_rate", metrics.hit_rate, {"excellent": 0.7, "good": 0.55, "acceptable": 0.45, "poor": 0.3}, True),
        ("expectancy_per_trade", metrics.expectancy_per_trade, {"excellent": 10.0, "good": 2.0, "acceptable": 0.5, "poor": 0.0}, True),
    ]:
        perf_scores.append(_score_metric(val, thresh, hib))
        perf_metrics.append(name)
    categories.append(CategoryScore(
        category="Performance",
        score=sum(perf_scores) / len(perf_scores),
        grade=_grade_from_score(sum(perf_scores) / len(perf_scores)),
        metrics_used=perf_metrics,
    ))

    # Risk
    risk_scores = []
    risk_metrics = []
    for name, val, thresh, hib in [
        ("max_drawdown", metrics.max_drawdown, {"excellent": -10, "good": -50, "acceptable": -200, "poor": -500}, True),
        ("cvar_95", metrics.cvar_95, {"excellent": -5, "good": -20, "acceptable": -50, "poor": -100}, True),
    ]:
        risk_scores.append(_score_metric(val, thresh, hib))
        risk_metrics.append(name)
    categories.append(CategoryScore(
        category="Risk",
        score=sum(risk_scores) / len(risk_scores),
        grade=_grade_from_score(sum(risk_scores) / len(risk_scores)),
        metrics_used=risk_metrics,
    ))

    # Robustness
    robust_scores = []
    robust_metrics = []
    for name, val in [
        ("walk_forward_efficiency", metrics.walk_forward_efficiency),
        ("parameter_stability", metrics.parameter_stability),
        ("feature_stability", metrics.feature_stability),
        ("regime_stability", metrics.regime_stability),
    ]:
        robust_scores.append(_score_metric(val, {"excellent": 0.9, "good": 0.7, "acceptable": 0.5, "poor": 0.3}, True))
        robust_metrics.append(name)
    categories.append(CategoryScore(
        category="Robustness",
        score=sum(robust_scores) / len(robust_scores),
        grade=_grade_from_score(sum(robust_scores) / len(robust_scores)),
        metrics_used=robust_metrics,
        notes=["Single-event run: WFC/parameter/feature/regime stability require multi-period campaign"],
    ))

    # Execution Realism
    exec_scores = []
    exec_metrics = []
    for name, val, thresh, hib in [
        ("slippage_bps", metrics.slippage_bps, {"excellent": 0.5, "good": 2.0, "acceptable": 5.0, "poor": 10.0}, False),
        ("fill_rate", metrics.fill_rate, {"excellent": 0.95, "good": 0.8, "acceptable": 0.6, "poor": 0.4}, True),
        ("execution_latency_vs_alpha_half_life", metrics.execution_latency_vs_alpha_half_life, {"excellent": 0.1, "good": 0.3, "acceptable": 0.5, "poor": 0.8}, False),
        ("adverse_selection_rate", metrics.adverse_selection_rate, {"excellent": 0.1, "good": 0.5, "acceptable": 1.0, "poor": 2.0}, False),
        ("capacity_estimate", metrics.capacity_estimate, {"excellent": 10000, "good": 1000, "acceptable": 100, "poor": 10}, True),
    ]:
        exec_scores.append(_score_metric(val, thresh, hib))
        exec_metrics.append(name)
    categories.append(CategoryScore(
        category="Execution Realism",
        score=sum(exec_scores) / len(exec_scores),
        grade=_grade_from_score(sum(exec_scores) / len(exec_scores)),
        metrics_used=exec_metrics,
    ))

    # Portfolio Fit
    pf_scores = []
    pf_metrics = []
    for name, val, thresh, hib in [
        ("correlation_to_existing_models", metrics.correlation_to_existing_models, {"excellent": 0.1, "good": 0.3, "acceptable": 0.5, "poor": 0.8}, False),
        ("marginal_sharpe_contribution", metrics.marginal_sharpe_contribution, {"excellent": 0.5, "good": 0.2, "acceptable": 0.0, "poor": -0.2}, True),
    ]:
        pf_scores.append(_score_metric(val, thresh, hib))
        pf_metrics.append(name)
    categories.append(CategoryScore(
        category="Portfolio Fit",
        score=sum(pf_scores) / len(pf_scores),
        grade=_grade_from_score(sum(pf_scores) / len(pf_scores)),
        metrics_used=pf_metrics,
        notes=["Requires multi-model campaign for correlation/marginal Sharpe"],
    ))

    # Prediction Calibration
    cal_scores = []
    cal_metrics = []
    for name, val, thresh, hib in [
        ("prediction_calibration_ic", metrics.ic, {"excellent": 0.3, "good": 0.15, "acceptable": 0.05, "poor": 0.0}, True),
        ("prediction_calibration_brier", metrics.brier_score, {"excellent": 0.1, "good": 0.2, "acceptable": 0.25, "poor": 0.3}, False),
        ("prediction_calibration_ece", metrics.expected_calibration_error, {"excellent": 0.05, "good": 0.1, "acceptable": 0.15, "poor": 0.2}, False),
        ("high_confident_trade_performance", metrics.high_confident_trade_performance, {"excellent": 10.0, "good": 3.0, "acceptable": 1.0, "poor": 0.0}, True),
    ]:
        cal_scores.append(_score_metric(val, thresh, hib))
        cal_metrics.append(name)
    categories.append(CategoryScore(
        category="Prediction Calibration",
        score=sum(cal_scores) / len(cal_scores),
        grade=_grade_from_score(sum(cal_scores) / len(cal_scores)),
        metrics_used=cal_metrics,
        notes=["Requires signal/return pair analysis over multiple events"],
    ))

    # Walk-Forward
    wf_scores = []
    wf_metrics = []
    wf_scores.append(_score_metric(metrics.walk_forward_efficiency, {"excellent": 0.9, "good": 0.7, "acceptable": 0.5, "poor": 0.3}, True))
    wf_metrics.append("walk_forward_efficiency")
    categories.append(CategoryScore(
        category="Walk-Forward",
        score=sum(wf_scores) / len(wf_scores),
        grade=_grade_from_score(sum(wf_scores) / len(wf_scores)),
        metrics_used=wf_metrics,
    ))

    weights = {
        "Performance": 0.25,
        "Risk": 0.20,
        "Robustness": 0.15,
        "Execution Realism": 0.15,
        "Portfolio Fit": 0.10,
        "Prediction Calibration": 0.10,
        "Walk-Forward": 0.05,
    }
    overall_score = sum(c.score * weights.get(c.category, 0.1) for c in categories)
    overall_grade = _grade_from_score(overall_score)

    # Generate metric groups
    metric_groups = metrics.to_groups()

    return ModelScorecard(
        model_id=model_id,
        run_id=run_id,
        overall_grade=overall_grade,
        overall_score=round(overall_score, 2),
        category_scores=categories,
        metrics=metrics,
        metric_groups=metric_groups,
    )
