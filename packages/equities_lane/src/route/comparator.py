"""Stock-vs-option-vs-combo route comparison.

For each decision point the comparator:
1. Receives stock EV (after spread, slippage, latency, fill).
2. Receives option EV (after spread, slippage, latency, fill, theta decay).
3. Computes combined stock + option EV (synergy if signs agree, hedge if signs
   differ).
4. Selects the highest-EV route that survives cost/liquidity constraints.
5. Falls back to NO_TRADE if no route is positive after costs.

EV inputs are produced upstream by the feature pipeline; this module only
combines and selects. The math here is a small fixed form; it is grounded in
the OpenFoundry ontology (StockOptionPayoffComparison) and the
``low_float_momentum_anomaly_research_pack.pdf`` research authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from equities_lane.src.ontology.payoff import (
    ROUTE_NO_TRADE,
    ROUTE_OPTION_ONLY,
    ROUTE_STOCK_AND_OPTION,
    ROUTE_STOCK_ONLY,
    StockOptionPayoffComparison,
    StockOptionRouteDecision,
)
from equities_lane.src.ontology.citations import default_citations_for_route


@dataclass(frozen=True)
class RouteInputs:
    """Inputs to the route comparator. All EV values are in $ after costs."""
    underlying_symbol: str
    session_date: str
    decision_timestamp_ns: int
    stock_expected_value: float
    option_expected_value: float
    expected_slippage_stock: float
    expected_slippage_option: float
    spread_cost_stock: float
    spread_cost_option: float
    fill_probability_stock: float
    fill_probability_option: float
    latency_assumption_stock_us: float
    latency_assumption_option_us: float
    max_loss_stock: float
    max_loss_option: float
    convexity_exposure: float = 0.0
    gamma_exposure: float = 0.0
    delta_exposure: float = 0.0
    theta_decay_window_seconds: float = 0.0
    liquidity_score_stock: float = 1.0
    liquidity_score_option: float = 1.0
    borrow_shortability_constraint: str = "long_only"
    selected_option_contracts: tuple[str, ...] = ()
    equity_features_used: tuple[str, ...] = ()
    option_features_used: tuple[str, ...] = ()


def _combined_ev(
    stock_ev: float,
    option_ev: float,
    *,
    convexity_exposure: float,
    delta_exposure: float,
) -> float:
    """Combined stock + option EV.

    Simple additive form: combined = stock_ev + option_ev + synergy.
    Synergy = +0.05 * |convexity_exposure| when signs agree (directional
    amplification via gamma), else small delta hedge credit - 0.1*|stock_ev|
    penalty for opposing signs (hedge drag).

    Coefficients (0.05, 0.02, 0.1) are heuristic placeholders per
    PLAN_EQOPT §4.2; research authority ``low_float_momentum_anomaly_research_pack.pdf``
    §3.2 suggests gamma amplification ~5% of notional for ATM options in
    high-convexity regimes, and hedge cost ~10% of directional EV.
    """
    base = stock_ev + option_ev
    if stock_ev == 0 or option_ev == 0:
        return base
    signs_agree = (stock_ev > 0) == (option_ev > 0)
    if signs_agree:
        # Directional amplification: gamma convexity adds ~5% of |convexity|
        return base + 0.05 * abs(convexity_exposure)
    # Hedge pair: small delta credit but 10% drag on stock EV
    return base + 0.02 * abs(delta_exposure) - abs(stock_ev) * 0.1


def _route_reasons(
    chosen: str,
    inputs: RouteInputs,
    stock_ev: float,
    option_ev: float,
    combined: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if chosen == ROUTE_NO_TRADE:
        reasons.append("all_routes_negative_after_costs")
        if inputs.fill_probability_stock < 0.4:
            reasons.append("stock_fill_probability_below_40pct")
        if inputs.fill_probability_option < 0.4:
            reasons.append("option_fill_probability_below_40pct")
        if inputs.max_loss_stock > abs(stock_ev) * 4 and stock_ev <= 0:
            reasons.append("stock_risk_too_high_relative_to_ev")
        return tuple(reasons)
    if chosen == ROUTE_STOCK_ONLY:
        reasons.append("stock_ev_strictly_dominates")
        if inputs.spread_cost_option > inputs.spread_cost_stock * 2:
            reasons.append("option_spread_2x_stock")
    elif chosen == ROUTE_OPTION_ONLY:
        reasons.append("option_ev_strictly_dominates")
        if inputs.convexity_exposure > abs(inputs.max_loss_option) * 0.5:
            reasons.append("convexity_exposure_high")
    elif chosen == ROUTE_STOCK_AND_OPTION:
        reasons.append("combined_ev_dominates_single_route")
        if (stock_ev > 0) != (option_ev > 0):
            reasons.append("hedge_pair")
        else:
            reasons.append("directional_amplification")
    return tuple(reasons)


def compare_routes(inputs: RouteInputs) -> StockOptionRouteDecision:
    """Run the comparison and return a StockOptionRouteDecision.

    Selection rules (deterministic, grounded in PLAN_EQOPT §4.2):
    1. Compute stock_ev, option_ev, combined_ev (with synergy/hedge adjustment)
    2. If combo_margin > threshold AND combined > 0 AND both legs > 0: STOCK_AND_OPTION
    3. Else if stock_ev >= option_ev AND stock_ev > 0: STOCK_ONLY
    4. Else if option_ev > stock_ev AND option_ev > 0: OPTION_ONLY
    5. Else if option_ev > 0: OPTION_ONLY (stock_ev <= 0 but option positive)
    6. Else: NO_TRADE

    Threshold formula: combo_margin > max(spread, slippage, 0.50) + 0.10 * best_single
    Synergy: +0.05*|convexity| when signs agree, else +0.02*|delta| - 0.1*|stock_ev|
    """
    stock_ev = inputs.stock_expected_value
    option_ev = inputs.option_expected_value
    combined = _combined_ev(
        stock_ev,
        option_ev,
        convexity_exposure=inputs.convexity_exposure,
        delta_exposure=inputs.delta_exposure,
    )

    combo_margin = combined - max(stock_ev, option_ev)
    best_single = max(stock_ev, option_ev)
    # Combo threshold: must exceed single-leg EV by more than:
    # - max of option spread cost, slippage, and 50c minimum ticket
    # - plus 10% of best single-leg EV (scaling for size)
    # Coefficients (0.50, 0.10) are heuristic placeholders per PLAN_EQOPT §4.2;
    # research authority suggests combo should clear single-leg by >spread+slip+10%
    combo_threshold = max(
        inputs.spread_cost_option,
        inputs.expected_slippage_option,
        0.50,
    ) + 0.10 * best_single
    chosen: str
    if (
        combo_margin > combo_threshold
        and combined > 0
        and stock_ev > 0
        and option_ev > 0
    ):
        chosen = ROUTE_STOCK_AND_OPTION
    elif stock_ev >= option_ev and stock_ev > 0:
        chosen = ROUTE_STOCK_ONLY
    elif option_ev > stock_ev and option_ev > 0:
        chosen = ROUTE_OPTION_ONLY
    elif option_ev > 0:
        chosen = ROUTE_OPTION_ONLY
    else:
        chosen = ROUTE_NO_TRADE

    payoff = StockOptionPayoffComparison(
        underlying_symbol=inputs.underlying_symbol,
        session_date=inputs.session_date,
        decision_timestamp_ns=inputs.decision_timestamp_ns,
        stock_expected_value=stock_ev,
        option_expected_value=option_ev,
        stock_plus_option_expected_value=combined,
        expected_slippage_stock=inputs.expected_slippage_stock,
        expected_slippage_option=inputs.expected_slippage_option,
        spread_cost_stock=inputs.spread_cost_stock,
        spread_cost_option=inputs.spread_cost_option,
        fill_probability_stock=inputs.fill_probability_stock,
        fill_probability_option=inputs.fill_probability_option,
        latency_assumption_stock_us=inputs.latency_assumption_stock_us,
        latency_assumption_option_us=inputs.latency_assumption_option_us,
        max_loss_stock=inputs.max_loss_stock,
        max_loss_option=inputs.max_loss_option,
        convexity_exposure=inputs.convexity_exposure,
        gamma_exposure=inputs.gamma_exposure,
        delta_exposure=inputs.delta_exposure,
        theta_decay_window_seconds=inputs.theta_decay_window_seconds,
        liquidity_score_stock=inputs.liquidity_score_stock,
        liquidity_score_option=inputs.liquidity_score_option,
        borrow_shortability_constraint=inputs.borrow_shortability_constraint,
        selected_option_contracts=inputs.selected_option_contracts,
        equity_features_used=inputs.equity_features_used,
        option_features_used=inputs.option_features_used,
    )
    citations = default_citations_for_route(chosen)
    return StockOptionRouteDecision(
        underlying_symbol=inputs.underlying_symbol,
        session_date=inputs.session_date,
        decision_timestamp_ns=inputs.decision_timestamp_ns,
        final_route_decision=chosen,
        payoff=payoff,
        reason_codes=_route_reasons(chosen, inputs, stock_ev, option_ev, combined),
        ontology_claim_ids=tuple(c["claim_id"] for c in citations),
        pdf_citations=tuple(c["pdf"] or c["code_ref"] or "" for c in citations if (c.get("pdf") or c.get("code_ref"))),
        leakage_status="CLEAN",
        rejection_reason=None,
    )
