"""Tests for features_engine.src.features.realized_measures.

Synthetic checks:
  1. GBM simulation — subsampled_rv within 20% of sigma^2 * T.
  2. Pure up-moves  — RS_plus approx RV, RS_minus approx 0.
  3. Symmetric returns — realized_skewness approx 0.
  4. Deterministic small vectors with hand-computed expected values.
  5. Empty / short inputs -> nan, never raises.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from features_engine.src.features.realized_measures import (
    log_returns,
    realized_kurtosis,
    realized_quarticity,
    realized_semivariances,
    realized_skewness,
    realized_variance,
    subsampled_rv,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NS = 1_000_000_000  # 1 s in nanoseconds


def _gbm_trades(
    sigma: float,
    T: float,
    n_trades: int,
    p0: float = 100.0,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate synthetic GBM trades at uniform timestamps.

    Parameters
    ----------
    sigma:  per-second vol (sigma^2 * T is the total variance).
    T:      total time in seconds.
    n_trades: number of trade events.
    p0:     initial price.
    seed:   RNG seed for reproducibility.

    Returns
    -------
    (ts_ns, px) as int64 and float64 arrays.
    """
    rng = np.random.default_rng(seed)
    dt = T / n_trades
    increments = sigma * math.sqrt(dt) * rng.standard_normal(n_trades)
    log_px = np.cumsum(np.concatenate([[math.log(p0)], increments]))
    px = np.exp(log_px)
    ts_sec = np.linspace(0, T, n_trades + 1)
    ts_ns = (ts_sec * _NS).astype(np.int64)
    return ts_ns, px


# ---------------------------------------------------------------------------
# 1. GBM — subsampled RV within tolerance of sigma^2 * T
# ---------------------------------------------------------------------------


def test_gbm_subsampled_rv_within_tolerance() -> None:
    """subsampled_rv should recover sigma^2*T within 20% on a large GBM path."""
    sigma = 0.02          # per-second vol (sigma^2 * T = 0.0004 * 6500 = 2.6)
    T = 6_500.0           # seconds in a trading day
    n_trades = 100_000

    ts_ns, px = _gbm_trades(sigma=sigma, T=T, n_trades=n_trades, seed=1)
    grid_ns = _NS         # 1-second grid

    srv = subsampled_rv(ts_ns, px, grid_ns, n_offsets=5)
    expected = sigma ** 2 * T
    assert not math.isnan(srv), "subsampled_rv returned nan for GBM path"
    rel_err = abs(srv - expected) / expected
    assert rel_err < 0.20, (
        f"RV error too large: got {srv:.6f}, expected {expected:.6f} (rel={rel_err:.2%})"
    )


# ---------------------------------------------------------------------------
# 2. Pure up-moves — RS_plus approx RV, RS_minus == 0
# ---------------------------------------------------------------------------


def test_pure_up_moves_semivariance() -> None:
    """All-positive returns should give RS_plus = RV and RS_minus = 0."""
    returns = np.array([0.01, 0.02, 0.005, 0.015])
    rv = realized_variance(returns)
    rs_minus, rs_plus = realized_semivariances(returns)
    np.testing.assert_allclose(rs_plus, rv, rtol=1e-12)
    assert rs_minus == 0.0, f"RS_minus should be 0 for pure up-moves, got {rs_minus}"


# ---------------------------------------------------------------------------
# 3. Symmetric returns — realized_skewness approx 0
# ---------------------------------------------------------------------------


def test_symmetric_returns_skewness_near_zero() -> None:
    """Perfectly symmetric returns (r, -r) should give realized_skewness = 0."""
    base = np.array([0.01, 0.02, 0.005, 0.015, 0.003])
    returns = np.concatenate([base, -base])
    rskew = realized_skewness(returns, grid_per_day=len(returns))
    assert abs(rskew) < 1e-10, f"Expected skewness approx 0, got {rskew}"


# ---------------------------------------------------------------------------
# 4. Deterministic small-vector hand-computed values
# ---------------------------------------------------------------------------


