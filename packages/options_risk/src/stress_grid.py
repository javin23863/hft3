"""Full-revaluation stress scenario grid for options and futures portfolios.

Design intent
-------------
Plan spec: price ±5% × vol 0.5-3.0x full revaluation, worst-cell hard limit;
overnight -3%/-5% joint vol x2/x3 cells.

All revaluations are INSTANTANEOUS shocks: time-to-expiry T is held constant
across all scenarios.  This is the correct convention for an end-of-day
risk grid where the holding period is effectively zero (mark-to-model shock,
not a theta-roll).  Calendar-time overnight scenarios should be layered on
top with a separate theta adjustment if needed.

Pricing model selection
-----------------------
European options (style='E'): Black-76 closed-form (options_pricing.black76.price).
American options  (style='A'): LR binomial tree with steps=101.
  Speed/accuracy tradeoff: 101 steps is a fast approximation; the American
  early-exercise premium at ATM for ES FOPs is typically < 0.5% of premium.
  Use steps >= 401 for production valuation; 101 is adequate for a risk-grid
  sweep that calls revalue thousands of times.
Futures (kind='future'): linear — PnL = qty * multiplier * (F_shocked - F0).

Sigma floor
-----------
After applying a vol multiplier, sigma is floored at 1e-9 to avoid
divide-by-zero in Black-76.  This floor effectively returns intrinsic
value for any mult that would otherwise produce sigma <= 0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np

from options_pricing.src.black76 import price as b76_price
from options_pricing.src.american import lr_tree_price

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SIGMA_FLOOR = 1e-9
_AMERICAN_STEPS = 101  # fast approximation for risk-grid sweeps; see module docstring

# ---------------------------------------------------------------------------
# Position dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    """A single position in the portfolio.

    Parameters
    ----------
    kind : 'option' or 'future'.
    qty : signed quantity (positive = long, negative = short).
    multiplier : contract multiplier (e.g. 50 for ES).
    T : time to expiry in years.  Required for options; ignored for futures
        in revalue (futures PnL is linear in F regardless of T).
    style : 'E' (European) or 'A' (American).  Only used when kind='option'.
    is_call : True = call, False = put.  Required when kind='option'.
    K : strike price.  Required when kind='option'.
    sigma : implied volatility at the position.  Required when kind='option'.

    Raises
    ------
    ValueError if kind='option' and any of is_call, K, sigma, style are absent
    or invalid.
    """

    kind: str           # 'option' | 'future'
    qty: float          # signed
    multiplier: float
    T: float            # years to expiry
    style: str = 'E'   # 'E' | 'A'
    is_call: bool | None = None
    K: float | None = None
    sigma: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in ('option', 'future'):
            raise ValueError(f"kind must be 'option' or 'future'; got {self.kind!r}")
        if self.style not in ('E', 'A'):
            raise ValueError(f"style must be 'E' or 'A'; got {self.style!r}")
        if self.kind == 'option':
            if self.is_call is None:
                raise ValueError("is_call required for kind='option'")
            if self.K is None:
                raise ValueError("K (strike) required for kind='option'")
            if self.sigma is None:
                raise ValueError("sigma required for kind='option'")
            if self.K <= 0:
                raise ValueError(f"K must be positive; got {self.K}")
            if self.sigma <= 0:
                raise ValueError(f"sigma must be > 0; got {self.sigma}")

    @property
    def is_option(self) -> bool:
        return self.kind == 'option'

    @property
    def is_future(self) -> bool:
        return self.kind == 'future'


# ---------------------------------------------------------------------------
# ScenarioCell
# ---------------------------------------------------------------------------


class ScenarioCell(NamedTuple):
    """One stress scenario cell.

    price_move : relative move applied to F0, e.g. -0.05 means F = F0 * 0.95.
    vol_mult   : multiplier applied to position's sigma, e.g. 2.0 means
                 sigma_shocked = sigma * 2.0 (floored at _SIGMA_FLOOR).
    weight     : portfolio-level weight for aggregation (default 1.0).
                 Extreme cells use 0.35 per SPAN convention.
    """

    price_move: float
    vol_mult: float
    weight: float = 1.0


# ---------------------------------------------------------------------------
# Default scenario grids
# ---------------------------------------------------------------------------

# Overnight joint scenarios: (price_move, vol_mult).
# Documented as plan spec: -3%/vol×2 and -5%/vol×3.
DEFAULT_OVERNIGHT_CELLS: tuple[ScenarioCell, ...] = (
    ScenarioCell(-0.03, 2.0),
    ScenarioCell(-0.05, 3.0),
)

# Default price moves: 8 points, -5% to +5%, symmetric.
_DEFAULT_PRICE_MOVES: tuple[float, ...] = (
    -0.05, -0.035, -0.02, -0.005,
    +0.005, +0.02, +0.035, +0.05,
)

# Default vol multipliers spanning 0.5× to 3.0×.
_DEFAULT_VOL_MULTS: tuple[float, ...] = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)


# ---------------------------------------------------------------------------
# StressGrid
# ---------------------------------------------------------------------------


class StressGrid:
    """Full-revaluation scenario grid for a portfolio.

    Parameters
    ----------
    price_moves : tuple of relative price moves (e.g. -0.05 = −5%).
                  Default: 8 symmetric points from −5% to +5%.
    vol_mults   : tuple of vol multipliers (e.g. 2.0 = double implied vol).
                  Default: (0.5, 0.75, 1.0, 1.5, 2.0, 3.0).
    overnight_cells : extra ScenarioCell entries appended to the grid.
                  Default: DEFAULT_OVERNIGHT_CELLS = ((-3%, 2×), (-5%, 3×)).

    The full grid is the Cartesian product of price_moves × vol_mults,
    plus all overnight_cells.  Duplicate cells (same price_move + vol_mult)
    are deduplicated; overnight cells take priority.
    """

    def __init__(
        self,
        price_moves: tuple[float, ...] = _DEFAULT_PRICE_MOVES,
        vol_mults: tuple[float, ...] = _DEFAULT_VOL_MULTS,
        overnight_cells: tuple[ScenarioCell, ...] = DEFAULT_OVERNIGHT_CELLS,
    ) -> None:
        self.price_moves = price_moves
        self.vol_mults = vol_mults
        self.overnight_cells = overnight_cells

    def cells(self) -> list[ScenarioCell]:
        """Return the full list of ScenarioCells (grid + overnight extras)."""
        seen: dict[tuple[float, float], ScenarioCell] = {}

        # Cartesian product first
        for pm in self.price_moves:
            for vm in self.vol_mults:
                key = (pm, vm)
                seen[key] = ScenarioCell(pm, vm, 1.0)

        # Overnight cells override (same key → overnight takes priority)
        for cell in self.overnight_cells:
            key = (cell.price_move, cell.vol_mult)
            seen[key] = cell

        return list(seen.values())


# ---------------------------------------------------------------------------
# Single-position revalue
# ---------------------------------------------------------------------------


def _option_value(pos: Position, F: float, r: float = 0.0) -> float:
    """Price a single option position at futures price F.

    Parameters
    ----------
    pos : Position with kind='option'.
    F   : (shocked) futures price.
    r   : risk-free rate; use 0.0 for risk-free discounting in stress grids
          unless the caller supplies a curve.  Black-76 price is insensitive
          to r in the short term for low-r environments.

    Returns
    -------
    Option value per contract (before qty/multiplier scaling).
    """
    assert pos.K is not None
    assert pos.sigma is not None
    assert pos.is_call is not None

    sigma = max(pos.sigma, _SIGMA_FLOOR)

    if pos.T <= 0.0:
        # Intrinsic under shocked F
        sign = 1.0 if pos.is_call else -1.0
        return max(sign * (F - pos.K), 0.0)

    if pos.style == 'A':
        return lr_tree_price(
            F, pos.K, pos.T, sigma, r, pos.is_call,
            steps=_AMERICAN_STEPS, american=True,
        )
    else:
        return float(b76_price(F, pos.K, pos.T, sigma, r, pos.is_call))


def revalue(pos: Position, F0: float, scenario: ScenarioCell, r: float = 0.0) -> float:
    """Compute PnL for one position under one stress scenario.

    Parameters
    ----------
    pos      : the position.
    F0       : current (pre-shock) futures price.
    scenario : ScenarioCell defining (price_move, vol_mult, weight).
    r        : risk-free rate (default 0.0).

    Returns
    -------
    PnL = (value_shocked - value_current) * qty * multiplier.

    Notes
    -----
    - Time is held constant (instantaneous shock; T is not decremented).
    - Vol is shocked by multiplying pos.sigma by scenario.vol_mult, then
      flooring at _SIGMA_FLOOR = 1e-9.
    - Futures positions are vol-invariant; the vol_mult is ignored for them.
    - The scenario weight is NOT applied here; it is the caller's responsibility
      to apply weights when aggregating (used by margin_approx).
    """
    F_shocked = F0 * (1.0 + scenario.price_move)

    if pos.is_future:
        # Linear P&L; vol is irrelevant for futures
        return pos.qty * pos.multiplier * (F_shocked - F0)

    # Option: apply vol shock
    assert pos.sigma is not None
    sigma_shocked = max(pos.sigma * scenario.vol_mult, _SIGMA_FLOOR)

    # Temporarily build a shocked position with mutated sigma.
    # Position is frozen; create a new one.
    shocked_pos = Position(
        kind=pos.kind,
        qty=pos.qty,
        multiplier=pos.multiplier,
        T=pos.T,
        style=pos.style,
        is_call=pos.is_call,
        K=pos.K,
        sigma=sigma_shocked,
    )

    v0 = _option_value(pos, F0, r)
    v1 = _option_value(shocked_pos, F_shocked, r)

    return pos.qty * pos.multiplier * (v1 - v0)


# ---------------------------------------------------------------------------
# Portfolio grid functions
# ---------------------------------------------------------------------------


def grid_pnl(
    positions: list[Position],
    F0: float,
    r: float = 0.0,
    grid: StressGrid | None = None,
) -> dict[ScenarioCell, float]:
    """Compute portfolio PnL for every cell in the stress grid.

    Parameters
    ----------
    positions : list of Position.
    F0        : current futures price.
    r         : risk-free rate (default 0.0).
    grid      : StressGrid instance.  If None, uses default grid.

    Returns
    -------
    dict mapping ScenarioCell -> total portfolio PnL.

    Notes
    -----
    Additivity: portfolio PnL = sum of individual position PnLs.
    This holds by construction since revalue is linear in qty.
    """
    if grid is None:
        grid = StressGrid()

    cells = grid.cells()
    result: dict[ScenarioCell, float] = {}

    for cell in cells:
        total = 0.0
        for pos in positions:
            total += revalue(pos, F0, cell, r)
        result[cell] = total

    return result


def worst_cell(
    positions: list[Position],
    F0: float,
    r: float = 0.0,
    grid: StressGrid | None = None,
) -> tuple[ScenarioCell, float]:
    """Find the stress scenario that produces the worst (most negative) PnL.

    Parameters
    ----------
    positions : list of Position.
    F0        : current futures price.
    r         : risk-free rate (default 0.0).
    grid      : StressGrid instance.  If None, uses default grid.

    Returns
    -------
    (worst_scenario, worst_pnl) where worst_pnl is the minimum portfolio PnL
    over all cells.  In the case of a profit-only portfolio, worst_pnl >= 0.
    """
    pnl_map = grid_pnl(positions, F0, r, grid)
    worst_sc = min(pnl_map, key=lambda sc: pnl_map[sc])
    return worst_sc, pnl_map[worst_sc]
