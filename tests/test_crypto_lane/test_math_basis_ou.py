"""Golden tests for basis OU math."""
from __future__ import annotations

import math

from crypto_lane.src.math.basis_ou import (
    annualized_continuous_basis_yield,
    fit_ou_ar1,
    forward_basis_compression_flag,
    ou_half_life_hours,
)


def test_annualized_basis_yield_sign():
    y = annualized_continuous_basis_yield(100.0, 101.0, funding_horizon_hours=8.0)
    assert y < 0  # perp above spot


def test_ou_fit_recovers_mean_reversion():
    import numpy as np
    rng = np.random.default_rng(0)
    mu, theta, sigma, dt = 10.0, 0.5, 0.2, 0.1
    b = [mu + 5.0]
    for _ in range(200):
        b.append(b[-1] + theta * (mu - b[-1]) * dt + sigma * math.sqrt(dt) * rng.normal())
    est_theta, est_mu, _ = fit_ou_ar1(b, dt=dt)
    assert est_theta > 0
    assert abs(est_mu - mu) < 2.0
    assert forward_basis_compression_flag(est_theta, ou_half_life_hours(est_theta) + 1) == 1