class TestDeterministicSmallVectors:
    """Hand-computed golden values for 3 returns [0.1, -0.1, 0.1]."""

    returns = np.array([0.1, -0.1, 0.1])
    grid_per_day = 3.0

    def test_realized_variance(self) -> None:
        # RV = 0.01 + 0.01 + 0.01 = 0.03
        rv = realized_variance(self.returns)
        np.testing.assert_allclose(rv, 0.03, rtol=1e-12)

    def test_realized_quarticity(self) -> None:
        # RQ = (N/3) * sum(r^4) = (3/3) * 3*(0.1^4) = 1 * 3*0.0001 = 0.0003
        rq = realized_quarticity(self.returns, self.grid_per_day)
        np.testing.assert_allclose(rq, 0.0003, rtol=1e-12)

    def test_realized_semivariances(self) -> None:
        # RS- = 0.01  (only r=-0.1)
        # RS+ = 0.02  (r=0.1 twice)
        rs_minus, rs_plus = realized_semivariances(self.returns)
        np.testing.assert_allclose(rs_minus, 0.01, rtol=1e-12)
        np.testing.assert_allclose(rs_plus, 0.02, rtol=1e-12)
        # RS- + RS+ should equal RV (up to float precision)
        rv = realized_variance(self.returns)
        np.testing.assert_allclose(rs_minus + rs_plus, rv, rtol=1e-12)

    def test_realized_skewness_formula(self) -> None:
        # sum(r^3) = 0.1^3 + (-0.1)^3 + 0.1^3 = 0.001 - 0.001 + 0.001 = 0.001
        # RSkew = sqrt(3) * 0.001 / 0.03^1.5
        rv = 0.03
        sum_r3 = 0.001
        expected = math.sqrt(3.0) * sum_r3 / (rv ** 1.5)
        rskew = realized_skewness(self.returns, self.grid_per_day)
        np.testing.assert_allclose(rskew, expected, rtol=1e-10)

    def test_realized_kurtosis_formula(self) -> None:
        # sum(r^4) = 3 * 0.0001 = 0.0003
        # RKurt = 3 * 0.0003 / 0.03^2 = 0.0009 / 0.0009 = 1.0
        rv = 0.03
        sum_r4 = 0.0003
        expected = self.grid_per_day * sum_r4 / (rv ** 2)
        rkurt = realized_kurtosis(self.returns, self.grid_per_day)
        np.testing.assert_allclose(rkurt, expected, rtol=1e-10)

    def test_log_returns_resampling(self) -> None:
        """Verify log_returns resampling with manually constructed timestamps."""
        # Two trades 2 s apart; grid = 1 s -> 3 grid bars, 2 returns
        # px: 100.0 at t=0s, 101.0 at t=2s
        # Bar 0 (t=0s): last trade <= 0s -> px=100.0
        # Bar 1 (t=1s): last trade <= 1s -> px=100.0 (forward-fill)
        # Bar 2 (t=2s): last trade <= 2s -> px=101.0
        # returns: log(100/100)=0, log(101/100)=log(1.01)
        ts_ns = np.array([0, 2 * _NS], dtype=np.int64)
        px = np.array([100.0, 101.0])
        rets = log_returns(ts_ns, px, _NS)
        assert rets.size == 2, f"Expected 2 returns, got {rets.size}"
        np.testing.assert_allclose(rets[0], 0.0, atol=1e-15)
        np.testing.assert_allclose(rets[1], math.log(101.0 / 100.0), rtol=1e-12)


# ---------------------------------------------------------------------------
# 5. Empty / short inputs -> nan, never raises
# ---------------------------------------------------------------------------


class TestEmptyAndShortInputs:

    def test_log_returns_empty(self) -> None:
        rets = log_returns(np.array([], dtype=np.int64), np.array([]), _NS)
        assert rets.size == 0

    def test_log_returns_single(self) -> None:
        rets = log_returns(np.array([0], dtype=np.int64), np.array([100.0]), _NS)
        assert rets.size == 0

    def test_realized_variance_empty(self) -> None:
        assert math.isnan(realized_variance(np.array([])))

    def test_realized_variance_single(self) -> None:
        assert math.isnan(realized_variance(np.array([0.01])))

    def test_subsampled_rv_empty(self) -> None:
        assert math.isnan(
            subsampled_rv(np.array([], dtype=np.int64), np.array([]), _NS, 5)
        )

    def test_realized_quarticity_empty(self) -> None:
        assert math.isnan(realized_quarticity(np.array([]), 23400))

    def test_realized_semivariances_empty(self) -> None:
        rs_minus, rs_plus = realized_semivariances(np.array([]))
        assert math.isnan(rs_minus) and math.isnan(rs_plus)

    def test_realized_semivariances_single(self) -> None:
        rs_minus, rs_plus = realized_semivariances(np.array([0.01]))
        assert math.isnan(rs_minus) and math.isnan(rs_plus)

    def test_realized_skewness_empty(self) -> None:
        assert math.isnan(realized_skewness(np.array([]), 23400))

    def test_realized_skewness_zero_rv(self) -> None:
        # All-zero returns -> RV=0 -> nan guard
        assert math.isnan(realized_skewness(np.array([0.0, 0.0, 0.0]), 3))

    def test_realized_kurtosis_empty(self) -> None:
        assert math.isnan(realized_kurtosis(np.array([]), 23400))

    def test_realized_kurtosis_zero_rv(self) -> None:
        assert math.isnan(realized_kurtosis(np.array([0.0, 0.0, 0.0]), 3))

    @pytest.mark.parametrize("n_offsets", [0, 1, 5])
    def test_subsampled_rv_single_trade(self, n_offsets: int) -> None:
        ts = np.array([0], dtype=np.int64)
        px = np.array([100.0])
        result = subsampled_rv(ts, px, _NS, n_offsets)
        assert math.isnan(result)
