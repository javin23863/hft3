"""Robustness pack: walk-forward hooks, purged CV, Monte Carlo, parameter sweep."""

from __future__ import annotations

import random
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from decision_engine.python.src.walk_forward import WalkForwardValidator
from workbench.src.robustness.purged_cv import purged_splits


HOLDOUT_YEARS = (2023, 2024)
PURGED_CV_EMBARGO = 10
REQUIRED_ROBUSTNESS_CHECKS = (
    "feature_leakage",
    "label_leakage",
    "timestamp_leakage",
    "future_data_leakage",
    "parameter_stability",
    "regime_stability",
    "drawdown_limit",
    "tail_risk_limit",
    "turnover_limit",
    "transaction_cost_sensitivity",
    "slippage_sensitivity",
    "latency_sensitivity",
    "operating_envelope_generated",
    "low_latency_execution_path_audit",
    "placement_speed_sensitivity",
    "async_ack_state_risk",
    "pending_exposure_guardrails",
    "composition_latency_feasibility",
    "competitor_speed_sensitivity",
    "liquidity_capacity_constraint",
    "event_window_stability",
    "data_resolution_eligibility",
    "model_combination_attribution",
    "model_combination_degradation_detection",
    "registry_eligibility",
    "artifact_completeness",
    "walk_forward_validation",
    "purged_cv",
    "monte_carlo_sharpe",
    "monte_carlo_drawdown",
    "bonferroni_penalty",
    "holdout_tuning_veto",
)


@dataclass(frozen=True)
class RobustnessCheck:
    name: str
    status: str
    passed: bool
    observed_value: Optional[float] = None
    threshold: Optional[float] = None
    comparison_operator: str = ""
    reason_code: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "passed": self.passed,
            "observed_value": self.observed_value,
            "threshold": self.threshold,
            "comparison_operator": self.comparison_operator,
            "reason_code": self.reason_code,
        }


@dataclass
class RobustnessResult:
    walk_forward: Dict[str, Any] = field(default_factory=dict)
    purged_cv: List[Dict[str, float]] = field(default_factory=list)
    monte_carlo: Dict[str, Any] = field(default_factory=dict)
    parameter_sweep: List[Dict[str, Any]] = field(default_factory=list)
    checks: List[RobustnessCheck] = field(default_factory=list)
    passed: bool = False
    overfit_risk: str = "unknown"
    bonferroni_penalty: float = 1.0

    def checks_dict(self) -> List[Dict[str, Any]]:
        return [check.to_dict() for check in self.checks]


def _pending(name: str, reason_code: str = "ROBUSTNESS_INPUT_MISSING") -> RobustnessCheck:
    return RobustnessCheck(name=name, status="PENDING", passed=False, reason_code=reason_code)


def _bool_check(name: str, value: Any, *, expected: bool = True) -> RobustnessCheck:
    if value is None:
        return _pending(name)
    if not isinstance(value, bool):
        return RobustnessCheck(name=name, status="FAIL", passed=False, reason_code="ROBUSTNESS_INPUT_MALFORMED")
    observed = value
    passed = observed is expected
    return RobustnessCheck(
        name=name,
        status="PASS" if passed else "FAIL",
        passed=passed,
        observed_value=1.0 if observed else 0.0,
        threshold=1.0 if expected else 0.0,
        comparison_operator="==",
        reason_code="PASS" if passed else "ROBUSTNESS_CHECK_FAILED",
    )


def _metric_check(name: str, value: Any, threshold: float, op: str) -> RobustnessCheck:
    if value is None:
        return _pending(name)
    if isinstance(value, bool) or isinstance(threshold, bool):
        return RobustnessCheck(name=name, status="FAIL", passed=False, reason_code="ROBUSTNESS_INPUT_MALFORMED")
    try:
        observed = float(value)
        threshold = float(threshold)
    except (TypeError, ValueError):
        return RobustnessCheck(name=name, status="FAIL", passed=False, reason_code="ROBUSTNESS_INPUT_MALFORMED")
    if not math.isfinite(observed) or not math.isfinite(threshold):
        return RobustnessCheck(name=name, status="FAIL", passed=False, reason_code="ROBUSTNESS_INPUT_MALFORMED")
    if op == ">=":
        passed = observed >= threshold
    elif op == "<=":
        passed = observed <= threshold
    elif op == ">":
        passed = observed > threshold
    else:
        raise ValueError(f"unsupported robustness comparison operator: {op}")
    return RobustnessCheck(
        name=name,
        status="PASS" if passed else "FAIL",
        passed=passed,
        observed_value=observed,
        threshold=threshold,
        comparison_operator=op,
        reason_code="PASS" if passed else "ROBUSTNESS_CHECK_FAILED",
    )


