"""Fixing-VWAP replication engine — WS-3.4.

ZERO-ALPHA hedge utility.  Schedules futures child orders to replicate the
CME 15:00 CT 30-second fixing VWAP (14:59:30–15:00:00 CT) for a target
quantity, so that expiring European options positions can be delta-flattened
at approximately the settlement price.

Rule 575 constraint (passive intent declaration)
------------------------------------------------
This module produces a *schedule* and *tracking math only* — no order
transport.  All child orders implied by a ReplicationSchedule carry intent
FIXING_REPLICATION_PASSIVE.  The transport layer (caller) is responsible for
order aggressiveness.  This module does NOT encode order aggressiveness; v0
slices are sized for benign participation and are intended as passive or
liquidity-taking in proportion to expected volume.

Operators MUST NOT use this schedule to trade in a way intended to move or
manipulate the fixing price.  CME Rule 575 prohibits transactions that are
part of, or are intended to create, an artificial price for futures or options
on futures.  Using the VWAP replicator to systematically lean on the fixing
window would constitute manipulation.  The passive replicator is designed to
participate proportionally with the market, not to influence it.

Volume profile (v0)
-------------------
The v0 expected-volume profile is UNIFORM across slices.  This is a
documented placeholder; calibrated profiles from fixing_window_study measure
output (WS-3.4 phase 2) will replace it.  Uniform allocation is equivalent to
spreading target_qty evenly and rounding, which is the least-informed prior.

Dependencies
------------
stdlib + numpy only (no scipy per lane policy).
zoneinfo for DST-correct window bounds; falls back to backports.zoneinfo on
Python < 3.9.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

from options_data.src.expiry_calendar import fixing_datetime_utc


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FixingWindow:
    """UTC start/end of the 15:00 CT 30-second fixing window for one expiry.

    The window is [start_utc, end_utc):
        start_utc = fixing_datetime_utc(expiry_date) - 30 seconds
        end_utc   = fixing_datetime_utc(expiry_date)         (15:00 CT)

    DST-correctness is delegated entirely to
    options_data.src.expiry_calendar.fixing_datetime_utc, which uses zoneinfo
    America/Chicago.  This class adds no offset arithmetic.

    Parameters
    ----------
    expiry_date : datetime.date
        Calendar date on which the option expires and settles.
    start_utc : datetime (tz-aware UTC)
        14:59:30 CT expressed in UTC.
    end_utc : datetime (tz-aware UTC)
        15:00:00 CT expressed in UTC (the fixing moment).
    """

    expiry_date: object  # datetime.date — typed as object to avoid circular import hassle
    start_utc: datetime
    end_utc: datetime

    @classmethod
    def from_expiry(cls, expiry_date: object) -> "FixingWindow":
        """Build a FixingWindow from an expiry date.

        Parameters
        ----------
        expiry_date : datetime.date
            The options expiry date.

        Returns
        -------
        FixingWindow
            start_utc = 14:59:30 CT in UTC (DST-correct)
            end_utc   = 15:00:00 CT in UTC (DST-correct)
        """
        end_utc = fixing_datetime_utc(expiry_date)  # 15:00 CT in UTC
        start_utc = end_utc - timedelta(seconds=30)
        return cls(expiry_date=expiry_date, start_utc=start_utc, end_utc=end_utc)

    def duration_seconds(self) -> float:
        """Return window duration in seconds (always 30.0)."""
        return (self.end_utc - self.start_utc).total_seconds()


@dataclass(frozen=True)
class Slice:
    """One time-bucket in the replication schedule.

    Parameters
    ----------
    bucket_start_utc : datetime (tz-aware UTC)
        Inclusive start of this time bucket.
    bucket_end_utc : datetime (tz-aware UTC)
        Exclusive end of this time bucket.
    qty : int
        Signed futures contracts to execute in this bucket.
        Positive = buy, negative = sell, zero = no action.
    """

    bucket_start_utc: datetime
    bucket_end_utc: datetime
    qty: int


@dataclass(frozen=True)
class ReplicationSchedule:
    """Time-bucketed schedule for replicating the fixing VWAP.

    Carries compliance metadata:
        intent_tag = 'FIXING_REPLICATION_PASSIVE'

    Rule 575 note: this schedule is generated with passive intent.  The
    transport layer decides per-slice order aggressiveness.  This module
    does not direct aggressive trading.  Sizes are proportional to expected
    volume (v0: uniform prior); they are not calibrated to move the price.

    Parameters
    ----------
    window : FixingWindow
        The fixing window this schedule covers.
    slices : tuple[Slice, ...]
        Ordered child buckets.  Quantities sum exactly to target_qty.
    target_qty : int
        Signed aggregate quantity (positive = buy, negative = sell).
    n_slices : int
        Number of equal-width time buckets.
    intent_tag : str
        Always 'FIXING_REPLICATION_PASSIVE'.  Immutable.

    Invariants
    ----------
    sum(s.qty for s in slices) == target_qty
    len(slices) == n_slices (or 0 when target_qty == 0)
    """

    window: FixingWindow
    slices: tuple
    target_qty: int
    n_slices: int
    intent_tag: str = field(default="FIXING_REPLICATION_PASSIVE")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_schedule(
    expiry_date: object,
    target_qty: int,
    n_slices: int = 6,
) -> ReplicationSchedule:
    """Build a ReplicationSchedule for fixing VWAP replication.

    v0 volume profile: UNIFORM — equal expected volume in each 5-second
    bucket.  Calibrated profiles (from fixing_window_study measure output)
    will replace this in a future phase.

    Child quantities are determined by largest-remainder rounding so that
    sum(slice.qty for slice in schedule.slices) == target_qty exactly.
    Negative target_qty is mirrored: quantities are negative, absolute values
    sum to abs(target_qty).

    Parameters
    ----------
    expiry_date : datetime.date
        Options expiry date.  Passed to FixingWindow.from_expiry.
    target_qty : int
        Signed total contracts to execute across the window.
        0 → empty schedule (zero slices).
    n_slices : int
        Number of equal-width time buckets.  Default 6 (× 5 s = 30 s).
        Must be >= 1.

    Returns
    -------
    ReplicationSchedule
        Slices covering [window.start_utc, window.end_utc) evenly,
        with quantities summing to target_qty.

    Raises
    ------
    ValueError
        If n_slices < 1.
    """
    if n_slices < 1:
        raise ValueError(f"n_slices must be >= 1, got {n_slices}")

    window = FixingWindow.from_expiry(expiry_date)

    if target_qty == 0:
        return ReplicationSchedule(
            window=window,
            slices=(),
            target_qty=0,
            n_slices=n_slices,
        )

    sign = 1 if target_qty > 0 else -1
    abs_qty = abs(target_qty)

    # Largest-remainder rounding over uniform base allocation.
    # Uses cumulative floor differencing to avoid floating-point accumulation.
    quantities = _largest_remainder(abs_qty, n_slices)

    # Build time buckets
    bucket_seconds = window.duration_seconds() / n_slices  # seconds per bucket
    slices_list = []
    for i in range(n_slices):
        b_start = window.start_utc + timedelta(seconds=bucket_seconds * i)
        b_end = window.start_utc + timedelta(seconds=bucket_seconds * (i + 1))
        slices_list.append(
            Slice(
                bucket_start_utc=b_start,
                bucket_end_utc=b_end,
                qty=sign * quantities[i],
            )
        )

    return ReplicationSchedule(
        window=window,
        slices=tuple(slices_list),
        target_qty=target_qty,
        n_slices=n_slices,
    )


def _largest_remainder(total: int, n: int) -> list[int]:
    """Distribute *total* into *n* integer buckets using largest-remainder rounding.

    Guarantees sum(result) == total.  Base allocation is uniform (total/n),
    fractional remainder distributed to the buckets with the largest
    sub-integer remainder, ties broken by lower index first.

    Parameters
    ----------
    total : int
        Non-negative total to distribute.
    n : int
        Number of buckets (>= 1).

    Returns
    -------
    list[int]
        Length-n list of non-negative integers summing to total.
    """
    if total == 0:
        return [0] * n

    # Exact rational allocation: i-th bucket gets total * (i+1) // n - total * i // n
    # This is the standard "integer Bresenham" / Huntington-Hill prior for uniform weight.
    result = []
    for i in range(n):
        hi = total * (i + 1) // n
        lo = total * i // n
        result.append(hi - lo)
    return result


# ---------------------------------------------------------------------------
# Tracking math
# ---------------------------------------------------------------------------

def vwap_from_trades(trades: Sequence[tuple[int, float]]) -> float:
    """Compute VWAP from a sequence of (qty, price) fills.

    Consistent with fixing_window_study._compute_vwap: sum(price * qty) /
    sum(qty), using absolute quantities.  Signed quantities are treated as
    absolute (both buys and sells contribute equally to the VWAP denominator,
    consistent with how the exchange computes the settlement VWAP).

    Parameters
    ----------
    trades : sequence of (qty, price)
        qty may be signed or unsigned; absolute value is used.
        Empty → returns float('nan').

    Returns
    -------
    float
        Volume-weighted average price, or nan if no trades.
    """
    total_qty = 0.0
    total_pv = 0.0
    for qty, price in trades:
        aq = abs(float(qty))
        total_qty += aq
        total_pv += aq * float(price)
    if total_qty == 0.0:
        return float("nan")
    return total_pv / total_qty


def replication_error(
    fills: Sequence[tuple[int, float]],
    fixing_vwap: float,
    tick_size: float = 0.25,
) -> float:
    """Compute signed replication slippage in ticks.

    Slippage = achieved_vwap - fixing_vwap, expressed in tick units.
    Positive → filled above the fixing (buy-side cost / sell-side gain).
    Negative → filled below the fixing (buy-side saving / sell-side cost).

    This is a *tracking* statistic, not a P&L.  Sign convention:
        error > 0  →  bought (on average) above the fixing VWAP
        error < 0  →  bought (on average) below the fixing VWAP

    For a sell replicator the sign is the same: error > 0 means you sold
    above the fixing (beneficial for the seller, adverse for the hedger).

    Parameters
    ----------
    fills : sequence of (qty, price)
        Executed fills; qty signed or unsigned (absolute used for weighting).
    fixing_vwap : float
        The realized 15:00 CT 30-second settlement VWAP.
    tick_size : float
        Instrument minimum price increment (default 0.25 for ES futures).

    Returns
    -------
    float
        (achieved_vwap - fixing_vwap) / tick_size.
        nan if fills is empty or fixing_vwap is nan.
    """
    if not fills:
        return float("nan")
    if math.isnan(fixing_vwap):
        return float("nan")
    achieved = vwap_from_trades(fills)
    if math.isnan(achieved):
        return float("nan")
    if tick_size == 0.0:
        raise ValueError("tick_size must be non-zero")
    return (achieved - fixing_vwap) / tick_size
