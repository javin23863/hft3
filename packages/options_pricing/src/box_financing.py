"""Box-spread implied financing curve reader.

Extracts a continuously-compounded risk-free rate term structure from
European options box-spread prices (CME settlement data or other sources).

Theory
------
A long box with strikes K1 < K2 on European options is:

    long call(K1) + short put(K1) + short call(K2) + long put(K2)
    = (C(K1) - P(K1)) - (C(K2) - P(K2))

At expiry T, payoff is exactly K2 - K1 (regardless of underlying).
Therefore the arbitrage-free (European, frictionless) price is:

    box_cost = (K2 - K1) * exp(-r * T)

Solving for r:

    r = -ln(box_cost / (K2 - K1)) / T

Negative rates are *allowed* — the formula is symmetric and European
eurozone-style negative rates can price the box above the undiscounted
spread.  Only box_cost <= 0 is mathematically invalid.

CRITICAL APPLICABILITY CONSTRAINT — EUROPEAN OPTIONS ONLY
---------------------------------------------------------
This module is ONLY valid when the underlying options are European-style
(no early-exercise right).  American-style options (e.g. CME standard
quarterly ES/NQ FOPs until a given expiry) embed an early-exercise
premium (EEP) that contaminates the box price:

    American box cost ≠ (K2 - K1) * exp(-r * T)

The deviation is approximately the call EEP minus the put EEP, which can
be several basis points for near-ATM options with T > 0.5 and r > 2%.

CME-specific calendar guidance (June 2026):
  - Weekly and End-of-Month (EOM) options on ES/NQ introduced post-
    Sept 2021 settle European-style and are VALID inputs.
  - Standard quarterly options (e.g. Mar/Jun/Sep/Dec) are American-style
    and MUST NOT be used.

Using American settlement prices here will produce biased rate estimates
and flagrant sanity-band violations.  Callers are responsible for
filtering to European expiries before calling this module.

WS-5.4 gate dependency
-----------------------
The financing_curve() output feeds the WS-5.4 no-arbitrage gate.  The
gate compares option-implied carry against box-implied r; a stale or
American-contaminated box rate will produce spurious gate signals.

References
----------
Garman, M. B. (1976). "A general theory of asset valuation under
  diffuse state distributions." Working paper 50, UC Berkeley.
Brennan, M. J. (1979). "The pricing of contingent claims in discrete
  time models." Journal of Finance, 34(1), 53-68.
"""
from __future__ import annotations

import math
from enum import Enum
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SanityLabel(str, Enum):
    """Classification of a box-implied rate vs a SOFR benchmark.

    Values
    ------
    OK        : |r - sofr_hint| <= 200bp (rate looks clean).
    SUSPECT   : |r - sofr_hint| > 200bp (likely settlement staleness or
                wide bid-ask marks).  This is a DATA-QUALITY flag, not a
                trading signal.  Do not infer an arb — the deviation almost
                certainly reflects stale or non-transactable settlement prices.
    UNCHECKED : no sofr_hint was supplied; cannot assess.
    """

    OK = "ok"
    SUSPECT = "suspect"
    UNCHECKED = "unchecked"


class FinancingPoint(NamedTuple):
    """Single point on the implied financing curve."""

    T: float   # time to expiry in years
    r: float   # continuously-compounded implied rate


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def box_price(
    K1: float,
    K2: float,
    call_K1: float,
    put_K1: float,
    call_K2: float,
    put_K2: float,
) -> float:
    """Long-box cost from European option mid-prices.

    The long box is:
        long call(K1) + short put(K1) - long call(K2) + long put(K2)
        = (C(K1) - P(K1)) - (C(K2) - P(K2))

    Parameters
    ----------
    K1 : lower strike (must be strictly less than K2).
    K2 : upper strike.
    call_K1 : call price at K1.
    put_K1  : put price at K1.
    call_K2 : call price at K2.
    put_K2  : put price at K2.

    Returns
    -------
    box_cost : float in the same units as the option prices.

    Raises
    ------
    ValueError : if K2 <= K1.

    Notes
    -----
    Inputs should be mid-prices or settlement prices.  Bid-ask spread
    widens effective box cost by roughly half the spread on all four legs;
    using settlement mids avoids this friction but introduces stale-mark
    risk (see sanity_band).
    """
    if K2 <= K1:
        raise ValueError(
            f"K2 must be strictly greater than K1; got K1={K1}, K2={K2}"
        )
    return (call_K1 - put_K1) - (call_K2 - put_K2)


