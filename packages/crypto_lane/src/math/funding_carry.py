"""Funding pressure and expected net carry."""
from __future__ import annotations

from typing import Sequence

import numpy as np


def funding_zscore(series: Sequence[float], window: int) -> float:
    x = np.asarray(series, dtype=float)
    if x.size == 0:
        return 0.0
    w = x[-window:] if x.size >= window else x
    mu = float(np.mean(w))
    sigma = float(np.std(w, ddof=1)) if w.size > 1 else 1.0
    if sigma <= 0:
        return 0.0
    return float((x[-1] - mu) / sigma)


def ar1_forecast(series: Sequence[float], steps: int) -> np.ndarray:
    x = np.asarray(series, dtype=float)
    if x.size < 2:
        return np.full(steps, x[-1] if x.size else 0.0)
    y = x[1:]
    z = x[:-1]
    A = np.column_stack([np.ones_like(z), z])
    coef, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
    a, phi = float(coef[0]), float(coef[1])
    out = []
    level = float(x[-1])
    for _ in range(steps):
        level = a + phi * level
        out.append(level)
    return np.asarray(out)


def latent_funding_pressure(series: Sequence[float], window: int = 24) -> float:
    """AR(1) smoothed funding level as latent pressure proxy."""
    x = np.asarray(series, dtype=float)
    if x.size < 3:
        return float(x[-1]) if x.size else 0.0
    w = x[-window:] if x.size >= window else x
    fc = ar1_forecast(w, 1)
    return float(fc[0])


def hedge_drift_estimate(spot_returns: Sequence[float], perp_returns: Sequence[float]) -> float:
    s = np.asarray(spot_returns, dtype=float)
    p = np.asarray(perp_returns, dtype=float)
    n = min(s.size, p.size)
    if n < 2:
        return 0.0
    s, p = s[-n:], p[-n:]
    cov = float(np.cov(s, p, ddof=1)[0, 1])
    var_s = float(np.var(s, ddof=1))
    beta = cov / var_s if var_s > 1e-12 else 1.0
    return float(np.sum(p - beta * s))


def expected_net_carry(
    funding: Sequence[float],
    perp_prices: Sequence[float],
    *,
    k_steps: int = 1,
    hedge_drift: float = 0.0,
    costs: float = 0.0,
) -> float:
    """E[Carry] ≈ Σ F_{t+i} P_{t+i} - ΔDrift - Costs."""
    f_fc = ar1_forecast(funding, k_steps)
    p_fc = ar1_forecast(perp_prices, k_steps)
    gross = float(np.sum(f_fc * p_fc))
    return gross - hedge_drift - costs
