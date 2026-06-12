"""Business-time volatility clock for CME ES/NQ options.

Maps calendar time to "business variance time" via a piecewise weight table
over the CME ES session structure in America/Chicago local time.  Used by
eSSVI calibration and 0DTE theta computation (plan WS-2).

Session structure (CT = America/Chicago)
-----------------------------------------
Monday–Friday:
  08:30–15:00  Regular Trading Hours (RTH)      weight 1.0
  15:00–16:00  Post-close / ETH transition       weight ETH (default 0.30)
  16:00–17:00  Daily maintenance halt            weight 0.0
  17:00–08:30  ETH overnight (next calendar day) weight ETH (default 0.30)
Saturday–Sunday:
  Fri 16:00 CT – Sun 17:00 CT                   weight WKND (default 0.05)

References
----------
CME Group Equity Index Futures product specs (ES/NQ):
  https://www.cmegroup.com/trading/equity-index/us-index/e-mini-sandp500.html
  Trading hours: Sun–Fri 17:00–16:00 CT with 60-min maintenance 16:00–17:00 CT.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_UTC_TZ = timezone.utc

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CT = ZoneInfo("America/Chicago")
_DAYS_PER_YEAR = 365.25
_SECS_PER_YEAR = _DAYS_PER_YEAR * 86400.0

# Weekday indices (datetime.weekday(): 0=Mon, …, 6=Sun)
_MON, _TUE, _WED, _THU, _FRI, _SAT, _SUN = 0, 1, 2, 3, 4, 5, 6


# ---------------------------------------------------------------------------
# Weight configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionWeights:
    """Per-session variance weights relative to RTH.

    CALIBRATABLE PLACEHOLDERS — these defaults are not measured truth.
    Calibrate from realized variance ratios (RTH vs ETH vs weekend) using
    ES/NQ tick data.  This is deferred to a later WS-1 study.

    Attributes
    ----------
    rth : weight for Regular Trading Hours (08:30–15:00 CT Mon–Fri).
          Defined as 1.0 by convention; all other weights are relative to it.
    eth : weight for Extended Trading Hours (overnight + 15:00–16:00 close).
          Placeholder: 0.30.
    maintenance : weight for the daily maintenance halt (16:00–17:00 CT).
                  Zero — no price discovery.
    weekend : weight for Fri 16:00 CT – Sun 17:00 CT.
              Placeholder: 0.05 (weekend news risk; not exactly zero).
    """

    rth: float = 1.0
    eth: float = 0.30
    maintenance: float = 0.0
    weekend: float = 0.05


DEFAULT_WEIGHTS = SessionWeights()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_ct(dt_utc: datetime) -> datetime:
    """Convert UTC datetime to America/Chicago, handling DST via zoneinfo."""
    return dt_utc.astimezone(_CT)


def _ct_today_at(date_ct: datetime, h: int, m: int) -> datetime:
    """Return a tz-aware CT datetime for date_ct's calendar date at HH:MM CT."""
    return datetime(date_ct.year, date_ct.month, date_ct.day, h, m,
                    tzinfo=_CT)


def _overlap_seconds(a_start: datetime, a_end: datetime,
                     b_start: datetime, b_end: datetime) -> float:
    """Seconds of overlap between two half-open intervals [a,a_end) and [b,b_end).

    All arguments must be tz-aware.  Converts to UTC before comparison to
    avoid Python's known DST subtraction pitfall: when two aware datetimes
    share the same tzinfo object, datetime.__sub__ performs naive wall-clock
    subtraction and ignores DST offset changes.
    """
    a_s = a_start.astimezone(_UTC_TZ)
    a_e = a_end.astimezone(_UTC_TZ)
    b_s = b_start.astimezone(_UTC_TZ)
    b_e = b_end.astimezone(_UTC_TZ)
    lo = max(a_s, b_s)
    hi = min(a_e, b_e)
    if hi <= lo:
        return 0.0
    return (hi - lo).total_seconds()


