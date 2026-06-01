"""Tests for crypto execution quality metrics (Phase 7)."""

from __future__ import annotations

import numpy as np
import pytest

from crypto_lane.src.validation.metrics import (
    compute_execution_quality,
    compute_pnl_trade,
    jump_metric,
    qqe_metric,
    slippage_bps,
)


class TestSlippageBps:
    def test_buy_adverse_slippage(self):
        result = slippage_bps(fill_price=101.0, reference_price=100.0, side="BUY")
        assert result == pytest.approx(100.0, rel=1e-9)

    def test_buy_favorable_slippage(self):
        result = slippage_bps(fill_price=99.0, reference_price=100.0, side="BUY")
        assert result == pytest.approx(-100.0, rel=1e-9)

    def test_sell_adverse_slippage(self):
        result = slippage_bps(fill_price=99.0, reference_price=100.0, side="SELL")
        assert result == pytest.approx(100.0, rel=1e-9)

    def test_sell_favorable_slippage(self):
        result = slippage_bps(fill_price=101.0, reference_price=100.0, side="SELL")
        assert result == pytest.approx(-100.0, rel=1e-9)

    def test_zero_reference_price(self):
        result = slippage_bps(fill_price=100.0, reference_price=0.0, side="BUY")
        assert result == 0.0

    def test_case_insensitive(self):
        assert slippage_bps(100, 99, "buy") > 0
        assert slippage_bps(100, 99, "BuY") > 0
        assert slippage_bps(100, 99, "sell") < 0
        assert slippage_bps(100, 99, "SeLl") < 0


class TestJumpMetric:
    def test_no_adverse_selection(self):
        fill_events = [
            {"side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0},
        ]
        prices = [100.5, 101.0, 101.5, 102.0, 102.5]
        result = jump_metric(fill_events, prices)
        assert result == pytest.approx(250.0, rel=1e-6)

    def test_adverse_selection(self):
        fill_events = [
            {"side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0},
        ]
        prices = [99.5, 99.0, 98.5, 98.0, 97.5]
        result = jump_metric(fill_events, prices)
        assert result == pytest.approx(-250.0, rel=1e-6)

    def test_sell_favorable(self):
        fill_events = [
            {"side": "SELL", "avg_fill_price": 100.0, "filled_quantity": 1.0},
        ]
        prices = [99.5, 99.0, 98.5, 98.0, 97.5]
        result = jump_metric(fill_events, prices)
        assert result == pytest.approx(250.0, rel=1e-6)

    def test_sell_adverse(self):
        fill_events = [
            {"side": "SELL", "avg_fill_price": 100.0, "filled_quantity": 1.0},
        ]
        prices = [100.5, 101.0, 101.5, 102.0, 102.5]
        result = jump_metric(fill_events, prices)
        assert result == pytest.approx(-250.0, rel=1e-6)

    def test_empty_fills(self):
        assert jump_metric([], [100.0]) == 0.0

    def test_too_few_prices(self):
        fills = [{"side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0}]
        assert jump_metric(fills, [100.0]) == 0.0

    def test_multiple_fills(self):
        fill_events = [
            {"side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0},
            {"side": "SELL", "avg_fill_price": 102.0, "filled_quantity": 1.0},
        ]
        prices = [101.0, 102.0, 103.0, 104.0, 105.0, 99.0, 98.0, 97.0, 96.0, 95.0]
        result = jump_metric(fill_events, prices)
        assert result == pytest.approx(593.14, rel=1e-3)


class TestQqeMetric:
    def test_single_level_back_of_queue(self):
        depth = [
            {"price": 100.0, "qty": 10.0},
        ]
        result = qqe_metric(depth, "BUY", 100.0, 0.1)
        assert result == 0.0

    def test_empty_depth(self):
        assert qqe_metric([], "BUY", 100.0, 0.1) == 1.0

    def test_l3_front(self):
        depth = [
            {"price": 100.0, "order_sequence": 0.0, "total_orders": 5.0},
        ]
        result = qqe_metric(depth, "BUY", 100.0, 0.1)
        assert result == pytest.approx(1.0, rel=1e-6)

    def test_l3_middle(self):
        depth = [
            {"price": 100.0, "order_sequence": 2.0, "total_orders": 5.0},
        ]
        result = qqe_metric(depth, "BUY", 100.0, 0.1)
        assert result == pytest.approx(0.6, rel=1e-6)

    def test_l3_back(self):
        depth = [
            {"price": 100.0, "order_sequence": 4.0, "total_orders": 5.0},
        ]
        result = qqe_metric(depth, "BUY", 100.0, 0.1)
        assert result == pytest.approx(0.2, rel=1e-6)

    def test_side_with_multiple_levels(self):
        depth = [
            {"price": 99.8, "qty": 10.0},
            {"price": 100.0, "qty": 5.0},
            {"price": 100.2, "qty": 20.0},
        ]
        result = qqe_metric(depth, "BUY", 100.0, 0.01)
        assert result == pytest.approx(0.5, rel=1e-6)


class TestComputePnlTrade:
    def test_profit(self):
        assert compute_pnl_trade(100.0, 110.0, 1.0) == 10.0

    def test_loss(self):
        assert compute_pnl_trade(100.0, 90.0, 1.0) == -10.0

    def test_zero_qty(self):
        assert compute_pnl_trade(100.0, 110.0, 0.0) == 0.0


class TestComputeExecutionQuality:
    def test_aggregates_metrics(self):
        fill_events = [
            {
                "side": "BUY",
                "avg_fill_price": 101.0,
                "filled_quantity": 1.0,
                "reference_price": 100.0,
            },
            {
                "side": "SELL",
                "avg_fill_price": 99.0,
                "filled_quantity": 1.0,
                "reference_price": 100.0,
            },
        ]
        result = compute_execution_quality(
            fill_events=fill_events,
            order_book_snapshots=None,
            subsequent_mid_prices=[100.5, 100.6, 100.7, 100.8, 100.9],
        )
        assert result["mean_slippage_bps"] == pytest.approx(100.0, abs=0.1)
        assert result["mean_jump_bps"] == pytest.approx(-9.9, rel=0.1)

    def test_empty_fills(self):
        result = compute_execution_quality([])
        assert result["mean_slippage_bps"] == 0.0
        assert result["mean_jump_bps"] == 0.0
        assert result["mean_qqe"] == 0.0
