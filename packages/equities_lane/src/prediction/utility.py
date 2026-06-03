"""Expected utility calculator and timing policy optimizer.

Computes EU for each candidate action and selects the optimal timing
policy conditional on the candidate's current state.
"""
from __future__ import annotations

from .types import (
    ExpectedUtility,
    HazardEstimate,
    ModelConfig,
    PayoffEstimate,
    RiskEstimate,
    RunnerPrediction,
    SnapshotType,
    TimingPolicy,
)


_POLICY_HORIZON_MAP: dict[TimingPolicy, str] = {
    TimingPolicy.WATCH: "5d",
    TimingPolicy.SEED_T2: "5d",
    TimingPolicy.ENTER_T1_CLOSE: "2d",
    TimingPolicy.ENTER_AFTER_HOURS: "1d",
    TimingPolicy.ENTER_PREMARKET: "1d",
    TimingPolicy.ENTER_OPEN_CONFIRMATION: "1d",
    TimingPolicy.ENTER_INTRADAY_CONTINUATION: "1d",
    TimingPolicy.REJECT_RISK_ADJUSTED: "reject",
}


_POLICY_CONFIDENCE_SCALE: dict[TimingPolicy, float] = {
    TimingPolicy.WATCH: 1.0,
    TimingPolicy.SEED_T2: 0.85,
    TimingPolicy.ENTER_T1_CLOSE: 0.90,
    TimingPolicy.ENTER_AFTER_HOURS: 0.80,
    TimingPolicy.ENTER_PREMARKET: 0.95,
    TimingPolicy.ENTER_OPEN_CONFIRMATION: 1.0,
    TimingPolicy.ENTER_INTRADAY_CONTINUATION: 0.75,
    TimingPolicy.REJECT_RISK_ADJUSTED: 0.0,
}


_POLICY_SLIPPAGE_MULT: dict[TimingPolicy, float] = {
    TimingPolicy.WATCH: 0.0,
    TimingPolicy.SEED_T2: 1.5,
    TimingPolicy.ENTER_T1_CLOSE: 1.2,
    TimingPolicy.ENTER_AFTER_HOURS: 2.0,
    TimingPolicy.ENTER_PREMARKET: 1.8,
    TimingPolicy.ENTER_OPEN_CONFIRMATION: 1.0,
    TimingPolicy.ENTER_INTRADAY_CONTINUATION: 0.8,
    TimingPolicy.REJECT_RISK_ADJUSTED: 0.0,
}


def compute_expected_utility(
    hazard: HazardEstimate,
    payoff: PayoffEstimate,
    risk: RiskEstimate,
    policy: TimingPolicy,
    config: ModelConfig,
) -> ExpectedUtility:
    if policy == TimingPolicy.REJECT_RISK_ADJUSTED:
        return ExpectedUtility(
            policy=policy,
            eu=0.0,
            p_event=0.0,
            e_mfe=0.0,
            e_mae=0.0,
            e_slippage=0.0,
            e_dilution=0.0,
            e_halt=0.0,
            e_capacity_penalty=0.0,
            e_manipulation=0.0,
        )

    horizon_key = _POLICY_HORIZON_MAP[policy]
    if horizon_key == "5d":
        p_event = hazard.p_run_5d
    elif horizon_key == "2d":
        p_event = hazard.p_run_2d
    elif horizon_key == "1d":
        p_event = hazard.p_run_1d
    else:
        p_event = hazard.p_run_1d

    confidence = _POLICY_CONFIDENCE_SCALE[policy]
    p_event *= confidence

    e_mfe = payoff.expected_mfe * p_event
    e_mae = abs(payoff.expected_mae) * (1 - p_event)

    slip_mult = _POLICY_SLIPPAGE_MULT[policy]
    e_slippage = risk.expected_slippage * slip_mult

    e_dilution = risk.expected_dilution_loss
    e_halt = risk.p_halt_event * config.utility_halt_penalty
    min_capacity = 50_000.0
    e_capacity = max(0.0, 1.0 - risk.expected_capacity / min_capacity) * config.utility_capacity_penalty
    e_manipulation = risk.p_manipulation_risk * config.utility_manipulation_penalty

    eu = (
        e_mfe
        - e_mae
        - e_slippage * config.utility_slippage_penalty
        - e_dilution * config.utility_dilution_penalty
        - e_halt
        - e_capacity
        - e_manipulation
    )

    return ExpectedUtility(
        policy=policy,
        eu=eu,
        p_event=p_event,
        e_mfe=e_mfe,
        e_mae=e_mae,
        e_slippage=e_slippage,
        e_dilution=e_dilution,
        e_halt=e_halt,
        e_capacity_penalty=e_capacity,
        e_manipulation=e_manipulation,
    )