def _required_checks(
    metrics: Dict[str, Any],
    *,
    wf_result: Dict[str, Any],
    purged: List[Dict[str, float]],
    monte_carlo: Dict[str, Any],
    bonferroni_penalty: float,
    holdout_used_for_tuning: bool,
    trade_sample_observed: bool,
) -> List[RobustnessCheck]:
    max_drawdown_limit = metrics.get("max_drawdown_limit", -500.0)
    tail_risk_limit = metrics.get("tail_risk_limit", -500.0)
    turnover_limit = metrics.get("turnover_limit", 100_000.0)
    parameter_stability_min = metrics.get("parameter_stability_min", 0.50)
    regime_stability_min = metrics.get("regime_stability_min", 0.0)
    sharpe_min = -0.5 / bonferroni_penalty

    wf_status = str(wf_result.get("status", "")).upper()
    if wf_status == "PASS":
        wf_check = RobustnessCheck("walk_forward_validation", "PASS", True, 1.0, 1.0, "==", "PASS")
    elif wf_status in ("", "SINGLE_WINDOW", "EMPTY"):
        wf_check = _pending("walk_forward_validation", "WALK_FORWARD_NOT_OBSERVED")
    elif wf_status == "INCOMPLETE":
        wf_check = _pending("walk_forward_validation", "WALK_FORWARD_INCOMPLETE")
    else:
        wf_check = RobustnessCheck("walk_forward_validation", "FAIL", False, 0.0, 1.0, "==", "WALK_FORWARD_FAILED")

    purged_check = (
        _bool_check("purged_cv", metrics.get("purged_cv_pass"))
        if purged
        else _pending("purged_cv", "PURGED_CV_NOT_OBSERVED")
    )
    mc_sharpe_check = (
        _metric_check("monte_carlo_sharpe", monte_carlo.get("sharpe_p05"), sharpe_min, ">")
        if trade_sample_observed
        else _pending("monte_carlo_sharpe", "TRADE_SAMPLE_MISSING")
    )
    mc_drawdown_check = (
        _metric_check("monte_carlo_drawdown", monte_carlo.get("drawdown_p95"), -500.0, ">")
        if trade_sample_observed
        else _pending("monte_carlo_drawdown", "TRADE_SAMPLE_MISSING")
    )

    checks = [
        _bool_check("feature_leakage", metrics.get("feature_leakage_detected"), expected=False),
        _bool_check("label_leakage", metrics.get("label_leakage_detected"), expected=False),
        _bool_check("timestamp_leakage", metrics.get("timestamp_leakage_detected"), expected=False),
        _bool_check("future_data_leakage", metrics.get("future_data_leakage_detected"), expected=False),
        _metric_check("parameter_stability", metrics.get("parameter_stability_score"), parameter_stability_min, ">="),
        _metric_check("regime_stability", metrics.get("regime_stability_score"), regime_stability_min, ">="),
        _metric_check("drawdown_limit", metrics.get("max_drawdown"), max_drawdown_limit, ">="),
        _metric_check("tail_risk_limit", metrics.get("tail_loss_p95"), tail_risk_limit, ">="),
        _metric_check("turnover_limit", metrics.get("turnover"), turnover_limit, "<="),
        _bool_check("transaction_cost_sensitivity", metrics.get("transaction_cost_sensitivity_pass")),
        _bool_check("slippage_sensitivity", metrics.get("slippage_sensitivity_pass")),
        _bool_check("latency_sensitivity", metrics.get("survives_cpp_execution_delay")),
        _bool_check("operating_envelope_generated", metrics.get("operating_envelope_generated")),
        _bool_check("low_latency_execution_path_audit", metrics.get("low_latency_execution_path_audit_pass")),
        _bool_check("placement_speed_sensitivity", metrics.get("placement_speed_sensitivity_pass")),
        _bool_check("async_ack_state_risk", metrics.get("async_ack_state_risk_pass")),
        _bool_check("pending_exposure_guardrails", metrics.get("pending_exposure_guardrails_pass")),
        _bool_check("composition_latency_feasibility", metrics.get("composition_latency_feasibility_pass")),
        _bool_check("competitor_speed_sensitivity", metrics.get("competitor_speed_sensitivity_pass")),
        _bool_check("liquidity_capacity_constraint", metrics.get("liquidity_capacity_pass")),
        _bool_check("event_window_stability", metrics.get("event_window_stability_pass")),
        _bool_check("data_resolution_eligibility", metrics.get("data_resolution_eligible")),
        _bool_check("model_combination_attribution", metrics.get("model_combination_attribution_complete")),
        _bool_check("model_combination_degradation_detection", metrics.get("model_combination_degradation_pass")),
        _bool_check("registry_eligibility", metrics.get("registry_eligible")),
        _bool_check("artifact_completeness", metrics.get("artifact_complete")),
        wf_check,
        purged_check,
        mc_sharpe_check,
        mc_drawdown_check,
        _metric_check("bonferroni_penalty", bonferroni_penalty, 1.0, ">="),
        RobustnessCheck(
            "holdout_tuning_veto",
            "PASS" if not holdout_used_for_tuning else "FAIL",
            not holdout_used_for_tuning,
            0.0 if not holdout_used_for_tuning else 1.0,
            0.0,
            "==",
            "PASS" if not holdout_used_for_tuning else "HOLDOUT_USED_FOR_TUNING",
        ),
    ]
    if tuple(check.name for check in checks) != REQUIRED_ROBUSTNESS_CHECKS:
        raise AssertionError("Phase 9 robustness check registry drift")
    return checks


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


