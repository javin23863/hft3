"""VRP and parity residual tests."""
from __future__ import annotations

import pytest

from crypto_lane.src.math.vol_rv_vrp import (
    put_call_parity_residual,
    realized_volatility,
    volatility_risk_premium,
)


def test_vrp_positive_when_iv_exceeds_rv():
    assert volatility_risk_premium(0.6, 0.4) == pytest.approx(0.2)


def test_realized_vol_nonnegative():
    rv = realized_volatility([0.01, -0.01, 0.005])
    assert rv >= 0


def test_parity_residual_zero_at_equilibrium():
    # symmetric call/put around ATM with zero rates
    eps = put_call_parity_residual(100.0, 100.0, 100.0, 100.0, rate=0.0, yield_q=0.0, tau_years=0.25)
    assert abs(eps) < 1e-6
