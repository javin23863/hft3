"""Realized variance, VRP, and put-call parity residual."""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np


def realized_variance(log_returns: Sequence[float], annualization: float = 365.0 * 24.0) -> float:
    r = np.asarray(log_returns, dtype=float)
    if r.size == 0:
        return 0.0
    return float(annualization * np.mean(r ** 2))


def realized_volatility(log_returns: Sequence[float], annualization: float = 365.0 * 24.0) -> float:
    return math.sqrt(max(0.0, realized_variance(log_returns, annualization)))


def volatility_risk_premium(atm_iv: float, expected_rv: float) -> float:
    """VRP_t = IV_t^ATM - E_t[RV_{t,t+h}]."""
    return float(atm_iv - expected_rv)


def put_call_parity_residual(
    call: float,
    put: float,
    spot: float,
    strike: float,
    *,
    rate: float = 0.0,
    yield_q: float = 0.0,
    tau_years: float,
) -> float:
    """ε_t = C - P - S e^{-qτ} + K e^{-rτ}."""
    df_r = math.exp(-rate * tau_years)
    df_q = math.exp(-yield_q * tau_years)
    return float(call - put - spot * df_q + strike * df_r)