def implied_financing_rate(
    box_cost: float,
    K1: float,
    K2: float,
    T: float,
) -> float:
    """Continuously-compounded risk-free rate implied by a European box price.

    Derivation:
        box_cost = (K2 - K1) * exp(-r * T)
        => r = -ln(box_cost / (K2 - K1)) / T

    Negative rates are allowed and handled correctly (they arise when the
    box trades ABOVE the undiscounted K-spread, e.g. negative SOFR regimes
    or deeply inverted curves).

    Parameters
    ----------
    box_cost : observed (or computed) long-box cost.
    K1       : lower strike.
    K2       : upper strike (K2 > K1 required for a positive K-spread).
    T        : time to expiry in years (must be > 0).

    Returns
    -------
    r : continuously-compounded rate (e.g. 0.053 means 5.3%).

    Raises
    ------
    ValueError : if box_cost <= 0 (mathematically invalid: ln is undefined;
                 a non-positive box cost implies infinite or undefined rate).
    ValueError : if K2 <= K1 (no well-defined payoff spread).
    ValueError : if T <= 0 (time must be positive).
    """
    if T <= 0.0:
        raise ValueError(f"T must be > 0; got T={T}")
    if K2 <= K1:
        raise ValueError(
            f"K2 must be > K1; got K1={K1}, K2={K2}"
        )
    if box_cost <= 0.0:
        raise ValueError(
            f"box_cost must be > 0; got box_cost={box_cost}. "
            "A non-positive box cost is mathematically invalid for rate extraction. "
            "Check option prices for data errors or American-style contamination."
        )
    spread = K2 - K1
    # r = -ln(box_cost / spread) / T
    # Note: box_cost > spread <=> r < 0 (negative rate); this is valid.
    return -math.log(box_cost / spread) / T


def financing_curve(
    boxes: list[tuple[float, float, float, float]],
) -> list[FinancingPoint]:
    """Build a sorted implied financing curve from multiple box observations.

    Parameters
    ----------
    boxes : list of (K1, K2, T, box_cost) tuples.  Each tuple must satisfy
            K2 > K1, box_cost > 0, T > 0.

    Returns
    -------
    Sorted list of FinancingPoint(T, r), ascending by T.
    Duplicate T values (same expiry, different strikes) are averaged; the
    average is a simple mean of the rates — not a weighted average.  When
    multiple box widths at the same expiry are available, the rates should
    be nearly identical for European options; averaging suppresses small
    discrepancies from bid-ask or rounding.

    Raises
    ------
    Propagates ValueError from implied_financing_rate for each invalid box.

    Notes
    -----
    Duplicate-T averaging policy: simple mean, because all boxes at the same
    expiry theoretically imply the same rate (European, no transaction costs).
    Deviations of more than a few bp across box widths at one expiry indicate
    data quality issues; consider calling sanity_band on each before averaging.
    """
    # Accumulate rates by T using a dict of lists
    by_t: dict[float, list[float]] = {}
    for K1, K2, T, bc in boxes:
        r = implied_financing_rate(bc, K1, K2, T)
        if T not in by_t:
            by_t[T] = []
        by_t[T].append(r)

    # Average duplicates, sort by T
    result = [
        FinancingPoint(T=t, r=sum(rs) / len(rs))
        for t, rs in sorted(by_t.items())
    ]
    return result


# ---------------------------------------------------------------------------
# Sanity classification
# ---------------------------------------------------------------------------

# 200 basis-points deviation threshold (plan: box rate ≈ SOFR + 0-10bp;
# wide deviation = data problem, NOT an arb opportunity).
_SUSPECT_THRESHOLD_BP = 200
_SUSPECT_THRESHOLD = _SUSPECT_THRESHOLD_BP * 1e-4   # 0.0200


def sanity_band(
    r: float,
    *,
    sofr_hint: float | None = None,
) -> SanityLabel:
    """Classify a box-implied rate relative to a SOFR benchmark.

    This is a DEFENSIVE DATA-QUALITY tool, not a trading signal.  The
    plan specifies that box-implied r should be approximately SOFR + 0-10bp
    for clean European settlement data.  A deviation exceeding 200bp
    almost certainly reflects stale settlement prices or wide bid-ask marks
    on low-liquidity strikes, not an arb opportunity.

    Parameters
    ----------
    r          : box-implied continuously-compounded rate.
    sofr_hint  : overnight SOFR rate (e.g. 0.0530 for 5.30%).  If None,
                 cannot assess — returns UNCHECKED.

    Returns
    -------
    SanityLabel.OK        : |r - sofr_hint| <= 200bp.
    SanityLabel.SUSPECT   : |r - sofr_hint| > 200bp.
    SanityLabel.UNCHECKED : sofr_hint is None.

    Notes
    -----
    The 200bp threshold is deliberately wide to avoid false positives from
    genuine term-structure slope (long-dated boxes may embed forward rate
    expectations).  Callers running a cross-expiry consistency check should
    use a tighter threshold appropriate for adjacent expiries.
    """
    if sofr_hint is None:
        return SanityLabel.UNCHECKED
    if abs(r - sofr_hint) > _SUSPECT_THRESHOLD:
        return SanityLabel.SUSPECT
    return SanityLabel.OK
