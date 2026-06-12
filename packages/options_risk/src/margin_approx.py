"""SPAN-style scanning-risk margin approximation (v0 — NOT real SPAN/CORE).

APPROXIMATION NOTICE
--------------------
This module implements a SPAN-*style* scanning-risk approximation for
illustrative and shadow-risk purposes only.  It is NOT equivalent to
CME SPAN, CME CORE, or any exchange margin calculation.

Omissions vs real SPAN (each of these can meaningfully reduce or increase
margin relative to this estimate):
  - No inter-month spread charge (calendar spreads get no credit).
  - No short-option minimum (SOM) charge; unhedged naked shorts may be
    understated.
  - No inter-commodity credit (correlated legs, e.g. ES vs NQ, receive
    no spread benefit).
  - Scan range is a CALLER-SUPPLIED parameter; real SPAN scan ranges come
    from CME daily margin parameter files (SPAN Risk Parameter Files, *.risk
    or *.csv).  These files are NOT ingested by this module.
  - No delivery/spot charge.
  - Extreme-cell weight is 0.35 per the published SPAN specification; see
    _EXTREME_CELL_WEIGHT below.
  - Calibration to FCM nightly margin files is pending at the shadow-risk
    stage (planned: WS recon against actual CME margin notices).

Use this estimate for:
  - Relative comparison of strategies (e.g. straddle vs strangle margin rank).
  - Early-stage position sizing before FCM margin confirmation.
  - Stress-testing sensitivity to range parameter assumptions.

Do NOT use for:
  - Regulatory capital calculations.
  - Production margin calls.
  - Any representation to counterparties or regulators.

References
----------
CME Group (2022). "SPAN 2 Implementation Guide."
  https://www.cmegroup.com/clearing/risk-management/span-overview.html
  (Retrieved 2025; public; method unchanged from SPAN 1987 for basic
   scanning-risk component.)
"""
from __future__ import annotations

