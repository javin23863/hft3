"""Evaluate candidates via workbench backtest engine."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from features_engine.src.model_registry import resolve_model_id

from research_pipeline.continuous_evaluation import (
    evaluate_continuous_from_candidate,
    is_continuous_candidate,
)
from research_pipeline.data_quality import NoOHLCVDataError, classify_evaluation_error
from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds


def evaluate_model(
    candidate: CandidateModel,
    event_id: str,
    repo_root: Path,
    *,
    chi404_summary: Optional[Path] = None,
    seed: int = 42,
    gates: Optional[GateThresholds] = None,
) -> EvaluationResult:
    """Evaluate candidate via workbench (event lane) or continuous evaluation (Phase 6)."""
    gates = gates or GateThresholds(min_trades=0)

    if is_continuous_candidate(candidate):
        return evaluate_continuous_from_candidate(
            candidate,
            event_id,
            repo_root,
            gates=gates,
            seed=seed,
        )

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
    except NoOHLCVDataError as exc:
        failure_class, message = classify_evaluation_error(exc)
        print(
            f"evaluate_model data_quality skip for {candidate.candidate_id} ({candidate.model_id}): {message}",
            file=sys.stderr,
        )
        return EvaluationResult(
            candidate=candidate,
            event_id=event_id,
            net_pnl=0.0,
            num_trades=0,
            win_rate=0.0,
            expectancy=0.0,
            tail_loss=0.0,
            gates=gates,
            error=message,
            failure_class=failure_class,
        )
    except Exception as exc:
        failure_class, message = classify_evaluation_error(exc)
        print(f"evaluate_model failed for {candidate.candidate_id} ({candidate.model_id}): {message}", file=sys.stderr)
        return EvaluationResult(
            candidate=candidate,
            event_id=event_id,
            net_pnl=0.0,
            num_trades=0,
            win_rate=0.0,
            expectancy=0.0,
            tail_loss=0.0,
            gates=gates,
            error=message,
            failure_class=failure_class,
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
