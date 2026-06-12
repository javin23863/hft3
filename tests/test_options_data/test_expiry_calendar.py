"""Tests for options_data.src.expiry_calendar.

Golden values
-------------
* 2025-06-20: 3rd Friday of June 2025.
    Fridays in June 2025: 6, 13, 20, 27.  3rd Friday = 2025-06-20.
    It is in a quarterly month (Jun), so kind = QUARTERLY, style = 'A'.

* 2024-03-29 Good Friday: Friday weekly that would be 2024-03-29 shifts to
    2024-03-28 (Thursday) per the v0 preceding-business-day rule.
    Good Friday 2024: Easter 2024 = March 31 (Gregorian), so Good Friday = March 29. ✓

* EOM 2025-05-30: May 2025 ends on Saturday 2025-05-31 (not a business day).
    Preceding business day = Friday 2025-05-30.

* exercise_style for WEEKLY_FRI:
    - 2021-09-10 (before STYLE_CONVERSION_DATE 2021-09-20) → 'A'
    - 2021-09-24 (after STYLE_CONVERSION_DATE) → 'E'
    Quarterly always 'A'.

* fixing_datetime_utc DST:
    - 2025-01-17: CST (UTC-6); 15:00 CT = 21:00 UTC.
    - 2025-06-20: CDT (UTC-5); 15:00 CT = 20:00 UTC.

* expiries_between for the week 2024-06-10 (Mon) to 2024-06-14 (Fri):
    All five weekday kinds should yield one entry each.
    Jun 2024 quarterly is 2024-06-21 (3rd Friday) — NOT in this window.
    - Mon 2024-06-10: WEEKLY_MON
    - Tue 2024-06-11: WEEKLY_TUE
    - Wed 2024-06-12: WEEKLY_WED
    - Thu 2024-06-13: WEEKLY_THU
    - Fri 2024-06-14: WEEKLY_FRI (not a quarterly)
    EOM May 2024 = 2024-05-31, EOM June 2024 = 2024-06-28 — neither in window.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_data.src.expiry_calendar import (
    ExpiryKind,
    STYLE_CONVERSION_DATE,
    exercise_style,
    expiries_between,
    fixing_datetime_utc,
    _good_friday,
    _monthly_eom_expiry,
    _third_friday,
)


# ---------------------------------------------------------------------------
# Quarterly
# ---------------------------------------------------------------------------

class TestThirdFriday:
    def test_jun_2025(self):
        # Fridays Jun 2025: 6,13,20,27 → 3rd = 20
        assert _third_friday(2025, 6) == date(2025, 6, 20)

    def test_mar_2025(self):
        # Fridays Mar 2025: 7,14,21,28 → 3rd = 21
        assert _third_friday(2025, 3) == date(2025, 3, 21)

    def test_dec_2024(self):
        # Fridays Dec 2024: 6,13,20,27 → 3rd = 20
        assert _third_friday(2024, 12) == date(2024, 12, 20)


class TestQuarterlyExpiry:
    def test_jun_2025_quarterly_in_expiries(self):
        """2025-06-20 should appear as QUARTERLY in expiries_between."""
        target = date(2025, 6, 20)
        results = expiries_between(target, target, kinds=[ExpiryKind.QUARTERLY])
        assert len(results) == 1
        d, kind = results[0]
        assert d == target
        assert kind == ExpiryKind.QUARTERLY

    def test_quarterly_style_always_american(self):
        assert exercise_style(ExpiryKind.QUARTERLY, date(2025, 6, 20)) == "A"
        assert exercise_style(ExpiryKind.QUARTERLY, date(2019, 3, 15)) == "A"
        # Post-conversion date still American
        assert exercise_style(ExpiryKind.QUARTERLY, date(2022, 3, 18)) == "A"


# ---------------------------------------------------------------------------
# Good Friday + weekly shift
# ---------------------------------------------------------------------------

class TestGoodFriday:
    def test_2024_good_friday(self):
        # Easter 2024 = March 31; Good Friday = March 29
        gf = _good_friday(2024)
        assert gf == date(2024, 3, 29)

    def test_2025_good_friday(self):
        # Easter 2025 = April 20; Good Friday = April 18
        gf = _good_friday(2025)
        assert gf == date(2025, 4, 18)


class TestFridayWeeklyHolidayShift:
    def test_good_friday_2024_shifts_to_thursday(self):
        """2024-03-29 is Good Friday; the WEEKLY_FRI that would fall on it
        must shift to the preceding business day 2024-03-28 (Thursday).

        v0 rule: preceding business day.
        """
        # Get WEEKLY_FRI expiries in the Good Friday week
        start = date(2024, 3, 25)
        end = date(2024, 3, 29)
        results = expiries_between(start, end, kinds=[ExpiryKind.WEEKLY_FRI])

        # The 2024-03-29 Friday weekly should shift to 2024-03-28
        dates = [d for d, _ in results]
        assert date(2024, 3, 28) in dates, (
            f"Expected 2024-03-28 (Good Friday shift); got {dates}"
        )
        assert date(2024, 3, 29) not in dates, (
            "Holiday date 2024-03-29 should not appear"
        )


# ---------------------------------------------------------------------------
# Monthly EOM
# ---------------------------------------------------------------------------

class TestMonthlyEOM:
    def test_may_2025_eom(self):
        """May 2025 ends on Saturday 31; last business day = Friday 2025-05-30."""
        eom = _monthly_eom_expiry(2025, 5)
        assert eom == date(2025, 5, 30)

    def test_april_2025_eom(self):
        """April 2025 ends on Wednesday 30; last business day = 2025-04-30."""
        eom = _monthly_eom_expiry(2025, 4)
        assert eom == date(2025, 4, 30)

    def test_eom_in_expiries_between(self):
        target = date(2025, 5, 30)
        results = expiries_between(target, target, kinds=[ExpiryKind.MONTHLY_EOM])
        dates = [d for d, _ in results]
        assert target in dates


# ---------------------------------------------------------------------------
# Exercise style
# ---------------------------------------------------------------------------

class TestExerciseStyle:
    def test_weekly_fri_before_conversion_american(self):
        # 2021-09-10 is before STYLE_CONVERSION_DATE 2021-09-20
        assert date(2021, 9, 10) < STYLE_CONVERSION_DATE
        assert exercise_style(ExpiryKind.WEEKLY_FRI, date(2021, 9, 10)) == "A"

    def test_weekly_fri_after_conversion_european(self):
        # 2021-09-24 is after STYLE_CONVERSION_DATE
        assert date(2021, 9, 24) >= STYLE_CONVERSION_DATE
        assert exercise_style(ExpiryKind.WEEKLY_FRI, date(2021, 9, 24)) == "E"

    def test_weekly_fri_on_conversion_date_european(self):
        assert exercise_style(ExpiryKind.WEEKLY_FRI, STYLE_CONVERSION_DATE) == "E"

    def test_eom_before_conversion_american(self):
        assert exercise_style(ExpiryKind.MONTHLY_EOM, date(2020, 12, 31)) == "A"

    def test_eom_after_conversion_european(self):
        assert exercise_style(ExpiryKind.MONTHLY_EOM, date(2022, 1, 31)) == "E"

    def test_weekly_mon_always_european(self):
        # Mon weeklies launched after conversion
        assert exercise_style(ExpiryKind.WEEKLY_MON, date(2019, 1, 7)) == "E"

    def test_weekly_tue_always_european(self):
        assert exercise_style(ExpiryKind.WEEKLY_TUE, date(2021, 1, 5)) == "E"

    def test_weekly_wed_always_european(self):
        assert exercise_style(ExpiryKind.WEEKLY_WED, date(2017, 1, 4)) == "E"

    def test_weekly_thu_always_european(self):
        assert exercise_style(ExpiryKind.WEEKLY_THU, date(2021, 1, 7)) == "E"

    def test_quarterly_always_american(self):
        for d in [date(2019, 3, 15), date(2021, 9, 17), date(2024, 12, 20)]:
            assert exercise_style(ExpiryKind.QUARTERLY, d) == "A"


# ---------------------------------------------------------------------------
# fixing_datetime_utc — DST-correct
# ---------------------------------------------------------------------------

class TestFixingDatetimeUTC:
    def test_january_2025_cst(self):
        """2025-01-17 is in standard time (CST = UTC-6).
        15:00 CT → 21:00 UTC.
        """
        result = fixing_datetime_utc(date(2025, 1, 17))
        assert result.hour == 21
        assert result.minute == 0
        assert result.utcoffset().total_seconds() == 0

    def test_june_2025_cdt(self):
        """2025-06-20 is in daylight time (CDT = UTC-5).
        15:00 CT → 20:00 UTC.
        """
        result = fixing_datetime_utc(date(2025, 6, 20))
        assert result.hour == 20
        assert result.minute == 0
        assert result.utcoffset().total_seconds() == 0

    def test_returns_utc_aware(self):
        result = fixing_datetime_utc(date(2025, 6, 20))
        # Should be UTC-aware (offset = 0)
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# expiries_between — daily Mon-Fri week
# ---------------------------------------------------------------------------

class TestExpiriesBetweenDailyWeek:
    """Verify that a 2024 week yields all five weekday expiry kinds."""

    # 2024-06-10 Mon through 2024-06-14 Fri — no holidays in this week
    START = date(2024, 6, 10)
    END = date(2024, 6, 14)

    def _get_kinds(self, kind: ExpiryKind) -> list[tuple[date, ExpiryKind]]:
        return expiries_between(self.START, self.END, kinds=[kind])

    def test_monday_weekly(self):
        results = self._get_kinds(ExpiryKind.WEEKLY_MON)
        assert any(d == date(2024, 6, 10) for d, _ in results)

    def test_tuesday_weekly(self):
        results = self._get_kinds(ExpiryKind.WEEKLY_TUE)
        assert any(d == date(2024, 6, 11) for d, _ in results)

    def test_wednesday_weekly(self):
        results = self._get_kinds(ExpiryKind.WEEKLY_WED)
        assert any(d == date(2024, 6, 12) for d, _ in results)

    def test_thursday_weekly(self):
        results = self._get_kinds(ExpiryKind.WEEKLY_THU)
        assert any(d == date(2024, 6, 13) for d, _ in results)

    def test_friday_weekly(self):
        """2024-06-14 is not a quarterly (quarterly is 2024-06-21) → WEEKLY_FRI."""
        results = self._get_kinds(ExpiryKind.WEEKLY_FRI)
        assert any(d == date(2024, 6, 14) for d, _ in results)

    def test_all_five_kinds_present(self):
        """All-kinds call over the week should contain at least one of each weekday kind."""
        results = expiries_between(self.START, self.END)
        kind_set = {k for _, k in results}
        for kind in (
            ExpiryKind.WEEKLY_MON,
            ExpiryKind.WEEKLY_TUE,
            ExpiryKind.WEEKLY_WED,
            ExpiryKind.WEEKLY_THU,
            ExpiryKind.WEEKLY_FRI,
        ):
            assert kind in kind_set, f"{kind} not found in {kind_set}"

    def test_sorted_ascending(self):
        results = expiries_between(self.START, self.END)
        dates = [d for d, _ in results]
        assert dates == sorted(dates)

    def test_no_quarterly_in_this_week(self):
        results = expiries_between(self.START, self.END, kinds=[ExpiryKind.QUARTERLY])
        # 2024-06-21 is the quarterly — outside [Jun 10, Jun 14]
        assert results == []