def _weighted_seconds_for_day(
    day_ct: datetime,
    t0_utc: datetime,
    t1_utc: datetime,
    w: SessionWeights,
) -> float:
    """Return weighted seconds contributed by one calendar day (CT) in [t0,t1].

    day_ct is any datetime whose .date() gives the CT calendar day to process.
    The function enumerates every session segment that falls on this CT calendar
    date (or straddles midnight) and sums overlap × weight.

    Session ownership by calendar date
    ------------------------------------
    We assign each segment to the CT calendar date on which the segment *starts*,
    except that the overnight ETH (17:00 CT → 08:30 CT next day) is assigned to
    the date when 17:00 CT occurs (not the following date).  This avoids
    double-counting and simplifies iteration: for each date we accumulate:

      If Mon–Fri:
        [00:00, 08:30) CT  — ETH overnight from prior date's 17:00 (owned by prior date)
                             So we only claim this period for the *prior* date.
        [08:30, 15:00) CT  — RTH          weight rth
        [15:00, 16:00) CT  — ETH close    weight eth
        [16:00, 17:00) CT  — maintenance  weight maintenance
        [17:00, next 00:00) CT — ETH start  weight eth  (owned by THIS date)

      But [17:00, next 00:00) is the same as [17:00, 24:00) on the current date,
      which overlaps into the *next* calendar date's [00:00, 08:30).  We assign
      the entire overnight block (this date 17:00 → next date 08:30) to THIS date.

    Weekend block (Fri 16:00 → Sun 17:00):
      Owned by Fri's date.  Saturday contributes nothing further.

    Sunday ETH (Sun 17:00 → Mon 08:30):
      Owned by Sunday's date.  This is the opening ETH of the new trading week.

    Result: for each date, claim RTH+close+maintenance+outgoing overnight,
    or (Fri) the weekend block, or (Sun) the opening ETH overnight.
    Saturday returns 0.
    """
    wd = day_ct.weekday()

    if wd == _SAT:
        return 0.0

    if wd == _SUN:
        # Sun 17:00 CT → Mon 08:30 CT: ETH overnight owned by Sunday (initiating date).
        # The weekend block [Fri 16:00, Sun 17:00] is owned by Friday; this segment
        # starts where that block ends.
        mon_ct = day_ct + timedelta(days=1)
        eth_s = _ct_today_at(day_ct, 17, 0)
        eth_e = _ct_today_at(mon_ct, 8, 30)
        return w.eth * _overlap_seconds(eth_s, eth_e, t0_utc, t1_utc)

    total = 0.0

    # -- RTH: 08:30–15:00 CT --
    rth_s = _ct_today_at(day_ct, 8, 30)
    rth_e = _ct_today_at(day_ct, 15, 0)
    total += w.rth * _overlap_seconds(rth_s, rth_e, t0_utc, t1_utc)

    # -- Post-close ETH: 15:00–16:00 CT --
    close_s = _ct_today_at(day_ct, 15, 0)
    close_e = _ct_today_at(day_ct, 16, 0)
    total += w.eth * _overlap_seconds(close_s, close_e, t0_utc, t1_utc)

    # -- Maintenance halt: 16:00–17:00 CT --
    maint_s = _ct_today_at(day_ct, 16, 0)
    maint_e = _ct_today_at(day_ct, 17, 0)
    total += w.maintenance * _overlap_seconds(maint_s, maint_e, t0_utc, t1_utc)

    if wd == _FRI:
        # -- Weekend block: Fri 16:00 CT – Sun 17:00 CT --
        # Owned entirely by Friday's date.
        # Sun 17:00 CT (next Sunday)
        sun_ct = day_ct + timedelta(days=2)
        wknd_s = _ct_today_at(day_ct, 16, 0)
        wknd_e = _ct_today_at(sun_ct, 17, 0)
        # Weekend weight replaces the maintenance weight for 16:00–17:00 Fri
        # (already counted maintenance above; subtract it, add weekend).
        # The maintenance halt on Friday IS part of the weekend gap by convention.
        # Recalculate: remove maintenance overlap that falls within weekend,
        # and instead count it at weekend weight.
        maint_in_wknd = _overlap_seconds(maint_s, maint_e, t0_utc, t1_utc)
        total -= w.maintenance * maint_in_wknd   # undo maintenance for 16:00–17:00
        # Weekend extends from 16:00 Fri through Sun 17:00
        total += w.weekend * _overlap_seconds(wknd_s, wknd_e, t0_utc, t1_utc)
    else:
        # -- Outgoing overnight ETH: 17:00 CT (this date) → 08:30 CT (next date) --
        # Next date's 08:30 CT:
        next_day_ct = day_ct + timedelta(days=1)
        # Skip if next day is Saturday (Fri handled above; this branch covers Mon–Thu)
        # For Mon–Thu the next day is always Tue–Fri (a valid trading day).
        eth_s = _ct_today_at(day_ct, 17, 0)
        eth_e = _ct_today_at(next_day_ct, 8, 30)
        total += w.eth * _overlap_seconds(eth_s, eth_e, t0_utc, t1_utc)

    return total


