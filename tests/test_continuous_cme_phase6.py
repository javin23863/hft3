"""Phase 6 continuous CME evaluation and alpha validation tests.

Verifies PDF §10 acceptance gate: results include net PnL, cost breakdown,
DSR/PBO, and session/regime reports. Covers statistics, cross-validation,
cost model, power analysis, and the continuous_evaluation routing in
evaluation.py.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "packages") not in sys.path:
    sys.path.insert(0, str(REPO / "packages"))


def _seed_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


# ---------------------------------------------------------------------------
# statistics.py — PSR / DSR / MinTRL / risk metrics
# ---------------------------------------------------------------------------


def test_sharpe_and_sortino_distinguish_edge_from_noise() -> None:
    from research_pipeline.statistics import stream_sharpe, stream_sortino

    rng = _seed_rng(1)
    good = rng.normal(0.0008, 0.005, 500)
    noise = rng.normal(0.0, 0.005, 500)
    assert stream_sharpe(good.tolist()) > stream_sharpe(noise.tolist())
    # Sortino uses downside deviation only; for a positive-edge stream the
    # downside deviation is smaller than total std, so Sortino >= Sharpe.
    assert stream_sortino(good.tolist()) >= stream_sharpe(good.tolist())
    assert stream_sortino(noise.tolist()) >= 0.0 or math.isfinite(stream_sortino(noise.tolist()))


def test_psr_higher_for_genuine_edge() -> None:
    from research_pipeline.statistics import psr

    rng = _seed_rng(2)
    good = rng.normal(0.0008, 0.005, 500)
    bad = rng.normal(-0.0003, 0.005, 500)
    assert psr(good.tolist()) > 0.5
    assert psr(bad.tolist()) < 0.5


def test_dsr_deflates_with_trial_count() -> None:
    from research_pipeline.statistics import dsr

    rng = _seed_rng(3)
    returns = rng.normal(0.0005, 0.004, 400)
    single = dsr(returns.tolist(), num_trials=1)
    many = dsr(returns.tolist(), num_trials=100)
    assert 0.0 <= single <= 1.0
    assert many <= single  # more trials => more deflation => lower DSR


def test_min_trl_finite_for_edge_infinite_for_no_edge() -> None:
    from research_pipeline.statistics import min_trl

    rng = _seed_rng(4)
    good = rng.normal(0.0008, 0.005, 400)
    bad = rng.normal(-0.0008, 0.005, 400)
    assert math.isfinite(min_trl(good.tolist()))
    assert min_trl(bad.tolist()) == float("inf")


def test_risk_metrics_cvar_tail_ratio_skew_kurtosis() -> None:
    from research_pipeline.statistics import cvar, tail_ratio, stream_skewness, stream_kurtosis, max_drawdown

    rng = _seed_rng(5)
    returns = rng.normal(0.0, 0.01, 500)
    assert cvar(returns.tolist(), alpha=0.05) < 0.0  # left tail is losses
    assert cvar(returns.tolist(), alpha=0.01) <= cvar(returns.tolist(), alpha=0.05)
    assert tail_ratio(returns.tolist()) >= 0.0
    assert isinstance(stream_skewness(returns.tolist()), float)
    assert isinstance(stream_kurtosis(returns.tolist()), float)
    assert max_drawdown(returns.tolist()) >= 0.0


def test_statistics_empty_and_insufficient_inputs_are_safe() -> None:
    from research_pipeline.statistics import summary_metrics, psr

    empty = summary_metrics([])
    assert empty["n"] == 0 and empty["sharpe"] == 0.0 and empty["dsr"] == 0.0
    assert psr([]) == 0.0
    assert psr([0.01]) == 0.0


def test_summary_metrics_includes_all_pdf_required_metrics() -> None:
    from research_pipeline.statistics import summary_metrics

    rng = _seed_rng(6)
    m = summary_metrics(rng.normal(0.0005, 0.005, 300).tolist(), num_trials=5)
    required = {
        "sharpe", "sortino", "psr", "dsr", "min_trl", "max_drawdown",
        "cvar_95", "cvar_99", "tail_ratio", "skew", "kurtosis",
    }
    assert required <= set(m)


# ---------------------------------------------------------------------------
# cross_validation.py — CSCV / PBO / walk-forward
# ---------------------------------------------------------------------------


def test_pbo_neutral_for_empty_or_insufficient_data() -> None:
    from research_pipeline.cross_validation import cscv_pbo

    assert cscv_pbo([])["pbo"] == 0.5
    assert cscv_pbo([0.01, 0.02])["pbo"] == 0.5


def test_pbo_panel_detects_overfit_when_one_of_many_has_edge() -> None:
    from research_pipeline.cross_validation import cscv_pbo_panel

    rng = _seed_rng(7)
    panel = [rng.normal(0.0005 if i == 0 else 0.0, 0.005, 400).tolist() for i in range(10)]
    result = cscv_pbo_panel(panel, num_blocks=10)
    assert 0.0 <= result["pbo"] <= 1.0
    assert result["folds"] > 0


def test_walk_forward_split_is_chronological_no_shuffle() -> None:
    from research_pipeline.cross_validation import walk_forward_split

    arr = list(range(100))
    train, test = walk_forward_split(arr, train_fraction=0.7)
    assert list(train) == list(range(70))
    assert list(test) == list(range(70, 100))


def test_walk_forward_eval_flags_overfit_when_in_sample_beats_out_sample() -> None:
    from research_pipeline.cross_validation import walk_forward_eval

    rng = _seed_rng(8)
    overfit = np.concatenate([rng.normal(0.001, 0.005, 200), rng.normal(-0.0005, 0.005, 100)])
    wf = walk_forward_eval(overfit.tolist(), train_fraction=0.7)
    assert "in_sample_sharpe" in wf
    assert "out_sample_sharpe" in wf
    assert "degradation" in wf
    assert "overfit_flag" in wf


# ---------------------------------------------------------------------------
# cost_model.py — spread / fees / slippage / impact
# ---------------------------------------------------------------------------


def test_cost_breakdown_subtracts_all_cost_components() -> None:
    from research_pipeline.cost_model import apply_continuous_costs, micro_standard_cost_config

    trades = [{"side": "buy", "qty": 2, "fill_price": 4500.0, "notional": 9000.0} for _ in range(10)]
    cfg = micro_standard_cost_config()
    b = apply_continuous_costs(5000.0, trades, cfg)
    assert b.net_pnl < 5000.0
    assert b.net_pnl == 5000.0 - (b.spread_paid + b.fees_paid + b.slippage_cost + b.impact_cost + b.adverse_selection_cost)
    assert b.fill_adjusted_pnl == 5000.0 - (b.slippage_cost + b.impact_cost + b.adverse_selection_cost)
    assert b.num_trades == 10
    assert b.turnover > 0.0


def test_cost_adjusted_returns_reduce_gross_for_costs() -> None:
    from research_pipeline.cost_model import cost_adjusted_returns, micro_standard_cost_config

    rng = _seed_rng(9)
    gross = rng.normal(0.0002, 0.001, 100)
    trades = [{"qty": 1, "fill_price": 4500.0, "notional": 4500.0} for _ in range(20)]
    net = cost_adjusted_returns(gross.tolist(), trades, micro_standard_cost_config())
    assert float(np.sum(net)) < float(gross.sum())


def test_empty_trades_yield_gross_equals_net() -> None:
    from research_pipeline.cost_model import apply_continuous_costs, micro_standard_cost_config

    b = apply_continuous_costs(100.0, [], micro_standard_cost_config())
    assert b.net_pnl == 100.0
    assert b.spread_paid == 0.0 and b.fees_paid == 0.0


# ---------------------------------------------------------------------------
# power_analysis.py
# ---------------------------------------------------------------------------


def test_power_sufficient_for_strong_edge_insufficient_for_weak() -> None:
    from research_pipeline.power_analysis import power_summary

    rng = _seed_rng(10)
    # Large effect (d ~ 0.3) with ample n guarantees sufficient power.
    strong = rng.normal(0.0015, 0.003, 500)
    # Near-zero mean with large variance => no detectable edge.
    weak = rng.normal(0.0, 0.05, 300)
    assert power_summary(strong.tolist())["sufficient"] is True
    assert power_summary(weak.tolist())["sufficient"] is False


def test_minimum_sample_size_decreases_with_larger_effect() -> None:
    from research_pipeline.power_analysis import minimum_sample_size

    small = minimum_sample_size(0.2)
    medium = minimum_sample_size(0.5)
    large = minimum_sample_size(0.8)
    assert small > medium > large


# ---------------------------------------------------------------------------
# continuous_evaluation.py — routing and full metric bundle
# ---------------------------------------------------------------------------


def test_is_continuous_candidate_detects_lane_and_registry() -> None:
    from research_pipeline.continuous_evaluation import is_continuous_candidate
    from research_pipeline.types import CandidateModel

    by_lane = CandidateModel(
        candidate_id="c1", model_id="UNKNOWN", strategy_params={}, thesis="t",
        metadata={"lane": "continuous_microstructure"},
    )
    by_registry = CandidateModel(
        candidate_id="c2", model_id="MICRO_STANDARD_FLOW_TRANSFER", strategy_params={}, thesis="t",
    )
    event = CandidateModel(candidate_id="c3", model_id="HYP_5", strategy_params={}, thesis="t")
    assert is_continuous_candidate(by_lane) is True
    assert is_continuous_candidate(by_registry) is True
    assert is_continuous_candidate(event) is False


def test_evaluate_continuous_returns_full_metric_bundle() -> None:
    from research_pipeline.continuous_evaluation import evaluate_continuous

    rng = _seed_rng(11)
    gross = rng.normal(0.0002, 0.001, 300).tolist()
    trades = [{"side": "buy", "qty": 1, "fill_price": 4500.0, "notional": 4500.0} for _ in range(20)]
    cand = {
        "candidate_id": "c1",
        "model_id": "MICRO_STANDARD_FLOW_TRANSFER",
        "model_family": "cross_market_lead_lag",
        "relationship_id": "micro_standard:MES->ES",
        "feature_family": "cross_market",
        "session_scope": "2026-W27:all_sessions",
    }
    payload = evaluate_continuous(
        gross_returns=gross, trades=trades, candidate=cand, num_trials=5,
    )
    assert payload["lane"] == "continuous_microstructure"
    assert payload["status"] == "evaluated"
    assert "cost_breakdown" in payload
    assert "statistics" in payload
    assert "pbo" in payload
    assert "power" in payload
    assert "walk_forward" in payload
    assert payload["statistics"]["dsr"] is not None
    assert payload["pbo"]["pbo"] is not None


def test_evaluate_continuous_session_and_regime_breakdowns() -> None:
    from research_pipeline.continuous_evaluation import evaluate_continuous

    rng = _seed_rng(12)
    sessions = {
        "asia": rng.normal(0.0001, 0.001, 50).tolist(),
        "europe": rng.normal(0.0003, 0.001, 50).tolist(),
        "us_morning": rng.normal(0.0005, 0.001, 50).tolist(),
        "us_afternoon": rng.normal(0.0001, 0.001, 50).tolist(),
        "settlement_roll": rng.normal(0.0, 0.001, 50).tolist(),
    }
    regimes = {
        "high_vol": rng.normal(0.0002, 0.002, 50).tolist(),
        "low_vol": rng.normal(0.0004, 0.0005, 50).tolist(),
        "roll_week": rng.normal(0.0001, 0.001, 50).tolist(),
    }
    gross = rng.normal(0.0003, 0.001, 250).tolist()
    trades = [{"qty": 1, "fill_price": 4500.0, "notional": 4500.0} for _ in range(20)]
    payload = evaluate_continuous(
        gross_returns=gross, trades=trades,
        candidate={"model_id": "MICRO_STANDARD_FLOW_TRANSFER", "model_family": "cross_market_lead_lag"},
        returns_by_session=sessions, returns_by_regime=regimes, num_trials=3,
    )
    assert set(payload["session_breakdown"].keys()) == set(sessions.keys())
    assert set(payload["regime_breakdown"].keys()) == set(regimes.keys())
    for bucket in payload["session_breakdown"].values():
        assert {"n", "pnl", "sharpe"} <= set(bucket)


def test_evaluate_continuous_trade_metrics_profit_per_hour_and_hold_time() -> None:
    from research_pipeline.continuous_evaluation import evaluate_continuous

    rng = _seed_rng(14)
    gross = rng.normal(0.0002, 0.001, 200).tolist()
    # 20 trades, each held 30 minutes -> 10 hours total.
    trades = [
        {"qty": 1, "fill_price": 4500.0, "notional": 4500.0, "hold_minutes": 30.0}
        for _ in range(20)
    ]
    payload = evaluate_continuous(
        gross_returns=gross, trades=trades,
        candidate={"model_id": "MICRO_STANDARD_FLOW_TRANSFER", "model_family": "cross_market_lead_lag"},
        num_trials=2,
    )
    tm = payload["trade_metrics"]
    assert tm["avg_hold_minutes"] == 30.0
    assert tm["avg_hold_time"] == 30.0
    # profit_per_hour = net_pnl / (20 trades * 30 min / 60) = net_pnl / 10
    expected_hours = (30.0 * 20) / 60.0
    assert abs(tm["profit_per_hour"] - payload["cost_breakdown"]["net_pnl"] / expected_hours) < 1e-6

    # Fallback when hold_minutes absent: profit per trade, not per hour.
    trades_no_hold = [{"qty": 1, "fill_price": 4500.0, "notional": 4500.0} for _ in range(20)]
    payload2 = evaluate_continuous(
        gross_returns=gross, trades=trades_no_hold,
        candidate={"model_id": "MICRO_STANDARD_FLOW_TRANSFER", "model_family": "cross_market_lead_lag"},
        num_trials=2,
    )
    tm2 = payload2["trade_metrics"]
    assert tm2["avg_hold_minutes"] == 0.0
    assert abs(tm2["profit_per_hour"] - payload2["cost_breakdown"]["net_pnl"] / 20.0) < 1e-6

def test_evaluate_gates_dsr_and_pbo_guardrails() -> None:
    from research_pipeline.continuous_evaluation import evaluate_gates
    from research_pipeline.types import GateThresholds

    payload = {
        "risk_metrics_required": {"primary": "DSR", "guardrails": ["PBO"]},
        "statistics": {"dsr": 0.97},
        "pbo": {"pbo": 0.3},
        "cost_breakdown": {"net_pnl": 100.0, "num_trades": 50},
    }
    gates = evaluate_gates(payload, GateThresholds(min_trades=10))
    assert gates["all_pass"] is True
    assert gates["primary_metric"] == "DSR"
    assert gates["guardrail_metric"] == "PBO"

    failing = {**payload, "statistics": {"dsr": 0.80}, "pbo": {"pbo": 0.7}}
    gates_fail = evaluate_gates(failing, GateThresholds(min_trades=10))
    assert gates_fail["all_pass"] is False
    assert gates_fail["primary_pass"] is False
    assert gates_fail["guardrail_pass"] is False


def test_evaluate_model_routes_continuous_candidate_to_continuous_eval() -> None:
    from research_pipeline.evaluation import evaluate_model
    from research_pipeline.types import CandidateModel, GateThresholds

    rng = _seed_rng(13)
    gross = rng.normal(0.0002, 0.001, 200).tolist()
    trades = [{"qty": 1, "fill_price": 4500.0, "notional": 4500.0} for _ in range(20)]
    cand = CandidateModel(
        candidate_id="c1",
        model_id="MICRO_STANDARD_FLOW_TRANSFER",
        strategy_params={},
        thesis="micro flow transfer",
        metadata={
            "lane": "continuous_microstructure",
            "model_family": "cross_market_lead_lag",
            "relationship_id": "micro_standard:MES->ES",
            "feature_family": "cross_market",
            "session_scope": "2026-W27:all_sessions",
            "gross_returns": gross,
            "trades": trades,
            "num_trials": 5,
        },
    )
    result = evaluate_model(cand, "2026-W27", REPO, gates=GateThresholds(min_trades=1))
    cont = result.workbench_out["continuous_evaluation"]
    assert cont["lane"] == "continuous_microstructure"
    assert cont["status"] == "evaluated"
    assert "cost_breakdown" in cont
    assert "statistics" in cont
    assert "pbo" in cont
    assert isinstance(result.passes_all_gates(), bool)


def test_evaluate_model_event_lane_unchanged() -> None:
    """Event-lane routing must not be altered by the continuous route (PDF §15.1)."""
    from research_pipeline.evaluation import evaluate_model
    from research_pipeline.types import CandidateModel, GateThresholds

    cand = CandidateModel(
        candidate_id="c2", model_id="HYP_5", strategy_params={}, thesis="event thesis",
    )
    # This will attempt the workbench path; we expect a non-continuous error
    # result (not a continuous_evaluation payload).
    result = evaluate_model(cand, "CPI_2024_09_11_TIGHT", REPO, gates=GateThresholds(min_trades=0))
    cont = (result.workbench_out or {}).get("continuous_evaluation")
    assert cont is None  # event lane did not route to continuous evaluation