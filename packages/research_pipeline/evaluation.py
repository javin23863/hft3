"""Evaluate candidates via workbench backtest engine with optional VectorBT pre-filter."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from features_engine.src.model_registry import resolve_model_id

from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

logger = logging.getLogger(__name__)


def evaluate_model(
    candidate: CandidateModel,
    event_id: str,
    repo_root: Path,
    *,
    chi404_summary: Optional[Path] = None,
    seed: int = 42,
    gates: Optional[GateThresholds] = None,
    vectorbt_pre_filter: bool = False,
    vectorbt_only: bool = False,
) -> EvaluationResult:
    """Evaluate candidate via HftBacktest (WorkbenchEngine) with optional VectorBT pre-filter.

    When vectorbt_pre_filter=True, runs VectorBT filter first. If candidate
    fails VectorBT gates, returns early with rejection reason — avoiding
    expensive HftBacktest simulation.
    When vectorbt_only=True, skips HftBacktest entirely and returns VectorBT
    metrics directly.
    """
    gates = gates or GateThresholds(min_trades=0)

    if vectorbt_pre_filter or vectorbt_only:
        from backtest_pipeline.src.vectorbt_adapter import filter_candidates

        vbt_result = filter_candidates(
            candidates=[candidate],
            parsed=None,
            event_id=event_id,
            repo_root=repo_root,
        )
        if vbt_result.rejected:
            r = vbt_result.rejected[0]
            return EvaluationResult(
                candidate=candidate,
                event_id=event_id,
                net_pnl=0.0,
                num_trades=0,
                win_rate=0.0,
                expectancy=0.0,
                tail_loss=0.0,
                gates=gates,
                error=f"VectorBT rejection: {r.reject_reason} ({r.candidate_id})",
            )
        if vbt_only:
            promoted = vbt_result.promoted
            if not promoted:
                return EvaluationResult(
                    candidate=candidate,
                    event_id=event_id,
                    net_pnl=0.0,
                    num_trades=0,
                    win_rate=0.0,
                    expectancy=0.0,
                    tail_loss=0.0,
                    gates=gates,
                    error="VectorBT-only: no promoted candidates",
                )
            p = promoted[0]
            return EvaluationResult(
                candidate=candidate,
                event_id=event_id,
                net_pnl=p.vectorbt_results.get("net_return_pct", 0.0),
                num_trades=p.vectorbt_results.get("num_trades", 0),
                win_rate=p.vectorbt_results.get("win_rate", 0.0),
                expectancy=p.vectorbt_results.get("expectancy", 0.0),
                tail_loss=p.vectorbt_results.get("max_drawdown_pct", 0.0),
                gates=gates,
                workbench_out={"vectorbt_result": p.to_dict(), "vectorbt_only": True},
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
    except Exception as exc:
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
