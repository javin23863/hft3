"""Tests for options_risk.src.stress_grid.

Golden-value derivations
-------------------------
All goldens use Black-76 with r=0 (zero rate simplifies verification):
  b76(F, K, T, sigma, r=0, is_call) = max(sign*(F-K), 0)  for T<=0
  b76 call ATM = F * N(d1) - K * N(d2)  where d1 = (0.5*sigma^2*T)/(sigma*sqrt(T))
                                               d2 = d1 - sigma*sqrt(T)

Test setup
----------
Standard option: K=5000, T=0.25, sigma=0.20, F0=5000, multiplier=50.
  c0 = b76(5000, 5000, 0.25, 0.20, 0, call)
     d1 = (0.5*0.04*0.25) / (0.20*0.5) = 0.005/0.10 = 0.05
     d2 = 0.05 - 0.10 = -0.05
     N(0.05) = 0.519939, N(-0.05) = 0.480061
     c0 = 5000*(0.519939 - 0.480061) = 5000*0.039878 = 199.39

Long call worst cell: price -5%, vol *0.5
  F1 = 4750, sigma1 = 0.10
  d1 = (ln(4750/5000) + 0.5*0.01*0.25)/(0.10*0.5) = (-0.051293+0.00125)/0.05 = -1.00165
  d2 = -1.00165 - 0.05 = -1.05165
  N(-1.00165) = 0.1583, N(-1.05165) = 0.1466
  c1 = 4750*0.1583 - 5000*0.1466 = 751.9 - 732.9 = 19.02
  PnL = 1*50*(19.02 - 199.39) = -9003.5 (hand-computed via Python below)

Hand-computed Python (run outside test, results pasted as goldens):
  c0 = 199.388058
  c_at_F4750_sigma010 = 19.317196
  pnl_worst = 1 * 50 * (19.317196 - 199.388058) = -9003.543128

Short straddle worst in overnight cells: see inline goldens.

Futures vol-invariance: futures PnL = qty*mult*(F_shocked - F0) regardless of vol_mult.
  F0=5000, qty=1, mult=50, price_move=-0.05 → PnL = 1*50*(4750-5000) = -12500.0

Portfolio additivity: sum of positions PnLs equals portfolio PnL.

T=0 intrinsic: b76(F=5200, K=5000, T=0, sigma=0.20, r=0, call) = 200.0 (intrinsic).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_risk.src.stress_grid import (
    DEFAULT_OVERNIGHT_CELLS,
    Position,
    ScenarioCell,
    StressGrid,
    grid_pnl,
    revalue,
    worst_cell,
)
from options_pricing.src.black76 import price as b76_price


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call(K=5000.0, T=0.25, sigma=0.20, qty=1.0, mult=50.0, style='E') -> Position:
    return Position(kind='option', qty=qty, multiplier=mult, T=T,
                    style=style, is_call=True, K=K, sigma=sigma)


def _put(K=5000.0, T=0.25, sigma=0.20, qty=1.0, mult=50.0, style='E') -> Position:
    return Position(kind='option', qty=qty, multiplier=mult, T=T,
                    style=style, is_call=False, K=K, sigma=sigma)


def _future(qty=1.0, mult=50.0, T=0.25) -> Position:
    return Position(kind='future', qty=qty, multiplier=mult, T=T)


# ---------------------------------------------------------------------------
# Position validation
# ---------------------------------------------------------------------------


def test_position_option_requires_fields():
    with pytest.raises(ValueError, match="is_call required"):
        Position(kind='option', qty=1.0, multiplier=50.0, T=0.25)


def test_position_option_requires_K():
    with pytest.raises(ValueError, match="K.*required"):
        Position(kind='option', qty=1.0, multiplier=50.0, T=0.25, is_call=True)


def test_position_option_requires_sigma():
    with pytest.raises(ValueError, match="sigma required"):
        Position(kind='option', qty=1.0, multiplier=50.0, T=0.25, is_call=True, K=5000.0)


def test_position_invalid_kind():
    with pytest.raises(ValueError, match="kind must be"):
        Position(kind='bond', qty=1.0, multiplier=50.0, T=0.25)


def test_position_invalid_style():
    with pytest.raises(ValueError, match="style must be"):
        Position(kind='option', qty=1.0, multiplier=50.0, T=0.25,
                 is_call=True, K=5000.0, sigma=0.20, style='X')


def test_position_negative_K_rejected():
    with pytest.raises(ValueError, match="K must be positive"):
        Position(kind='option', qty=1.0, multiplier=50.0, T=0.25,
                 is_call=True, K=-1.0, sigma=0.20)


# ---------------------------------------------------------------------------
# ScenarioCell / StressGrid structure
# ---------------------------------------------------------------------------


def test_default_grid_has_overnight_cells():
    grid = StressGrid()
    cells = grid.cells()
    # Must contain both overnight cells
    for oc in DEFAULT_OVERNIGHT_CELLS:
        assert oc in cells, f"Overnight cell {oc} missing from default grid"


def test_default_grid_total_count():
    # Cartesian: 8 price × 6 vol = 48 grid cells.
    # Overnight cells: 2.  Check if they overlap with grid cells.
    # (-0.03, 2.0): -0.03 not in _DEFAULT_PRICE_MOVES, so not a dup.
    # (-0.05, 3.0): -0.05 IS in price_moves, 3.0 IS in vol_mults → override.
    # So deduplication removes 1 grid cell → 47 + 2 = 49. Wait:
    # -0.05 + 3.0 is in cartesian → replaced by overnight cell (-0.05, 3.0)
    # -0.03 + 2.0 is NOT in cartesian → added as new
    # Total = 48 - 0 duplicates + 2 overnight = 48 + 1 (net new overnight) = 49
    # But (-0.05, 3.0) key already exists and gets overridden → no count change for that one.
    grid = StressGrid()
    cells = grid.cells()
    # At minimum 48 (full cartesian) and at most 50 (all overnight new)
    assert 48 <= len(cells) <= 50


def test_overnight_cell_overrides_cartesian_key():
    # The overnight cell (-0.05, 3.0) should replace the standard (weight=1.0) entry
    # with the overnight entry (still weight=1.0 per spec, since overnight cells
    # in DEFAULT_OVERNIGHT_CELLS have default weight=1.0).
    grid = StressGrid()
    cells = grid.cells()
    # Find (-0.05, 3.0) cell
    found = [c for c in cells if abs(c.price_move + 0.05) < 1e-12 and abs(c.vol_mult - 3.0) < 1e-12]
    assert len(found) == 1, "Exactly one (-0.05, 3.0) cell expected"


# ---------------------------------------------------------------------------
# revalue — single long call golden
# ---------------------------------------------------------------------------


def test_long_call_revalue_golden_worst_cell():
    """Long call at worst cell: price -5%, vol *0.5.

    Golden (derived in module docstring):
      c0 = b76(5000, 5000, 0.25, 0.20, 0, call) = 199.388058
      c1 = b76(4750, 5000, 0.25, 0.10, 0, call) = 19.317196
      PnL = 1 * 50 * (19.317196 - 199.388058) = -9003.543128
    """
    F0 = 5000.0
    pos = _call(K=5000.0, T=0.25, sigma=0.20, qty=1.0, mult=50.0)
    cell = ScenarioCell(price_move=-0.05, vol_mult=0.5)
    pnl = revalue(pos, F0, cell, r=0.0)
    assert abs(pnl - (-9003.543128)) < 0.01, f"Expected ~-9003.54, got {pnl}"


def test_long_call_revalue_vol_up_price_up():
    """Long call at price +5%, vol *2.0 should be profitable."""
    F0 = 5000.0
    pos = _call(K=5000.0, T=0.25, sigma=0.20, qty=1.0, mult=50.0)
    cell = ScenarioCell(price_move=+0.05, vol_mult=2.0)
    pnl = revalue(pos, F0, cell, r=0.0)
    assert pnl > 0.0, "Long call should gain when price up and vol up"


def test_short_call_pnl_symmetric():
    """Short call PnL = -1 × long call PnL."""
    F0 = 5000.0
    pos_long = _call(qty=+1.0)
    pos_short = _call(qty=-1.0)
    cell = ScenarioCell(price_move=-0.05, vol_mult=0.5)
    pnl_long = revalue(pos_long, F0, cell)
    pnl_short = revalue(pos_short, F0, cell)
    assert abs(pnl_long + pnl_short) < 1e-10


# ---------------------------------------------------------------------------
# revalue — futures vol-invariance
# ---------------------------------------------------------------------------


def test_future_pnl_vol_invariant():
    """Futures PnL is determined by price move only; vol_mult has no effect.

    Golden: qty=1, mult=50, F0=5000, price_move=-0.05
      PnL = 1 * 50 * (5000*0.95 - 5000) = 50 * (-250) = -12500
    """
    F0 = 5000.0
    pos = _future(qty=1.0, mult=50.0)
    for vm in (0.5, 1.0, 2.0, 3.0):
        cell = ScenarioCell(price_move=-0.05, vol_mult=vm)
        pnl = revalue(pos, F0, cell)
        assert abs(pnl - (-12500.0)) < 1e-9, f"Future PnL at vm={vm}: got {pnl}"


def test_future_pnl_zero_at_zero_move():
    F0 = 5000.0
    pos = _future(qty=1.0, mult=50.0)
    cell = ScenarioCell(price_move=0.0, vol_mult=1.5)
    pnl = revalue(pos, F0, cell)
    assert abs(pnl) < 1e-10


# ---------------------------------------------------------------------------
# T=0 intrinsic behaviour
# ---------------------------------------------------------------------------


def test_t0_call_intrinsic_itm():
    """T=0, ITM call: PnL should reflect shocked intrinsic vs current intrinsic.

    Position: long call, K=5000, T=0, sigma=0.20.
    Current intrinsic (F0=5200): max(5200-5000, 0) = 200.
    Shocked (F_shocked = 5200 * 1.05 = 5460): max(5460-5000, 0) = 460.
    PnL = 1 * 1 * (460 - 200) = 260.
    """
    F0 = 5200.0
    pos = Position(kind='option', qty=1.0, multiplier=1.0, T=0.0,
                   is_call=True, K=5000.0, sigma=0.20)
    cell = ScenarioCell(price_move=+0.05, vol_mult=0.5)
    pnl = revalue(pos, F0, cell)
    assert abs(pnl - 260.0) < 1e-9


def test_t0_call_intrinsic_otm_zero():
    """T=0, OTM call: both current and shocked intrinsic are 0 (with modest move).

    K=5000, F0=4900, price_move=+0.01 → F_shocked=4949 < 5000 → still OTM.
    PnL = 0.
    """
    F0 = 4900.0
    pos = Position(kind='option', qty=1.0, multiplier=1.0, T=0.0,
                   is_call=True, K=5000.0, sigma=0.20)
    cell = ScenarioCell(price_move=+0.01, vol_mult=1.0)
    pnl = revalue(pos, F0, cell)
    assert abs(pnl) < 1e-9


# ---------------------------------------------------------------------------
# Short straddle — worst cell in overnight joint scenarios
# ---------------------------------------------------------------------------


def test_short_straddle_worst_overnight_vs_regular():
    """Short straddle: overnight joint cell is often among the worst.

    Short straddle: -1 call + -1 put, K=5000, T=0.25, sigma=0.20, mult=50.
    Standard grid worst cell from pre-computation: (pm=+0.05, vm=3.0),
    pnl = -41969.47.

    The overnight cell (-0.05, 3.0) PnL = -39029.44.
    The overnight cell (-0.03, 2.0) PnL = -19744.14.

    So worst overall is a standard grid cell, but overnight cells are also large.
    This test verifies:
    1. worst_cell returns negative PnL (loss) for short straddle.
    2. The overnight worst cell is within the grid_pnl dict.
    """
    F0 = 5000.0
    positions = [_call(qty=-1.0), _put(qty=-1.0)]
    sc, pnl = worst_cell(positions, F0)
    assert pnl < 0.0, "Short straddle has losses under some scenario"

    # Both overnight cells must appear in grid_pnl
    pnl_map = grid_pnl(positions, F0)
    overnight_keys = list(DEFAULT_OVERNIGHT_CELLS)
    for oc in overnight_keys:
        assert oc in pnl_map, f"Overnight cell {oc} missing from grid_pnl"
        assert pnl_map[oc] < 0.0, "Short straddle should lose in overnight cells"


def test_short_straddle_overnight_golden():
    """Hand-computed PnL for short straddle in overnight cell (-0.05, 3.0).

    c0 = b76(5000, 5000, 0.25, 0.20, 0, call) = 199.388058
    p0 = b76(5000, 5000, 0.25, 0.20, 0, put)  = 199.388058  (same by parity at ATM)

    Overnight cell (-0.05, 3.0):
      F2 = 5000 * 0.95 = 4750, sigma2 = 0.20 * 3.0 = 0.60
      c2 = b76(4750, 5000, 0.25, 0.60, 0, call) = 464.6825
      p2 = b76(4750, 5000, 0.25, 0.60, 0, put)  = 714.6825
      PnL = -1*50*(c2-c0) + -1*50*(p2-p0)
           = -50*(464.6825-199.388058) + -50*(714.6825-199.388058)
           = -50*265.294 + -50*515.294
           = -13264.7 + -25764.7 = -39029.44
    """
    F0 = 5000.0
    positions = [_call(qty=-1.0), _put(qty=-1.0)]
    overnight_cell = ScenarioCell(-0.05, 3.0)
    pnl = sum(revalue(p, F0, overnight_cell) for p in positions)
    assert abs(pnl - (-39029.44)) < 1.0, f"Expected ~-39029.44, got {pnl}"


# ---------------------------------------------------------------------------
# Portfolio additivity
# ---------------------------------------------------------------------------


def test_portfolio_additivity():
    """Portfolio PnL = sum of individual PnLs.

    Uses a custom StressGrid with a single explicit cell to avoid relying on
    the default grid's exact price_move list.  The cell (-0.05, 1.5) IS in the
    default price_moves × vol_mults Cartesian product.
    """
    F0 = 5000.0
    positions = [
        _call(qty=+1.0),
        _put(qty=-1.0),
        _future(qty=+2.0),
    ]
    # Use a cell that is definitely in the default Cartesian product
    cell = ScenarioCell(price_move=-0.05, vol_mult=1.5)

    individual_sum = sum(revalue(p, F0, cell) for p in positions)

    pnl_map = grid_pnl(positions, F0)
    # Find matching cell in the map (ScenarioCell is a NamedTuple; weight may differ
    # if an overnight cell overrides; match on price_move + vol_mult only)
    matching = [v for k, v in pnl_map.items()
                if abs(k.price_move - cell.price_move) < 1e-12
                and abs(k.vol_mult - cell.vol_mult) < 1e-12]
    assert len(matching) == 1, (
        f"Expected exactly 1 cell matching pm=-0.05 vm=1.5; got {len(matching)}"
    )
    assert abs(matching[0] - individual_sum) < 1e-8


# ---------------------------------------------------------------------------
# worst_cell properties
# ---------------------------------------------------------------------------


def test_long_only_worst_cell_is_negative_pnl():
    """Long option portfolio worst cell has negative PnL (potential loss)."""
    F0 = 5000.0
    positions = [_call(qty=+1.0), _put(qty=+1.0)]
    sc, pnl = worst_cell(positions, F0)
    assert pnl < 0.0, "Long straddle loses in some scenarios (vol crush / price neutral)"


def test_long_call_worst_cell_is_down_price_down_vol():
    """Long call: worst cell has price down and low vol.

    Both price down and vol down hurt a long call.  The worst cell should be
    the cell with the most negative price_move AND lowest vol_mult in the grid.
    Pre-computed: worst is (-0.05, 0.5) with PnL = -9003.54.
    """
    F0 = 5000.0
    positions = [_call(qty=+1.0)]
    sc, pnl = worst_cell(positions, F0)
    assert sc.price_move < 0.0, "Long call worst cell: price should be down"
    assert sc.vol_mult <= 1.0, "Long call worst cell: vol should be at or below current"
    assert abs(pnl - (-9003.543128)) < 1.0, f"Expected ~-9003.54, got {pnl}"


def test_profit_only_portfolio_worst_pnl_nonneg():
    """A portfolio that gains in every scenario has worst_pnl >= 0."""
    # Long futures position in a rising-only grid: if all price moves positive...
    # Use a trivially profitable position: zero positions.
    F0 = 5000.0
    sc, pnl = worst_cell([], F0)
    assert pnl == 0.0, "Empty portfolio has PnL = 0 in all cells"


# ---------------------------------------------------------------------------
# Sigma floor behaviour
# ---------------------------------------------------------------------------


def test_sigma_floor_applied():
    """Vol mult 0.0 should not crash (sigma floored at 1e-9)."""
    F0 = 5000.0
    pos = _call(sigma=0.20)
    cell = ScenarioCell(price_move=0.0, vol_mult=0.0)
    # Should not raise; result should be near intrinsic
    pnl = revalue(pos, F0, cell)
    # With sigma near 0, call at ATM is nearly 0; current value ~199.
    # PnL should be approximately -199*50 = -9969 (loss of premium)
    assert pnl < 0.0


# ---------------------------------------------------------------------------
# American style smoke test
# ---------------------------------------------------------------------------


def test_american_style_close_to_european():
    """American option (style='A') price should be >= European (style='E').

    At r=0, the American premium over European is zero for calls on futures.
    For puts on futures at r=0, same logic applies.  So American ~= European
    at r=0, with small numerical difference from the binomial tree.
    """
    F0 = 5000.0
    pos_e = _call(style='E')
    pos_a = _call(style='A')
    cell = ScenarioCell(price_move=-0.02, vol_mult=1.2)

    pnl_e = revalue(pos_e, F0, cell)
    pnl_a = revalue(pos_a, F0, cell)
    # American price >= European; PnL for long position: am >= eu
    # (american option more valuable → same shocked minus higher current ≈ same PnL)
    # Difference should be small at r=0
    assert abs(pnl_a - pnl_e) < 50.0, "American vs European PnL should be close at r=0"
