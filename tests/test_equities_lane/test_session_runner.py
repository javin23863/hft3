"""Tests for the session runner experiment (session_runner.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


class TestRouteReachability:
    """All 4 routes are reachable from the comparator under valid inputs."""

    def test_all_routes_reachable(self):
        from equities_lane.src.route.comparator import RouteInputs, compare_routes
        from equities_lane.src.ontology.payoff import (
            ROUTE_STOCK_ONLY,
            ROUTE_OPTION_ONLY,
            ROUTE_STOCK_AND_OPTION,
            ROUTE_NO_TRADE,
        )

        routes_seen = set()
        configs = [
            RouteInputs(
                underlying_symbol="T", session_date="2024-01-15",
                decision_timestamp_ns=100,
                stock_expected_value=10.0, option_expected_value=0.3,
                expected_slippage_stock=0.0, expected_slippage_option=0.0,
                spread_cost_stock=0.0, spread_cost_option=0.0,
                fill_probability_stock=0.9, fill_probability_option=0.8,
                latency_assumption_stock_us=0, latency_assumption_option_us=0,
                max_loss_stock=100.0, max_loss_option=50.0,
                convexity_exposure=0.0, gamma_exposure=0.0, delta_exposure=0.0,
                theta_decay_window_seconds=0, liquidity_score_stock=0.9,
                liquidity_score_option=0.5, borrow_shortability_constraint="long_only",
                selected_option_contracts=(),
                equity_features_used=("a",), option_features_used=("b",),
            ),
            RouteInputs(
                underlying_symbol="T", session_date="2024-01-15",
                decision_timestamp_ns=100,
                stock_expected_value=0.3, option_expected_value=10.0,
                expected_slippage_stock=0.0, expected_slippage_option=0.0,
                spread_cost_stock=0.0, spread_cost_option=0.0,
                fill_probability_stock=0.9, fill_probability_option=0.8,
                latency_assumption_stock_us=0, latency_assumption_option_us=0,
                max_loss_stock=100.0, max_loss_option=50.0,
                convexity_exposure=0.0, gamma_exposure=0.0, delta_exposure=0.0,
                theta_decay_window_seconds=0, liquidity_score_stock=0.9,
                liquidity_score_option=0.5, borrow_shortability_constraint="long_only",
                selected_option_contracts=(),
                equity_features_used=("a",), option_features_used=("b",),
            ),
            RouteInputs(
                underlying_symbol="T", session_date="2024-01-15",
                decision_timestamp_ns=100,
                stock_expected_value=8.0, option_expected_value=7.0,
                expected_slippage_stock=0.0, expected_slippage_option=0.0,
                spread_cost_stock=0.0, spread_cost_option=0.0,
                fill_probability_stock=0.9, fill_probability_option=0.8,
                latency_assumption_stock_us=0, latency_assumption_option_us=0,
                max_loss_stock=100.0, max_loss_option=50.0,
                convexity_exposure=5.0, gamma_exposure=0.0, delta_exposure=0.0,
                theta_decay_window_seconds=0, liquidity_score_stock=0.9,
                liquidity_score_option=0.5, borrow_shortability_constraint="long_only",
                selected_option_contracts=(),
                equity_features_used=("a",), option_features_used=("b",),
            ),
            RouteInputs(
                underlying_symbol="T", session_date="2024-01-15",
                decision_timestamp_ns=100,
                stock_expected_value=-1.0, option_expected_value=-2.0,
                expected_slippage_stock=0.0, expected_slippage_option=0.0,
                spread_cost_stock=0.0, spread_cost_option=0.0,
                fill_probability_stock=0.9, fill_probability_option=0.8,
                latency_assumption_stock_us=0, latency_assumption_option_us=0,
                max_loss_stock=100.0, max_loss_option=50.0,
                convexity_exposure=0.0, gamma_exposure=0.0, delta_exposure=0.0,
                theta_decay_window_seconds=0, liquidity_score_stock=0.9,
                liquidity_score_option=0.5, borrow_shortability_constraint="long_only",
                selected_option_contracts=(),
                equity_features_used=("a",), option_features_used=("b",),
            ),
        ]
        for inp in configs:
            d = compare_routes(inp)
            routes_seen.add(d.final_route_decision)
            d.validate()

        assert ROUTE_STOCK_ONLY in routes_seen
        assert ROUTE_OPTION_ONLY in routes_seen
        assert ROUTE_STOCK_AND_OPTION in routes_seen
        assert ROUTE_NO_TRADE in routes_seen


class TestPITFilterIntegration:
    """End-to-end: PIT filter rejects future data and passes clean data."""

    def test_equity_ticks_clean_passes(self):
        from equities_lane.src.models import SessionTick
        from equities_lane.src.integrity.pit_filter import check_equity_ticks

        ticks = [
            SessionTick(ts_ns=100, bid_px=10.0, bid_sz=100, ask_px=10.1, ask_sz=100),
            SessionTick(ts_ns=200, bid_px=10.0, bid_sz=100, ask_px=10.1, ask_sz=100),
        ]
        result = check_equity_ticks(ticks, decision_ts_ns=200)
        assert result.is_pit_clean

    def test_equity_ticks_future_rejected(self):
        from equities_lane.src.models import SessionTick
        from equities_lane.src.integrity.pit_filter import check_equity_ticks

        ticks = [
            SessionTick(ts_ns=100, bid_px=10.0, bid_sz=100, ask_px=10.1, ask_sz=100),
            SessionTick(ts_ns=201, bid_px=10.0, bid_sz=100, ask_px=10.1, ask_sz=100),
        ]
        result = check_equity_ticks(ticks, decision_ts_ns=200)
        assert not result.is_pit_clean
        assert "equity leakage" in result.rejection_reason


class TestDecisionOutputFormat:
    """Verify the decision output has all required fields for the engineering report."""

    def test_decision_has_all_report_fields(self):
        from equities_lane.src.route.comparator import RouteInputs, compare_routes

        inp = RouteInputs(
            underlying_symbol="EXPR", session_date="2024-01-15",
            decision_timestamp_ns=1705312200000000000,
            stock_expected_value=5.0, option_expected_value=3.0,
            expected_slippage_stock=0.05, expected_slippage_option=0.10,
            spread_cost_stock=0.05, spread_cost_option=0.10,
            fill_probability_stock=0.9, fill_probability_option=0.7,
            latency_assumption_stock_us=5000.0, latency_assumption_option_us=8000.0,
            max_loss_stock=100.0, max_loss_option=50.0,
            convexity_exposure=0.0, gamma_exposure=0.0, delta_exposure=0.0,
            theta_decay_window_seconds=3600.0,
            liquidity_score_stock=0.9, liquidity_score_option=0.5,
            borrow_shortability_constraint="long_only",
            selected_option_contracts=(),
            equity_features_used=("ofi_zscore",), option_features_used=("iv_atm",),
        )
        d = compare_routes(inp)
        d.validate()
        out = d.to_dict()
        required = [
            "underlying_symbol", "session_date", "decision_timestamp_ns",
            "final_route_decision", "payoff", "reason_codes",
            "ontology_claim_ids", "pdf_citations", "leakage_status",
        ]
        for field in required:
            assert field in out, f"missing field: {field}"
