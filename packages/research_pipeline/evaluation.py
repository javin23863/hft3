"""Evaluate candidates via workbench backtest engine."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from features_engine.src.model_registry import resolve_model_id

from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds


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
) -> EvaluationResult:
    """Aggregate per-event evaluation results into one risk-gated result."""
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
        )
    net_pnls = [float(result.net_pnl) for result in results]
    total_trades = sum(int(result.num_trades) for result in results)
    total_pnl = sum(net_pnls)
    weighted_wins = sum(float(result.win_rate) * int(result.num_trades) for result in results)
    win_rate = weighted_wins / total_trades if total_trades > 0 else 0.0
    expectancy = total_pnl / total_trades if total_trades > 0 else 0.0
    sharpe = _sharpe(net_pnls)
    sortino = _sortino(net_pnls)
    max_drawdown = _max_drawdown(net_pnls)
    event_payloads = [
        {
            "event_id": result.event_id,
            "net_pnl": result.net_pnl,
            "num_trades": result.num_trades,
            "win_rate": result.win_rate,
            "expectancy": result.expectancy,
            "tail_loss": result.tail_loss,
            "error": result.error,
            "passes": result.passes_all_gates(),
        }
        for result in results
    ]
    errors = [f"{result.event_id}:{result.error}" for result in results if result.error]
    return EvaluationResult(
        candidate=candidate,
        event_id=",".join(result.event_id for result in results),
        net_pnl=total_pnl,
        num_trades=total_trades,
        win_rate=win_rate,
        expectancy=expectancy,
        tail_loss=_worst_signed_tail_pnl(results),
        gates=gates,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        risk_metrics_source="cross_event_net_pnl_input_order_diagnostic",
        risk_metrics_gateable=False,
        event_results=event_payloads,
        error=";".join(errors) if errors else None,
    )


def _worst_signed_tail_pnl(results: Sequence[EvaluationResult]) -> float:
    return min(float(result.tail_loss) for result in results)


def evaluate_candidate_events(
    candidate: CandidateModel,
    event_ids: Sequence[str],
    repo_root: Path,
    *,
    chi404_summary: Optional[Path] = None,
    seed: int = 42,
    gates: Optional[GateThresholds] = None,
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
        )
        for event_id in event_ids
    ]
    return aggregate_evaluation_results(candidate, results, gates=gates)


def evaluate_model(
    candidate: CandidateModel,
    event_id: str,
    repo_root: Path,
    *,
    chi404_summary: Optional[Path] = None,
    seed: int = 42,
    gates: Optional[GateThresholds] = None,
) -> EvaluationResult:
    """Evaluate candidate via HftBacktest (WorkbenchEngine)."""
    gates = gates or GateThresholds(min_trades=0)

    try:
        model_id = resolve_model_id(candidate.model_id)
    except KeyError as exc:
        return EvaluationResult(
            candidate=candidate,
            event_id=event_id,
            net_pnl=0.0,
            num_trades=0,
            win_rate=0.0,
            expectancy=0.0,
            tail_loss=0.0,
            gates=gates,
            error=str(exc),
        )

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
        print(f"evaluate_model failed for {candidate.candidate_id} ({candidate.model_id}): {exc}", file=sys.stderr)
        return EvaluationResult(
            candidate=candidate,
            event_id=event_id,
            net_pnl=0.0,
            num_trades=0,
            win_rate=0.0,
            expectancy=0.0,
            tail_loss=0.0,
            gates=gates,
            error=str(exc),
        )

    report = out.get("report") or {}
    diag = out.get("diagnostics") or {}
    net_pnl = float(report.get("net_pnl", diag.get("net_pnl", 0.0)))
    num_trades = int(report.get("num_trades", diag.get("num_trades", 0)))
    win_rate = float(diag.get("win_rate", 0.0))
    expectancy = float(diag.get("expectancy", report.get("expectancy", 0.0)))
    tail_loss = float(diag.get("tail_loss", 0.0))

    return EvaluationResult(
        candidate=candidate,
        event_id=event_id,
        net_pnl=net_pnl,
        num_trades=num_trades,
        win_rate=win_rate,
        expectancy=expectancy,
        tail_loss=tail_loss,
        gates=gates,
        workbench_out=out,
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
    downside_std = _stddev(downside)
    if downside_std == 0.0:
        if downside:
            return 0.0
        mean = _mean(pnls)
        return 1e9 if mean > 0.0 else 0.0
    return _mean(pnls) / downside_std


def _max_drawdown(pnls: Sequence[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd
