"""Tests for vol_clock — business-time volatility clock.

Golden values
-------------
All hand-computed in comments next to each test.  Time units:
  years = seconds / (365.25 * 86400)

Session weights used (DEFAULT_WEIGHTS):
  rth=1.0, eth=0.30, maintenance=0.0, weekend=0.05

Timezone note:
  CST = UTC-6, CDT = UTC-5.
  America/Chicago uses CST Nov–Mar (approx), CDT Mar–Nov.
  2025 spring-forward: Sun 2025-03-09 02:00 CST → 03:00 CDT.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_pricing.src.vol_clock import (
    DEFAULT_WEIGHTS,
    SessionWeights,
    business_time_years,
    business_vol_from_calendar,
    calendar_time_years,
    calendar_vol_from_business,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CT = ZoneInfo("America/Chicago")
_UTC = timezone.utc
_SECS_PER_YEAR = 365.25 * 86400.0


def ct(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Construct a tz-aware CT datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=_CT)


def utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Construct a tz-aware UTC datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=_UTC)


# ---------------------------------------------------------------------------
# calendar_time_years
# ---------------------------------------------------------------------------


class TestCalendarTimeYears:
    def test_one_hour(self):
        """1 hour = 3600 / _SECS_PER_YEAR years."""
        t0 = utc(2024, 1, 15, 10, 0)
        t1 = utc(2024, 1, 15, 11, 0)
        expected = 3600.0 / _SECS_PER_YEAR
        assert abs(calendar_time_years(t0, t1) - expected) < 1e-15

    def test_zero(self):
        t = utc(2024, 6, 1, 12, 0)
        assert calendar_time_years(t, t) == 0.0

    def test_one_year_approx(self):
        """2024 is a leap year (366 days); span ≈ 366/365.25 years."""
        t0 = utc(2024, 1, 1, 0, 0)
        t1 = utc(2025, 1, 1, 0, 0)
        expected = 366.0 * 86400.0 / _SECS_PER_YEAR  # 2024 is a leap year
        assert abs(calendar_time_years(t0, t1) - expected) < 1e-10


# ---------------------------------------------------------------------------
# business_time_years — RTH and maintenance
# ---------------------------------------------------------------------------


class TestRTH:
    def test_one_full_rth_day(self):
        """Mon 08:30–15:00 CT = 6.5h × 1.0 weight.

        Hand-computed:
          weighted_secs = 6.5 * 3600 = 23400
          years = 23400 / (365.25 * 86400)
        Using 2024-01-08 (Monday) which is in CST (UTC-6).
        """
        # 2024-01-08 is a Monday; CST offset −6h
        t0 = ct(2024, 1, 8, 8, 30)
        t1 = ct(2024, 1, 8, 15, 0)
        expected = 6.5 * 3600.0 / _SECS_PER_YEAR
        result = business_time_years(t0, t1)
        assert abs(result - expected) < 1e-14, (
            f"RTH 6.5h: got {result:.15e}, expected {expected:.15e}"
        )

    def test_maintenance_hour_zero(self):
        """Mon 16:00–17:00 CT maintenance halt: weight 0.0, business time = 0.

        Hand-computed:
          weighted_secs = 3600 * 0.0 = 0
        """
        t0 = ct(2024, 1, 8, 16, 0)
        t1 = ct(2024, 1, 8, 17, 0)
        result = business_time_years(t0, t1)
        assert result == 0.0, f"maintenance hour must be 0, got {result}"

    def test_rth_partial_start(self):
        """Entering RTH mid-session: Mon 10:00–15:00 = 5h × 1.0.

        weighted_secs = 5 * 3600 = 18000
        """
        t0 = ct(2024, 1, 8, 10, 0)
        t1 = ct(2024, 1, 8, 15, 0)
        expected = 5.0 * 3600.0 / _SECS_PER_YEAR
        result = business_time_years(t0, t1)
        assert abs(result - expected) < 1e-14

    def test_eth_close_weight(self):
        """Mon 15:00–16:00 CT post-close ETH: weight 0.30.

        weighted_secs = 3600 * 0.30 = 1080
        """
        t0 = ct(2024, 1, 8, 15, 0)
        t1 = ct(2024, 1, 8, 16, 0)
        expected = 1.0 * 3600.0 * 0.30 / _SECS_PER_YEAR
        result = business_time_years(t0, t1)
        assert abs(result - expected) < 1e-14

    def test_eth_overnight_weight(self):
        """Mon 17:00 → Tue 08:30 CT overnight ETH: 15.5h × 0.30.

        weighted_secs = 15.5 * 3600 * 0.30 = 16740
        """
        t0 = ct(2024, 1, 8, 17, 0)
        t1 = ct(2024, 1, 9, 8, 30)
        expected = 15.5 * 3600.0 * 0.30 / _SECS_PER_YEAR
        result = business_time_years(t0, t1)
        assert abs(result - expected) < 1e-14, (
            f"ETH overnight: got {result:.15e}, expected {expected:.15e}"
        )


# ---------------------------------------------------------------------------
# business_time_years — weekend
# ---------------------------------------------------------------------------


class TestWeekend:
    def test_full_weekend_non_dst(self):
        """Fri 16:00 CT → Sun 17:00 CT (non-DST): 49h × 0.05.

        Using 2024-01-12 (Friday, CST).
        Hand-computed:
          Fri 16:00 CST to Sun 17:00 CST = 49 calendar hours.
          weighted_secs = 49 * 3600 * 0.05 = 8820
          years = 8820 / (365.25 * 86400)
        """
        t0 = ct(2024, 1, 12, 16, 0)   # Friday
        t1 = ct(2024, 1, 14, 17, 0)   # Sunday
        expected = 49.0 * 3600.0 * 0.05 / _SECS_PER_YEAR
        result = business_time_years(t0, t1)
        assert abs(result - expected) < 1e-14, (
            f"Weekend 49h: got {result:.15e}, expected {expected:.15e}"
        )

    def test_weekend_partial_entry(self):
        """Entering from Sat noon: only Sat 12:00 → Sun 17:00 = 29h × 0.05.

        Using 2024-01-13 (Saturday, CST).
        weekend block is Fri 16:00 → Sun 17:00; query starts Sat 12:00.
        overlap = Sat 12:00 to Sun 17:00 = 29h.
        weighted_secs = 29 * 3600 * 0.05 = 5220
        """
        t0 = ct(2024, 1, 13, 12, 0)   # Saturday noon
        t1 = ct(2024, 1, 14, 17, 0)   # Sunday 17:00
        expected = 29.0 * 3600.0 * 0.05 / _SECS_PER_YEAR
        result = business_time_years(t0, t1)
        assert abs(result - expected) < 1e-14, (
            f"Partial weekend: got {result:.15e}, expected {expected:.15e}"
        )

    def test_weekend_exits_at_sun_17(self):
        """Weekend ends at Sun 17:00 CT; Sun 17:00 onwards is ETH (owned by Sunday).

        Query: Sun 16:00 → Sun 18:00.
          Sun 16:00–17:00: 1h × 0.05 (weekend block owned by preceding Friday)
          Sun 17:00–18:00: 1h × 0.30 (ETH overnight owned by Sunday's date)
          Result = 1h × 0.05 + 1h × 0.30

        Hand-computed:
          weighted_secs = 3600 * 0.05 + 3600 * 0.30 = 180 + 1080 = 1260
          years = 1260 / (365.25 * 86400)
        """
        # 2024-01-14 is Sunday (CST)
        t0 = ct(2024, 1, 14, 16, 0)
        t1 = ct(2024, 1, 14, 18, 0)
        # Weekend [Fri16:00, Sun17:00] gives 1h, ETH [Sun17:00, Mon08:30] gives 1h:
        expected = (1.0 * 3600.0 * 0.05 + 1.0 * 3600.0 * 0.30) / _SECS_PER_YEAR
        result = business_time_years(t0, t1)
        assert abs(result - expected) < 1e-14, (
            f"Weekend exit: got {result:.15e}, expected {expected:.15e}"
        )

    def test_sun_17_to_mon_830_captured(self):
        """Sun 17:00 CT → Mon 08:30 CT: 15.5h × 0.30 (ETH overnight, owned by Sunday).

        This is the opening ETH of the trading week.  CME ES/NQ trading hours
        are Sun–Fri 17:00–16:00 CT; the Sunday open must contribute business time.

        Hand-computed (CST, 2024-01-14 Sunday):
          15.5 h × 3600 s/h × 0.30 = 16740 weighted seconds
          years = 16740 / (365.25 × 86400)
        """
        # 2024-01-14 Sun, 2024-01-15 Mon (both CST)
        t0 = ct(2024, 1, 14, 17, 0)   # Sunday 17:00 CT
        t1 = ct(2024, 1, 15, 8, 30)   # Monday 08:30 CT
        expected = 15.5 * 3600.0 * 0.30 / _SECS_PER_YEAR
        result = business_time_years(t0, t1)
        assert abs(result - expected) < 1e-14, (
            f"Sun17→Mon08:30 ETH: got {result:.15e}, expected {expected:.15e}"
        )


# ---------------------------------------------------------------------------
# business_time_years — full trading week
# ---------------------------------------------------------------------------


class TestFullWeek:
    def test_mon_rth_to_fri_rth(self):
        """Mon 08:30 → Fri 15:00 CT (non-DST, week of 2024-01-08).

        Hand-computed (all times CST, no DST):
          Mon: RTH 6.5h×1.0 + close 1h×0.30 + maint 1h×0.0 + overnight 15.5h×0.30
          Tue: same
          Wed: same
          Thu: same
          Fri: RTH 6.5h×1.0 only (t1 = Fri 15:00, close/maint/weekend not reached)

          RTH:       5 × 6.5h × 1.0   = 32.5h → 32.5 × 3600 × 1.0
          ETH close: 4 × 1h   × 0.30  = 1.2h equiv → 4 × 3600 × 0.30
          maint:     4 × 1h   × 0.0   = 0
          overnight: 4 × 15.5h × 0.30 = 18.6h equiv → 4 × 15.5 × 3600 × 0.30

          total weighted secs = 3600 × (32.5 + 4×0.30 + 0 + 4×15.5×0.30)
                              = 3600 × (32.5 + 1.2 + 18.6)
                              = 3600 × 52.3
                              = 188280
          years = 188280 / (365.25 × 86400)
        """
        t0 = ct(2024, 1, 8, 8, 30)    # Monday 2024-01-08
        t1 = ct(2024, 1, 12, 15, 0)   # Friday 2024-01-12
        rth_secs = 5 * 6.5 * 3600.0
        close_secs = 4 * 1.0 * 3600.0 * 0.30
        overnight_secs = 4 * 15.5 * 3600.0 * 0.30
        expected = (rth_secs + close_secs + overnight_secs) / _SECS_PER_YEAR
        result = business_time_years(t0, t1)
        assert abs(result - expected) < 1e-13, (
            f"Full week: got {result:.15e}, expected {expected:.15e}"
        )


# ---------------------------------------------------------------------------
# business_time_years — DST transition
# ---------------------------------------------------------------------------


class TestDST:
    def test_weekend_across_spring_forward_2025(self):
        """Weekend Fri 2025-03-07 16:00 CT → Sun 2025-03-09 17:00 CT: 48h × 0.05.

        Spring-forward: Sun 2025-03-09 02:00 CST → 03:00 CDT (clocks jump forward,
        Sunday has only 23 actual hours).

        Hand-computed in UTC:
          Fri 2025-03-07 16:00 CST = Fri 2025-03-07 22:00 UTC (CST = UTC-6)
          Sun 2025-03-09 17:00 CDT = Sun 2025-03-09 22:00 UTC (CDT = UTC-5)
          calendar seconds = 48 × 3600 = 172800   (NOT 49h — DST removes one hour)
          weighted_secs = 172800 × 0.05 = 8640
          years = 8640 / (365.25 × 86400)
        """
        t0 = ct(2025, 3, 7, 16, 0)    # Friday (CST)
        t1 = ct(2025, 3, 9, 17, 0)    # Sunday (CDT after spring-forward)
        expected = 48.0 * 3600.0 * 0.05 / _SECS_PER_YEAR
        result = business_time_years(t0, t1)
        assert abs(result - expected) < 1e-14, (
            f"DST weekend 48h: got {result:.15e}, expected {expected:.15e}"
        )

    def test_calendar_time_dst_weekend(self):
        """calendar_time_years confirms DST weekend = 48h not 49h."""
        t0 = ct(2025, 3, 7, 16, 0)
        t1 = ct(2025, 3, 9, 17, 0)
        expected = 48.0 * 3600.0 / _SECS_PER_YEAR
        result = calendar_time_years(t0, t1)
        assert abs(result - expected) < 1e-14, (
            f"DST calendar 48h: got {result:.15e}, expected {expected:.15e}"
        )

    def test_rth_unchanged_after_dst(self):
        """RTH slot 08:30–15:00 CDT still = 6.5h after spring-forward.

        Using Mon 2025-03-10 (first trading Monday in CDT).
        """
        t0 = ct(2025, 3, 10, 8, 30)
        t1 = ct(2025, 3, 10, 15, 0)
        expected = 6.5 * 3600.0 / _SECS_PER_YEAR
        result = business_time_years(t0, t1)
        assert abs(result - expected) < 1e-14, (
            f"RTH after DST: got {result:.15e}, expected {expected:.15e}"
        )


# ---------------------------------------------------------------------------
# business_time_years — event lumps
# ---------------------------------------------------------------------------


class TestEventLumps:
    def test_event_inside_interval_counted(self):
        """Lump strictly inside (t0, t1] is counted.

        t0 = Mon 08:30 CT, t1 = Mon 15:00 CT, event = Mon 10:00 CT.
        RTH secs = 6.5 * 3600 * 1.0
        lump = 1e-4 years
        total = RTH + 1e-4
        """
        t0 = ct(2024, 1, 8, 8, 30)
        ev = ct(2024, 1, 8, 10, 0)
        t1 = ct(2024, 1, 8, 15, 0)
        lump = 1e-4
        rth = 6.5 * 3600.0 / _SECS_PER_YEAR
        expected = rth + lump
        result = business_time_years(t0, t1, events=[(ev, lump)])
        assert abs(result - expected) < 1e-14

    def test_event_at_t1_counted(self):
        """Event at exactly t1 is counted (interval is (t0, t1])."""
        t0 = ct(2024, 1, 8, 8, 30)
        t1 = ct(2024, 1, 8, 15, 0)
        lump = 2e-4
        rth = 6.5 * 3600.0 / _SECS_PER_YEAR
        result = business_time_years(t0, t1, events=[(t1, lump)])
        assert abs(result - rth - lump) < 1e-14

    def test_event_at_t0_not_counted(self):
        """Event exactly at t0 is NOT counted (open on left)."""
        t0 = ct(2024, 1, 8, 8, 30)
        t1 = ct(2024, 1, 8, 15, 0)
        lump = 2e-4
        rth = 6.5 * 3600.0 / _SECS_PER_YEAR
        result = business_time_years(t0, t1, events=[(t0, lump)])
        assert abs(result - rth) < 1e-14

    def test_event_outside_interval_not_counted(self):
        """Event outside (t0, t1] is not counted."""
        t0 = ct(2024, 1, 8, 8, 30)
        t1 = ct(2024, 1, 8, 15, 0)
        ev_before = ct(2024, 1, 7, 12, 0)   # yesterday
        ev_after = ct(2024, 1, 9, 12, 0)    # tomorrow
        lump = 1e-3
        rth = 6.5 * 3600.0 / _SECS_PER_YEAR
        result = business_time_years(t0, t1, events=[(ev_before, lump), (ev_after, lump)])
        assert abs(result - rth) < 1e-14

    def test_multiple_events(self):
        """Two events both inside: both lumps are added."""
        t0 = ct(2024, 1, 8, 8, 30)
        t1 = ct(2024, 1, 8, 15, 0)
        ev1 = ct(2024, 1, 8, 9, 0)
        ev2 = ct(2024, 1, 8, 14, 0)
        lump1, lump2 = 1e-4, 2e-4
        rth = 6.5 * 3600.0 / _SECS_PER_YEAR
        result = business_time_years(t0, t1, events=[(ev1, lump1), (ev2, lump2)])
        assert abs(result - rth - lump1 - lump2) < 1e-14


# ---------------------------------------------------------------------------
# Variance-preserving vol conversions
# ---------------------------------------------------------------------------


class TestVolConversions:
    def test_roundtrip_sigma_cal_to_biz_to_cal(self):
        """sigma_cal → sigma_biz → sigma_cal round-trips exactly.

        Variance preservation: sigma_cal^2 * T_cal = sigma_biz^2 * T_biz.
        So converting back must recover original sigma_cal.
        """
        sigma_cal = 0.20
        t0 = ct(2024, 1, 8, 8, 30)
        t1 = ct(2024, 1, 12, 15, 0)   # full RTH week
        sigma_biz = business_vol_from_calendar(sigma_cal, t0, t1)
        sigma_cal_back = calendar_vol_from_business(sigma_biz, t0, t1)
        assert abs(sigma_cal_back - sigma_cal) < 1e-14, (
            f"round-trip: {sigma_cal} → {sigma_biz:.8f} → {sigma_cal_back:.15f}"
        )

    def test_roundtrip_sigma_biz_to_cal_to_biz(self):
        """sigma_biz → sigma_cal → sigma_biz round-trips exactly."""
        sigma_biz = 0.30
        t0 = ct(2024, 1, 8, 8, 30)
        t1 = ct(2024, 1, 12, 15, 0)
        sigma_cal = calendar_vol_from_business(sigma_biz, t0, t1)
        sigma_biz_back = business_vol_from_calendar(sigma_cal, t0, t1)
        assert abs(sigma_biz_back - sigma_biz) < 1e-14

    def test_variance_preservation(self):
        """sigma_cal^2 * T_cal == sigma_biz^2 * T_biz (tolerance 1e-14)."""
        sigma_cal = 0.20
        t0 = ct(2024, 1, 8, 8, 30)
        t1 = ct(2024, 1, 12, 15, 0)
        T_cal = calendar_time_years(t0, t1)
        T_biz = business_time_years(t0, t1)
        sigma_biz = business_vol_from_calendar(sigma_cal, t0, t1)
        var_cal = sigma_cal ** 2 * T_cal
        var_biz = sigma_biz ** 2 * T_biz
        # float64 epsilon ~2.2e-16; product of magnitudes ~5e-4 → ulp ~1e-19
        assert abs(var_cal - var_biz) < 1e-18, (
            f"variance not preserved: {var_cal:.20e} vs {var_biz:.20e}"
        )

    def test_biz_vol_greater_than_cal_when_biz_lt_cal(self):
        """When T_biz < T_cal, sigma_biz > sigma_cal (same total variance, shorter time).

        For a period with mixed weights (some < 1), T_biz < T_cal,
        so sigma_biz must be larger to preserve variance.
        """
        sigma_cal = 0.20
        t0 = ct(2024, 1, 12, 16, 0)   # Friday 16:00 → weekend
        t1 = ct(2024, 1, 14, 17, 0)   # Sunday 17:00
        sigma_biz = business_vol_from_calendar(sigma_cal, t0, t1)
        # T_biz = 49h * 0.05 < T_cal = 49h, so sigma_biz > sigma_cal
        assert sigma_biz > sigma_cal

    def test_zero_biz_time_returns_zero_sigma(self):
        """Pure maintenance window: T_biz=0 → sigma_biz=0 (not inf)."""
        sigma_cal = 0.20
        t0 = ct(2024, 1, 8, 16, 0)
        t1 = ct(2024, 1, 8, 17, 0)
        sigma_biz = business_vol_from_calendar(sigma_cal, t0, t1)
        assert sigma_biz == 0.0

    def test_zero_cal_time_returns_zero_sigma(self):
        """T_cal=0 → sigma_cal=0 from calendar_vol_from_business."""
        t = ct(2024, 1, 8, 10, 0)
        sigma_cal = calendar_vol_from_business(0.30, t, t)
        assert sigma_cal == 0.0


# ---------------------------------------------------------------------------
# Custom weights
# ---------------------------------------------------------------------------


class TestCustomWeights:
    def test_custom_eth_weight(self):
        """Custom eth=0.50 changes ETH close period contribution."""
        w = SessionWeights(eth=0.50)
        t0 = ct(2024, 1, 8, 15, 0)
        t1 = ct(2024, 1, 8, 16, 0)
        expected = 1.0 * 3600.0 * 0.50 / _SECS_PER_YEAR
        result = business_time_years(t0, t1, weights=w)
        assert abs(result - expected) < 1e-14

    def test_custom_weekend_weight_zero(self):
        """Setting weekend=0.0 eliminates weekend contribution."""
        w = SessionWeights(weekend=0.0)
        t0 = ct(2024, 1, 12, 16, 0)
        t1 = ct(2024, 1, 14, 17, 0)
        result = business_time_years(t0, t1, weights=w)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    def test_reversed_raises(self):
        """t1 < t0 raises ValueError."""
        t0 = utc(2024, 1, 8, 10, 0)
        t1 = utc(2024, 1, 8, 9, 0)
        with pytest.raises(ValueError, match=">="):
            business_time_years(t0, t1)

    def test_reversed_calendar_raises(self):
        t0 = utc(2024, 1, 8, 10, 0)
        t1 = utc(2024, 1, 8, 9, 0)
        with pytest.raises(ValueError, match=">="):
            calendar_time_years(t0, t1)

    def test_naive_t0_raises(self):
        """Naive t0 datetime raises ValueError."""
        t0_naive = datetime(2024, 1, 8, 10, 0)   # no tzinfo
        t1 = utc(2024, 1, 8, 11, 0)
        with pytest.raises(ValueError, match="tz-aware"):
            business_time_years(t0_naive, t1)

    def test_naive_t1_raises(self):
        """Naive t1 datetime raises ValueError."""
        t0 = utc(2024, 1, 8, 10, 0)
        t1_naive = datetime(2024, 1, 8, 11, 0)
        with pytest.raises(ValueError, match="tz-aware"):
            business_time_years(t0, t1_naive)

    def test_equal_datetimes_zero(self):
        """t0 == t1 returns 0.0 (not error)."""
        t = utc(2024, 1, 8, 10, 0)
        assert business_time_years(t, t) == 0.0

    def test_naive_event_raises(self):
        """Naive event datetime raises ValueError."""
        t0 = utc(2024, 1, 8, 8, 0)
        t1 = utc(2024, 1, 8, 16, 0)
        ev_naive = datetime(2024, 1, 8, 10, 0)
        with pytest.raises(ValueError, match="tz-aware"):
            business_time_years(t0, t1, events=[(ev_naive, 1e-4)])


# ---------------------------------------------------------------------------
# Monotonicity and sanity
# ---------------------------------------------------------------------------


class TestSanity:
    def test_business_time_le_calendar_time(self):
        """For any interval, business_time <= calendar_time (all weights <= 1.0)."""
        # Use default weights where max weight is rth=1.0
        cases = [
            (ct(2024, 1, 8, 8, 30), ct(2024, 1, 8, 15, 0)),    # RTH only
            (ct(2024, 1, 8, 0, 0), ct(2024, 1, 8, 23, 59)),    # full day
            (ct(2024, 1, 8, 8, 30), ct(2024, 1, 12, 15, 0)),   # full week
            (ct(2024, 1, 12, 16, 0), ct(2024, 1, 14, 17, 0)),  # weekend
        ]
        for t0, t1 in cases:
            T_cal = calendar_time_years(t0, t1)
            T_biz = business_time_years(t0, t1)
            assert T_biz <= T_cal + 1e-14, (
                f"T_biz ({T_biz}) > T_cal ({T_cal}) for {t0}–{t1}"
            )

    def test_business_time_nonneg(self):
        """business_time_years is always >= 0."""
        cases = [
            (ct(2024, 1, 8, 16, 0), ct(2024, 1, 8, 17, 0)),   # maintenance
            (ct(2024, 1, 8, 8, 30), ct(2024, 1, 8, 15, 0)),   # RTH
            (ct(2024, 1, 12, 0, 0), ct(2024, 1, 14, 0, 0)),   # sat
        ]
        for t0, t1 in cases:
            assert business_time_years(t0, t1) >= 0.0

    def test_additivity(self):
        """business_time_years(t0, t1) + business_time_years(t1, t2)
        == business_time_years(t0, t2) for non-overlapping contiguous intervals."""
        t0 = ct(2024, 1, 8, 8, 30)
        t1 = ct(2024, 1, 8, 15, 0)
        t2 = ct(2024, 1, 9, 8, 30)
        ab = business_time_years(t0, t1) + business_time_years(t1, t2)
        full = business_time_years(t0, t2)
        assert abs(ab - full) < 1e-14, f"additivity: {ab} vs {full}"