def _iter_ct_dates(t0_utc: datetime, t1_utc: datetime):
    """Yield distinct CT calendar dates (as date objects) covering [t0,t1].

    Prepends owning dates for segments that start before the calendar date of t0:
    - Sat/Sun start: prepend the preceding Friday (owns the weekend block
      [Fri 16:00, Sun 17:00]).
    - Mon start before 08:30 CT: prepend the preceding Sunday (owns the ETH
      overnight [Sun 17:00, Mon 08:30]).
    """
    start_ct = _to_ct(t0_utc)
    start_date_ct = start_ct.date()
    end_ct = _to_ct(t1_utc).date()

    # Prepend owning date(s) for segments whose initiating date precedes start_date_ct
    wd = start_date_ct.weekday()
    if wd == _SAT:
        yield start_date_ct - timedelta(days=1)   # Friday owns Sat/Sun
    elif wd == _SUN:
        yield start_date_ct - timedelta(days=2)   # Friday owns weekend block
        # Sunday is yielded by the main loop below and handles Sun 17:00→Mon 08:30
    elif wd == _MON and (start_ct.hour, start_ct.minute) < (8, 30):
        # Sunday owns Sun 17:00→Mon 08:30; prepend Sunday so its ETH is captured
        yield start_date_ct - timedelta(days=1)   # Sunday

    cur = start_date_ct
    while cur <= end_ct:
        yield cur
        cur += timedelta(days=1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def calendar_time_years(t0: datetime, t1: datetime) -> float:
    """Calendar time between t0 and t1 in ACT/365.25 year units.

    Parameters
    ----------
    t0, t1 : tz-aware UTC (or any offset-aware) datetimes.

    Returns
    -------
    float >= 0.

    Raises
    ------
    ValueError : if t1 < t0 or either datetime is naive.
    """
    _check_aware(t0, "t0")
    _check_aware(t1, "t1")
    if t1 < t0:
        raise ValueError(f"t1 ({t1}) must be >= t0 ({t0})")
    # Convert to UTC before subtraction: direct (t1 - t0) on two aware datetimes
    # sharing the same tzinfo object performs naive wall-clock subtraction in
    # CPython and ignores DST offset changes (e.g. spring-forward removes 1 hour).
    return (t1.astimezone(_UTC_TZ) - t0.astimezone(_UTC_TZ)).total_seconds() / _SECS_PER_YEAR


def business_time_years(
    t0_utc: datetime,
    t1_utc: datetime,
    *,
    weights: SessionWeights = DEFAULT_WEIGHTS,
    events: tuple | list = (),
) -> float:
    """Business variance time between two UTC datetimes in ACT/365.25 year units.

    Integrates the piecewise session weight over [t0, t1], expressed in
    calendar-ACT/365.25 units.  Handles DST correctly via zoneinfo.

    Parameters
    ----------
    t0_utc : start datetime (tz-aware).
    t1_utc : end datetime (tz-aware).
    weights : SessionWeights instance (default DEFAULT_WEIGHTS).
    events  : iterable of (event_utc, variance_lump_years) pairs.
              Each lump is added iff event_utc is in (t0_utc, t1_utc].

    Returns
    -------
    float >= 0 (in years).

    Raises
    ------
    ValueError : if t1 < t0 or either datetime is naive.
    """
    _check_aware(t0_utc, "t0_utc")
    _check_aware(t1_utc, "t1_utc")
    if t1_utc < t0_utc:
        raise ValueError(f"t1_utc ({t1_utc}) must be >= t0_utc ({t0_utc})")
    if t1_utc == t0_utc:
        return 0.0

    total_secs = 0.0

    for date_ct in _iter_ct_dates(t0_utc, t1_utc):
        # Anchor datetime for this CT calendar date (arbitrary time; just need the date)
        day_anchor = datetime(date_ct.year, date_ct.month, date_ct.day, 12, 0,
                              tzinfo=_CT)
        total_secs += _weighted_seconds_for_day(day_anchor, t0_utc, t1_utc, weights)

    # Add event variance lumps for events strictly inside (t0, t1]
    event_years = 0.0
    for ev_utc, lump in events:
        _check_aware(ev_utc, "event datetime")
        if t0_utc < ev_utc <= t1_utc:
            event_years += float(lump)

    return total_secs / _SECS_PER_YEAR + event_years


def business_vol_from_calendar(
    sigma_cal: float,
    t0_utc: datetime,
    t1_utc: datetime,
    *,
    weights: SessionWeights = DEFAULT_WEIGHTS,
    events: tuple | list = (),
) -> float:
    """Convert calendar-time volatility to business-time volatility.

    Variance-preserving: sigma_cal^2 * T_cal == sigma_biz^2 * T_biz.

    Parameters
    ----------
    sigma_cal : calendar-time annualised volatility.
    t0_utc, t1_utc : period start/end (tz-aware).

    Returns
    -------
    sigma_biz >= 0.

    Notes
    -----
    Returns 0.0 when T_biz == 0 (e.g. pure maintenance window).
    """
    T_cal = calendar_time_years(t0_utc, t1_utc)
    T_biz = business_time_years(t0_utc, t1_utc, weights=weights, events=events)
    if T_biz == 0.0:
        return 0.0
    return sigma_cal * math.sqrt(T_cal / T_biz)


def calendar_vol_from_business(
    sigma_biz: float,
    t0_utc: datetime,
    t1_utc: datetime,
    *,
    weights: SessionWeights = DEFAULT_WEIGHTS,
    events: tuple | list = (),
) -> float:
    """Convert business-time volatility to calendar-time volatility.

    Variance-preserving: sigma_biz^2 * T_biz == sigma_cal^2 * T_cal.

    Parameters
    ----------
    sigma_biz : business-time annualised volatility.
    t0_utc, t1_utc : period start/end (tz-aware).

    Returns
    -------
    sigma_cal >= 0.

    Notes
    -----
    Returns 0.0 when T_cal == 0.
    """
    T_cal = calendar_time_years(t0_utc, t1_utc)
    T_biz = business_time_years(t0_utc, t1_utc, weights=weights, events=events)
    if T_cal == 0.0:
        return 0.0
    return sigma_biz * math.sqrt(T_biz / T_cal)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def _check_aware(dt: datetime, name: str) -> None:
    """Raise ValueError if dt is naive (no tzinfo)."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(
            f"{name} must be a tz-aware datetime; got naive datetime {dt!r}"
        )
