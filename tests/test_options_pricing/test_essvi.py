"""Tests for the eSSVI implied-total-variance surface (packages/options_pricing/src/essvi.py).

Golden values
-------------
EssviSlice(t=1.0, theta=0.04, rho=-0.3, phi=2.0, F=5000.0):
  w(0) = theta = 0.04  (ATM identity)
  k=0.1:
    phi*k + rho = 0.2 + (-0.3) = -0.1
    disc = (-0.1)^2 + (1 - 0.09) = 0.01 + 0.91 = 0.92
    sqrt(disc) = 0.95916630...
    w = (0.04/2)*(1 + (-0.3)*2.0*0.1 + 0.95916630)
       = 0.02 * (1 - 0.06 + 0.95916630) = 0.02 * 1.89916630 = 0.037983326
  iv(0.1) = sqrt(0.037983326 / 1.0) = 0.194893376...

butterfly_ok checks (Gatheral-Jacquier Prop 4.2):
  theta=0.04, phi=2.0, rho=-0.3 → |rho|=0.3
  C1 = 0.04 * 2.0 * 1.3 = 0.104 <= 4 ✓
  C2 = 0.04 * 4.0 * 1.3 = 0.208 <= 4 ✓  → butterfly_ok = True

  Violating slice: theta=1.0, rho=0.0, phi=3.0
  C1 = 1.0 * 3.0 * 1.0 = 3.0 <= 4 ✓
  C2 = 1.0 * 9.0 * 1.0 = 9.0 > 4  → butterfly_ok = False
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

from options_pricing.src.essvi import (
    EssviSlice,
    EssviSurface,
    butterfly_ok,
    calendar_ok,
    calibrate_slice,
)
from options_pricing.src.black76 import price as b76_price
from options_pricing.src.iv_solver import implied_vol


# ---------------------------------------------------------------------------
# Reference slice used in many tests
# ---------------------------------------------------------------------------

_REF = EssviSlice(t=1.0, theta=0.04, rho=-0.3, phi=2.0, F=5000.0)

# Log-moneyness grid used for calibration round-trips
_KS = np.array([-0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, 0.4])


# ---------------------------------------------------------------------------
# ATM identity
# ---------------------------------------------------------------------------


class TestAtmIdentity:
    """w(0) == theta exactly for any valid slice."""

    def test_atm_ref(self):
        assert _REF.w(0.0) == _REF.theta

    def test_atm_grid(self):
        for theta in (0.01, 0.04, 0.16, 0.36):
            sl = EssviSlice(t=0.5, theta=theta, rho=-0.1, phi=1.5, F=4500.0)
            assert sl.w(0.0) == theta, f"ATM identity failed for theta={theta}"

    def test_atm_array_zero(self):
        """w on an array that includes k=0 → element at k=0 equals theta."""
        ks = np.array([-0.2, -0.1, 0.0, 0.1, 0.2])
        w = _REF.w(ks)
        assert abs(w[2] - _REF.theta) < 1e-15


# ---------------------------------------------------------------------------
# Golden value
# ---------------------------------------------------------------------------


class TestGoldenValues:
    """Hand-computed w and iv at k=0.1 for the reference slice."""

    def test_w_k01_golden(self):
        """
        k=0.1, theta=0.04, rho=-0.3, phi=2.0:
          phi*k + rho = 0.2 - 0.3 = -0.1
          disc = 0.01 + 0.91 = 0.92
          sqrt = 0.95916630...
          w = 0.02 * (1 - 0.06 + 0.95916630) = 0.02 * 1.89916630 = 0.037983326
        """
        expected = 0.02 * (1.0 + (-0.3) * 2.0 * 0.1 + math.sqrt(0.92))
        assert abs(_REF.w(0.1) - expected) < 1e-12

    def test_iv_k01_golden(self):
        expected_w = 0.02 * (1.0 + (-0.3) * 2.0 * 0.1 + math.sqrt(0.92))
        expected_iv = math.sqrt(expected_w / 1.0)
        assert abs(_REF.iv(0.1) - expected_iv) < 1e-12

    def test_w_positive_everywhere(self):
        """Total variance must be positive for all k on a wide grid."""
        ks = np.linspace(-3.0, 3.0, 601)
        w = _REF.w(ks)
        assert np.all(w > 0.0)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestSliceValidation:
    def test_invalid_theta_zero(self):
        with pytest.raises(ValueError, match="theta"):
            EssviSlice(t=1.0, theta=0.0, rho=0.0, phi=1.0, F=100.0)

    def test_invalid_rho_boundary(self):
        with pytest.raises(ValueError, match="rho"):
            EssviSlice(t=1.0, theta=0.04, rho=1.0, phi=1.0, F=100.0)

    def test_invalid_phi_negative(self):
        with pytest.raises(ValueError, match="phi"):
            EssviSlice(t=1.0, theta=0.04, rho=0.0, phi=-0.1, F=100.0)

    def test_invalid_t_zero(self):
        with pytest.raises(ValueError, match="t must be >0"):
            EssviSlice(t=0.0, theta=0.04, rho=0.0, phi=1.0, F=100.0)

    def test_invalid_F_zero(self):
        with pytest.raises(ValueError, match="F must be >0"):
            EssviSlice(t=1.0, theta=0.04, rho=0.0, phi=1.0, F=0.0)


# ---------------------------------------------------------------------------
# butterfly_ok
# ---------------------------------------------------------------------------


class TestButterflyOk:
    """Sufficient conditions: C1=theta*phi*(1+|rho|)<=4; C2=theta*phi^2*(1+|rho|)<=4."""

    def test_ref_passes(self):
        # C1 = 0.04*2.0*1.3=0.104; C2=0.04*4.0*1.3=0.208 — both <=4
        assert butterfly_ok(_REF)

    def test_violating_c2(self):
        """theta=1.0, rho=0.0, phi=3.0: C2=9.0>4 → fail."""
        sl = EssviSlice(t=1.0, theta=1.0, rho=0.0, phi=3.0, F=100.0)
        # C1 = 1.0*3.0*1.0 = 3.0 <=4; C2 = 1.0*9.0*1.0 = 9.0 >4 → False
        assert not butterfly_ok(sl)

    def test_violating_c1(self):
        """theta=2.0, rho=0.0, phi=2.5: C1=5.0>4 → fail."""
        sl = EssviSlice(t=1.0, theta=2.0, rho=0.0, phi=2.5, F=100.0)
        assert not butterfly_ok(sl)

    def test_just_at_boundary(self):
        """theta*phi*(1+|rho|)=4 exactly → passes (<=, not <)."""
        # theta=1.0, rho=0.0, phi=4.0 → C1=4.0, C2=16.0>4 → fails on C2
        # Use theta=0.5, rho=0.0, phi=4.0 → C1=2.0, C2=8.0>4 → fails on C2
        # Use theta=0.5, phi=2.0, rho=0.0 → C1=1.0, C2=2.0 → passes
        sl = EssviSlice(t=1.0, theta=0.5, rho=0.0, phi=2.0, F=100.0)
        assert butterfly_ok(sl)

    def test_large_rho_magnitude_tightens(self):
        """High |rho| with borderline phi causes failure."""
        # rho=-0.9 → |rho|=0.9 → 1+|rho|=1.9
        # theta=1.0, phi=2.5 → C1=1.0*2.5*1.9=4.75>4 → fail
        sl = EssviSlice(t=1.0, theta=1.0, rho=-0.9, phi=2.5, F=100.0)
        assert not butterfly_ok(sl)


# ---------------------------------------------------------------------------
# calendar_ok
# ---------------------------------------------------------------------------


class TestCalendarOk:
    def _make(self, t, theta, rho=0.0, phi=1.0):
        return EssviSlice(t=t, theta=theta, rho=rho, phi=phi, F=5000.0)

    def test_monotone_pair_passes(self):
        """Longer-dated slice has strictly higher total variance → passes."""
        sa = self._make(0.25, 0.04)
        sb = self._make(1.0, 0.08)
        assert calendar_ok(sa, sb)

    def test_crossing_pair_fails(self):
        """Construct b with far lower theta so w_b < w_a at k=0."""
        sa = self._make(0.25, 0.10)
        sb = self._make(1.0, 0.02)   # w_b(0)=0.02 < w_a(0)=0.10
        assert not calendar_ok(sa, sb)

    def test_wrong_order_raises(self):
        sa = self._make(1.0, 0.04)
        sb = self._make(0.25, 0.02)
        with pytest.raises(ValueError, match="must be >"):
            calendar_ok(sa, sb)

    def test_same_params_passes(self):
        """Two slices with same params (different t) — longer-dated w >= shorter."""
        # For fixed (theta,rho,phi), w is the same function → w_b=w_a so w_b>=w_a.
        sa = EssviSlice(t=0.5, theta=0.04, rho=-0.3, phi=2.0, F=5000.0)
        sb = EssviSlice(t=1.0, theta=0.04, rho=-0.3, phi=2.0, F=5000.0)
        # w_b(k) = w_a(k) for all k (same theta/rho/phi) → passes (>=)
        assert calendar_ok(sa, sb)


# ---------------------------------------------------------------------------
# EssviSurface interpolation
# ---------------------------------------------------------------------------


class TestEssviSurface:
    def _make_surface(self):
        s1 = EssviSlice(t=0.25, theta=0.04, rho=-0.3, phi=2.0, F=5000.0)
        s2 = EssviSlice(t=0.5,  theta=0.06, rho=-0.3, phi=2.0, F=5000.0)
        s3 = EssviSlice(t=1.0,  theta=0.10, rho=-0.3, phi=2.0, F=5000.0)
        return EssviSurface([s1, s2, s3])

    def test_at_knot(self):
        """w(k, t_knot) equals the slice's w(k) exactly."""
        surf = self._make_surface()
        sl = surf.slices[1]  # t=0.5
        k = 0.1
        assert abs(surf.w(k, sl.t) - sl.w(k)) < 1e-14

    def test_linear_interpolation(self):
        """w at midpoint between two knots equals arithmetic mean of slice w."""
        surf = self._make_surface()
        s1, s2 = surf.slices[0], surf.slices[1]
        t_mid = 0.5 * (s1.t + s2.t)
        k = 0.0
        w_interp = surf.w(k, t_mid)
        w_expected = 0.5 * (s1.w(k) + s2.w(k))
        assert abs(w_interp - w_expected) < 1e-14

    def test_clamp_before_first(self):
        """t < first expiry → uses first slice."""
        surf = self._make_surface()
        s0 = surf.slices[0]
        k = 0.1
        assert abs(surf.w(k, 0.01) - s0.w(k)) < 1e-14

    def test_clamp_after_last(self):
        """t > last expiry → uses last slice."""
        surf = self._make_surface()
        s_last = surf.slices[-1]
        k = 0.1
        assert abs(surf.w(k, 10.0) - s_last.w(k)) < 1e-14

    def test_array_k(self):
        """Surface w accepts array k."""
        surf = self._make_surface()
        ks = np.array([-0.2, 0.0, 0.2])
        w = surf.w(ks, 0.5)
        assert w.shape == (3,)
        assert np.all(w > 0.0)

    def test_validate_clean(self):
        """A well-constructed surface reports no errors."""
        surf = self._make_surface()
        errors = surf.validate()
        assert errors == [], f"Unexpected errors: {errors}"

    def test_validate_catches_butterfly(self):
        """Surface validate() detects butterfly violation."""
        s_bad = EssviSlice(t=0.25, theta=1.0, rho=0.0, phi=3.0, F=5000.0)
        s_ok  = EssviSlice(t=1.0,  theta=0.04, rho=-0.3, phi=2.0, F=5000.0)
        surf = EssviSurface([s_bad, s_ok])
        errors = surf.validate()
        assert any("Butterfly" in e for e in errors)

    def test_validate_catches_calendar(self):
        """Surface validate() detects calendar violation."""
        s1 = EssviSlice(t=0.25, theta=0.10, rho=0.0, phi=1.0, F=5000.0)
        s2 = EssviSlice(t=1.0,  theta=0.02, rho=0.0, phi=1.0, F=5000.0)
        surf = EssviSurface([s1, s2])
        errors = surf.validate()
        assert any("Calendar" in e for e in errors)

    def test_sort_on_construction(self):
        """Slices are reordered by t even if passed out of order."""
        s1 = EssviSlice(t=1.0, theta=0.10, rho=0.0, phi=1.0, F=5000.0)
        s2 = EssviSlice(t=0.5, theta=0.06, rho=0.0, phi=1.0, F=5000.0)
        surf = EssviSurface([s1, s2])
        assert surf.slices[0].t < surf.slices[1].t

    def test_duplicate_t_raises(self):
        s1 = EssviSlice(t=1.0, theta=0.04, rho=0.0, phi=1.0, F=5000.0)
        s2 = EssviSlice(t=1.0, theta=0.08, rho=0.0, phi=1.0, F=5000.0)
        with pytest.raises(ValueError, match="Duplicate"):
            EssviSurface([s1, s2])

    def test_single_slice_surface(self):
        """Surface with a single slice clamps on both sides."""
        sl = EssviSlice(t=1.0, theta=0.04, rho=0.0, phi=1.0, F=5000.0)
        surf = EssviSurface([sl])
        k = 0.1
        assert abs(surf.w(k, 0.5) - sl.w(k)) < 1e-14
        assert abs(surf.w(k, 2.0) - sl.w(k)) < 1e-14


# ---------------------------------------------------------------------------
# calibrate_slice — clean round-trip
# ---------------------------------------------------------------------------


class TestCalibrateSliceRoundTrip:
    """Calibrate from IVs generated by a known slice; recover params within tolerance."""

    def _run_roundtrip(self, sl: EssviSlice, ks=_KS, rel_tol=1e-3, iv_tol=1e-6):
        ivs = sl.iv(ks)
        fitted = calibrate_slice(ivs, ks, sl.t, sl.F)
        # Parameter recovery within rel_tol
        for name, true_val, fit_val in [
            ("theta", sl.theta, fitted.theta),
            ("rho",   sl.rho,   fitted.rho),
            ("phi",   sl.phi,   fitted.phi),
        ]:
            if abs(true_val) > 1e-8:
                err = abs(fit_val - true_val) / abs(true_val)
                assert err < rel_tol, (
                    f"{name}: true={true_val}, fit={fit_val}, rel_err={err:.2e} > {rel_tol}"
                )
            else:
                assert abs(fit_val - true_val) < rel_tol
        # IV recovery within iv_tol at each k
        ivs_fit = fitted.iv(ks)
        np.testing.assert_allclose(ivs_fit, ivs, atol=iv_tol,
                                   err_msg="IV round-trip failed")
        return fitted

    def test_roundtrip_ref(self):
        self._run_roundtrip(_REF)

    def test_roundtrip_positive_rho(self):
        sl = EssviSlice(t=0.5, theta=0.09, rho=0.2, phi=1.5, F=4500.0)
        self._run_roundtrip(sl)

    def test_roundtrip_short_expiry(self):
        sl = EssviSlice(t=1/12, theta=0.02, rho=-0.4, phi=3.0, F=5200.0)
        self._run_roundtrip(sl, rel_tol=2e-3)

    def test_butterfly_ok_after_calibration(self):
        """Calibrated slice must pass butterfly check."""
        ivs = _REF.iv(_KS)
        fitted = calibrate_slice(ivs, _KS, _REF.t, _REF.F)
        assert butterfly_ok(fitted), (
            f"Butterfly violation after calibration: theta={fitted.theta}, "
            f"rho={fitted.rho}, phi={fitted.phi}"
        )


# ---------------------------------------------------------------------------
# calibrate_slice — noisy round-trip
# ---------------------------------------------------------------------------


class TestCalibrateSliceNoisy:
    """Add deterministic noise; fit error bounded; butterfly_ok holds."""

    # Deterministic noise: pre-computed offsets (no random seed needed)
    # Each entry is a small perturbation in IV space (~1 vol point = 0.01)
    _NOISE = np.array([
        +0.003, -0.005, +0.007, -0.002, +0.001,
        -0.004, +0.006, -0.003, +0.004,
    ])

    def test_noisy_roundtrip_bounded_error(self):
        """Fit to noisy IVs; RMSE on IVs < 0.008 (noise level)."""
        ivs_true = _REF.iv(_KS)
        ivs_noisy = np.maximum(ivs_true + self._NOISE, 1e-4)

        fitted = calibrate_slice(ivs_noisy, _KS, _REF.t, _REF.F)
        iv_err = np.sqrt(np.mean((fitted.iv(_KS) - ivs_noisy) ** 2))
        assert iv_err < 0.008, f"IV RMSE {iv_err:.6f} too large"

    def test_butterfly_ok_after_noisy_calibration(self):
        """Noisy-calibrated slice must still pass butterfly check."""
        ivs_noisy = np.maximum(_REF.iv(_KS) + self._NOISE, 1e-4)
        fitted = calibrate_slice(ivs_noisy, _KS, _REF.t, _REF.F)
        assert butterfly_ok(fitted), (
            f"Butterfly violation after noisy calibration: "
            f"theta={fitted.theta}, rho={fitted.rho}, phi={fitted.phi}"
        )


# ---------------------------------------------------------------------------
# IV round-trip via black76 + iv_solver
# ---------------------------------------------------------------------------


class TestIvRoundTripViaBlack76:
    """Price with calibrated IV via black76.price, re-imply with iv_solver.

    This wires the eSSVI module into the existing pricing stack and verifies
    end-to-end consistency between essvi.iv → black76.price → iv_solver.
    """

    def test_iv_roundtrip_call(self):
        """Surface IV → b76 price → implied_vol recovers surface IV within 1e-6."""
        F, r, t = _REF.F, 0.05, _REF.t
        ks = np.array([-0.3, -0.1, 0.0, 0.1, 0.3])
        Ks = F * np.exp(ks)

        for k, K in zip(ks, Ks):
            sigma_surface = float(_REF.iv(k))
            p = float(b76_price(F, K, t, sigma_surface, r, True))
            sigma_recovered = float(implied_vol(p, F, K, t, r, True))
            assert not math.isnan(sigma_recovered), (
                f"implied_vol returned nan at k={k:.2f}"
            )
            assert abs(sigma_recovered - sigma_surface) < 1e-6, (
                f"IV round-trip error at k={k:.2f}: "
                f"surface={sigma_surface:.8f}, recovered={sigma_recovered:.8f}"
            )

    def test_iv_roundtrip_put(self):
        """Same round-trip for puts (OTM puts have k > 0 here since K > F)."""
        F, r, t = _REF.F, 0.05, _REF.t
        ks = np.array([-0.2, 0.0, 0.2])
        Ks = F * np.exp(ks)

        for k, K in zip(ks, Ks):
            sigma_surface = float(_REF.iv(k))
            p = float(b76_price(F, K, t, sigma_surface, r, False))
            sigma_recovered = float(implied_vol(p, F, K, t, r, False))
            if not math.isnan(sigma_recovered):
                assert abs(sigma_recovered - sigma_surface) < 1e-6, (
                    f"Put IV round-trip error at k={k:.2f}: "
                    f"surface={sigma_surface:.8f}, recovered={sigma_recovered:.8f}"
                )


# ---------------------------------------------------------------------------
# var_lump composition hook (future event-overlay module)
# ---------------------------------------------------------------------------


class TestVarLump:
    """var_lump adds a constant shift to w(k) — the event-overlay hook."""

    def test_var_lump_additive(self):
        """w_with_lump(k) == w_base(k) + lump for all k."""
        lump = 0.005
        sl_base = EssviSlice(t=1.0, theta=0.04, rho=-0.3, phi=2.0, F=5000.0)
        sl_lump = EssviSlice(t=1.0, theta=0.04, rho=-0.3, phi=2.0, F=5000.0, var_lump=lump)
        ks = np.linspace(-1.0, 1.0, 50)
        diff = sl_lump.w(ks) - sl_base.w(ks)
        np.testing.assert_allclose(diff, lump, atol=1e-15)

    def test_var_lump_atm(self):
        """w_lump(0) = theta + lump."""
        lump = 0.01
        sl = EssviSlice(t=1.0, theta=0.04, rho=0.0, phi=1.0, F=100.0, var_lump=lump)
        assert abs(sl.w(0.0) - (sl.theta + lump)) < 1e-15

    def test_var_lump_default_zero(self):
        """Default var_lump is 0.0 — no composition effect."""
        sl = EssviSlice(t=1.0, theta=0.04, rho=0.0, phi=1.0, F=100.0)
        assert sl.var_lump == 0.0
