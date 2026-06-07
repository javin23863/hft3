"""Evaluate candidates via workbench backtest engine."""

from __future__ import annotations

import sys
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from features_engine.src.model_registry import resolve_model_id

from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds


def _first_float(*values: Any) -> Optional[float]:
    for value in values:
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            return numeric
    return None


def _required_float(name: str, invalid_metrics: List[str], *values: Any) -> float:
    for value in values:
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            return numeric
        invalid_metrics.append(name)
        return 0.0
    return 0.0


def _required_int(name: str, invalid_metrics: List[str], *values: Any) -> int:
    for value in values:
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            return int(numeric)
        invalid_metrics.append(name)
        return 0
    return 0


def _drawdown_bps(report: Dict[str, Any], diag: Dict[str, Any]) -> Optional[float]:
    bps = _first_float(
        diag.get("drawdown_bps"),
        diag.get("max_drawdown_bps"),
        diag.get("proxy_max_drawdown_bps"),
        report.get("drawdown_bps"),
        report.get("max_drawdown_bps"),
        report.get("proxy_max_drawdown_bps"),
    )
    if bps is not None:
        return abs(bps)
    pct = _first_float(diag.get("max_drawdown_pct"), report.get("max_drawdown_pct"))
    if pct is None:
        return None
    return abs(pct) * 100.0


def _avg_latency_us(candidate: CandidateModel, report: Dict[str, Any], diag: Dict[str, Any]) -> Optional[float]:
    execution_quality = candidate.metadata.get("execution_quality")
    if not isinstance(execution_quality, dict):
        execution_quality = {}
    latency_authority = report.get("latency_authority")
    if not isinstance(latency_authority, dict):
        latency_authority = {}
    return _first_float(
        execution_quality.get("avg_latency_us"),
        execution_quality.get("mean_latency_us"),
        execution_quality.get("mean_submit_ack_us"),
        diag.get("avg_latency_us"),
        diag.get("mean_latency_us"),
        report.get("avg_latency_us"),
        report.get("mean_latency_us"),
        latency_authority.get("avg_latency_us"),
        latency_authority.get("mean_latency_us"),
    )


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
    invalid_metrics: List[str] = []
    net_pnl = _required_float("net_pnl", invalid_metrics, report.get("net_pnl"), diag.get("net_pnl"))
    num_trades = _required_int("num_trades", invalid_metrics, report.get("num_trades"), diag.get("num_trades"))
    win_rate = _required_float("win_rate", invalid_metrics, diag.get("win_rate"), report.get("win_rate"))
    expectancy = _required_float("expectancy", invalid_metrics, diag.get("expectancy"), report.get("expectancy"))
    tail_loss = _required_float("tail_loss", invalid_metrics, diag.get("tail_loss"), report.get("tail_loss"))
    sharpe = _first_float(
        diag.get("sharpe"),
        diag.get("sharpe_ratio"),
        diag.get("proxy_sharpe"),
        report.get("sharpe"),
        report.get("sharpe_ratio"),
        report.get("proxy_sharpe"),
    )
    drawdown_bps = _drawdown_bps(report, diag)
    avg_latency_us = _avg_latency_us(candidate, report, diag)

    return EvaluationResult(
        candidate=candidate,
        event_id=event_id,
        net_pnl=net_pnl,
        num_trades=num_trades,
        win_rate=win_rate,
        expectancy=expectancy,
        tail_loss=tail_loss,
        gates=gates,
        sharpe=sharpe,
        drawdown_bps=drawdown_bps,
        avg_latency_us=avg_latency_us,
        workbench_out=out,
        error=(
            f"non_finite_metric: {', '.join(sorted(set(invalid_metrics)))}"
            if invalid_metrics
            else None
        ),
    )
