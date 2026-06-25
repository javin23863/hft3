"""Evaluate candidates via workbench backtest engine."""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from features_engine.src.model_registry import resolve_model_id

from research_pipeline.cost_model import apply_costs
from research_pipeline.power_analysis import compute_effect_size, minimum_sample_size
from research_pipeline.regime import group_performance_by_label
from research_pipeline.statistics import (
    adjusted_p_value as multiple_test_adjusted_p_value,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)
from research_pipeline.types import (
    CandidateModel,
    EvaluationResult,
    GateThresholds,
    signed_tail_loss_value,
)


def parse_event_ids(values: str | Sequence[str]) -> list[str]:
    """Parse repeated and comma-separated event ids, preserving order."""
    raw_values = [values] if isinstance(values, str) else list(values)
    event_ids: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for part in str(raw).split(","):
            event_id = part.strip()
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            event_ids.append(event_id)
    if not event_ids:
        raise ValueError("at least one event id is required")
    return event_ids


def aggregate_evaluation_results(
    candidate: CandidateModel,
    event_results: Iterable[EvaluationResult],
    *,
    gates: GateThresholds,
    num_trials: int = 1,
    trial_sr_variance: float = 0.0,
    sr_benchmark: float = 0.0,
    alpha: float = 0.05,
    power: float = 0.8,
) -> EvaluationResult:
    """Aggregate per-event evaluation results into one risk-gated result.

    Sharpe and Sortino use each event's total net PnL as one diagnostic
    observation. Callers should compare only like-duration event windows when
    treating those ratios as statistical risk metrics.
    """
    results = list(event_results)
    if not results:
        return EvaluationResult(
            candidate=candidate,
            event_id="",
            net_pnl=0.0,
            num_trades=0,
            win_rate=0.0,
            expectancy=0.0,
            tail_loss=0.0,
            gates=gates,
            error="no_event_results",
            risk_metric_warning=(
                "risk_metric_gates_not_applied:no_event_results"
                if gates.requires_gateable_risk_metrics()
                else None
            ),
        )

    dated_results = [
        (_event_date_key(result.event_id), position, result)
        for position, result in enumerate(results)
    ]
    date_keys = [date_key for date_key, _, _ in dated_results if date_key is not None]
    missing_date_event_ids = [
        result.event_id for date_key, _, result in dated_results if date_key is None
    ]
    risk_metrics_gateable = len(date_keys) == len(results)
    duplicate_date_keys = (
        {
            date_key
            for date_key in date_keys
            if date_keys.count(date_key) > 1
        }
        if risk_metrics_gateable
        else set()
    )
    ordered_results = (
        [
            result
            for _, _, result in sorted(
                dated_results,
                key=lambda item: (item[0], item[1]),
            )
        ]
        if risk_metrics_gateable
        else results
    )
    net_pnls = [float(result.net_pnl) for result in ordered_results]
    total_trades = sum(int(result.num_trades) for result in ordered_results)
    total_pnl = sum(net_pnls)
    gross_pnl = sum(
        float(result.gross_pnl)
        if result.gross_pnl is not None
        else float(result.net_pnl)
        for result in ordered_results
    )
    cost_total = sum(float(result.cost_total) for result in ordered_results)
    cost_breakdown = _sum_cost_breakdowns(ordered_results)
    weighted_wins = sum(
        float(result.win_rate) * int(result.num_trades)
        for result in ordered_results
    )
    win_rate = weighted_wins / total_trades if total_trades > 0 else 0.0
    expectancy = total_pnl / total_trades if total_trades > 0 else 0.0
    sharpe = _sharpe(net_pnls)
    sortino = _sortino(net_pnls)
    max_drawdown = _max_drawdown(net_pnls)
    pnl_series = [
        value
        for result in ordered_results
        for value in (result.pnl_series or [float(result.net_pnl)])
    ]
    gross_pnl_series = [
        value
        for result in ordered_results
        for value in (result.gross_pnl_series or [])
    ]
    edge_observations = pnl_series or net_pnls
    skew, kurtosis = _moments(edge_observations)
    effect_size = compute_effect_size(sharpe or 0.0, sr_benchmark)
    if effect_size > 0.0:
        required_sample_size = minimum_sample_size(effect_size, alpha, power)
        sample_size_pass = len(edge_observations) >= required_sample_size
    else:
        required_sample_size = None
        sample_size_pass = False if edge_observations else True
    event_payloads = [
        {
            "event_id": result.event_id,
            "gross_pnl": result.gross_pnl,
            "net_pnl": result.net_pnl,
            "cost_total": result.cost_total,
            "num_trades": result.num_trades,
            "win_rate": result.win_rate,
            "expectancy": result.expectancy,
            "tail_loss": signed_tail_loss_value(result.tail_loss),
            "error": result.error,
            "passes": _passes_basic_event_gates(result, gates),
            "risk_metric_warning": _event_risk_metric_warning(
                result.event_id,
                duplicate_date_keys,
            ),
        }
        for result in ordered_results
    ]
    errors = [
        f"{result.event_id}:{result.error}"
        for result in ordered_results
        if result.error
    ]
    risk_metrics_gateable = risk_metrics_gateable and not errors and len(net_pnls) >= 2
    non_gateable_reasons: list[str] = []
    if len(net_pnls) < 2:
        non_gateable_reasons.append("insufficient_event_count")
    if missing_date_event_ids:
        non_gateable_reasons.append("missing_event_date")
    if errors:
        non_gateable_reasons.append("event_errors")
    risk_metric_warning = (
        f"risk_metric_gates_not_applied:{','.join(non_gateable_reasons)}"
        if gates.requires_gateable_risk_metrics() and not risk_metrics_gateable
        else None
    )
    result = EvaluationResult(
        candidate=candidate,
        event_id=",".join(result.event_id for result in ordered_results),
        net_pnl=total_pnl,
        num_trades=total_trades,
        win_rate=win_rate,
        expectancy=expectancy,
        tail_loss=_worst_signed_tail_pnl(ordered_results),
        gates=gates,
        gross_pnl=gross_pnl,
        cost_total=cost_total,
        cost_breakdown=cost_breakdown,
        pnl_series=pnl_series,
        gross_pnl_series=gross_pnl_series,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        required_sample_size=required_sample_size,
        sample_size_pass=sample_size_pass,
        skew=skew,
        kurtosis=kurtosis,
        cvar_95=_cvar(edge_observations, 0.95),
        cvar_99=_cvar(edge_observations, 0.99),
        tail_ratio=_tail_ratio(edge_observations),
        pbo=_max_optional(result.pbo for result in ordered_results),
        performance_degradation=_max_optional(
            result.performance_degradation for result in ordered_results
        ),
        probability_of_loss=_max_optional(
            result.probability_of_loss for result in ordered_results
        ),
        risk_metrics_source=(
            "cross_event_net_pnl_chronological"
            if risk_metrics_gateable
            else "cross_event_net_pnl_diagnostic"
        ),
        risk_metrics_gateable=risk_metrics_gateable,
        risk_metric_warning=risk_metric_warning,
        event_results=event_payloads,
        error=";".join(errors) if errors else None,
    )
    return refresh_selection_bias_metrics(
        result,
        num_trials=num_trials,
        trial_sr_variance=trial_sr_variance,
        sr_benchmark=sr_benchmark,
    )


_EVENT_DATE_RE = re.compile(r"(20\d{2})[_-](\d{2})[_-](\d{2})")


def _event_date_key(event_id: str) -> tuple[int, int, int] | None:
    match = _EVENT_DATE_RE.search(event_id)
    if not match:
        return None
    year, month, day = match.groups()
    return int(year), int(month), int(day)


def _event_risk_metric_warning(
    event_id: str,
    duplicate_date_keys: set[tuple[int, int, int]],
) -> str | None:
    date_key = _event_date_key(event_id)
    if date_key is None:
        return "missing_event_date"
    if date_key in duplicate_date_keys:
        return "same_date_event_window"
    return None


def _worst_signed_tail_pnl(results: Sequence[EvaluationResult]) -> float:
    return min(signed_tail_loss_value(result.tail_loss) for result in results)


def _passes_basic_event_gates(result: EvaluationResult, gates: GateThresholds) -> bool:
    if result.error:
        return False
    return gates.passes(
        result.net_pnl,
        result.num_trades,
        result.tail_loss,
        result.win_rate,
        include_risk_metrics=False,
        include_edge_metrics=False,
    )


def evaluate_candidate_events(
    candidate: CandidateModel,
    event_ids: Sequence[str],
    repo_root: Path,
    *,
    chi404_summary: Optional[Path] = None,
    seed: int = 42,
    gates: Optional[GateThresholds] = None,
    num_trials: int = 1,
    trial_sr_variance: float = 0.0,
    sr_benchmark: float = 0.0,
    alpha: float = 0.05,
    power: float = 0.8,
    cost_config: Optional[Mapping[str, Any]] = None,
) -> EvaluationResult:
    """Evaluate one candidate over one or more events and aggregate risk metrics."""
    gates = gates or GateThresholds(min_trades=0)
    results = [
        evaluate_model(
            candidate,
            event_id,
            repo_root,
            chi404_summary=chi404_summary,
            seed=seed,
            gates=gates,
            num_trials=num_trials,
            trial_sr_variance=trial_sr_variance,
            sr_benchmark=sr_benchmark,
            alpha=alpha,
            power=power,
            cost_config=cost_config,
        )
        for event_id in event_ids
    ]
    return aggregate_evaluation_results(
        candidate,
        results,
        gates=gates,
        num_trials=num_trials,
        trial_sr_variance=trial_sr_variance,
        sr_benchmark=sr_benchmark,
        alpha=alpha,
        power=power,
    )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _numeric_sequence(value: Any) -> List[float]:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, Iterable):
        return []
    out: List[float] = []
    for item in value:
        if isinstance(item, Iterable) and not isinstance(item, (str, bytes, Mapping)):
            out.extend(_numeric_sequence(item))
            continue
        parsed = _as_float(item)
        if parsed is not None:
            out.append(parsed)
    return out