def _finite_trade_pnls(trade_pnls: List[float]) -> List[float]:
    out: List[float] = []
    for pnl in trade_pnls:
        if isinstance(pnl, bool):
            return []
        try:
            value = float(pnl)
        except (TypeError, ValueError):
            return []
        if not math.isfinite(value):
            return []
        out.append(value)
    return out


def walk_forward_from_campaign_periods(
    period_summaries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build walk-forward result from campaign period summaries (B4)."""
    if not period_summaries:
        return {"periods": [], "status": "EMPTY"}
    by_name = {p.get("name"): p for p in period_summaries}
    required = ("Discovery", "Confirmation", "Holdout", "Recent holdout")
    ordered = []
    status = "PASS"
    missing = [pname for pname in required if pname not in by_name]
    for pname in required:
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
    if missing and status == "PASS":
        status = "INCOMPLETE"
    return {"periods": ordered, "status": status, "missing_periods": missing}


def run_robustness_pack(
    backtest_fn: Callable[[], Dict[str, float]],
    trade_pnls: List[float],
    *,
    n_purged_splits: int = 3,
    sweep_count: int = 1,
    holdout_used_for_tuning: bool = False,
    campaign_periods: Optional[List[Dict[str, Any]]] = None,
) -> RobustnessResult:
    base_metrics = backtest_fn()
    finite_trade_pnls = _finite_trade_pnls(trade_pnls)
    wf = WalkForwardValidator()
    if campaign_periods is not None:
        wf_result = walk_forward_from_campaign_periods(campaign_periods)
    else:
        wf_result = {"periods": [p.name for p in wf.periods], "status": "single_window"}
    purged = []
    min_purged_sample = n_purged_splits + PURGED_CV_EMBARGO + 1
    if n_purged_splits > 0 and len(finite_trade_pnls) >= min_purged_sample:
        for train_idx, test_idx in purged_splits(
            len(finite_trade_pnls),
            n_purged_splits,
            embargo=PURGED_CV_EMBARGO,
        ):
            if not train_idx or not test_idx:
                continue
            purged.append({"train_size": len(train_idx), "test_size": len(test_idx), "score": base_metrics.get("expectancy", 0.0)})
    mc = monte_carlo_ci(finite_trade_pnls)
    penalty = max(1.0, sweep_count)
    checks = _required_checks(
        base_metrics,
        wf_result=wf_result,
        purged=purged,
        monte_carlo=mc,
        bonferroni_penalty=penalty,
        holdout_used_for_tuning=holdout_used_for_tuning,
        trade_sample_observed=bool(finite_trade_pnls),
    )
    passed = all(check.passed for check in checks)
    risk = "low" if passed else ("high" if holdout_used_for_tuning else "medium")
    return RobustnessResult(
        walk_forward=wf_result,
        purged_cv=purged,
        monte_carlo=mc,
        checks=checks,
        passed=passed,
        overfit_risk=risk,
        bonferroni_penalty=penalty,
    )