def compute_all_utilities(
    hazard: HazardEstimate,
    payoff: PayoffEstimate,
    risk: RiskEstimate,
    config: ModelConfig,
) -> list[ExpectedUtility]:
    results: list[ExpectedUtility] = []
    for policy in TimingPolicy:
        eu = compute_expected_utility(hazard, payoff, risk, policy, config)
        results.append(eu)
    return results


def select_optimal_policy(
    utility_by_policy: list[ExpectedUtility],
    config: ModelConfig,
) -> TimingPolicy:
    min_eu_threshold = 0.005

    best: ExpectedUtility | None = None
    for eu in utility_by_policy:
        if eu.policy == TimingPolicy.REJECT_RISK_ADJUSTED:
            continue
        if eu.policy == TimingPolicy.WATCH:
            continue
        if eu.eu > min_eu_threshold and (best is None or eu.eu > best.eu):
            best = eu

    if best is None:
        watch = next(
            (e for e in utility_by_policy if e.policy == TimingPolicy.WATCH),
            None,
        )
        if watch is not None and watch.p_event > 0.10:
            return TimingPolicy.WATCH
        return TimingPolicy.REJECT_RISK_ADJUSTED

    return best.policy


def build_reason_codes(
    hazard: HazardEstimate,
    payoff: PayoffEstimate,
    risk: RiskEstimate,
) -> tuple[list[str], list[str]]:
    positive: list[str] = []
    negative: list[str] = []

    if hazard.p_run_5d > 0.15:
        positive.append("HIGH_5D_HAZARD")
    if hazard.p_run_1d > 0.10:
        positive.append("NEAR_TERM_IGNITION")
    if payoff.expected_mfe > 0.30:
        positive.append("LARGE_MFE_EXPECTED")
    if payoff.p_mfe_before_mae > 0.65:
        positive.append("FAVORABLE_PATH_ORDERING")
    if payoff.expected_mfe > 0 and abs(payoff.expected_mae) > 0:
        ratio = payoff.expected_mfe / abs(payoff.expected_mae)
        if ratio > 2.0:
            positive.append("STRONG_REWARD_RISK")

    if risk.p_dilution_gap > 0.20:
        negative.append("HIGH_DILUTION_RISK")
    if risk.p_halt_event > 0.10:
        negative.append("HALT_RISK_ELEVATED")
    if risk.expected_slippage > 0.03:
        negative.append("EXCESSIVE_SLIPPAGE")
    if risk.p_manipulation_risk > 0.25:
        negative.append("MANIPULATION_SUSPECTED")
    if payoff.expected_mfe > 0 and abs(payoff.expected_mae) > 0:
        ratio = payoff.expected_mfe / abs(payoff.expected_mae)
        if ratio < 1.0:
            negative.append("UNFAVORABLE_REWARD_RISK")
    if payoff.p_mfe_before_mae < 0.40:
        negative.append("ADVERSE_PATH_ORDERING")

    return positive, negative


def compute_confidence(
    hazard: HazardEstimate,
    payoff: PayoffEstimate,
    risk: RiskEstimate,
) -> float:
    score = 0.0
    if hazard.p_run_5d > 0.05:
        score += 0.2
    if hazard.p_run_5d > 0.15:
        score += 0.2
    if payoff.p_mfe_before_mae > 0.55:
        score += 0.2
    if risk.p_dilution_gap < 0.10:
        score += 0.15
    if risk.p_halt_event < 0.05:
        score += 0.1
    if risk.expected_slippage < 0.02:
        score += 0.15
    return min(score, 1.0)


def compute_calibration_bucket(p_event: float) -> int:
    if p_event < 0.02:
        return 0
    if p_event < 0.05:
        return 1
    if p_event < 0.10:
        return 2
    if p_event < 0.20:
        return 3
    if p_event < 0.35:
        return 4
    return 5


def assemble_prediction(
    ticker: str,
    timestamp: str,
    snapshot_type: SnapshotType,
    hazard: HazardEstimate,
    payoff: PayoffEstimate,
    risk: RiskEstimate,
    config: ModelConfig,
) -> RunnerPrediction:
    utility_by_policy = compute_all_utilities(hazard, payoff, risk, config)
    recommended = select_optimal_policy(utility_by_policy, config)
    pos_codes, neg_codes = build_reason_codes(hazard, payoff, risk)
    confidence = compute_confidence(hazard, payoff, risk)
    bucket = compute_calibration_bucket(hazard.p_run_5d)

    return RunnerPrediction(
        ticker=ticker,
        timestamp=timestamp,
        snapshot_type=snapshot_type,
        hazard=hazard,
        payoff=payoff,
        risk=risk,
        utility_by_policy=utility_by_policy,
        recommended_policy=recommended,
        positive_reason_codes=pos_codes,
        negative_reason_codes=neg_codes,
        confidence_score=confidence,
        calibration_bucket=bucket,
    )
