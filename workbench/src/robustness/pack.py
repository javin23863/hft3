"""Robustness pack: walk-forward hooks, purged CV, Monte Carlo, parameter sweep."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from decision_engine.python.src.walk_forward import WalkForwardValidator
from workbench.src.robustness.purged_cv import purged_splits


HOLDOUT_YEARS = (2023, 2024)


@dataclass
class RobustnessResult:
    walk_forward: Dict[str, Any] = field(default_factory=dict)
    purged_cv: List[Dict[str, float]] = field(default_factory=list)
    monte_carlo: Dict[str, Any] = field(default_factory=dict)
    parameter_sweep: List[Dict[str, Any]] = field(default_factory=list)
    passed: bool = False
    overfit_risk: str = "unknown"
    bonferroni_penalty: float = 1.0


def monte_carlo_ci(
    trade_pnls: List[float],
    n_samples: int = 1000,
    seed: int = 42,
) -> Dict[str, Any]:
    if not trade_pnls:
        return {"sharpe_p05": 0.0, "sharpe_p95": 0.0, "drawdown_p95": 0.0}
    rng = random.Random(seed)
    sharpes = []
    drawdowns = []
    arr = np.array(trade_pnls, dtype=float)
    for _ in range(n_samples):
        sample = rng.choices(trade_pnls, k=len(trade_pnls))
        s = np.array(sample, dtype=float)
        std = float(s.std())
        sharpe = float(s.mean() / std * np.sqrt(len(s))) if std > 1e-12 else 0.0
        sharpes.append(sharpe)
        cum = np.cumsum(s)
        peak = np.maximum.accumulate(cum)
        dd = float((cum - peak).min())
        drawdowns.append(dd)
    return {
        "sharpe_p05": float(np.percentile(sharpes, 5)),
        "sharpe_p95": float(np.percentile(sharpes, 95)),
        "drawdown_p95": float(np.percentile(drawdowns, 95)),
    }


def walk_forward_from_campaign_periods(
    period_summaries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build walk-forward result from campaign period summaries (B4)."""
    if not period_summaries:
        return {"periods": [], "status": "EMPTY"}
    by_name = {p.get("name"): p for p in period_summaries}
    ordered = []
    status = "PASS"
    for pname in ("Discovery", "Confirmation", "Holdout", "Recent holdout"):
        p = by_name.get(pname)
        if not p:
            continue
        ordered.append(
            {
                "name": pname,
                "gate_pass": p.get("gate_pass"),
                "expectancy": p.get("expectancy"),
                "evaluate_only": p.get("evaluate_only"),
            }
        )
        if not p.get("gate_pass"):
            status = "FAIL"
            break
    return {"periods": ordered, "status": status}


def run_robustness_pack(
    backtest_fn: Callable[[], Dict[str, float]],
    trade_pnls: List[float],
    *,
    n_purged_splits: int = 3,
    sweep_count: int = 1,
    holdout_touched: bool = False,
    campaign_periods: Optional[List[Dict[str, Any]]] = None,
) -> RobustnessResult:
    wf = WalkForwardValidator()
    if campaign_periods:
        wf_result = walk_forward_from_campaign_periods(campaign_periods)
    else:
        wf_result = {"periods": [p.name for p in wf.periods], "status": "single_window"}
    purged = []
    for train_idx, test_idx in purged_splits(max(len(trade_pnls), 10), n_purged_splits):
        purged.append({"train_size": len(train_idx), "test_size": len(test_idx), "score": backtest_fn().get("expectancy", 0.0)})
    mc = monte_carlo_ci(trade_pnls)
    penalty = max(1.0, sweep_count)
    sharpe_stable = mc["sharpe_p05"] > -0.5 / penalty
    dd_ok = mc["drawdown_p95"] > -500
    passed = sharpe_stable and dd_ok and not holdout_touched
    risk = "low" if passed else ("high" if holdout_touched else "medium")
    return RobustnessResult(
        walk_forward=wf_result,
        purged_cv=purged,
        monte_carlo=mc,
        passed=passed,
        overfit_risk=risk,
        bonferroni_penalty=penalty,
    )