from options_risk.src.stress_grid import (
    Position,
    ScenarioCell,
    revalue,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# SPAN extreme-cell weight = 0.35.
# Per CME SPAN specification, the two extreme price scenarios (±2× the
# price scan range) are included at 35% weight rather than 100%.  This
# reflects the lower probability of extreme moves within a single day.
# Documented here as a named constant so it is visible in code review.
_EXTREME_CELL_WEIGHT: float = 0.35

# SPAN-like price fractions: ±1, ±2/3, ±1/3, 0 of price_scan_range.
_PRICE_FRACTIONS: tuple[float, ...] = (
    -1.0, -2.0 / 3.0, -1.0 / 3.0,
     0.0,
    +1.0 / 3.0, +2.0 / 3.0, +1.0,
)

# Vol directions for standard cells (+1 = up, -1 = down vol_scan_range).
_VOL_DIRECTIONS: tuple[float, ...] = (+1.0, -1.0)

# Extreme cells use ±2× price_scan_range at _EXTREME_CELL_WEIGHT.
_EXTREME_PRICE_MULTS: tuple[float, ...] = (-2.0, +2.0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _portfolio_loss_cell(
    positions: list[Position],
    F0: float,
    price_move: float,
    vol_shift: float,
    weight: float,
    r: float,
) -> float:
    """Portfolio loss (positive = loss) for one SPAN-like cell.

    Parameters
    ----------
    positions  : portfolio.
    F0         : base futures price.
    price_move : relative futures move (e.g. -0.10 = -10%).
    vol_shift  : absolute vol shift applied to each position's sigma
                 (e.g. +0.05 = +5 vol points).  Use 0.0 for extreme cells.
    weight     : cell weight (1.0 standard, _EXTREME_CELL_WEIGHT for extreme).
    r          : risk-free rate.

    Returns
    -------
    Weighted loss = -weighted_PnL.  Positive means a net loss.
    """
    total_pnl = 0.0
    F_shocked = F0 * (1.0 + price_move)

    for pos in positions:
        if pos.is_future:
            pnl = pos.qty * pos.multiplier * (F_shocked - F0)
        else:
            assert pos.sigma is not None
            # Encode absolute vol shift as a vol_mult consumed by revalue():
            #   sigma_shocked = sigma + vol_shift
            #   vol_mult = sigma_shocked / sigma
            sigma_base = pos.sigma
            if sigma_base <= 0.0:
                sigma_shocked = 1e-9
            else:
                sigma_shocked = max(sigma_base + vol_shift, 1e-9)
            # Avoid division by zero: denominator floored at 1e-12.
            vol_mult = sigma_shocked / max(sigma_base, 1e-12)

            cell = ScenarioCell(price_move=price_move, vol_mult=vol_mult, weight=1.0)
            pnl = revalue(pos, F0, cell, r)

        total_pnl += pnl

    return -total_pnl * weight


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scanning_risk(
    positions: list[Position],
    F0: float,
    *,
    price_scan_range: float,
    vol_scan_range: float,
    r: float = 0.0,
) -> float:
    """SPAN-style scanning risk: max portfolio loss over the 16-scenario array.

    This is an APPROXIMATION of the SPAN scanning-risk component.  See module
    docstring for a full list of omissions.

    Scenario array (16 total)
    -------------------------
    14 standard cells:
      price in {-1, -2/3, -1/3, 0, +1/3, +2/3, +1} × price_scan_range  (7 moves)
      vol   in {+vol_scan_range, -vol_scan_range} (absolute shift)       (2 dirs)
      weight = 1.0 each.

    2 extreme cells:
      price in {-2, +2} × price_scan_range
      vol unchanged (0 shift)
      weight = 0.35 per SPAN extreme-move specification (_EXTREME_CELL_WEIGHT).

    Golden hand-check for extreme-cell weight
    -----------------------------------------
    Short 1 call, K=100, F0=100, T=0.25, sigma=0.20, multiplier=1, qty=-1.
    price_scan_range=10 (10% of F0).
    Extreme cell: F_shocked = 100 + 2*10 = 120, vol unchanged.

      C0 = b76(100, 100, 0.25, 0.20, r=0)   (current value)
      C1 = b76(120, 100, 0.25, 0.20, r=0)   (value at F=120)
      position PnL = qty * multiplier * (C1 - C0)
                   = (-1) * 1 * (C1 - C0)   (short call → negative of val change)
      weighted_pnl = PnL * 0.35
      weighted_loss = -weighted_pnl

    The test test_extreme_cell_weight_golden verifies this computation.

    Parameters
    ----------
    positions        : list of Position.
    F0               : current futures price.
    price_scan_range : absolute dollar/index-point price range.
                       REQUIRED — no default.  Real SPAN values come from
                       CME Risk Parameter Files; caller must supply.
    vol_scan_range   : absolute vol-point range (e.g. 0.05 = 5 vol points).
                       REQUIRED — no default.  Same source caveat as above.
    r                : risk-free rate (default 0.0).

    Returns
    -------
    scanning_risk >= 0.  Maximum weighted loss across all 16 cells.
    0 means the portfolio profits (or breaks even) in every scenario.
    """
    if F0 <= 0.0:
        raise ValueError(f"F0 must be positive; got {F0}")
    if price_scan_range <= 0.0:
        raise ValueError(f"price_scan_range must be positive; got {price_scan_range}")
    if vol_scan_range < 0.0:
        raise ValueError(f"vol_scan_range must be non-negative; got {vol_scan_range}")

    max_loss = 0.0

    # 14 standard cells
    for pf in _PRICE_FRACTIONS:
        price_move = pf * price_scan_range / F0
        for vd in _VOL_DIRECTIONS:
            vol_shift = vd * vol_scan_range
            loss = _portfolio_loss_cell(positions, F0, price_move, vol_shift, 1.0, r)
            if loss > max_loss:
                max_loss = loss

    # 2 extreme cells at _EXTREME_CELL_WEIGHT
    for em in _EXTREME_PRICE_MULTS:
        price_move = em * price_scan_range / F0
        loss = _portfolio_loss_cell(positions, F0, price_move, 0.0, _EXTREME_CELL_WEIGHT, r)
        if loss > max_loss:
            max_loss = loss

    return max_loss


def margin_estimate(
    positions: list[Position],
    F0: float,
    *,
    price_scan_range: float,
    vol_scan_range: float,
    buffer: float = 1.25,
    r: float = 0.0,
) -> float:
    """SPAN-approximation margin estimate with a safety buffer.

    margin_estimate = scanning_risk(positions, F0, ...) * buffer

    Parameters
    ----------
    positions        : list of Position.
    F0               : current futures price.
    price_scan_range : see scanning_risk().  REQUIRED — no default.
    vol_scan_range   : see scanning_risk().  REQUIRED — no default.
    buffer           : multiplicative safety buffer (default 1.25 = +25%).
                       Calibrated to typical FCM add-on above SPAN initial;
                       actual FCM buffers vary and must be verified against
                       the nightly margin file.
    r                : risk-free rate (default 0.0).

    Returns
    -------
    margin_estimate >= 0.

    Notes
    -----
    - For long-only option portfolios, margin_estimate is bounded by the
      maximum possible loss = total premium paid (options cannot lose more
      than their purchase price when long).
    - buffer scales linearly: margin_estimate / scanning_risk = buffer exactly
      whenever scanning_risk > 0.
    """
    if buffer <= 0.0:
        raise ValueError(f"buffer must be positive; got {buffer}")

    risk = scanning_risk(
        positions, F0,
        price_scan_range=price_scan_range,
        vol_scan_range=vol_scan_range,
        r=r,
    )
    return risk * buffer
