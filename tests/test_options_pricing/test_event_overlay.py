"""Tests for packages/options_pricing/src/event_overlay.py.

Golden-value derivations are shown inline.  All tests are deterministic.

Protocol: arXiv 2606.12872 — implied event variance extraction.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_pricing.src.event_overlay import (
    EventLump,
    apply_overlay,
    atm_event_premium_ticks,
    forward_vol,
    implied_event_variance,
)
from options_pricing.src.essvi import EssviSlice, EssviSurface

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_slice(t: float, theta: float, var_lump: float = 0.0) -> EssviSlice:
    return EssviSlice(t=t, theta=theta, rho=-0.3, phi=2.0, F=5000.0,
                      var_lump=var_lump)


def _make_surface(*slices: EssviSlice) -> EssviSurface:
    return EssviSurface(list(slices))


# ---------------------------------------------------------------------------
# EventLump validation
# ---------------------------------------------------------------------------


class TestEventLump:
    def test_valid_construction(self):
        ev = EventLump(
            event_utc=datetime(2026, 6, 15, 14, 0, tzinfo=_UTC),
            var_lump=0.003,
            label="FOMC-2026-06",
        )
        assert ev.var_lump == 0.003
        assert ev.label == "FOMC-2026-06"

    def test_zero_var_lump_allowed(self):
        ev = EventLump(
            event_utc=datetime(2026, 6, 15, tzinfo=_UTC),
            var_lump=0.0,
            label="no-op",
        )
        assert ev.var_lump == 0.0

    def test_negative_var_lump_raises(self):
        with pytest.raises(ValueError, match="var_lump must be >= 0"):
            EventLump(
                event_utc=datetime(2026, 6, 15, tzinfo=_UTC),
                var_lump=-0.001,
                label="bad",
            )

    def test_naive_datetime_raises(self):
        with pytest.raises(ValueError, match="tz-aware"):
            EventLump(
                event_utc=datetime(2026, 6, 15, 14, 0),  # naive
                var_lump=0.001,
                label="bad",
            )

    def test_frozen_immutable(self):
        ev = EventLump(
            event_utc=datetime(2026, 6, 15, tzinfo=_UTC),
            var_lump=0.002,
            label="x",
        )
        with pytest.raises((AttributeError, TypeError)):
            ev.var_lump = 0.005  # type: ignore[misc]


# ---------------------------------------------------------------------------
# implied_event_variance — synthetic round-trip
# ---------------------------------------------------------------------------


class TestImpliedEventVarianceCalendar:
    """Calendar-clock (clock=None) synthetic round-trip.

    Construction:
      sigma_d^2 = 0.04^2 = 0.0016  (diffusive variance rate)
      t_short = 0.25 yr,  t_long = 0.75 yr
      w_short = 0.0016 * 0.25 = 0.0004
      v_e = 0.005
      w_long = 0.0016 * 0.75 + v_e = 0.0012 + 0.005 = 0.0062

    Extraction:
      delta_w = w_long - w_short = 0.0062 - 0.0004 = 0.0058
      sigma_d^2_hat = w_short / t_short = 0.0004 / 0.25 = 0.0016
      diffusive_fwd = 0.0016 * (0.75 - 0.25) = 0.0016 * 0.5 = 0.0008
      v_e_hat = 0.0058 - 0.0008 = 0.005  ✓ recovers exactly
    """

    _SIGMA_D_SQ = 0.0016
    _T_SHORT = 0.25
    _T_LONG = 0.75
    _V_E = 0.005

    @property
    def _w_short(self) -> float:
        return self._SIGMA_D_SQ * self._T_SHORT

    @property
    def _w_long(self) -> float:
        return self._SIGMA_D_SQ * self._T_LONG + self._V_E

    def test_round_trip_calendar(self):
        """Extract v_e using calendar clock; recover within 1e-12."""
        v_hat = implied_event_variance(
            self._w_short,
            self._T_SHORT,
            self._w_long,
            self._T_LONG,
            event_time=self._T_LONG,  # event at t_long (boundary: counts)
        )
        assert abs(v_hat - self._V_E) < 1e-12, f"Extraction error: {v_hat - self._V_E}"

    def test_event_at_t_long_boundary_counts(self):
        """event_time == t_long is valid (<=)."""
        v_hat = implied_event_variance(
            self._w_short, self._T_SHORT,
            self._w_long, self._T_LONG,
            event_time=self._T_LONG,
        )
        assert abs(v_hat - self._V_E) < 1e-12

    def test_event_at_t_short_raises(self):
        """event_time == t_short is invalid (not strictly inside)."""
        with pytest.raises(ValueError, match="t_short < event_time <= t_long"):
            implied_event_variance(
                self._w_short, self._T_SHORT,
                self._w_long, self._T_LONG,
                event_time=self._T_SHORT,
            )

    def test_event_before_t_short_raises(self):
        """event_time < t_short raises ValueError."""
        with pytest.raises(ValueError, match="t_short < event_time <= t_long"):
            implied_event_variance(
                self._w_short, self._T_SHORT,
                self._w_long, self._T_LONG,
                event_time=self._T_SHORT - 0.01,
            )

    def test_event_after_t_long_raises(self):
        """event_time > t_long raises ValueError."""
        with pytest.raises(ValueError, match="t_short < event_time <= t_long"):
            implied_event_variance(
                self._w_short, self._T_SHORT,
                self._w_long, self._T_LONG,
                event_time=self._T_LONG + 0.01,
            )


class TestImpliedEventVarianceVolClock:
    """Business-time clock synthetic round-trip.

    Construction:
      sigma_d^2 = 0.0016  (as above, but in business time)
      btime_short = 0.20 (business time is compressed relative to calendar)
      btime_long  = 0.60
      w_short = 0.0016 * 0.20 = 0.00032
      v_e = 0.005
      w_long = 0.0016 * 0.60 + v_e = 0.00096 + 0.005 = 0.00596

    Clock callable returns (btime_short, btime_long).

    Extraction:
      delta_w = 0.00596 - 0.00032 = 0.00564
      sigma_d^2_hat = 0.00032 / 0.20 = 0.0016
      diffusive_fwd = 0.0016 * (0.60 - 0.20) = 0.0016 * 0.40 = 0.00064
      v_e_hat = 0.00564 - 0.00064 = 0.005  ✓
    """

    _SIGMA_D_SQ = 0.0016
    _T_SHORT = 0.25
    _T_LONG = 0.75
    _BTIME_SHORT = 0.20
    _BTIME_LONG = 0.60
    _V_E = 0.005

    @property
    def _w_short(self) -> float:
        return self._SIGMA_D_SQ * self._BTIME_SHORT

    @property
    def _w_long(self) -> float:
        return self._SIGMA_D_SQ * self._BTIME_LONG + self._V_E

    def _clock(self, t_s: float, t_l: float):
        return self._BTIME_SHORT, self._BTIME_LONG

    def test_round_trip_vol_clock(self):
        """Extract v_e using business-time clock; recover within 1e-12."""
        v_hat = implied_event_variance(
            self._w_short, self._T_SHORT,
            self._w_long, self._T_LONG,
            event_time=self._T_LONG,
            clock=self._clock,
        )
        assert abs(v_hat - self._V_E) < 1e-12, f"Extraction error: {v_hat - self._V_E}"


class TestImpliedEventVarianceNegative:
    """Negative extraction result is returned unclamped.

    Construction: set w_long < diffusive prediction so v_e < 0.
      sigma_d^2 = 0.0016, t_short=0.25, t_long=0.75
      w_short = 0.0004
      w_long = 0.0009  (only diffusive growth, no event — less than diffusive prediction)

      Extraction:
        delta_w = 0.0009 - 0.0004 = 0.0005
        sigma_d^2_hat = 0.0004 / 0.25 = 0.0016
        diffusive_fwd = 0.0016 * 0.5 = 0.0008
        v_e_hat = 0.0005 - 0.0008 = -0.0003  (negative; returned as-is)
    """

    def test_negative_returned_unclamped(self):
        w_short = 0.0016 * 0.25   # 0.0004
        w_long = 0.0009            # less than pure diffusive (0.0012)
        v_hat = implied_event_variance(
            w_short, 0.25, w_long, 0.75, event_time=0.75
        )
        # delta_w = 0.0005; diffusive_fwd = 0.0016 * 0.5 = 0.0008; v_e = -0.0003
        expected = 0.0005 - 0.0008
        assert abs(v_hat - expected) < 1e-15
        assert v_hat < 0.0, "Negative extraction must be returned unclamped"


class TestImpliedEventVarianceErrors:
    """Input validation for implied_event_variance."""

    def test_t_short_zero_raises(self):
        with pytest.raises(ValueError, match="t_short must be > 0"):
            implied_event_variance(0.001, 0.0, 0.002, 0.5, event_time=0.5)

    def test_t_long_le_t_short_raises(self):
        with pytest.raises(ValueError, match="t_long .* must be > t_short"):
            implied_event_variance(0.001, 0.5, 0.002, 0.5, event_time=0.5)

    def test_w_short_negative_raises(self):
        with pytest.raises(ValueError, match="w_short must be >= 0"):
            implied_event_variance(-0.001, 0.25, 0.002, 0.75, event_time=0.75)

    def test_w_long_negative_raises(self):
        with pytest.raises(ValueError, match="w_long must be >= 0"):
            implied_event_variance(0.001, 0.25, -0.002, 0.75, event_time=0.75)


# ---------------------------------------------------------------------------
# forward_vol golden value + calendar-arb violation
# ---------------------------------------------------------------------------


class TestForwardVol:
    """Golden value derivation:
      w_short = 0.04 * 0.5 = 0.02   (sigma=0.2, t=0.5)
      w_long  = 0.04 * 1.0 = 0.04   (sigma=0.2, t=1.0)
      fwd_vol = sqrt((0.04 - 0.02) / (1.0 - 0.5)) = sqrt(0.04) = 0.2
    """

    def test_golden_value(self):
        """
        w_short=0.02, t_short=0.5, w_long=0.04, t_long=1.0:
          fwd_var = (0.04 - 0.02) / (1.0 - 0.5) = 0.02 / 0.5 = 0.04
          fwd_vol = sqrt(0.04) = 0.2
        """
        fv = forward_vol(0.02, 0.5, 0.04, 1.0)
        assert abs(fv - 0.2) < 1e-14

    def test_calendar_arb_violation_raises(self):
        """w_long < w_short is a calendar arbitrage; raises ValueError."""
        with pytest.raises(ValueError, match="Calendar arbitrage"):
            forward_vol(0.04, 0.5, 0.02, 1.0)

    def test_equal_w_gives_zero_fwd_vol(self):
        """w_long == w_short → fwd_var = 0 → fwd_vol = 0."""
        fv = forward_vol(0.03, 0.5, 0.03, 1.0)
        assert fv == 0.0

    def test_t_long_le_t_short_raises(self):
        with pytest.raises(ValueError, match="t_long .* must be > t_short"):
            forward_vol(0.02, 1.0, 0.04, 0.5)

    def test_t_short_zero_raises(self):
        with pytest.raises(ValueError, match="t_short must be > 0"):
            forward_vol(0.02, 0.0, 0.04, 0.5)

    def test_w_negative_raises(self):
        with pytest.raises(ValueError, match="w_long must be >= 0"):
            forward_vol(0.02, 0.5, -0.01, 1.0)


# ---------------------------------------------------------------------------
# apply_overlay
# ---------------------------------------------------------------------------


class TestApplyOverlay:
    """apply_overlay assigns per-slice var_lump = sum of applicable lumps."""

    _VAL_UTC = datetime(2026, 6, 1, tzinfo=_UTC)

    def _make_surface(self) -> EssviSurface:
        # Slices at t = 0.25, 0.5, 1.0 yr from _VAL_UTC
        s1 = _make_slice(0.25, 0.04)
        s2 = _make_slice(0.50, 0.06)
        s3 = _make_slice(1.00, 0.10)
        return _make_surface(s1, s2, s3)

    def _event_at_yf(self, yf: float) -> datetime:
        """Event UTC = valuation + yf years (ACT/365.25)."""
        secs = yf * 365.25 * 86400.0
        return self._VAL_UTC + timedelta(seconds=secs)

    def test_lump_lands_on_correct_slices(self):
        """Event at t=0.30yr falls in slices with t>=0.30 (i.e. t=0.5 and t=1.0)."""
        surf = self._make_surface()
        ev_utc = self._event_at_yf(0.30)
        lump = EventLump(event_utc=ev_utc, var_lump=0.005, label="test")
        new_surf = apply_overlay(surf, [lump], valuation_utc=self._VAL_UTC)

        slices = new_surf.slices
        # t=0.25: event at 0.30yr > 0.25yr expiry → does NOT apply
        assert slices[0].var_lump == 0.0
        # t=0.50: event at 0.30yr <= 0.50yr expiry → applies
        assert abs(slices[1].var_lump - 0.005) < 1e-15
        # t=1.00: event at 0.30yr <= 1.00yr expiry → applies
        assert abs(slices[2].var_lump - 0.005) < 1e-15

    def test_two_lumps_accumulate(self):
        """Two events: one at 0.20yr, one at 0.60yr.

        t=0.25 slice: event@0.20 applies (0.20 <= 0.25); event@0.60 does not → 0.003
        t=0.50 slice: event@0.20 applies; event@0.60 does not (0.60 > 0.50) → 0.003
        t=1.00 slice: both apply → 0.003 + 0.007 = 0.010
        """
        surf = self._make_surface()
        e1 = EventLump(self._event_at_yf(0.20), 0.003, "e1")
        e2 = EventLump(self._event_at_yf(0.60), 0.007, "e2")
        new_surf = apply_overlay(surf, [e1, e2], valuation_utc=self._VAL_UTC)

        slices = new_surf.slices
        assert abs(slices[0].var_lump - 0.003) < 1e-15  # t=0.25: only e1
        assert abs(slices[1].var_lump - 0.003) < 1e-15  # t=0.50: only e1
        assert abs(slices[2].var_lump - 0.010) < 1e-15  # t=1.00: both

    def test_input_surface_unmutated(self):
        """Original surface slices must not be modified."""
        surf = self._make_surface()
        original_lumps = [sl.var_lump for sl in surf.slices]
        ev = EventLump(self._event_at_yf(0.10), 0.005, "x")
        _ = apply_overlay(surf, [ev], valuation_utc=self._VAL_UTC)
        for orig, sl in zip(original_lumps, surf.slices):
            assert sl.var_lump == orig, "Input surface was mutated"

    def test_validate_passes_with_nonneg_lumps(self):
        """Surface with non-negative event lumps should still pass validate().

        With var_lump >= 0 on all slices, w_total(k) >= w_essvi(k) for all k.
        Calendar-arb check: w_b_total(k) >= w_a_total(k) iff w_b_essvi(k) + lump_b
        >= w_a_essvi(k) + lump_a.  Since our slices have increasing theta and
        non-decreasing lumps (both events apply to later slices too), this holds.
        """
        surf = self._make_surface()
        ev = EventLump(self._event_at_yf(0.30), 0.001, "ok")
        new_surf = apply_overlay(surf, [ev], valuation_utc=self._VAL_UTC)
        errors = new_surf.validate()
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_no_lumps_gives_zero_var_lump(self):
        """Empty lump list → all var_lump = 0."""
        surf = self._make_surface()
        new_surf = apply_overlay(surf, [], valuation_utc=self._VAL_UTC)
        for sl in new_surf.slices:
            assert sl.var_lump == 0.0

    def test_naive_valuation_utc_raises(self):
        """Naive valuation_utc raises ValueError."""
        surf = self._make_surface()
        with pytest.raises(ValueError, match="tz-aware"):
            apply_overlay(surf, [],
                          valuation_utc=datetime(2026, 6, 1))  # naive


# ---------------------------------------------------------------------------
# atm_event_premium_ticks
# ---------------------------------------------------------------------------


class TestAtmEventPremiumTicks:
    """Golden value derivation:

    F=5000, w_base=0.04, v_e=0.005, tick_size=0.25, df=1.0

    straddle(w) = 2 * (1/sqrt(2*pi)) * F * sqrt(w) * df
               = 2 * 0.3989422804... * 5000 * sqrt(w)

    straddle(0.04)  = 2 * 0.3989422804 * 5000 * 0.2
                    = 2 * 0.3989422804 * 1000
                    = 797.884560...

    straddle(0.045) = 2 * 0.3989422804 * 5000 * sqrt(0.045)
                    = 2 * 0.3989422804 * 5000 * 0.21213203...
                    = 2 * 0.3989422804 * 1060.66017...
                    = 845.9723...

    uplift_price = 845.9723 - 797.884560 = 48.0878...
    uplift_ticks = 48.0878 / 0.25 = 192.35...

    Let's compute precisely:
      _INV_SQRT2PI = 1/sqrt(2*pi) = 0.39894228040143265
      straddle_base = 2 * 0.39894228040143265 * 5000 * sqrt(0.04)
                    = 2 * 0.39894228040143265 * 5000 * 0.2
                    = 797.8845608028653
      straddle_top  = 2 * 0.39894228040143265 * 5000 * sqrt(0.045)
                    = 2 * 0.39894228040143265 * 5000 * 0.21213203435596427
                    = 845.9718896...
      uplift = 845.9718... - 797.8845... = 48.0873...
      ticks  = 48.0873... / 0.25 = 192.349...
    """

    _F = 5000.0
    _W_BASE = 0.04
    _V_E = 0.005
    _TICK = 0.25
    _INV_SQRT2PI = 1.0 / math.sqrt(2.0 * math.pi)

    def _expected_ticks(self) -> float:
        s_base = 2.0 * self._INV_SQRT2PI * self._F * math.sqrt(self._W_BASE)
        s_top = 2.0 * self._INV_SQRT2PI * self._F * math.sqrt(self._W_BASE + self._V_E)
        return (s_top - s_base) / self._TICK

    def test_golden_value(self):
        result = atm_event_premium_ticks(
            self._F, self._W_BASE, self._V_E, self._TICK
        )
        expected = self._expected_ticks()
        assert abs(result - expected) < 1e-10, (
            f"Expected {expected}, got {result}"
        )

    def test_discounted(self):
        """With df=0.99, uplift scales by 0.99."""
        df = 0.99
        undiscounted = self._expected_ticks()
        result = atm_event_premium_ticks(
            self._F, self._W_BASE, self._V_E, self._TICK, df=df
        )
        # Both straddles scale by df → uplift also scales by df
        assert abs(result - df * undiscounted) < 1e-10

    def test_zero_v_e_gives_zero_uplift(self):
        result = atm_event_premium_ticks(self._F, self._W_BASE, 0.0, self._TICK)
        assert result == 0.0

    def test_negative_v_e_gives_negative_uplift(self):
        """Negative v_e (from unclamped extraction) produces negative uplift."""
        result = atm_event_premium_ticks(self._F, self._W_BASE, -0.002, self._TICK)
        assert result < 0.0

    def test_tick_size_scales_result(self):
        """Doubling tick size halves the result in ticks."""
        r1 = atm_event_premium_ticks(self._F, self._W_BASE, self._V_E, self._TICK)
        r2 = atm_event_premium_ticks(self._F, self._W_BASE, self._V_E, self._TICK * 2)
        assert abs(r1 - 2.0 * r2) < 1e-10

    def test_invalid_F_raises(self):
        with pytest.raises(ValueError, match="F must be > 0"):
            atm_event_premium_ticks(0.0, self._W_BASE, self._V_E, self._TICK)

    def test_invalid_tick_raises(self):
        with pytest.raises(ValueError, match="tick_size must be > 0"):
            atm_event_premium_ticks(self._F, self._W_BASE, self._V_E, 0.0)

    def test_invalid_w_base_raises(self):
        with pytest.raises(ValueError, match="w_base must be >= 0"):
            atm_event_premium_ticks(self._F, -0.01, self._V_E, self._TICK)

    def test_invalid_df_raises(self):
        with pytest.raises(ValueError, match="df must be > 0"):
            atm_event_premium_ticks(self._F, self._W_BASE, self._V_E, self._TICK, df=0.0)

    def test_w_base_zero_negative_v_e_raises(self):
        # w_base=0 + v_e=-0.005 => w_base+v_e < 0; previously clamped to 0 giving
        # silent zero uplift; now must raise so caller is forced to handle it.
        with pytest.raises(ValueError, match="w_base \\+ v_e"):
            atm_event_premium_ticks(5000.0, 0.0, -0.005, 0.25)
