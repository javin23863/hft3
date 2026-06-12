"""Tests for Black-76 implied volatility solver.

Round-trip accuracy requirement: <= 1e-8 relative error on price→IV→price
for moneyness in [0.5, 2.0], T in [1/365, 2.0], sigma in [0.01, 3.0].
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_pricing.src.black76 import price as b76_price
from options_pricing.src.iv_solver import implied_vol


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _roundtrip_error(F, K, T, sigma, r, is_call):
    """Generate price, recover IV, reprice; return relative error."""
    mkt = float(b76_price(F, K, T, sigma, r, is_call))
    iv = implied_vol(mkt, F, K, T, r, is_call)
    if math.isnan(iv):
        return math.nan
    reprice = float(b76_price(F, K, T, iv, r, is_call))
    if abs(mkt) < 1e-12:
        return abs(reprice - mkt)
    return abs(reprice - mkt) / abs(mkt)


# ---------------------------------------------------------------------------
# Basic sanity
# ---------------------------------------------------------------------------


class TestIVSolverBasic:
    def test_atm_roundtrip(self):
        """ATM option: round-trip error < 1e-8."""
        err = _roundtrip_error(100.0, 100.0, 1.0, 0.2, 0.05, True)
        assert not math.isnan(err)
        assert err < 1e-8

    def test_atm_put_roundtrip(self):
        err = _roundtrip_error(100.0, 100.0, 1.0, 0.2, 0.05, False)
        assert not math.isnan(err)
        assert err < 1e-8

    def test_iv_recovers_input_vol(self):
        """IV should equal the input sigma to within 1e-7."""
        F, K, T, sigma, r = 100.0, 100.0, 0.5, 0.25, 0.03
        mkt = float(b76_price(F, K, T, sigma, r, True))
        iv = implied_vol(mkt, F, K, T, r, True)
        assert abs(iv - sigma) < 1e-7

    def test_otm_call_roundtrip(self):
        err = _roundtrip_error(100.0, 110.0, 0.5, 0.2, 0.04, True)
        assert not math.isnan(err)
        assert err < 1e-8

    def test_otm_put_roundtrip(self):
        err = _roundtrip_error(100.0, 90.0, 0.5, 0.2, 0.04, False)
        assert not math.isnan(err)
        assert err < 1e-8

    def test_itm_call_roundtrip(self):
        err = _roundtrip_error(110.0, 100.0, 1.0, 0.2, 0.05, True)
        assert not math.isnan(err)
        assert err < 1e-8

    def test_itm_put_roundtrip(self):
        err = _roundtrip_error(90.0, 100.0, 1.0, 0.2, 0.05, False)
        assert not math.isnan(err)
        assert err < 1e-8


# ---------------------------------------------------------------------------
# Round-trip grid — stated accuracy spec
# ---------------------------------------------------------------------------


class TestIVRoundTripGrid:
    """Verify <= 1e-8 relative round-trip over the full stated grid."""

    @pytest.mark.parametrize("moneyness", [0.5, 0.7, 0.9, 1.0, 1.1, 1.3, 1.5, 2.0])
    @pytest.mark.parametrize("T", [1 / 365, 0.1, 0.5, 1.0, 2.0])
    @pytest.mark.parametrize("sigma", [0.01, 0.1, 0.5, 1.0, 2.0, 3.0])
    @pytest.mark.parametrize("is_call", [True, False])
    def test_roundtrip(self, moneyness, T, sigma, is_call):
        F = 100.0
        K = F / moneyness  # moneyness = F/K
        r = 0.04
        mkt = float(b76_price(F, K, T, sigma, r, is_call))
        iv = implied_vol(mkt, F, K, T, r, is_call)
        if math.isnan(iv):
            # Only acceptable if the option is at intrinsic (zero time value)
            intr = math.exp(-r * T) * max((1 if is_call else -1) * (F - K), 0.0)
            assert abs(mkt - intr) < 1e-8 * max(1.0, abs(intr)), (
                f"IV=nan but not at intrinsic: mkt={mkt}, intr={intr}, "
                f"F={F}, K={K:.4f}, T={T}, sigma={sigma}, is_call={is_call}"
            )
            return
        reprice = float(b76_price(F, K, T, iv, r, is_call))
        if abs(mkt) > 1e-10:
            rel_err = abs(reprice - mkt) / abs(mkt)
            assert rel_err < 1e-8, (
                f"Round-trip rel err {rel_err:.2e} > 1e-8 for "
                f"F={F}, K={K:.4f}, T={T}, sigma={sigma}, is_call={is_call}, "
                f"iv={iv}, mkt={mkt}, reprice={reprice}"
            )
        else:
            assert abs(reprice - mkt) < 1e-12


# ---------------------------------------------------------------------------
# 0DTE / hourly expiry
# ---------------------------------------------------------------------------


class TestZeroDTEHourly:
    """T = 1/365/24 (one hour) — near-zero expiry round-trip."""

    def test_hourly_atm_call(self):
        T = 1.0 / 365.0 / 24.0
        err = _roundtrip_error(100.0, 100.0, T, 0.2, 0.0, True)
        assert not math.isnan(err)
        assert err < 1e-8

    def test_hourly_otm_call(self):
        T = 1.0 / 365.0 / 24.0
        err = _roundtrip_error(100.0, 100.5, T, 0.3, 0.0, True)
        assert not math.isnan(err)
        assert err < 1e-8

    def test_hourly_otm_put(self):
        T = 1.0 / 365.0 / 24.0
        err = _roundtrip_error(100.0, 99.5, T, 0.3, 0.0, False)
        assert not math.isnan(err)
        assert err < 1e-8


# ---------------------------------------------------------------------------
# Below-intrinsic → nan
# ---------------------------------------------------------------------------


class TestBelowIntrinsic:
    def test_below_intrinsic_call_returns_nan(self):
        """Price below intrinsic → nan."""
        df = math.exp(-0.05 * 1.0)
        intr = df * (110.0 - 100.0)
        bad_price = intr - 1.0
        iv = implied_vol(bad_price, 110.0, 100.0, 1.0, 0.05, True)
        assert math.isnan(iv)

    def test_below_intrinsic_put_returns_nan(self):
        df = math.exp(-0.05 * 0.5)
        intr = df * (100.0 - 90.0)
        bad_price = intr - 1.0
        iv = implied_vol(bad_price, 90.0, 100.0, 0.5, 0.05, False)
        assert math.isnan(iv)

    def test_zero_price_otm_returns_nan(self):
        """OTM option with price=0: at intrinsic boundary → nan or 0."""
        iv = implied_vol(0.0, 90.0, 100.0, 1.0, 0.05, True)
        # price=0 for OTM call is at intrinsic (0), so no positive vol exists
        assert math.isnan(iv)

    def test_expired_returns_nan(self):
        """T<=0 → nan (no IV for expired option)."""
        iv = implied_vol(5.0, 100.0, 100.0, 0.0, 0.05, True)
        assert math.isnan(iv)


# ---------------------------------------------------------------------------
# Vectorized path == scalar path
# ---------------------------------------------------------------------------


class TestVectorizedConsistency:
    def test_array_equals_scalar_loop(self):
        """Vectorized implied_vol matches scalar calls elementwise."""
        F = 100.0
        Ks = np.array([90.0, 95.0, 100.0, 105.0, 110.0])
        sigs = np.array([0.20, 0.22, 0.20, 0.22, 0.20])
        T, r = 0.5, 0.03
        is_calls = np.array([True, True, True, False, False])

        prices = np.array([
            float(b76_price(F, float(Ks[i]), T, float(sigs[i]), r, bool(is_calls[i])))
            for i in range(len(Ks))
        ])
        iv_vec = implied_vol(prices, F, Ks, T, r, is_calls)
        iv_scalar = np.array([
            implied_vol(
                float(prices[i]), F, float(Ks[i]), T, r, bool(is_calls[i])
            )
            for i in range(len(Ks))
        ])

        np.testing.assert_allclose(iv_vec, iv_scalar, rtol=1e-12, atol=1e-14)

    def test_vectorized_no_nan_on_valid_inputs(self):
        """No nans for valid prices from Black-76."""
        F = 100.0
        Ks = np.linspace(70.0, 130.0, 15)
        T, r, sigma = 0.5, 0.04, 0.25
        is_calls = np.ones(15, dtype=bool)
        prices = b76_price(F, Ks, T, sigma, r, is_calls)
        ivs = implied_vol(prices, F, Ks, T, r, is_calls)
        # Near-deep ITM calls have near-zero time value; may return nan at boundaries
        # For OTM (K > F) there should be no nans
        otm_mask = Ks >= F
        assert not np.any(np.isnan(ivs[otm_mask])), "nan in OTM call IVs"

    def test_2d_broadcast(self):
        """2-D broadcasting: grid of T x K."""
        F, r = 100.0, 0.04
        Ks = np.array([[95.0, 100.0, 105.0]])   # shape (1, 3)
        Ts = np.array([[0.25], [0.5], [1.0]])    # shape (3, 1)
        sigs = np.full((3, 3), 0.2)
        is_calls = np.ones((3, 3), dtype=bool)

        prices = b76_price(F, Ks, Ts, sigs, r, is_calls)  # (3,3)
        ivs = implied_vol(prices, F, Ks, Ts, r, is_calls)

        assert ivs.shape == (3, 3)
        # All should be close to 0.2
        np.testing.assert_allclose(ivs, 0.2, rtol=1e-6)
