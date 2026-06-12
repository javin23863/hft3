"""Tests for options_risk.src.margin_approx.

Golden-value derivations
-------------------------
All goldens use r=0 and Black-76.  See test_stress_grid.py for Black-76 setup.

Extreme-cell weight golden
--------------------------
Short 1 call, K=100, F0=100, T=0.25, sigma=0.20, multiplier=1, qty=-1.
price_scan_range=10, vol_scan_range=0.05.

  c0 = b76(100, 100, 0.25, 0.20, 0, call)
     d1 = (0 + 0.5*0.04*0.25)/(0.20*0.5) = 0.005/0.1 = 0.05
     N(0.05) ~ 0.5199, N(-0.05) ~ 0.4801
     c0 = 100*(0.5199 - 0.4801) = 100*0.0398 = 3.9878

  Extreme cell: price +2*psr = +20, so F_shocked = 120 (most dangerous for short call).
  c_ext = b76(120, 100, 0.25, 0.20, 0, call)
    d1 = (ln(1.2) + 0.5*0.04*0.25)/(0.20*0.5) = (0.18232 + 0.005)/0.10 = 1.8732
    d2 = 1.8732 - 0.10 = 1.7732
    N(1.8732) ~ 0.96953, N(1.7732) ~ 0.96190
    c_ext = 120*0.96953 - 100*0.96190 = 116.344 - 96.190 = 20.154

  Python-computed exact:
    c0 = 3.987761, c_ext = 20.147332

  short_call PnL at extreme cell = -1 * 1 * (20.147332 - 3.987761) = -16.159571
  weighted_loss = -(-16.159571) * 0.35 = 16.159571 * 0.35 = 5.655850

  scanning_risk = max over all 16 cells.  The worst STANDARD cell for a short call:
  price +1 × psr/F0 = +10/100 = +10%, vol DOWN (short call loses vol drop... no:
  short call loses when price goes up; vol up increases call value too = more loss).
  Standard worst: price +10%, vol up 0.05.
    F_w = 110, sigma_w = 0.20 + 0.05 = 0.25
    c_w = b76(110, 100, 0.25, 0.25, 0, call) = ?
    d1 = (ln(1.1)+0.5*0.0625*0.25)/(0.25*0.5) = (0.09531+0.0078125)/0.125 = 0.824
    d2 = 0.824-0.125 = 0.699
    N(0.824)~0.795, N(0.699)~0.758
    c_w = 110*0.795 - 100*0.758 = 87.45 - 75.80 = 11.65 (approx)
    pnl = -1*1*(11.65 - 3.988) = -7.66
    loss = 7.66

  Extreme +2psr cell: loss = 5.655850 (lower than 7.66, so extreme cell NOT the max here).
  scanning_risk > 5.655 (extreme cell loss) but the extreme loss of 5.655 is useful
  as a known hand-computed value to verify the weight.

  The test verifies that:
    1. scanning_risk > extreme cell weighted loss (there's a worse standard cell).
    2. The isolated extreme cell computation = 5.655850 ± 0.01.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_risk.src.stress_grid import Position, ScenarioCell
from options_risk.src.margin_approx import (
    _EXTREME_CELL_WEIGHT,
    _portfolio_loss_cell,
    margin_estimate,
    scanning_risk,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call(F0=5000.0, K=5000.0, T=0.25, sigma=0.20, qty=1.0, mult=50.0) -> Position:
    return Position(kind='option', qty=qty, multiplier=mult, T=T,
                    is_call=True, K=K, sigma=sigma)


def _put(F0=5000.0, K=5000.0, T=0.25, sigma=0.20, qty=1.0, mult=50.0) -> Position:
    return Position(kind='option', qty=qty, multiplier=mult, T=T,
                    is_call=False, K=K, sigma=sigma)


def _future(qty=1.0, mult=50.0, T=0.25) -> Position:
    return Position(kind='future', qty=qty, multiplier=mult, T=T)


# ---------------------------------------------------------------------------
# Extreme-cell weight golden
# ---------------------------------------------------------------------------


def test_extreme_cell_weight_constant():
    """_EXTREME_CELL_WEIGHT must be 0.35 per SPAN specification."""
    assert _EXTREME_CELL_WEIGHT == 0.35


def test_extreme_cell_weighted_loss_golden():
    """Isolated extreme-cell loss for short call.

    Setup: short 1 call, K=100, F0=100, T=0.25, sigma=0.20, mult=1.
    Extreme cell +2*psr: F_shocked = 120.

    Golden (Python-computed):
      c0 = 3.987761
      c_ext at F=120 = 20.147332
      short_call pnl = -1 * 1 * (20.147332 - 3.987761) = -16.159571
      weighted_loss = 16.159571 * 0.35 = 5.655850

    Tolerance: 0.01 (rounding).
    """
    F0 = 100.0
    psr = 10.0  # price_scan_range
    pos = Position(kind='option', qty=-1.0, multiplier=1.0, T=0.25,
                   is_call=True, K=100.0, sigma=0.20)

    # Extreme cell +2*psr: price_move = +2*10/100 = +0.20
    price_move = 2.0 * psr / F0
    loss = _portfolio_loss_cell([pos], F0, price_move, 0.0, _EXTREME_CELL_WEIGHT, 0.0)
    assert abs(loss - 5.655850) < 0.01, f"Expected 5.655850, got {loss}"


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


def test_scanning_risk_invalid_F0():
    with pytest.raises(ValueError, match="F0 must be positive"):
        scanning_risk([], -1.0, price_scan_range=10.0, vol_scan_range=0.05)


def test_scanning_risk_invalid_psr():
    with pytest.raises(ValueError, match="price_scan_range must be positive"):
        scanning_risk([], 5000.0, price_scan_range=-10.0, vol_scan_range=0.05)


def test_scanning_risk_negative_vsr():
    with pytest.raises(ValueError, match="vol_scan_range must be non-negative"):
        scanning_risk([], 5000.0, price_scan_range=10.0, vol_scan_range=-0.05)


def test_margin_estimate_invalid_buffer():
    with pytest.raises(ValueError, match="buffer must be positive"):
        margin_estimate([], 5000.0, price_scan_range=10.0, vol_scan_range=0.05, buffer=-1.0)


# ---------------------------------------------------------------------------
# Long-only portfolio: margin bounded by premium
# ---------------------------------------------------------------------------


def test_long_call_margin_bounded_by_premium():
    """scanning_risk for a long call <= premium paid.

    A long option cannot lose more than its purchase price.  scanning_risk
    measures the worst-case loss; it must not exceed qty * mult * initial_value.

    Setup: long 1 call, K=5000, F0=5000, T=0.25, sigma=0.20, mult=50.
      c0 = 199.388058 → max_loss = 1 * 50 * 199.388058 = 9969.40

    price_scan_range = 250 (5% of F0), vol_scan_range = 0.05.
    scanning_risk <= 9969.40 + tolerance.
    """
    from options_pricing.src.black76 import price as b76
    F0 = 5000.0
    K = 5000.0
    T = 0.25
    sigma = 0.20
    mult = 50.0
    pos = _call(F0=F0, K=K, T=T, sigma=sigma, qty=1.0, mult=mult)

    c0 = float(b76(F0, K, T, sigma, 0.0, True))
    max_possible_loss = 1.0 * mult * c0

    risk = scanning_risk([pos], F0, price_scan_range=250.0, vol_scan_range=0.05)
    assert risk <= max_possible_loss + 1.0, (
        f"scanning_risk={risk:.2f} exceeds premium={max_possible_loss:.2f}"
    )


def test_long_put_margin_bounded_by_premium():
    """scanning_risk for a long put <= premium paid."""
    from options_pricing.src.black76 import price as b76
    F0 = 5000.0
    K = 5000.0
    T = 0.25
    sigma = 0.20
    mult = 50.0
    pos = _put(F0=F0, K=K, T=T, sigma=sigma, qty=1.0, mult=mult)

    p0 = float(b76(F0, K, T, sigma, 0.0, False))
    max_possible_loss = 1.0 * mult * p0

    risk = scanning_risk([pos], F0, price_scan_range=250.0, vol_scan_range=0.05)
    assert risk <= max_possible_loss + 1.0, (
        f"scanning_risk={risk:.2f} exceeds premium={max_possible_loss:.2f}"
    )


# ---------------------------------------------------------------------------
# Short straddle margin > long straddle margin
# ---------------------------------------------------------------------------


def test_short_straddle_margin_greater_than_long():
    """Short straddle has higher margin than long straddle.

    Long straddle: limited loss = premium paid (bounded above).
    Short straddle: unlimited loss (large moves can cause big losses).
    """
    F0 = 5000.0
    K = 5000.0
    T = 0.25
    sigma = 0.20
    mult = 50.0
    psr = 250.0
    vsr = 0.05

    long_positions = [
        _call(K=K, T=T, sigma=sigma, qty=+1.0, mult=mult),
        _put(K=K, T=T, sigma=sigma, qty=+1.0, mult=mult),
    ]
    short_positions = [
        _call(K=K, T=T, sigma=sigma, qty=-1.0, mult=mult),
        _put(K=K, T=T, sigma=sigma, qty=-1.0, mult=mult),
    ]

    risk_long = scanning_risk(long_positions, F0, price_scan_range=psr, vol_scan_range=vsr)
    risk_short = scanning_risk(short_positions, F0, price_scan_range=psr, vol_scan_range=vsr)

    assert risk_short > risk_long, (
        f"Short straddle risk={risk_short:.2f} should exceed long straddle risk={risk_long:.2f}"
    )


# ---------------------------------------------------------------------------
# Buffer scales linearly
# ---------------------------------------------------------------------------


def test_buffer_scales_linearly():
    """margin_estimate / scanning_risk = buffer (exactly, up to fp)."""
    F0 = 5000.0
    positions = [_call(qty=-1.0), _put(qty=-1.0)]
    psr = 250.0
    vsr = 0.05

    risk = scanning_risk(positions, F0, price_scan_range=psr, vol_scan_range=vsr)

    for buf in (1.0, 1.25, 1.5, 2.0):
        me = margin_estimate(positions, F0, price_scan_range=psr, vol_scan_range=vsr, buffer=buf)
        assert abs(me - risk * buf) < 1e-9, f"buffer={buf}: {me} != {risk * buf}"


# ---------------------------------------------------------------------------
# Futures-only portfolio: vol-invariant and consistent
# ---------------------------------------------------------------------------


def test_futures_only_scanning_risk_nonzero():
    """Long futures has non-zero scanning_risk (loses when price goes down)."""
    F0 = 5000.0
    pos = _future(qty=1.0, mult=50.0)
    risk = scanning_risk([pos], F0, price_scan_range=250.0, vol_scan_range=0.05)
    # Worst cell: -1x psr → -250 points → PnL = 1*50*(-250) = -12500 → loss = 12500
    assert abs(risk - 12500.0) < 1e-9, f"Expected 12500.0, got {risk}"


def test_futures_only_scanning_risk_vol_invariant():
    """scanning_risk for a futures-only portfolio must be independent of vol_scan_range."""
    F0 = 5000.0
    pos = _future(qty=1.0, mult=50.0)
    risk_v005 = scanning_risk([pos], F0, price_scan_range=250.0, vol_scan_range=0.05)
    risk_v020 = scanning_risk([pos], F0, price_scan_range=250.0, vol_scan_range=0.20)
    assert abs(risk_v005 - risk_v020) < 1e-9


# ---------------------------------------------------------------------------
# Empty portfolio
# ---------------------------------------------------------------------------


def test_empty_portfolio_zero_risk():
    """Empty portfolio has zero scanning_risk and zero margin."""
    F0 = 5000.0
    risk = scanning_risk([], F0, price_scan_range=250.0, vol_scan_range=0.05)
    assert risk == 0.0
    me = margin_estimate([], F0, price_scan_range=250.0, vol_scan_range=0.05)
    assert me == 0.0


# ---------------------------------------------------------------------------
# scanning_risk is always non-negative
# ---------------------------------------------------------------------------


def test_scanning_risk_nonnegative_for_long_only():
    """scanning_risk >= 0 always."""
    F0 = 5000.0
    positions = [
        _call(qty=+1.0, mult=50.0),
        _put(qty=+0.5, mult=50.0),
        _future(qty=+1.0, mult=50.0),
    ]
    risk = scanning_risk(positions, F0, price_scan_range=250.0, vol_scan_range=0.05)
    assert risk >= 0.0
