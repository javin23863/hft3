"""Basis yield and Ornstein-Uhlenbeck mean reversion on spot/perp basis."""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np


def annualized_continuous_basis_yield(
    spot: float,
    perp: float,
    *,
    funding_horizon_hours: float = 8.0,
) -> float:
    """Y_t = ln(S_t / P_t) * (365*24 / h)."""
    if spot <= 0 or perp <= 0 or funding_horizon_hours <= 0:
        return 0.0
    return math.log(spot / perp) * (365.0 * 24.0 / funding_horizon_hours)


def basis_series(spot: Sequence[float], perp: Sequence[float]) -> np.ndarray:
    s = np.asarray(spot, dtype=float)
    p = np.asarray(perp, dtype=float)
    return p - s


def fit_ou_ar1(b: Sequence[float], dt: float = 1.0) -> tuple[float, float, float]:
    """
    Discretized OU via AR(1): B_{t+1} = a + phi B_t + eps.
    Returns (theta, mu, sigma) with phi = 1 - theta*dt.
    """
    x = np.asarray(b, dtype=float)
    if x.size < 3:
        return 0.0, float(np.mean(x)) if x.size else 0.0, 0.0
    y = x[1:]
    z = x[:-1]
    A = np.column_stack([np.ones_like(z), z])
    coef, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
    a, phi = float(coef[0]), float(coef[1])
    phi = max(min(phi, 0.9999), 0.0001)
    theta = (1.0 - phi) / dt
    mu = a / (1.0 - phi) if abs(1.0 - phi) > 1e-9 else float(np.mean(x))
    resid = y - (a + phi * z)
    sigma = float(np.std(resid, ddof=1)) if resid.size > 1 else 0.0
    return max(theta, 0.0), mu, sigma


def ou_half_life_hours(theta: float) -> float:
    if theta <= 0:
        return float("inf")
    return math.log(2.0) / theta


def forward_basis_compression_flag(
    theta: float,
    horizon_hours: float,
) -> int:
    """1 if expected half-life is shorter than prediction horizon."""
    hl = ou_half_life_hours(theta)
    return int(hl <= horizon_hours and hl < float("inf"))