def _first_float(*mappings: Mapping[str, Any], keys: Sequence[str]) -> Optional[float]:
    for mapping in mappings:
        for key in keys:
            parsed = _as_float(mapping.get(key))
            if parsed is not None:
                return parsed
    return None


def _find_pnl_series(out: Mapping[str, Any]) -> List[float]:
    report = _as_mapping(out.get("report"))
    diag = _as_mapping(out.get("diagnostics"))
    result = _as_mapping(out.get("result"))
    keys = (
        "gross_pnl_series",
        "pnl_series",
        "net_pnl_series",
        "trade_pnls",
        "trade_pnl",
        "returns",
        "return_series",
    )
    for mapping in (report, diag, result, out):
        for key in keys:
            series = _numeric_sequence(mapping.get(key))
            if series:
                return series
    return []


def _moments(values: Sequence[float]) -> tuple[Optional[float], Optional[float]]:
    if len(values) < 2:
        return None, None
    avg = _mean(values)
    centered = [value - avg for value in values]
    variance = sum(value * value for value in centered) / len(centered)
    if variance <= 0.0:
        return 0.0, 3.0
    stdev = math.sqrt(variance)
    skew = sum((value / stdev) ** 3 for value in centered) / len(centered)
    kurt = sum((value / stdev) ** 4 for value in centered) / len(centered)
    return skew, kurt


