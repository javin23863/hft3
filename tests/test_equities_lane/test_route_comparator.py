"""Tests for the route comparator (comparator.py)."""
from __future__ import annotations

import pytest

from equities_lane.src.route.comparator import RouteInputs, compare_routes
from equities_lane.src.ontology.payoff import (
    ROUTE_STOCK_ONLY,
    ROUTE_OPTION_ONLY,
    ROUTE_STOCK_AND_OPTION,
    ROUTE_NO_TRADE,
)


def _inputs(
    stock_ev: float = 10.0,
    option_ev: float = 0.0,
    convexity: float = 0.0,
    delta: float = 0.0,
    fill_stock: float = 0.9,
    fill_option: float = 0.8,
    spread_stock: float = 0.05,
    spread_option: float = 0.10,
    max_loss_stock: float = 100.0,
    max_loss_option: float = 50.0,
) -> RouteInputs:
    return RouteInputs(
        underlying_symbol="TEST",
        session_date="2024-01-15",
        decision_timestamp_ns=1705312200000000000,
        stock_expected_value=stock_ev,
        option_expected_value=option_ev,
        expected_slippage_stock=0.05,
        expected_slippage_option=0.10,
        spread_cost_stock=spread_stock,
        spread_cost_option=spread_option,
        fill_probability_stock=fill_stock,
        fill_probability_option=fill_option,
        latency_assumption_stock_us=5000.0,
        latency_assumption_option_us=8000.0,
        max_loss_stock=max_loss_stock,
        max_loss_option=max_loss_option,
        convexity_exposure=convexity,
        gamma_exposure=0.0,
        delta_exposure=delta,
        theta_decay_window_seconds=3600.0,
        liquidity_score_stock=0.9,
        liquidity_score_option=0.5,
        borrow_shortability_constraint="long_only",
        selected_option_contracts=(),
        equity_features_used=("ofi_zscore",),
        option_features_used=("iv_atm",),
    )


class TestCompareRoutes:
    def test_stock_only_when_stock_dominates(self):
        d = compare_routes(_inputs(stock_ev=10.0, option_ev=0.5))
        assert d.final_route_decision == ROUTE_STOCK_ONLY
        assert d.leakage_status == "CLEAN"

    def test_option_only_when_option_dominates(self):
        d = compare_routes(_inputs(stock_ev=0.5, option_ev=10.0))
        assert d.final_route_decision == ROUTE_OPTION_ONLY
        assert d.leakage_status == "CLEAN"

    def test_combo_when_combined_dominates(self):
        d = compare_routes(_inputs(stock_ev=8.0, option_ev=7.0, convexity=5.0))
        assert d.final_route_decision == ROUTE_STOCK_AND_OPTION

    def test_no_trade_when_both_negative(self):
        d = compare_routes(_inputs(stock_ev=-1.0, option_ev=-2.0))
        assert d.final_route_decision == ROUTE_NO_TRADE

    def test_no_trade_when_both_zero(self):
        d = compare_routes(_inputs(stock_ev=0.0, option_ev=0.0))
        assert d.final_route_decision == ROUTE_NO_TRADE

    def test_stock_only_when_option_ev_zero(self):
        d = compare_routes(_inputs(stock_ev=5.0, option_ev=0.0))
        assert d.final_route_decision == ROUTE_STOCK_ONLY

    def test_option_only_when_stock_ev_zero(self):
        d = compare_routes(_inputs(stock_ev=0.0, option_ev=5.0))
        assert d.final_route_decision == ROUTE_OPTION_ONLY

    def test_ineligible_option_route_cannot_win(self):
        inp = RouteInputs(
            **{
                **_inputs(stock_ev=0.0, option_ev=10.0).__dict__,
                "option_route_eligible": False,
                "option_route_block_reasons": ("synthetic_only_not_executable",),
            }
        )
        d = compare_routes(inp)
        assert d.final_route_decision == ROUTE_NO_TRADE
        assert d.payoff.option_expected_value == 10.0
        assert d.payoff.stock_plus_option_expected_value == 0.0
        assert "option_route_ineligible" in d.reason_codes
        assert "synthetic_only_not_executable" in d.reason_codes

    def test_ineligible_option_route_falls_back_to_stock(self):
        inp = RouteInputs(
            **{
                **_inputs(stock_ev=5.0, option_ev=10.0).__dict__,
                "option_route_eligible": False,
                "option_route_block_reasons": ("no_real_executable_option_quotes",),
            }
        )
        d = compare_routes(inp)
        assert d.final_route_decision == ROUTE_STOCK_ONLY
        assert d.payoff.option_expected_value == 10.0
        assert d.payoff.stock_plus_option_expected_value == 5.0
        assert "no_real_executable_option_quotes" in d.reason_codes

    def test_decision_validates(self):
        d = compare_routes(_inputs(stock_ev=5.0, option_ev=0.0))
        d.validate()

    def test_ontology_claim_ids_nonempty_for_clean(self):
        d = compare_routes(_inputs(stock_ev=5.0, option_ev=0.0))
        assert len(d.ontology_claim_ids) > 0

    def test_reason_codes_stock_only(self):
        d = compare_routes(_inputs(stock_ev=10.0, option_ev=0.5))
        assert "stock_ev_strictly_dominates" in d.reason_codes

    def test_reason_codes_option_only(self):
        d = compare_routes(_inputs(stock_ev=0.5, option_ev=10.0))
        assert "option_ev_strictly_dominates" in d.reason_codes

    def test_reason_codes_no_trade(self):
        d = compare_routes(_inputs(stock_ev=-1.0, option_ev=-2.0))
        assert "all_routes_negative_after_costs" in d.reason_codes

    def test_reason_codes_combo_directional(self):
        d = compare_routes(_inputs(stock_ev=8.0, option_ev=7.0, convexity=5.0))
        assert "combined_ev_dominates_single_route" in d.reason_codes
        assert "directional_amplification" in d.reason_codes

    def test_reason_codes_combo_hedge(self):
        d = compare_routes(_inputs(stock_ev=8.0, option_ev=-3.0, convexity=5.0, delta=2.0))
        assert d.final_route_decision == ROUTE_STOCK_ONLY

    def test_payoff_roundtrip_to_dict(self):
        d = compare_routes(_inputs(stock_ev=5.0, option_ev=0.0))
        p = d.payoff.to_dict()
        assert p["stock_expected_value"] == 5.0
        assert p["underlying_symbol"] == "TEST"

    def test_high_spread_option_leads_to_stock(self):
        d = compare_routes(_inputs(stock_ev=5.0, option_ev=0.5, spread_option=5.0))
        assert d.final_route_decision == ROUTE_STOCK_ONLY

    def test_low_fill_option_flags_in_no_trade(self):
        d = compare_routes(_inputs(stock_ev=-1.0, option_ev=-1.0, fill_option=0.2))
        assert "option_fill_probability_below_40pct" in d.reason_codes


class TestRouteInputsFrozen:
    def test_route_inputs_is_frozen(self):
        inp = _inputs()
        with pytest.raises(AttributeError):
            inp.stock_expected_value = 99.0
