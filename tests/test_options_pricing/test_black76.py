"""Tests for Black-76 option pricing and Greeks.

Golden values
-------------
F=100, K=100, T=1, sigma=0.2, r=0.05, call:
  d1 = ln(1) + 0.5*0.04*1) / 0.2 = 0.02/0.2 = 0.1
  d2 = 0.1 - 0.2 = -0.1
  N(0.1)  = 0.539828   N(-0.1)  = 0.460172
  N(-0.1) = 0.460172   N(0.1)   = 0.539828  (put)
  df = exp(-0.05) = 0.951229
  N(d1)=N(0.1)=0.53982784  N(d2)=N(-0.1)=0.46017216
  df=exp(-0.05)=0.95122942
  Call = df*(F*N(d1)-K*N(d2)) = 0.95122942*(100*0.53982784-100*0.46017216)
       = 0.95122942 * 7.96556846 = 7.57708215
  Pre-discount spread = 7.9656 (matches task spec citation)
  Put = df*(K*N(-d2)-F*N(-d1)) = same as Call when F=K (parity C-P=0) = 7.5771
  Actual hand-checked: 7.5771 ± 1e-3.
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

from options_pricing.src.black76 import (
    chain_greeks,
    delta,
    gamma,
    price,
    rho,
    theta,
    vega,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fd_greek(func, param_idx, args, h=1e-5):
    """Central finite difference on args[param_idx]."""
    args_p = list(args)
    args_m = list(args)
    args_p[param_idx] = args[param_idx] + h
    args_m[param_idx] = args[param_idx] - h
    return (func(*args_p) - func(*args_m)) / (2 * h)


# Standard test parameters
_F, _K, _T, _SIG, _R = 100.0, 100.0, 1.0, 0.2, 0.05


# ---------------------------------------------------------------------------
# Golden value tests
# ---------------------------------------------------------------------------


class TestGoldenValues:
    """Hand-verified Black-76 prices for ATM futures option."""

    def test_call_price_golden(self):
        """ATM call: F=K=100, T=1, sig=0.2, r=0.05 → 7.5771 ± 1e-3.

        Hand-verified:
          d1=0.1, d2=-0.1, N(d1)=0.53982784, N(d2)=0.46017216
          df=exp(-0.05)=0.95122942
          C = 0.95122942 * (100*0.53982784 - 100*0.46017216) = 7.57708215
        Pre-discount spread F*N(d1)-K*N(d2) = 7.9656 (matches task spec).
        """
        c = price(_F, _K, _T, _SIG, _R, True)
        assert abs(c - 7.5771) < 1e-3, f"call price {c:.6f} deviates from 7.5771"

    def test_put_price_golden_atm(self):
        """ATM put equals ATM call by parity when F=K."""
        c = price(_F, _K, _T, _SIG, _R, True)
        p = price(_F, _K, _T, _SIG, _R, False)
        assert abs(c - p) < 1e-10  # F=K → df*(F-K)=0

    def test_call_price_itm(self):
        """ITM call (F=110, K=100) has higher value than ATM."""
        c_itm = price(110.0, 100.0, _T, _SIG, _R, True)
        c_atm = price(_F, _K, _T, _SIG, _R, True)
        assert c_itm > c_atm

    def test_put_price_itm(self):
        """ITM put (F=90, K=100) has higher value than ATM."""
        p_itm = price(90.0, 100.0, _T, _SIG, _R, False)
        p_atm = price(_F, _K, _T, _SIG, _R, False)
        assert p_itm > p_atm

    def test_price_monotone_in_sigma(self):
        """Both call and put increase with sigma."""
        for is_call in (True, False):
            p_lo = price(_F, _K, _T, 0.1, _R, is_call)
            p_hi = price(_F, _K, _T, 0.4, _R, is_call)
            assert p_hi > p_lo

    def test_price_decreases_with_r(self):
        """Higher r → lower discount factor → lower futures option price (same vol)."""
        c_lo_r = price(_F, _K, _T, _SIG, 0.0, True)
        c_hi_r = price(_F, _K, _T, _SIG, 0.1, True)
        assert c_lo_r > c_hi_r

    def test_price_nonnegative(self):
        """Prices are always non-negative."""
        for is_call in (True, False):
            for K in (50.0, 100.0, 150.0):
                p = price(_F, K, _T, _SIG, _R, is_call)
                assert p >= 0.0


# ---------------------------------------------------------------------------
# Put-call parity
# ---------------------------------------------------------------------------


class TestPutCallParity:
    """C - P = exp(-rT)*(F - K) across a strike grid."""

    def test_parity_strike_grid(self):
        strikes = np.linspace(70.0, 130.0, 25)
        F, T, sig, r = 100.0, 0.5, 0.25, 0.04
        df = math.exp(-r * T)
        calls = price(F, strikes, T, sig, r, True)
        puts = price(F, strikes, T, sig, r, False)
        expected = df * (F - strikes)
        np.testing.assert_allclose(calls - puts, expected, atol=1e-10)

    def test_parity_forward_grid(self):
        forwards = np.linspace(80.0, 120.0, 20)
        K, T, sig, r = 100.0, 0.25, 0.2, 0.05
        df = math.exp(-r * T)
        calls = price(forwards, K, T, sig, r, True)
        puts = price(forwards, K, T, sig, r, False)
        expected = df * (forwards - K)
        np.testing.assert_allclose(calls - puts, expected, atol=1e-10)

    def test_parity_vol_grid(self):
        vols = np.linspace(0.05, 1.5, 30)
        F, K, T, r = 100.0, 95.0, 1.0, 0.03
        df = math.exp(-r * T)
        calls = price(F, K, T, vols, r, True)
        puts = price(F, K, T, vols, r, False)
        expected = df * (F - K)
        np.testing.assert_allclose(calls - puts, expected, atol=1e-10)


# ---------------------------------------------------------------------------
# Greeks vs finite differences
# ---------------------------------------------------------------------------


class TestGreeksFiniteDiff:
    """Analytic Greeks match central finite differences to rel tol 1e-5."""

    _params = (_F, _K, _T, _SIG, _R)

    def _check_greek(self, analytic_fn, fd_fn, args, rtol=1e-5, atol=1e-8):
        analytic = float(np.atleast_1d(analytic_fn(*args))[0])
        fd = fd_fn(*args)
        if abs(fd) > atol:
            assert abs(analytic - fd) / abs(fd) < rtol, f"analytic={analytic}, fd={fd}"
        else:
            assert abs(analytic - fd) < atol

    def test_delta_call_fd(self):
        args = (_F, _K, _T, _SIG, _R, True)
        fd = _fd_greek(price, 0, args, h=0.01)
        analytic = delta(*args)
        assert abs(analytic - fd) / abs(fd) < 1e-5

    def test_delta_put_fd(self):
        args = (_F, _K, _T, _SIG, _R, False)
        fd = _fd_greek(price, 0, args, h=0.01)
        analytic = delta(*args)
        assert abs(analytic - fd) / abs(fd) < 1e-5

    def test_gamma_fd(self):
        args = (_F, _K, _T, _SIG, _R, True)
        # Second derivative via FD: [p(F+h) - 2p(F) + p(F-h)] / h²
        h = 0.5
        fd2 = (price(_F + h, _K, _T, _SIG, _R, True)
               - 2 * price(_F, _K, _T, _SIG, _R, True)
               + price(_F - h, _K, _T, _SIG, _R, True)) / (h * h)
        analytic = gamma(_F, _K, _T, _SIG, _R)
        assert abs(analytic - fd2) / abs(fd2) < 1e-4  # FD of FD is less accurate

    def test_vega_fd(self):
        args = (_F, _K, _T, _SIG, _R, True)
        fd = _fd_greek(price, 3, args, h=0.001)
        analytic = vega(*args[:5])
        assert abs(analytic - fd) / abs(fd) < 1e-5

    def test_theta_fd(self):
        args = (_F, _K, _T, _SIG, _R, True)
        fd = _fd_greek(price, 2, args, h=1e-4)
        analytic = theta(*args)
        # theta can be small; use absolute fallback
        if abs(fd) > 1e-6:
            assert abs(analytic - fd) / abs(fd) < 1e-4
        else:
            assert abs(analytic - fd) < 1e-7

    def test_rho_fd(self):
        args = (_F, _K, _T, _SIG, _R, True)
        fd = _fd_greek(price, 4, args, h=1e-5)
        analytic = rho(*args)
        if abs(fd) > 1e-6:
            assert abs(analytic - fd) / abs(fd) < 1e-4
        else:
            assert abs(analytic - fd) < 1e-7

    def test_delta_call_put_symmetry(self):
        """delta_call - delta_put = exp(-rT) (Black-76 identity)."""
        dc = delta(_F, _K, _T, _SIG, _R, True)
        dp = delta(_F, _K, _T, _SIG, _R, False)
        df = math.exp(-_R * _T)
        assert abs(dc - dp - df) < 1e-10

    def test_greeks_across_strike_grid(self):
        """Analytic delta matches FD at rel 1e-5 across 10 strikes."""
        strikes = np.linspace(80.0, 120.0, 10)
        for K in strikes:
            for is_call in (True, False):
                args = (_F, float(K), _T, _SIG, _R, is_call)
                fd = _fd_greek(price, 0, args, h=0.01)
                an = delta(*args)
                if abs(fd) > 1e-8:
                    assert abs(an - fd) / abs(fd) < 1e-5, f"K={K}, is_call={is_call}"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_expired_call_intrinsic(self):
        """T=0 → intrinsic for call."""
        c = price(110.0, 100.0, 0.0, 0.2, 0.05, True)
        assert abs(c - 10.0) < 1e-10

    def test_expired_put_intrinsic(self):
        """T=0 → intrinsic for put."""
        p = price(90.0, 100.0, 0.0, 0.2, 0.05, False)
        assert abs(p - 10.0) < 1e-10

    def test_expired_otm_zero(self):
        """T=0, OTM → zero."""
        c = price(90.0, 100.0, 0.0, 0.2, 0.05, True)
        assert c == 0.0
        p = price(110.0, 100.0, 0.0, 0.2, 0.05, False)
        assert p == 0.0

    def test_zero_sigma_call(self):
        """sigma=0 → discounted intrinsic."""
        df = math.exp(-0.05 * 1.0)
        c = price(110.0, 100.0, 1.0, 0.0, 0.05, True)
        assert abs(c - df * 10.0) < 1e-10

    def test_zero_sigma_put(self):
        df = math.exp(-0.05 * 1.0)
        p = price(90.0, 100.0, 1.0, 0.0, 0.05, False)
        assert abs(p - df * 10.0) < 1e-10

    def test_zero_sigma_otm_zero(self):
        c = price(90.0, 100.0, 1.0, 0.0, 0.05, True)
        assert c == 0.0

    def test_no_nan_for_valid_inputs(self):
        """No nans across a parameter grid."""
        Fs = [80.0, 100.0, 120.0]
        Ks = [90.0, 100.0, 110.0]
        Ts = [0.1, 0.5, 1.0]
        sigs = [0.05, 0.2, 0.5]
        for F in Fs:
            for K in Ks:
                for T in Ts:
                    for s in sigs:
                        for is_call in (True, False):
                            p = price(F, K, T, s, 0.05, is_call)
                            assert not math.isnan(p), f"nan for F={F},K={K},T={T},s={s},is_call={is_call}"

    def test_greeks_zero_at_expiry(self):
        """All greeks except rho are 0 at T=0."""
        for g_fn, extra in [(delta, [True]), (gamma, []), (vega, []), (theta, [True])]:
            val = g_fn(_F, _K, 0.0, _SIG, _R, *extra)
            assert val == 0.0, f"{g_fn.__name__} not zero at T=0"


# ---------------------------------------------------------------------------
# Vectorized chain
# ---------------------------------------------------------------------------


class TestChainGreeks:
    def test_chain_matches_scalar(self):
        """chain_greeks output equals elementwise scalar calls."""
        F, T, r = 100.0, 0.5, 0.05
        Ks = np.array([90.0, 95.0, 100.0, 105.0, 110.0])
        sigs = np.array([0.25, 0.22, 0.20, 0.22, 0.25])
        is_calls = np.array([True, True, True, False, False])

        cg = chain_greeks(F, Ks, T, sigs, r, is_calls)

        for i in range(len(Ks)):
            args = (F, float(Ks[i]), T, float(sigs[i]), r, bool(is_calls[i]))
            assert abs(cg["price"][i] - price(*args)) < 1e-12
            assert abs(cg["delta"][i] - delta(*args)) < 1e-12
            assert abs(cg["vega"][i] - vega(F, float(Ks[i]), T, float(sigs[i]), r)) < 1e-12

    def test_chain_keys_present(self):
        Ks = np.array([95.0, 100.0, 105.0])
        sigs = np.array([0.2, 0.2, 0.2])
        is_calls = np.array([True, True, False])
        cg = chain_greeks(100.0, Ks, 1.0, sigs, 0.05, is_calls)
        for key in ("price", "delta", "gamma", "vega", "theta", "rho"):
            assert key in cg
            assert len(cg[key]) == 3

    def test_chain_no_nan(self):
        Ks = np.linspace(70.0, 130.0, 20)
        sigs = np.full(20, 0.2)
        is_calls = np.ones(20, dtype=bool)
        cg = chain_greeks(100.0, Ks, 0.5, sigs, 0.05, is_calls)
        for key, arr in cg.items():
            assert not np.any(np.isnan(arr)), f"nan in {key}"