def _cvar(values: Sequence[float], confidence: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    tail_count = max(1, math.ceil(len(ordered) * (1.0 - confidence)))
    lower_tail = ordered[:tail_count]
    lower_mean = sum(lower_tail) / len(lower_tail)
    return max(0.0, -lower_mean)


def _tail_ratio(values: Sequence[float], quantile: float = 0.05) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    tail_count = max(1, math.ceil(len(ordered) * quantile))
    lower = ordered[:tail_count]
    upper = ordered[-tail_count:]
    upper_gain = sum(max(0.0, value) for value in upper) / len(upper)
    lower_loss = sum(max(0.0, -value) for value in lower) / len(lower)
    if lower_loss <= 0.0:
        if upper_gain > 0.0:
            return math.inf
        return None
    return upper_gain / lower_loss


def _max_optional(values: Iterable[float | None]) -> float | None:
    finite = [float(value) for value in values if value is not None]
    return max(finite) if finite else None


def _sum_cost_breakdowns(results: Sequence[EvaluationResult]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for result in results:
        for key, value in result.cost_breakdown.items():
            totals[key] = totals.get(key, 0.0) + float(value)
    if totals and "total" not in totals:
        totals["total"] = sum(totals.values())
    return totals


def _error_result(
    *,
    candidate: CandidateModel,
    event_id: str,
    gates: GateThresholds,
    error: str,
) -> EvaluationResult:
    return EvaluationResult(
        candidate=candidate,
        event_id=event_id,
        net_pnl=0.0,
        num_trades=0,
        win_rate=0.0,
        expectancy=0.0,
        tail_loss=0.0,
        gates=gates,
        error=error,
        gross_pnl=0.0,
    )


def refresh_selection_bias_metrics(
    result: EvaluationResult,
    *,
    num_trials: int,
    trial_sr_variance: float,
    sr_benchmark: float = 0.0,
) -> EvaluationResult:
    """Refresh PSR/DSR after the full candidate batch is known."""
    result.num_trials = max(1, int(num_trials))
    result.trial_sr_variance = max(0.0, float(trial_sr_variance))
    sample_size = len(result.pnl_series) if result.pnl_series else result.num_trades
    if result.sharpe is None or sample_size < 2:
        result.psr = 0.0
        result.dsr = 0.0
        result.adjusted_p_value = 1.0
        return result
    skew = result.skew if result.skew is not None else 0.0
    kurtosis = result.kurtosis if result.kurtosis is not None else 3.0
    try:
        result.psr = probabilistic_sharpe_ratio(
            result.sharpe,
            sr_benchmark,
            sample_size,
            skew,
            kurtosis,
        )
        result.dsr = deflated_sharpe_ratio(
            result.sharpe,
            sr_benchmark,
            sample_size,
            skew,
            kurtosis,
            result.trial_sr_variance,
            result.num_trials,
        )
        result.adjusted_p_value = multiple_test_adjusted_p_value(
            1.0 - result.psr,
            result.num_trials,
            method="holm",
        )
    except ValueError:
        result.psr = 0.0
        result.dsr = 0.0
        result.adjusted_p_value = 1.0
    return result


def evaluate_model(
    candidate: CandidateModel,
    event_id: str,
    repo_root: Path,
    *,
    chi404_summary: Optional[Path] = None,
    seed: int = 42,
    gates: Optional[GateThresholds] = None,
    num_trials: int = 1,
    trial_sr_variance: float = 0.0,
    sr_benchmark: float = 0.0,
    alpha: float = 0.05,
    power: float = 0.8,
    cost_config: Optional[Mapping[str, Any]] = None,
    regime_labels: Optional[Sequence[str]] = None,
    instrument_labels: Optional[Sequence[str]] = None,
    validation_summary: Optional[Mapping[str, Any]] = None,
) -> EvaluationResult:
    """Evaluate candidate via HftBacktest (WorkbenchEngine)."""
    gates = gates or GateThresholds(min_trades=0)

    try:
        model_id = resolve_model_id(candidate.model_id)
    except KeyError as exc:
        return _error_result(candidate=candidate, event_id=event_id, gates=gates, error=str(exc))

    try:
        from workbench.src.run.engine import WorkbenchEngine

        engine = WorkbenchEngine(repo_root)
        out: Dict[str, Any] = engine.run(
            model_id,
            event_id,
            chi404_summary=chi404_summary,
            seed=seed,
            skip_history_gate=True,
            strategy_params=dict(candidate.strategy_params),
        )
    except Exception as exc:
        print(
            f"evaluate_model failed for {candidate.candidate_id} ({candidate.model_id}): {exc}",
            file=sys.stderr,
        )
        return _error_result(candidate=candidate, event_id=event_id, gates=gates, error=str(exc))

    report = _as_mapping(out.get("report"))
    diag = _as_mapping(out.get("diagnostics"))
    reported_net_pnl = _first_float(report, diag, keys=("net_pnl",)) or 0.0
    num_trades = int(_first_float(report, diag, keys=("num_trades",)) or 0)
    gross_pnl = _first_float(report, diag, keys=("gross_pnl", "raw_pnl", "pnl"))
    if gross_pnl is None:
        gross_pnl = reported_net_pnl
    explicit_gross_series = _find_pnl_series(out)
    gross_series = explicit_gross_series
    if not gross_series and (gross_pnl != 0.0 or num_trades > 0):
        gross_series = [gross_pnl]

    cost_config_for_run = dict(cost_config or {})
    if not explicit_gross_series and num_trades > 1:
        cost_config_for_run.setdefault("quantity", num_trades)
    net_series, cost_breakdown = apply_costs(
        gross_series,
        config=cost_config_for_run,
        market_data=out,
    )
    net_pnl = float(sum(net_series)) if net_series else reported_net_pnl
    cost_total = max(0.0, float(sum(gross_series) - net_pnl)) if gross_series else 0.0
    cost_breakdown = {str(key): float(value) for key, value in cost_breakdown.items()}
    cost_breakdown.setdefault("total", cost_total)

    if num_trades <= 0:
        num_trades = len(net_series)
    win_rate = float(diag.get("win_rate", 0.0))
    if net_series:
        win_rate = sum(1 for value in net_series if value > 0.0) / len(net_series)
    expectancy = float(diag.get("expectancy", report.get("expectancy", 0.0)))
    if net_series:
        expectancy = sum(net_series) / len(net_series)
    tail_loss = signed_tail_loss_value(float(diag.get("tail_loss", 0.0)))
    if net_series and not tail_loss:
        tail_loss = min(0.0, min(net_series))

    sharpe = _sharpe(net_series)
    sortino = _sortino(net_series)
    skew, kurtosis = _moments(net_series)
    effect_size = compute_effect_size(sharpe or 0.0, sr_benchmark)
    if effect_size > 0.0:
        required_sample_size = minimum_sample_size(effect_size, alpha, power)
        sample_size_pass = len(net_series) >= required_sample_size
    else:
        required_sample_size = None
        sample_size_pass = False if net_series else True

    regime_metrics = (
        group_performance_by_label(net_series, regime_labels)
        if regime_labels is not None
        else {}
    )
    instrument_metrics = (
        group_performance_by_label(net_series, instrument_labels)
        if instrument_labels is not None
        else {}
    )
    validation_summary = _as_mapping(validation_summary)

    risk_metrics_gateable = len(net_series) >= 2
    risk_metric_warning = (
        "risk_metric_gates_not_applied:insufficient_pnl_observations"
        if gates.requires_gateable_risk_metrics() and not risk_metrics_gateable
        else None
    )
    result = EvaluationResult(
        candidate=candidate,
        event_id=event_id,
        net_pnl=net_pnl,
        num_trades=num_trades,
        win_rate=win_rate,
        expectancy=expectancy,
        tail_loss=tail_loss,
        gates=gates,
        workbench_out=out,
        gross_pnl=float(sum(gross_series)) if gross_series else gross_pnl,
        cost_total=cost_total,
        cost_breakdown=cost_breakdown,
        pnl_series=list(net_series),
        gross_pnl_series=list(gross_series),
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=_max_drawdown(net_series),
        risk_metrics_source="pnl_series" if risk_metrics_gateable else "single_run_diagnostic",
        risk_metrics_gateable=risk_metrics_gateable,
        risk_metric_warning=risk_metric_warning,
        required_sample_size=required_sample_size,
        sample_size_pass=sample_size_pass,
        skew=skew,
        kurtosis=kurtosis,
        cvar_95=_cvar(net_series, 0.95),
        cvar_99=_cvar(net_series, 0.99),
        tail_ratio=_tail_ratio(net_series),
        turnover=_first_float(report, diag, keys=("turnover", "avg_turnover")),
        avg_trade_duration=_first_float(
            report,
            diag,
            keys=("avg_trade_duration", "avg_trade_duration_ms"),
        ),
        pbo=_as_float(validation_summary.get("pbo")),
        performance_degradation=_as_float(validation_summary.get("median_performance_degradation")),
        probability_of_loss=_as_float(validation_summary.get("probability_of_loss")),
        regime_metrics=regime_metrics,
        instrument_metrics=instrument_metrics,
    )
    return refresh_selection_bias_metrics(
        result,
        num_trials=num_trials,
        trial_sr_variance=trial_sr_variance,
        sr_benchmark=sr_benchmark,
    )


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return variance ** 0.5


def _sharpe(pnls: Sequence[float]) -> float:
    if len(pnls) < 2:
        return 0.0
    std = _stddev(pnls)
    if std == 0.0:
        mean = _mean(pnls)
        if mean > 0.0:
            return 1e9
        if mean < 0.0:
            return -1e9
        return 0.0
    return _mean(pnls) / std


def _sortino(pnls: Sequence[float]) -> float:
    downside = [value for value in pnls if value < 0.0]
    mean = _mean(pnls)
    if not downside:
        if mean > 0.0:
            return 1e9
        return 0.0
    downside_deviation = (sum(value * value for value in downside) / len(downside)) ** 0.5
    if downside_deviation == 0.0:
        return 0.0
    return mean / downside_deviation


def _max_drawdown(pnls: Sequence[float]) -> float:
    if not pnls:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd
