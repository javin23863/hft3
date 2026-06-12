"""Tests for Zakamouline (2006) delta-hedging band and HedgeBandPolicy.

Golden values
-------------
Parameters used throughout (unless overridden):
  delta_bs=0.5, gamma_bs=0.02, F=5000.0, T=0.25 (years),
  sigma=0.20, risk_aversion=1.0, cost_rate=0.0005, multiplier=1.0

H0 = cost_rate / (risk_aversion * F * sigma² * T)
   = 0.0005 / (1.0 * 5000.0 * 0.04 * 0.25)
   = 0.0005 / 50.0
   = 1.0e-5  (= 0.00001)

r=0 for futures → exp(-rT) = 1 → (exp(-rT)/sigma)^0.25 = (1/sigma)^0.25

H1 = 1.12 * cost_rate^0.31 * T^0.05 * (1/sigma)^0.25 * (|gamma_bs|/risk_aversion)^0.5
   = 1.12 * 0.0005^0.31 * 0.25^0.05 * (1/0.20)^0.25 * (0.02/1.0)^0.5
   step by step:
     0.0005^0.31 = exp(0.31 * ln(0.0005)) = exp(0.31 * -7.60090) = exp(-2.35628) ≈ 0.094485
     0.25^0.05   = exp(0.05 * ln(0.25))   = exp(0.05 * -1.38629) = exp(-0.06931) ≈ 0.93303
     5.0^0.25    = exp(0.25 * ln(5.0))    = exp(0.25 * 1.60944)  = exp(0.40236) ≈ 1.49535
     0.02^0.5    = sqrt(0.02)             ≈ 0.14142
   H1 = 1.12 * 0.094485 * 0.93303 * 1.49535 * 0.14142
      ≈ 1.12 * 0.018637
      ≈ 0.020874

half_width = H0 + H1 ≈ 1.0e-5 + 0.020874 ≈ 0.020884
lower = 0.5 - 0.020884 ≈ 0.479116
upper = 0.5 + 0.020884 ≈ 0.520884

(Precise value from Python: H0+H1 ≈ 0.020953691)

WW half-width:
  (1.5 * cost_rate * F * gamma_bs² / risk_aversion)^(1/3)
  = (1.5 * 0.0005 * 5000.0 * 0.0004 / 1.0)^(1/3)
  = (0.0015)^(1/3)
  ≈ 0.114471
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_lane.src.hedging.zakamouline import (
    HedgeBandPolicy,
    whalley_wilmott_band,
    zakamouline_band,
)

# ---------------------------------------------------------------------------
# Standard parameters (all golden values derived from these)
# ---------------------------------------------------------------------------

_DELTA = 0.5
_GAMMA = 0.02
_F = 5000.0
_T = 0.25
_SIG = 0.20
_RA = 1.0
_CR = 0.0005
_MUL = 1.0

# Precise computed references (Python float, see module docstring)
_H0_REF = 1.0e-5
_H1_REF = 0.020944  # 1.12 * 0.0005^0.31 * 0.25^0.05 * 5^0.25 * sqrt(0.02) ≈ 0.02094
_HW_REF = _H0_REF + _H1_REF  # ≈ 0.02095
_WW_REF = 0.11447  # (1.5 * 0.0005 * 5000 * 0.0004)^(1/3) = (0.0015)^(1/3)

_TOL = 1e-4  # tolerance for golden comparisons (5 sig figs)


# ---------------------------------------------------------------------------
# zakamouline_band: H0 and H1 golden values
# ---------------------------------------------------------------------------

class TestZakamoulineBandGoldens:
    def test_H0_golden(self):
        # H0 = cost_rate / (risk_aversion * F * sigma^2 * T)
        # = 0.0005 / (1.0 * 5000.0 * 0.04 * 0.25) = 0.0005 / 50.0 = 1.0e-5
        lower, upper = zakamouline_band(
            _DELTA, _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        # band is symmetric: half_width = (upper - lower) / 2
        half_width = (upper - lower) / 2.0
        # H0 is small; check the full half_width is close to H0+H1
        assert abs(half_width - (_H0_REF + _H1_REF)) < _TOL, (
            f"half_width={half_width:.6f}, expected≈{_H0_REF + _H1_REF:.6f}"
        )

    def test_H1_golden(self):
        # H1 ≈ 0.020944 (computed above)
        # H0 = 1e-5, so H1 = half_width - H0
        lower, upper = zakamouline_band(
            _DELTA, _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        half_width = (upper - lower) / 2.0
        H1_actual = half_width - _H0_REF
        assert abs(H1_actual - _H1_REF) < _TOL, (
            f"H1={H1_actual:.6f}, expected≈{_H1_REF:.6f}"
        )

    def test_band_symmetry_around_delta(self):
        # Band must be symmetric: lower = delta - hw, upper = delta + hw
        lower, upper = zakamouline_band(
            _DELTA, _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        mid = (lower + upper) / 2.0
        assert abs(mid - _DELTA) < 1e-12

    def test_lower_below_delta(self):
        lower, upper = zakamouline_band(
            _DELTA, _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        assert lower < _DELTA < upper

    def test_negative_gamma_same_band(self):
        # |gamma| is used; sign should not matter
        lower_pos, upper_pos = zakamouline_band(
            _DELTA, _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        lower_neg, upper_neg = zakamouline_band(
            _DELTA, -_GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        assert abs(lower_pos - lower_neg) < 1e-12
        assert abs(upper_pos - upper_neg) < 1e-12

    def test_multiplier_does_not_alter_band(self):
        # multiplier is dimensional scaling, band is in delta space
        lower1, upper1 = zakamouline_band(
            _DELTA, _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR, multiplier=1.0,
        )
        lower50, upper50 = zakamouline_band(
            _DELTA, _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR, multiplier=50.0,
        )
        assert abs(lower1 - lower50) < 1e-12
        assert abs(upper1 - upper50) < 1e-12


# ---------------------------------------------------------------------------
# zakamouline_band: zero cost degeneracy
# ---------------------------------------------------------------------------

class TestZakamoulineZeroCost:
    def test_zero_cost_band_collapses(self):
        # cost_rate=0 → H0=0, H1=0 → band = (delta_bs, delta_bs)
        lower, upper = zakamouline_band(
            _DELTA, _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=0.0,
        )
        assert lower == _DELTA
        assert upper == _DELTA

    def test_zero_cost_degenerate_is_exact_delta(self):
        # Band [delta, delta]: any deviation from exact delta is "outside"
        lower, upper = zakamouline_band(
            0.3, _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=0.0,
        )
        assert lower == 0.3
        assert upper == 0.3


# ---------------------------------------------------------------------------
# zakamouline_band: monotonicity in cost_rate
# ---------------------------------------------------------------------------

class TestZakamoulineMonotonicity:
    def test_band_width_increasing_in_cost_rate(self):
        # H0 and H1 are both strictly increasing in cost_rate > 0 → band widens
        cost_rates = [0.0001, 0.001, 0.005, 0.01]
        widths = []
        for cr in cost_rates:
            lower, upper = zakamouline_band(
                _DELTA, _GAMMA, _F, _T, _SIG,
                risk_aversion=_RA, cost_rate=cr,
            )
            widths.append(upper - lower)
        for i in range(len(widths) - 1):
            assert widths[i] < widths[i + 1], (
                f"Band not monotone: width[{i}]={widths[i]:.6f} >= width[{i+1}]={widths[i+1]:.6f}"
            )

    def test_band_width_wider_for_higher_gamma(self):
        # Higher |gamma| → larger H1 → wider band
        lower_lo, upper_lo = zakamouline_band(
            _DELTA, 0.005, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        lower_hi, upper_hi = zakamouline_band(
            _DELTA, 0.05, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        assert (upper_lo - lower_lo) < (upper_hi - lower_hi)

    def test_band_width_narrower_for_higher_risk_aversion(self):
        # Higher risk_aversion → smaller H0 and H1 → narrower band
        lower_lo, upper_lo = zakamouline_band(
            _DELTA, _GAMMA, _F, _T, _SIG,
            risk_aversion=0.5, cost_rate=_CR,
        )
        lower_hi, upper_hi = zakamouline_band(
            _DELTA, _GAMMA, _F, _T, _SIG,
            risk_aversion=5.0, cost_rate=_CR,
        )
        assert (upper_lo - lower_lo) > (upper_hi - lower_hi)


# ---------------------------------------------------------------------------
# zakamouline_band: input validation
# ---------------------------------------------------------------------------

class TestZakamoulineValidation:
    def test_T_zero_raises(self):
        with pytest.raises(ValueError, match="T must be > 0"):
            zakamouline_band(_DELTA, _GAMMA, _F, 0.0, _SIG, risk_aversion=_RA, cost_rate=_CR)

    def test_T_negative_raises(self):
        with pytest.raises(ValueError, match="T must be > 0"):
            zakamouline_band(_DELTA, _GAMMA, _F, -0.1, _SIG, risk_aversion=_RA, cost_rate=_CR)

    def test_sigma_zero_raises(self):
        with pytest.raises(ValueError, match="sigma must be > 0"):
            zakamouline_band(_DELTA, _GAMMA, _F, _T, 0.0, risk_aversion=_RA, cost_rate=_CR)

    def test_sigma_negative_raises(self):
        with pytest.raises(ValueError, match="sigma must be > 0"):
            zakamouline_band(_DELTA, _GAMMA, _F, _T, -0.1, risk_aversion=_RA, cost_rate=_CR)

    def test_risk_aversion_zero_raises(self):
        with pytest.raises(ValueError, match="risk_aversion must be > 0"):
            zakamouline_band(_DELTA, _GAMMA, _F, _T, _SIG, risk_aversion=0.0, cost_rate=_CR)

    def test_risk_aversion_negative_raises(self):
        with pytest.raises(ValueError, match="risk_aversion must be > 0"):
            zakamouline_band(_DELTA, _GAMMA, _F, _T, _SIG, risk_aversion=-1.0, cost_rate=_CR)

    def test_cost_rate_negative_raises(self):
        with pytest.raises(ValueError, match="cost_rate must be >= 0"):
            zakamouline_band(_DELTA, _GAMMA, _F, _T, _SIG, risk_aversion=_RA, cost_rate=-0.001)

    def test_F_zero_raises(self):
        with pytest.raises(ValueError, match="F must be > 0"):
            zakamouline_band(_DELTA, _GAMMA, 0.0, _T, _SIG, risk_aversion=_RA, cost_rate=_CR)

    def test_F_negative_raises(self):
        with pytest.raises(ValueError, match="F must be > 0"):
            zakamouline_band(_DELTA, _GAMMA, -100.0, _T, _SIG, risk_aversion=_RA, cost_rate=_CR)


# ---------------------------------------------------------------------------
# whalley_wilmott_band: golden value and properties
# ---------------------------------------------------------------------------

class TestWhalleyWilmottBand:
    def test_ww_golden(self):
        # (1.5 * 0.0005 * 5000 * 0.02^2 / 1.0)^(1/3)
        # = (1.5 * 0.0005 * 5000 * 0.0004)^(1/3)
        # = (0.0015)^(1/3)
        # ≈ 0.11447
        hw = whalley_wilmott_band(
            _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        assert abs(hw - _WW_REF) < _TOL, f"WW hw={hw:.6f}, expected≈{_WW_REF:.6f}"

    def test_ww_zero_cost(self):
        hw = whalley_wilmott_band(
            _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=0.0,
        )
        assert hw == 0.0

    def test_ww_positive(self):
        hw = whalley_wilmott_band(
            _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        assert hw > 0.0

    def test_ww_monotone_in_cost_rate(self):
        # WW is strictly increasing in cost_rate
        hw1 = whalley_wilmott_band(_GAMMA, _F, _T, _SIG, risk_aversion=_RA, cost_rate=0.0001)
        hw2 = whalley_wilmott_band(_GAMMA, _F, _T, _SIG, risk_aversion=_RA, cost_rate=0.001)
        hw3 = whalley_wilmott_band(_GAMMA, _F, _T, _SIG, risk_aversion=_RA, cost_rate=0.01)
        assert hw1 < hw2 < hw3

    def test_ww_gamma_sign_symmetry(self):
        # gamma_bs^2 is used → sign-symmetric
        hw_pos = whalley_wilmott_band(
            _GAMMA, _F, _T, _SIG, risk_aversion=_RA, cost_rate=_CR,
        )
        hw_neg = whalley_wilmott_band(
            -_GAMMA, _F, _T, _SIG, risk_aversion=_RA, cost_rate=_CR,
        )
        assert abs(hw_pos - hw_neg) < 1e-12

    def test_ww_validation_T_zero(self):
        with pytest.raises(ValueError, match="T must be > 0"):
            whalley_wilmott_band(_GAMMA, _F, 0.0, _SIG, risk_aversion=_RA, cost_rate=_CR)

    def test_ww_validation_sigma_zero(self):
        with pytest.raises(ValueError, match="sigma must be > 0"):
            whalley_wilmott_band(_GAMMA, _F, _T, 0.0, risk_aversion=_RA, cost_rate=_CR)

    def test_ww_validation_F_zero(self):
        with pytest.raises(ValueError, match="F must be > 0"):
            whalley_wilmott_band(_GAMMA, 0.0, _T, _SIG, risk_aversion=_RA, cost_rate=_CR)

    def test_ww_vs_zakamouline_own_goldens(self):
        # Both formulas tested against their own hand-computed values, not against each other.
        # WW ≈ 0.11447; Zak half_width ≈ 0.020954 — different formulas, different regimes.
        hw_ww = whalley_wilmott_band(
            _GAMMA, _F, _T, _SIG, risk_aversion=_RA, cost_rate=_CR,
        )
        _, zak_upper = zakamouline_band(
            _DELTA, _GAMMA, _F, _T, _SIG, risk_aversion=_RA, cost_rate=_CR,
        )
        zak_hw = zak_upper - _DELTA
        # Each matches its own golden; no universal ordering is asserted.
        assert abs(hw_ww - _WW_REF) < _TOL
        assert abs(zak_hw - _HW_REF) < _TOL


# ---------------------------------------------------------------------------
# HedgeBandPolicy: should_rehedge logic
# ---------------------------------------------------------------------------

class TestHedgeBandPolicy:
    """
    Band used in all cases: lower=4.79, upper=5.21 (delta_bs=5.0, half_width=0.21)
    This uses scaled delta units (futures-equivalent contracts).

    Golden cases for should_rehedge(portfolio_delta, band):
    -------------------------------------------------------
    Case 1: inside band
      portfolio_delta=5.0, band=(4.79, 5.21) → 0 (no trade)

    Case 2: above upper edge
      portfolio_delta=6.0, band=(4.79, 5.21)
      nearest edge = 5.21
      qty = round(5.21 - 6.0) = round(-0.79) = -1 → sell 1 contract

    Case 3: below lower edge
      portfolio_delta=3.0, band=(4.79, 5.21)
      nearest edge = 4.79
      qty = round(4.79 - 3.0) = round(1.79) = 2 → buy 2 contracts

    Case 4: slightly above, rounds to 0 (remain outside band)
      portfolio_delta=5.22, band=(4.79, 5.21)
      nearest edge = 5.21
      qty = round(5.21 - 5.22) = round(-0.01) = 0 → no trade generated

    Case 5: negative delta (put/short position)
      portfolio_delta=-2.0, band=(-3.0, -1.5)
      nearest edge = -2.0? No: -3.0 < -2.0 < -1.5 → inside band → 0

    Case 6: negative delta below lower
      portfolio_delta=-4.0, band=(-3.0, -1.5)
      below lower=-3.0
      nearest edge = -3.0
      qty = round(-3.0 - (-4.0)) = round(1.0) = 1 → buy 1 contract
    """

    def setup_method(self):
        self.policy = HedgeBandPolicy(cost_rate=_CR)
        self.band = (4.79, 5.21)

    def test_inside_band_no_trade(self):
        qty = self.policy.should_rehedge(5.0, self.band)
        assert qty == 0

    def test_inside_band_at_lower_edge_no_trade(self):
        qty = self.policy.should_rehedge(4.79, self.band)
        assert qty == 0

    def test_inside_band_at_upper_edge_no_trade(self):
        qty = self.policy.should_rehedge(5.21, self.band)
        assert qty == 0

    def test_above_upper_sell_one(self):
        # portfolio_delta=6.0 > upper=5.21 → sell 1 (golden: round(5.21-6.0)=-1)
        qty = self.policy.should_rehedge(6.0, self.band)
        assert qty == -1

    def test_below_lower_buy_two(self):
        # portfolio_delta=3.0 < lower=4.79 → buy 2 (golden: round(4.79-3.0)=2)
        qty = self.policy.should_rehedge(3.0, self.band)
        assert qty == 2

    def test_fractional_overshoot_rounds_to_zero(self):
        # delta=5.22, just above upper=5.21 → round(5.21-5.22)=round(-0.01)=0
        qty = self.policy.should_rehedge(5.22, self.band)
        assert qty == 0

    def test_fractional_undershoot_rounds_to_zero(self):
        # delta=4.78, just below lower=4.79 → round(4.79-4.78)=round(0.01)=0
        qty = self.policy.should_rehedge(4.78, self.band)
        assert qty == 0

    def test_negative_delta_inside_band(self):
        # portfolio_delta=-2.0, band=(-3.0, -1.5) → inside → 0
        qty = self.policy.should_rehedge(-2.0, (-3.0, -1.5))
        assert qty == 0

    def test_negative_delta_below_lower(self):
        # portfolio_delta=-4.0, band=(-3.0, -1.5)
        # below lower=-3.0; nearest edge = lower = -3.0
        # qty = round(-3.0 - (-4.0)) = round(1.0) = 1 → buy 1
        qty = self.policy.should_rehedge(-4.0, (-3.0, -1.5))
        assert qty == 1

    def test_negative_delta_above_upper(self):
        # portfolio_delta=-1.0, band=(-3.0, -1.5)
        # above upper=-1.5; nearest edge = upper = -1.5
        # qty = round(-1.5 - (-1.0)) = round(-0.5) = 0 (banker's rounding for -0.5) or -1?
        # Python round(-0.5) = 0 (round half to even), round(-1.5) = -2
        # round(-1.5 - (-1.0)) = round(-0.5) = 0
        qty = self.policy.should_rehedge(-1.0, (-3.0, -1.5))
        # Exact qty = round(-0.5); Python uses banker's rounding = 0
        assert qty == 0

    def test_large_deviation_multiple_contracts(self):
        # portfolio_delta=10.0, band=(4.79, 5.21)
        # nearest edge = upper = 5.21
        # qty = round(5.21 - 10.0) = round(-4.79) = -5
        qty = self.policy.should_rehedge(10.0, self.band)
        assert qty == -5

    def test_returns_int(self):
        qty = self.policy.should_rehedge(6.0, self.band)
        assert isinstance(qty, int)

    def test_update_position_tracks_state(self):
        assert self.policy.current_futures_pos == 0
        self.policy.update_position(-1)
        assert self.policy.current_futures_pos == -1
        self.policy.update_position(2)
        assert self.policy.current_futures_pos == 1

    def test_zero_cost_policy_collapses_band(self):
        # With cost_rate=0, band = (delta, delta); any deviation triggers rehedge
        policy_zc = HedgeBandPolicy(cost_rate=0.0)
        lower, upper = zakamouline_band(
            5.0, _GAMMA, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=0.0,
        )
        assert lower == 5.0
        assert upper == 5.0
        # Exactly on delta → 0
        qty = policy_zc.should_rehedge(5.0, (lower, upper))
        assert qty == 0

    def test_negative_cost_rate_raises(self):
        with pytest.raises(ValueError, match="cost_rate must be >= 0"):
            HedgeBandPolicy(cost_rate=-0.001)


# ---------------------------------------------------------------------------
# Integration: end-to-end band computation and policy decision
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_end_to_end_above_band_sell(self):
        # Use delta_bs=50.0 (e.g. 100 options * 0.5 delta each in contract units)
        # so that integer-contract rounding produces non-zero trades for a 2-contract
        # deviation from the band edge.
        #
        # band lower/upper ≈ 50 ± 0.021 (half_width ≈ 0.021 at these params)
        # portfolio_delta = upper + 2.0 → round(upper - (upper+2)) = round(-2) = -2
        delta_bs = 50.0
        gamma_bs = 0.02
        lower, upper = zakamouline_band(
            delta_bs, gamma_bs, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        policy = HedgeBandPolicy(cost_rate=_CR)
        # portfolio_delta 2 contracts above upper → sell 2
        qty = policy.should_rehedge(upper + 2.0, (lower, upper))
        assert qty == -2

    def test_end_to_end_below_band_buy(self):
        # portfolio_delta 3 contracts below lower → buy 3
        delta_bs = 50.0
        gamma_bs = 0.02
        lower, upper = zakamouline_band(
            delta_bs, gamma_bs, _F, _T, _SIG,
            risk_aversion=_RA, cost_rate=_CR,
        )
        policy = HedgeBandPolicy(cost_rate=_CR)
        qty = policy.should_rehedge(lower - 3.0, (lower, upper))
        assert qty == 3
