"""Continuous CME lane evaluation and alpha validation (Phase 6 §10).

Routes continuous_microstructure candidates through cost-adjusted, execution-
realistic, statistically deflated testing. Produces the PDF §10.1 metric bundle:
gross/net PnL, fill-adjusted PnL, Sharpe, Sortino, DSR, PSR, PBO, CVaR, tail
ratio, skew, kurtosis, session/regime breakdowns.

The event-lane ``evaluation.py`` delegates here when a candidate is tagged
``lane == "continuous_microstructure"`` or resolves to a continuous-eligible
registry slug.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from features_engine.src.model_registry import (
    continuous_eligible_slugs,
    get_continuous_model_entry,
)
from research_pipeline.cost_model import (
    ContinuousCostBreakdown,
    CostModelConfig,
    apply_continuous_costs,
    cost_adjusted_returns,
    micro_standard_cost_config,
    standard_contract_cost_config,
)
from research_pipeline.cross_validation import (
    cscv_pbo,
    walk_forward_eval,
)
from research_pipeline.power_analysis import power_summary
from research_pipeline.statistics import summary_metrics
from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

CONTINUOUS_LANE_TAG = "continuous_microstructure"


def is_continuous_candidate(candidate: CandidateModel | Mapping[str, Any]) -> bool:
    """True when the candidate belongs to the continuous microstructure lane.

    Detection order: explicit ``lane`` metadata, registry ``kind``, then
    membership in ``continuous_eligible_slugs``.
    """
    meta = getattr(candidate, "metadata", None) or {}
    if isinstance(candidate, Mapping):
        meta = candidate.get("metadata") or {}
        model_id = candidate.get("model_id")
        lane = candidate.get("lane")
    else:
        model_id = candidate.model_id
        lane = meta.get("lane")
    if lane == CONTINUOUS_LANE_TAG:
        return True
    if model_id in continuous_eligible_slugs():
        return True
    try:
        entry = get_continuous_model_entry(str(model_id))
    except KeyError:
        return False
    return entry.get("kind") == CONTINUOUS_LANE_TAG


def _cost_config_for_candidate(candidate: Mapping[str, Any]) -> CostModelConfig:
    """Pick a cost model preset from candidate metadata or relationship family."""
    family = str(candidate.get("model_family") or candidate.get("edge_family_id") or "")
    source_root = str(candidate.get("source_root") or "")
    if "micro" in family or source_root.upper().startswith("M"):
        return micro_standard_cost_config()
    return standard_contract_cost_config()


SESSION_BUCKETS = ("asia", "europe", "us_morning", "us_afternoon", "settlement_roll")
REGIME_BUCKETS = ("high_vol", "low_vol", "high_liquidity", "low_liquidity", "roll_week", "inventory_proximity", "macro_proximity")


def _bucket_summary(returns_by_bucket: Mapping[str, Iterable[float]]) -> dict[str, dict[str, float]]:
    """Compute per-bucket Sharpe and PnL for session/regime breakdowns."""
    out: dict[str, dict[str, float]] = {}
    for bucket, rets in returns_by_bucket.items():
        arr = np.asarray([r for r in rets if isinstance(r, (int, float)) and np.isfinite(r)], dtype=np.float64)
        if arr.size < 2:
            out[bucket] = {"n": int(arr.size), "pnl": float(arr.sum()), "sharpe": 0.0}
            continue
        std = float(arr.std(ddof=1))
        sharpe = float(arr.mean() / std) if std > 0.0 else 0.0
        out[bucket] = {
            "n": int(arr.size),
            "pnl": float(arr.sum()),
            "sharpe": sharpe,
        }
    return out


def _trade_duration_metrics(trades: list[Mapping[str, Any]], net_pnl: float) -> dict[str, float]:
    """PDF section 10.1 trade-level metrics: profit per hour, avg hold time.

    Each trade may carry an optional ``hold_minutes`` (time held). When absent
    the average is reported as 0.0 and profit_per_hour falls back to a per-trade
    rate so the metric is still present.
    """
    hold_minutes: list[float] = []
    for t in trades:
        hm = t.get("hold_minutes") if isinstance(t, Mapping) else None
        if isinstance(hm, (int, float)) and np.isfinite(hm) and hm > 0.0:
            hold_minutes.append(float(hm))
    avg_hold_minutes = float(np.mean(hold_minutes)) if hold_minutes else 0.0
    total_hours = (avg_hold_minutes * len(trades)) / 60.0 if avg_hold_minutes > 0.0 else 0.0
    if total_hours > 0.0:
        profit_per_hour = net_pnl / total_hours
    elif trades:
        profit_per_hour = net_pnl / float(len(trades))
    else:
        profit_per_hour = 0.0
    return {
        "profit_per_hour": float(profit_per_hour),
        "avg_hold_minutes": float(avg_hold_minutes),
        "avg_hold_time": float(avg_hold_minutes),
    }


def evaluate_continuous(
    *,
    gross_returns: Iterable[float],
    trades: Iterable[Mapping[str, Any]],
    candidate: Mapping[str, Any],
    num_trials: int = 1,
    pbo_random_state: int = 42,
    returns_by_session: Mapping[str, Iterable[float]] | None = None,
    returns_by_regime: Mapping[str, Iterable[float]] | None = None,
) -> dict[str, Any]:
    """Full PDF §10.1 metric bundle for a continuous candidate.

    This is the standalone evaluator: it does not require the event-lane
    ``EvaluationResult`` plumbing and is the function tests call directly.
    """
    gross = np.asarray([r for r in gross_returns if isinstance(r, (int, float)) and np.isfinite(r)], dtype=np.float64)
    cfg = _cost_config_for_candidate(candidate)
    gross_pnl = float(gross.sum())
    cost = apply_continuous_costs(gross_pnl, list(trades), cfg)

    net_returns = cost_adjusted_returns(gross.tolist(), list(trades), cfg)
    net_returns_list = list(net_returns)
    stats = summary_metrics(net_returns_list, num_trials=max(1, num_trials))
    stats_gross = summary_metrics(gross.tolist(), num_trials=max(1, num_trials))
    pbo = cscv_pbo(net_returns_list, random_state=pbo_random_state)
    wf = walk_forward_eval(net_returns_list)
    power = power_summary(net_returns_list)
    duration = _trade_duration_metrics(list(trades), cost.net_pnl)

    entry = {}
    try:
        entry = get_continuous_model_entry(str(candidate.get("model_id")))
    except KeyError:
        pass

    payload: dict[str, Any] = {
        "lane": CONTINUOUS_LANE_TAG,
        "status": "evaluated",
        "candidate_id": candidate.get("candidate_id"),
        "model_id": candidate.get("model_id"),
        "relationship_id": candidate.get("relationship_id"),
        "feature_family": candidate.get("feature_family"),
        "session_scope": candidate.get("session_scope"),
        "model_family": candidate.get("model_family"),
        "risk_metrics_required": entry.get("risk_metrics"),
        "num_trials": int(num_trials),
        "cost_breakdown": cost.to_dict(),
        "statistics": stats,
        "statistics_gross": stats_gross,
        "pbo": pbo,
        "walk_forward": wf,
        "power": power,
        "trade_metrics": duration,
        "gates": {},
    }

    if returns_by_session:
        payload["session_breakdown"] = _bucket_summary(returns_by_session)
    if returns_by_regime:
        payload["regime_breakdown"] = _bucket_summary(returns_by_regime)
    return payload


def evaluate_continuous_from_candidate(
    candidate: CandidateModel,
    event_id: str,
    repo_root: Path,
    *,
    gross_returns: Iterable[float] | None = None,
    trades: Iterable[Mapping[str, Any]] | None = None,
    num_trials: int = 1,
    gates: GateThresholds | None = None,
    seed: int = 42,
) -> EvaluationResult:
    """Event-lane-compatible wrapper that returns an ``EvaluationResult``.

    When a candidate does not yet carry simulated returns (Phase 6 stub data is
    not wired to a real backtest), the function synthesises an empty result so
    the pipeline can route continuous candidates without crashing. Real
    backtest feeds supply ``gross_returns``/``trades`` via candidate metadata
    or a future backtest harness.
    """
    gates = gates or GateThresholds(min_trades=0)
    meta = dict(candidate.metadata or {})
    cand_map: dict[str, Any] = {
        "candidate_id": candidate.candidate_id,
        "model_id": candidate.model_id,
        "model_family": meta.get("model_family"),
        "relationship_id": meta.get("relationship_id"),
        "feature_family": meta.get("feature_family"),
        "session_scope": meta.get("session_scope"),
        "source_root": meta.get("source_root"),
    }

    if gross_returns is None:
        gross_returns = meta.get("gross_returns") or []
    if trades is None:
        trades = meta.get("trades") or []

    has_returns = bool(list(gross_returns) if not isinstance(gross_returns, (list, tuple)) else gross_returns)
    if not has_returns and not trades:
        return EvaluationResult(
            candidate=candidate,
            event_id=event_id,
            net_pnl=0.0,
            num_trades=0,
            win_rate=0.0,
            expectancy=0.0,
            tail_loss=0.0,
            gates=gates,
            error="missing_backtest_data: continuous candidate has no gross_returns/trades",
            failure_class="data_quality",
            workbench_out={
                "continuous_evaluation": {
                    "lane": CONTINUOUS_LANE_TAG,
                    "status": "missing_backtest_data",
                    "candidate_id": candidate.candidate_id,
                    "model_id": candidate.model_id,
                    "gates": {"all_pass": False, "primary_metric": "DSR", "primary_pass": False},
                }
            },
        )

    payload = evaluate_continuous(
        gross_returns=gross_returns,
        trades=trades,
        candidate=cand_map,
        num_trials=max(1, int(meta.get("num_trials", num_trials))),
        pbo_random_state=seed,
        returns_by_session=meta.get("returns_by_session"),
        returns_by_regime=meta.get("returns_by_regime"),
    )

    cost = payload["cost_breakdown"]
    stats = payload["statistics"]
    net_pnl = float(cost["net_pnl"])
    num_trades = int(cost["num_trades"])
    win_rate = float(meta.get("win_rate", 0.0))
    expectancy = float(meta.get("expectancy", 0.0))
    tail_loss = abs(float(stats.get("cvar_95", 0.0)))

    gate_result = evaluate_gates(payload, gates)
    payload["gates"] = gate_result

    return EvaluationResult(
        candidate=candidate,
        event_id=event_id,
        net_pnl=net_pnl,
        num_trades=num_trades,
        win_rate=win_rate,
        expectancy=expectancy,
        tail_loss=tail_loss,
        gates=gates,
        workbench_out={"continuous_evaluation": payload},
        gross_pnl=float(cost.get("gross_pnl", net_pnl)),
        cost_total=float(
            cost.get("spread_paid", 0.0) + cost.get("fees_paid", 0.0)
            + cost.get("slippage_cost", 0.0) + cost.get("impact_cost", 0.0)
            + cost.get("adverse_selection_cost", 0.0)
        ),
        cost_breakdown=dict(cost),
        sharpe=float(stats.get("sharpe", 0.0)),
        sortino=float(stats.get("sortino", 0.0)),
        max_drawdown=float(stats.get("max_drawdown", 0.0)),
        psr=float(stats.get("psr", 0.0)) if stats.get("psr") is not None else None,
        dsr=float(stats.get("dsr", 0.0)) if stats.get("dsr") is not None else None,
        skew=float(stats.get("skew", 0.0)) if stats.get("skew") is not None else None,
        kurtosis=float(stats.get("kurtosis", 0.0)) if stats.get("kurtosis") is not None else None,
        cvar_95=float(stats.get("cvar_95", 0.0)) if stats.get("cvar_95") is not None else None,
        cvar_99=float(stats.get("cvar_99", 0.0)) if stats.get("cvar_99") is not None else None,
        tail_ratio=float(stats.get("tail_ratio", 0.0)) if stats.get("tail_ratio") is not None else None,
        pbo=float(payload.get("pbo", {}).get("pbo", 0.5)) if payload.get("pbo") else None,
        num_trials=int(payload.get("num_trials", 1)),
        risk_metrics_source="continuous_evaluation",
    )


def evaluate_gates(payload: dict[str, Any], gates: GateThresholds) -> dict[str, Any]:
    """Apply DSR/PBO guardrails from registry risk_metrics plus gate thresholds.

    The registry declares ``risk_metrics: {primary: DSR, guardrails: [PBO]}``.
    A candidate passes when:
      - net_pnl >= gates.min_net_pnl
      - num_trades >= gates.min_trades
      - DSR >= dsr_threshold (default 0.95 per PDF promotion philosophy)
      - PBO <= pbo_guardrail (default 0.5 — no worse than random)
    """
    risk = payload.get("risk_metrics_required") or {}
    stats = payload.get("statistics") or {}
    pbo = payload.get("pbo") or {}

    dsr_value = float(stats.get("dsr", 0.0))
    pbo_value = float(pbo.get("pbo", 0.5))
    net_pnl = float((payload.get("cost_breakdown") or {}).get("net_pnl", 0.0))
    num_trades = int((payload.get("cost_breakdown") or {}).get("num_trades", 0))

    dsr_threshold = 0.95
    pbo_guardrail = 0.5
    all_pass = (
        net_pnl >= gates.min_net_pnl
        and num_trades >= gates.min_trades
        and dsr_value >= dsr_threshold
        and pbo_value <= pbo_guardrail
    )
    primary_pass = dsr_value >= dsr_threshold
    guardrail_pass = pbo_value <= pbo_guardrail
    return {
        "all_pass": bool(all_pass),
        "primary_metric": "DSR",
        "primary_threshold": dsr_threshold,
        "primary_value": dsr_value,
        "primary_pass": bool(primary_pass),
        "guardrail_metric": "PBO",
        "guardrail_threshold": pbo_guardrail,
        "guardrail_value": pbo_value,
        "guardrail_pass": bool(guardrail_pass),
        "min_net_pnl_pass": net_pnl >= gates.min_net_pnl,
        "min_trades_pass": num_trades >= gates.min_trades,
        "risk_metrics_required": risk,
    }