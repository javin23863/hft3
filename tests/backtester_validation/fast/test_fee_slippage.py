"""T0: fee model unit checks."""
from __future__ import annotations

import pytest

from backtest_pipeline.src.fee_model import FeeModel


def test_fee_per_contract_es_member() -> None:
    fm = FeeModel(product="ES", tier="member")
    assert fm.get_fee_per_contract() == 0.35


def test_fee_per_contract_mes_non_member() -> None:
    fm = FeeModel(product="MES", tier="non_member")
    assert fm.get_fee_per_contract() == 0.25


def test_trade_cost_includes_slippage_for_market() -> None:
    fm = FeeModel(product="ES", tier="member")
    cost = fm.calculate_trade_cost(2, is_market_order=True, slippage_ticks=1, tick_value=12.50)
    assert cost == pytest.approx(0.35 * 2 + 12.50 * 2)


def test_trade_cost_limit_no_slippage() -> None:
    fm = FeeModel(product="ES", tier="member")
    cost = fm.calculate_trade_cost(1, is_market_order=False)
    assert cost == pytest.approx(0.35)
