"""Event-variance jump overlay for eSSVI implied-vol surfaces.

Implements the additive event-variance decomposition protocol described in
arXiv 2606.12872 ("implied event variance extraction").  The model:

    w_total(T) = w_diffusive(T) + sum{ v_e : t_e <= T }

where v_e is an event variance lump (total-variance units) for a scheduled
event e at time t_e, and w_diffusive grows with business-time (vol_clock.py).

Composes with essvi.EssviSlice.var_lump: set each slice's var_lump to the
sum of applicable event lumps before calibrating.

References
----------
arXiv 2606.12872. "Implied event variance extraction." (protocol reference)
Gatheral, J. & Jacquier, A. (2014). "Arbitrage-free SVI volatility surfaces."
  Quantitative Finance, 14(1), 59-71.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from options_pricing.src.essvi import EssviSlice, EssviSurface

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DAYS_PER_YEAR = 365.25
_SECS_PER_YEAR = _DAYS_PER_YEAR * 86400.0

# ATM straddle approximation coefficient  1 / sqrt(2*pi) = 0.398942...
_INV_SQRT2PI = 1.0 / math.sqrt(2.0 * math.pi)

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# EventLump dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventLump:
    """A scheduled-event variance lump in total-variance units.

    Parameters
    ----------
    event_utc : tz-aware UTC datetime of the event (e.g. earnings, FOMC).
                Must be timezone-aware.
    var_lump  : event variance contribution in total-variance units (>= 0).
                Zero is allowed (no-op lump); negative raises ValueError.
    label     : human-readable identifier (e.g. "FOMC-2026-06", "ES-earnings").

    Raises
    ------
    ValueError : if event_utc is naive or var_lump < 0.
    """

    event_utc: datetime
    var_lump: float
    label: str

    def __post_init__(self) -> None:
        if self.event_utc.tzinfo is None or self.event_utc.utcoffset() is None:
            raise ValueError(
                f"event_utc must be tz-aware; got naive datetime {self.event_utc!r}"
            )
        if self.var_lump < 0.0:
            raise ValueError(f"var_lump must be >= 0; got {self.var_lump}")


# ---------------------------------------------------------------------------
# implied_event_variance
# ---------------------------------------------------------------------------


def implied_event_variance(
    w_short: float,
    t_short: float,
    w_long: float,
    t_long: float,
    event_time: float,
    *,
    clock=None,
) -> float:
    """Extract an event-variance lump from two ATM total variances straddling an event.

    Model
    -----
    Forward total variance over (t_short, t_long]:

        delta_w = w_long - w_short

    Under the assumption that diffusive vol is locally flat across the two
    expiries (sigma_d constant), the diffusive contribution to delta_w is
    proportional to the business-time increment:

        w_diffusive_fwd = sigma_d^2 * (btime(t_long) - btime(t_short))

    The diffusive rate sigma_d^2 is estimated from the pre-event slice:

        sigma_d^2 = w_short / btime(t_short)

    where btime(t) is business time from valuation to t.  Without a clock,
    btime(t) = t (calendar year-fractions).

    Event variance:

        v_e = delta_w - sigma_d^2 * (btime(t_long) - btime(t_short))

    Estimator assumptions
    ---------------------
    - Diffusive vol is locally flat across the two expiries; any term-structure
      slope of diffusive vol will bias the extraction.
    - The pre-event slice is a clean ATM total variance with no prior event lumps;
      if w_short already contains event lumps they must be stripped before calling.
    - The clock (vol_clock.business_time_years) must be built with the same
      valuation anchor as the t_short / t_long year-fractions.

    Sign contract
    -------------
    Negative result means the diffusive-flat assumption failed (e.g. term
    structure is steep, or the event already decayed out).  The negative value
    is returned unclamped so the caller can decide.

    Parameters
    ----------
    w_short     : ATM total variance at the shorter expiry (>= 0).
    t_short     : year-fraction to shorter expiry (> 0).
    w_long      : ATM total variance at the longer expiry (>= 0).
    t_long      : year-fraction to longer expiry (> 0).
    event_time  : year-fraction of the event; must satisfy t_short < event_time <= t_long.
    clock       : optional callable (t_short_yf, t_long_yf) -> (btime_short, btime_long)
                  returning business-time year-fractions.  If None, calendar time is
                  used (btime(t) = t).

    Returns
    -------
    v_e : float — event variance lump.  May be negative (see sign contract).

    Raises
    ------
    ValueError : if event_time not in (t_short, t_long], or w_long < 0, w_short < 0,
                 or t_short <= 0, t_long <= 0, or t_long <= t_short.
    """
    if t_short <= 0.0:
        raise ValueError(f"t_short must be > 0; got {t_short}")
    if t_long <= 0.0:
        raise ValueError(f"t_long must be > 0; got {t_long}")
    if t_long <= t_short:
        raise ValueError(
            f"t_long ({t_long}) must be > t_short ({t_short})"
        )
    if w_short < 0.0:
        raise ValueError(f"w_short must be >= 0; got {w_short}")
    if w_long < 0.0:
        raise ValueError(f"w_long must be >= 0; got {w_long}")
    if not (t_short < event_time <= t_long):
        raise ValueError(
            f"event_time must satisfy t_short < event_time <= t_long; "
            f"got t_short={t_short}, event_time={event_time}, t_long={t_long}"
        )

    if clock is not None:
        btime_short, btime_long = clock(t_short, t_long)
    else:
        btime_short, btime_long = t_short, t_long

    if btime_short <= 0.0:
        raise ValueError(
            f"btime_short must be > 0 for diffusive-rate estimation; got {btime_short}"
        )

    delta_w = w_long - w_short
    sigma_d_sq = w_short / btime_short
    diffusive_fwd = sigma_d_sq * (btime_long - btime_short)
    return delta_w - diffusive_fwd


# ---------------------------------------------------------------------------
# forward_vol
# ---------------------------------------------------------------------------


def forward_vol(
    w_short: float,
    t_short: float,
    w_long: float,
    t_long: float,
) -> float:
    """Standard forward implied volatility between two expiries.

    Definition
    ----------
        fwd_vol = sqrt((w_long - w_short) / (t_long - t_short))

    Uses calendar year-fractions as the time measure (not business time).

    Parameters
    ----------
    w_short : ATM total variance at shorter expiry.
    t_short : year-fraction to shorter expiry (> 0).
    w_long  : ATM total variance at longer expiry.
    t_long  : year-fraction to longer expiry (> t_short).

    Returns
    -------
    Forward implied volatility (>= 0).

    Raises
    ------
    ValueError : if w_long < w_short (calendar arbitrage violation) or
                 if t_long <= t_short, t_short <= 0, w_short < 0, w_long < 0.
    """
    if t_short <= 0.0:
        raise ValueError(f"t_short must be > 0; got {t_short}")
    if t_long <= t_short:
        raise ValueError(
            f"t_long ({t_long}) must be > t_short ({t_short})"
        )
    if w_short < 0.0:
        raise ValueError(f"w_short must be >= 0; got {w_short}")
    if w_long < 0.0:
        raise ValueError(f"w_long must be >= 0; got {w_long}")
    if w_long < w_short:
        raise ValueError(
            f"Calendar arbitrage: w_long ({w_long}) < w_short ({w_short}); "
            f"forward variance would be negative."
        )
    fwd_var = (w_long - w_short) / (t_long - t_short)
    return math.sqrt(fwd_var)


# ---------------------------------------------------------------------------
# apply_overlay
# ---------------------------------------------------------------------------


def apply_overlay(
    surface: EssviSurface,
    lumps: Sequence[EventLump],
    *,
    valuation_utc: datetime | None = None,
) -> EssviSurface:
    """Return a new EssviSurface with per-slice var_lump set from event lumps.

    For each slice, var_lump = sum of lump.var_lump for all lumps where
    lump.event_utc <= slice_expiry_utc.

    Slice expiry as UTC timestamp
    ------------------------------
    EssviSlice stores only year-fraction t (no absolute timestamp).  Two modes:

    1. If valuation_utc is provided: slice_expiry_utc = valuation_utc + t years.
       Year-fraction is converted via ACT/365.25.
    2. If valuation_utc is None: lumps are matched by comparing lump.event_utc
       converted to a year-fraction offset from the Unix epoch against slice t.
       This mode is NOT recommended for production; provide valuation_utc.

    In both cases event_utc <= slice_expiry_utc uses UTC comparison.

    Input surface is NOT mutated — a new EssviSurface is always constructed.
    EssviSlice is a frozen dataclass so rebuilding is required regardless.

    Parameters
    ----------
    surface       : input EssviSurface (read-only).
    lumps         : sequence of EventLump objects.
    valuation_utc : tz-aware UTC datetime anchor.  If None, year-fraction
                    matching against Unix epoch is used (see above).

    Returns
    -------
    New EssviSurface with updated var_lump per slice.

    Raises
    ------
    ValueError : if valuation_utc is naive (has no tzinfo).
    """
    if valuation_utc is not None:
        if valuation_utc.tzinfo is None or valuation_utc.utcoffset() is None:
            raise ValueError(
                f"valuation_utc must be tz-aware; got naive datetime {valuation_utc!r}"
            )

    new_slices: list[EssviSlice] = []
    for sl in surface.slices:
        if valuation_utc is not None:
            # Convert slice t (year-fraction) to absolute UTC datetime
            slice_expiry_utc = valuation_utc + _yf_to_timedelta(sl.t)
            total_lump = sum(
                lump.var_lump
                for lump in lumps
                if lump.event_utc <= slice_expiry_utc
            )
        else:
            # Fallback: compare lump year-fraction from Unix epoch to slice t.
            # Not recommended for production; document as approximate.
            _EPOCH = datetime(1970, 1, 1, tzinfo=_UTC)
            total_lump = sum(
                lump.var_lump
                for lump in lumps
                if (lump.event_utc - _EPOCH).total_seconds() / _SECS_PER_YEAR <= sl.t
            )
        new_slices.append(
            EssviSlice(
                t=sl.t,
                theta=sl.theta,
                rho=sl.rho,
                phi=sl.phi,
                F=sl.F,
                var_lump=total_lump,
            )
        )
    return EssviSurface(new_slices)


def _yf_to_timedelta(yf: float):
    """Convert year-fraction (ACT/365.25) to datetime.timedelta."""
    import datetime as _dt
    total_seconds = yf * _SECS_PER_YEAR
    return _dt.timedelta(seconds=total_seconds)


# ---------------------------------------------------------------------------
# atm_event_premium_ticks
# ---------------------------------------------------------------------------


def atm_event_premium_ticks(
    F: float,
    w_base: float,
    v_e: float,
    tick_size: float,
    df: float = 1.0,
) -> float:
    """ATM straddle price uplift from an event variance lump, in ticks.

    Uses the Black-76 ATM straddle approximation:

        straddle ≈ 2 * (1/sqrt(2*pi)) * F * sqrt(w) * df
                 = 2 * 0.398942... * F * sqrt(w) * df

    where w is the ATM total variance and df is the discount factor.

    The derivation: ATM straddle = call + put with K=F.  At K=F, d1 = sqrt(w)/2
    and d2 = -sqrt(w)/2.  For small w, N(d1) ≈ 0.5 + phi(0)*d1 and
    N(-d2) ≈ 0.5 + phi(0)*d1.  Straddle = df * F * (N(d1) - N(-d1)) * 2
    ≈ 2 * df * F * phi(0) * sqrt(w) where phi(0) = 1/sqrt(2*pi).

    This approximation is accurate for small-to-moderate w (w < 1.0); for
    large total variance the exact Black-76 formula should be used instead.

    Uplift = straddle(w_base + v_e) - straddle(w_base).

    Parameters
    ----------
    F         : futures/forward price (> 0).
    w_base    : baseline ATM total variance (>= 0).
    v_e       : event variance lump (may be negative; uplift will be negative
                if v_e < 0, which propagates from an unclamped extraction).
    tick_size : futures tick size in price units (> 0).
    df        : discount factor (default 1.0, i.e. undiscounted).

    Returns
    -------
    Straddle price uplift in ticks (float; may be negative).

    Raises
    ------
    ValueError : if F <= 0, tick_size <= 0, w_base < 0, or df <= 0.
    """
    if F <= 0.0:
        raise ValueError(f"F must be > 0; got {F}")
    if tick_size <= 0.0:
        raise ValueError(f"tick_size must be > 0; got {tick_size}")
    if w_base < 0.0:
        raise ValueError(f"w_base must be >= 0; got {w_base}")
    if df <= 0.0:
        raise ValueError(f"df must be > 0; got {df}")

    def _straddle(w: float) -> float:
        if w < 0.0:
            raise ValueError(
                f"w_base + v_e = {w:.6g} < 0; caller must ensure w_base >= |v_e| "
                "when v_e is negative"
            )
        return 2.0 * _INV_SQRT2PI * F * math.sqrt(w) * df

    uplift_price = _straddle(w_base + v_e) - _straddle(w_base)
    return uplift_price / tick_size
