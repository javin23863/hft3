"""Tests for box-spread implied financing curve (box_financing.py).

Golden-value derivations
------------------------
Round-trip at r=0.0530, T=0.25, K-spread=100:
    box_cost = 100 * exp(-0.0530 * 0.25)
             = 100 * exp(-0.013250)
             = 100 * 0.98684163...
             = 98.684163...
    recover:  r = -ln(98.684163... / 100) / 0.25
                = -(-0.013250) / 0.25
                = 0.053000...  (exact by construction, up to float precision)

Negative rate path at r=-0.0100, T=0.5, K-spread=50:
    box_cost = 50 * exp(-(-0.0100) * 0.5)
             = 50 * exp(0.005)
             = 50 * 1.005012...
             = 50.2506...
    box_cost > K-spread (50) confirms negative rate pricing; recovery works.

financing_curve duplicate-T averaging:
    Two boxes at T=0.5: r1=0.04, r2=0.06 => averaged r=0.05.
    Box costs back-computed from r1, r2; curve must return (T=0.5, r=0.05).

sanity_band thresholds:
    sofr=0.0530; |r-sofr| = 0.0200 (exactly 200bp) -> OK (threshold is strict >).
    sofr=0.0530; |r-sofr| = 0.0201 -> SUSPECT.
    no hint -> UNCHECKED.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_pricing.src.box_financing import (
    FinancingPoint,
    SanityLabel,
    box_price,
    financing_curve,
    implied_financing_rate,
    sanity_band,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_box_cost(K1: float, K2: float, T: float, r: float) -> float:
    """European box cost for given rate: (K2-K1)*exp(-r*T)."""
    return (K2 - K1) * math.exp(-r * T)


# ---------------------------------------------------------------------------
# 1. box_price
# ---------------------------------------------------------------------------


class TestBoxPrice:
    """box_price = (C(K1) - P(K1)) - (C(K2) - P(K2))."""

    def test_basic_value(self):
        # Synthetic put-call parity: C(K) - P(K) ≈ F - K (ignoring discount).
        # Here just check arithmetic directly.
        # C(K1)=10, P(K1)=5 => synth fwd at K1 = 5
        # C(K2)=3,  P(K2)=8 => synth fwd at K2 = -5
        # box = 5 - (-5) = 10 = K2 - K1 (at-the-money box at r=0)
        result = box_price(100.0, 110.0, 10.0, 5.0, 3.0, 8.0)
        assert math.isclose(result, 10.0, rel_tol=1e-12)

    def test_at_par_no_discount(self):
        # Exactly zero-rate box: box_cost = K2 - K1
        K1, K2 = 50.0, 75.0
        # C-P at each strike = F - K (for r=0, T=0)
        # choose F=60: C(50)-P(50) = 10, C(75)-P(75) = -15
        # box = 10 - (-15) = 25 = K2-K1
        result = box_price(50.0, 75.0, 10.0, 0.0, 0.0, 15.0)
        assert math.isclose(result, 25.0, rel_tol=1e-12)

    def test_k2_le_k1_raises(self):
        with pytest.raises(ValueError, match="K2 must be strictly greater than K1"):
            box_price(110.0, 100.0, 5.0, 2.0, 3.0, 1.0)

    def test_k2_equal_k1_raises(self):
        with pytest.raises(ValueError, match="K2 must be strictly greater than K1"):
            box_price(100.0, 100.0, 5.0, 2.0, 3.0, 1.0)

    def test_negative_box_cost_allowed(self):
        # Degenerate prices can yield negative box_cost; box_price itself
        # does not validate the sign — implied_financing_rate does.
        result = box_price(100.0, 110.0, 1.0, 5.0, 6.0, 4.0)
        # (1-5) - (6-4) = -4 - 2 = -6  — negative, no error from box_price
        assert result < 0.0


# ---------------------------------------------------------------------------
# 2. implied_financing_rate
# ---------------------------------------------------------------------------


class TestImpliedFinancingRate:
    """Round-trip and error-path tests."""

    def test_golden_roundtrip(self):
        # r=0.0530, T=0.25, spread=100
        # box_cost = 100*exp(-0.053*0.25) = 100*exp(-0.013250)
        r_true = 0.0530
        T = 0.25
        K1, K2 = 4000.0, 4100.0
        bc = _make_box_cost(K1, K2, T, r_true)
        r_recovered = implied_financing_rate(bc, K1, K2, T)
        # Must recover to machine precision (1e-12 tolerance per spec)
        assert abs(r_recovered - r_true) < 1e-12

    def test_negative_rate_allowed(self):
        # r=-0.0100, T=0.5, spread=50
        # box_cost = 50*exp(0.005) > 50 (box above undiscounted spread)
        r_true = -0.0100
        T = 0.5
        K1, K2 = 1000.0, 1050.0
        bc = _make_box_cost(K1, K2, T, r_true)
        assert bc > (K2 - K1), "negative rate => box_cost > spread"
        r_recovered = implied_financing_rate(bc, K1, K2, T)
        assert abs(r_recovered - r_true) < 1e-12

    def test_zero_rate(self):
        # r=0: box_cost == K2-K1 exactly
        T = 1.0
        K1, K2 = 200.0, 300.0
        bc = K2 - K1   # = 100.0
        r_recovered = implied_financing_rate(bc, K1, K2, T)
        assert abs(r_recovered) < 1e-12

    def test_box_cost_zero_raises(self):
        with pytest.raises(ValueError, match="box_cost must be > 0"):
            implied_financing_rate(0.0, 100.0, 110.0, 0.25)

    def test_box_cost_negative_raises(self):
        with pytest.raises(ValueError, match="box_cost must be > 0"):
            implied_financing_rate(-5.0, 100.0, 110.0, 0.25)

    def test_T_zero_raises(self):
        with pytest.raises(ValueError, match="T must be > 0"):
            implied_financing_rate(95.0, 100.0, 200.0, 0.0)

    def test_T_negative_raises(self):
        with pytest.raises(ValueError, match="T must be > 0"):
            implied_financing_rate(95.0, 100.0, 200.0, -0.5)

    def test_k2_le_k1_raises(self):
        with pytest.raises(ValueError, match="K2 must be > K1"):
            implied_financing_rate(95.0, 200.0, 100.0, 0.25)

    def test_k2_equal_k1_raises(self):
        with pytest.raises(ValueError, match="K2 must be > K1"):
            implied_financing_rate(95.0, 100.0, 100.0, 0.25)

    def test_various_tenors_and_spreads(self):
        # Parametric check: any valid (r, T, spread) round-trips exactly.
        cases = [
            (0.05, 0.083, 50.0),    # ~1 month
            (0.053, 0.25, 100.0),   # quarterly
            (0.04, 1.0, 500.0),     # 1 year, large spread
            (-0.005, 0.5, 25.0),    # negative rate, short tenor
        ]
        for r_true, T, spread in cases:
            K1, K2 = 2000.0, 2000.0 + spread
            bc = _make_box_cost(K1, K2, T, r_true)
            r_rec = implied_financing_rate(bc, K1, K2, T)
            assert abs(r_rec - r_true) < 1e-12, (
                f"Failed for r={r_true}, T={T}, spread={spread}: got {r_rec}"
            )


# ---------------------------------------------------------------------------
# 3. financing_curve
# ---------------------------------------------------------------------------


class TestFinancingCurve:
    """Sorting, duplicate-T averaging, and multi-point curve."""

    def test_single_point(self):
        r = 0.0530
        T = 0.25
        K1, K2 = 4000.0, 4100.0
        bc = _make_box_cost(K1, K2, T, r)
        curve = financing_curve([(K1, K2, T, bc)])
        assert len(curve) == 1
        assert math.isclose(curve[0].T, T, rel_tol=1e-12)
        assert abs(curve[0].r - r) < 1e-12

    def test_sorted_by_T(self):
        # Supply boxes out of order; curve must come back sorted ascending.
        boxes = []
        expected_Ts = [1.0, 0.5, 0.25, 2.0]
        for T in expected_Ts:
            r = 0.05
            K1, K2 = 100.0, 200.0
            bc = _make_box_cost(K1, K2, T, r)
            boxes.append((K1, K2, T, bc))
        curve = financing_curve(boxes)
        Ts = [p.T for p in curve]
        assert Ts == sorted(Ts), f"Curve not sorted: {Ts}"
        assert Ts == sorted(expected_Ts)

    def test_duplicate_T_averaged(self):
        # Two boxes at T=0.5: one implying r=0.04, other implying r=0.06.
        # Averaged r should be 0.05.
        T = 0.5
        K1a, K2a = 100.0, 150.0   # spread=50
        K1b, K2b = 200.0, 300.0   # spread=100
        r1, r2 = 0.04, 0.06
        bc1 = _make_box_cost(K1a, K2a, T, r1)
        bc2 = _make_box_cost(K1b, K2b, T, r2)
        curve = financing_curve([(K1a, K2a, T, bc1), (K1b, K2b, T, bc2)])
        assert len(curve) == 1, "duplicate T must be collapsed to one point"
        assert math.isclose(curve[0].T, T, rel_tol=1e-12)
        assert abs(curve[0].r - 0.05) < 1e-12

    def test_duplicate_T_three_boxes_averaged(self):
        T = 1.0
        rates = [0.03, 0.05, 0.07]
        boxes = []
        for i, r in enumerate(rates):
            K1 = float(100 + i * 100)
            K2 = K1 + 50.0
            bc = _make_box_cost(K1, K2, T, r)
            boxes.append((K1, K2, T, bc))
        curve = financing_curve(boxes)
        assert len(curve) == 1
        assert abs(curve[0].r - 0.05) < 1e-12   # (0.03+0.05+0.07)/3 = 0.05

    def test_multi_tenor_curve_sorted(self):
        # Three distinct tenors, verify correct r extraction at each.
        spec = [
            (4000.0, 4100.0, 0.25, 0.053),
            (4000.0, 4200.0, 0.5,  0.052),
            (4000.0, 4500.0, 1.0,  0.050),
        ]
        boxes = []
        for K1, K2, T, r in spec:
            bc = _make_box_cost(K1, K2, T, r)
            boxes.append((K1, K2, T, bc))
        # shuffle input
        boxes_shuffled = [boxes[2], boxes[0], boxes[1]]
        curve = financing_curve(boxes_shuffled)
        assert len(curve) == 3
        Ts = [p.T for p in curve]
        assert Ts == sorted(Ts)
        for pt, (_, _, T_exp, r_exp) in zip(curve, sorted(spec, key=lambda x: x[2])):
            assert math.isclose(pt.T, T_exp, rel_tol=1e-12)
            assert abs(pt.r - r_exp) < 1e-12

    def test_empty_input(self):
        curve = financing_curve([])
        assert curve == []

    def test_invalid_box_propagates(self):
        with pytest.raises(ValueError):
            financing_curve([(100.0, 110.0, 0.25, -5.0)])


# ---------------------------------------------------------------------------
# 4. sanity_band
# ---------------------------------------------------------------------------


class TestSanityBand:
    """Classification of implied rates vs SOFR benchmark."""

    _SOFR = 0.0530   # 5.30%

    def test_unchecked_without_hint(self):
        assert sanity_band(0.0530) is SanityLabel.UNCHECKED

    def test_ok_exact_sofr(self):
        assert sanity_band(self._SOFR, sofr_hint=self._SOFR) is SanityLabel.OK

    def test_ok_plus_10bp(self):
        # Box rate = SOFR + 10bp = 5.40% (within plan-specified normal range)
        r = self._SOFR + 0.0010
        assert sanity_band(r, sofr_hint=self._SOFR) is SanityLabel.OK

    def test_ok_at_200bp_boundary(self):
        # Exactly 200bp above SOFR: OK (threshold is strict >)
        r = self._SOFR + 0.0200
        assert sanity_band(r, sofr_hint=self._SOFR) is SanityLabel.OK

    def test_ok_at_minus_200bp_boundary(self):
        # Exactly 200bp below SOFR: OK
        r = self._SOFR - 0.0200
        assert sanity_band(r, sofr_hint=self._SOFR) is SanityLabel.OK

    def test_suspect_above_200bp(self):
        # 200.1bp above SOFR: SUSPECT
        r = self._SOFR + 0.0200 + 1e-5
        assert sanity_band(r, sofr_hint=self._SOFR) is SanityLabel.SUSPECT

    def test_suspect_below_200bp(self):
        # 201bp below SOFR: SUSPECT
        r = self._SOFR - 0.0201
        assert sanity_band(r, sofr_hint=self._SOFR) is SanityLabel.SUSPECT

    def test_negative_rate_ok_when_close_to_sofr(self):
        # sofr_hint=-0.005, r=-0.003: |diff|=2bp < 200bp => OK
        assert sanity_band(-0.003, sofr_hint=-0.005) is SanityLabel.OK

    def test_negative_rate_suspect_when_far(self):
        # sofr_hint=0.05, r=-0.01: |diff|=6% = 600bp => SUSPECT
        assert sanity_band(-0.01, sofr_hint=0.05) is SanityLabel.SUSPECT

    def test_labels_are_strings(self):
        # SanityLabel is a str Enum — usable as plain string
        label = sanity_band(self._SOFR, sofr_hint=self._SOFR)
        assert label == "ok"
        assert isinstance(label, str)


# ---------------------------------------------------------------------------
# 5. Integration: box_price -> implied_financing_rate -> financing_curve
# ---------------------------------------------------------------------------


class TestIntegration:
    """End-to-end: construct synthetic option prices, recover curve."""

    def test_full_pipeline_single_expiry(self):
        # Set up a synthetic market at r=5.30%, T=0.25
        r_true = 0.0530
        T = 0.25
        F = 4000.0   # futures price
        K1, K2 = 3950.0, 4050.0

        # Synthetic European put-call parity (r=0 approx for illustration):
        # C(K) - P(K) = (F - K) * exp(-r*T)  for European futures options
        df = math.exp(-r_true * T)
        c_k1 = max(F - K1, 0.0) * df + 1.0   # add premium to prevent degeneracy
        p_k1 = c_k1 - (F - K1) * df
        c_k2 = max(F - K2, 0.0) * df + 0.5
        p_k2 = c_k2 - (F - K2) * df

        bc = box_price(K1, K2, c_k1, p_k1, c_k2, p_k2)
        # Verify box_cost = (K2-K1)*exp(-r*T)
        expected_bc = (K2 - K1) * df
        assert math.isclose(bc, expected_bc, rel_tol=1e-12)

        r_rec = implied_financing_rate(bc, K1, K2, T)
        assert abs(r_rec - r_true) < 1e-12

        curve = financing_curve([(K1, K2, T, bc)])
        assert len(curve) == 1
        assert abs(curve[0].r - r_true) < 1e-12

        label = sanity_band(curve[0].r, sofr_hint=0.0530)
        assert label is SanityLabel.OK

    def test_financing_point_namedtuple_fields(self):
        pt = FinancingPoint(T=0.25, r=0.053)
        assert pt.T == 0.25
        assert pt.r == 0.053
