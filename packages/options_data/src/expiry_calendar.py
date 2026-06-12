"""ES/NQ options-on-futures expiry calendar — rule-based v0.

RULE-BASED v0, to be verified against Databento definition records when
backfill lands (WS-1.4); known gaps listed inline.

Exercise-style eras
-------------------
* Quarterly (QUARTERLY): 3rd Friday of Mar/Jun/Sep/Dec — American exercise,
  unchanged by the 2021 conversion.
* Monthly EOM (MONTHLY_EOM): last business day of month — converted from
  American to European on STYLE_CONVERSION_DATE (approximate; see constant).
* Friday weeklies (WEEKLY_FRI): converted from American to European on
  STYLE_CONVERSION_DATE.
* Monday–Thursday weeklies (WEEKLY_MON/TUE/WED/THU): launched after the
  style conversion date, so always European.

Holiday treatment (v0)
----------------------
Fixed federal holiday table with observed-date shifting (nearest Monday/Friday
rule for Sat/Sun holidays).  CME holiday calendar verification deferred to
WS-1.4.  Expiries falling on a US holiday shift to the PRECEDING business day
(v0 assumption — to be verified against CME rules).

Juneteenth (June 19) only applies from 2022 onwards (first federal observance).
"""
from __future__ import annotations

import enum
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Approximate launch dates for each weekly expiry kind.
# UNVERIFIED — each marked; to be confirmed from Databento definition records.
# ---------------------------------------------------------------------------

# Friday weeklies: launched ~2005 but earliest reliable data approx 2011;
# using 2011-01-07 as a conservative v0 start.  UNVERIFIED.
WEEKLY_FRI_STARTS_ON: date = date(2011, 1, 7)

# Wednesday weeklies added ~2017.  UNVERIFIED.
WEEKLY_WED_STARTS_ON: date = date(2017, 1, 4)

# Monday weeklies added ~2019.  UNVERIFIED.
WEEKLY_MON_STARTS_ON: date = date(2019, 1, 7)

# Tuesday and Thursday weeklies added 2021.  UNVERIFIED.
WEEKLY_TUE_STARTS_ON: date = date(2021, 1, 5)
WEEKLY_THU_STARTS_ON: date = date(2021, 1, 7)

# Full Mon-Fri daily weeklies available from 2022-05.  UNVERIFIED.
DAILY_FULL_STARTS_ON: date = date(2022, 5, 2)

# Exercise-style conversion: Friday weeklies + EOM converted American→European
# September 2021.  Exact date UNVERIFIED; using 2021-09-20 as placeholder.
STYLE_CONVERSION_DATE: date = date(2021, 9, 20)  # APPROXIMATE — verify from CME rulebook


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------

class ExpiryKind(enum.Enum):
    """CME ES/NQ options-on-futures expiry kinds."""
    QUARTERLY = "QUARTERLY"        # 3rd Friday Mar/Jun/Sep/Dec
    MONTHLY_EOM = "MONTHLY_EOM"    # last business day of month
    WEEKLY_MON = "WEEKLY_MON"      # Monday weekly
    WEEKLY_TUE = "WEEKLY_TUE"      # Tuesday weekly
    WEEKLY_WED = "WEEKLY_WED"      # Wednesday weekly
    WEEKLY_THU = "WEEKLY_THU"      # Thursday weekly
    WEEKLY_FRI = "WEEKLY_FRI"      # Friday weekly (non-quarterly)


# ---------------------------------------------------------------------------
# v0 US holiday table (fixed rules; observed-date shifting)
# ---------------------------------------------------------------------------

def _observed(d: date) -> date:
    """Return the observed date for a holiday falling on a weekend.

    Saturday → preceding Friday; Sunday → following Monday.
    v0 approximation — CME uses its own calendar (verify WS-1.4).
    """
    wd = d.weekday()  # 0=Mon … 6=Sun
    if wd == 5:  # Saturday → Friday
        return d - timedelta(days=1)
    if wd == 6:  # Sunday → Monday
        return d + timedelta(days=1)
    return d


def _us_holidays(year: int) -> frozenset[date]:
    """Return fixed federal US holidays for *year* with observed-date shifting.

    Juneteenth (June 19) included only from 2022 onwards (first federal observance).

    v0 approximation — CME holiday calendar may differ; verify WS-1.4.
    """
    holidays: list[date] = []

    # New Year's Day — Jan 1
    holidays.append(_observed(date(year, 1, 1)))

    # MLK Day — 3rd Monday of January
    holidays.append(_nth_weekday(year, 1, 0, 3))

    # Presidents' Day — 3rd Monday of February
    holidays.append(_nth_weekday(year, 2, 0, 3))

    # Good Friday — Friday before Easter Sunday
    holidays.append(_good_friday(year))

    # Memorial Day — last Monday of May
    holidays.append(_last_weekday(year, 5, 0))

    # Juneteenth — June 19, from 2022 onward
    if year >= 2022:
        holidays.append(_observed(date(year, 6, 19)))

    # Independence Day — July 4
    holidays.append(_observed(date(year, 7, 4)))

    # Labor Day — first Monday of September
    holidays.append(_nth_weekday(year, 9, 0, 1))

    # Thanksgiving — 4th Thursday of November
    holidays.append(_nth_weekday(year, 11, 3, 4))

    # Christmas Day — Dec 25
    holidays.append(_observed(date(year, 12, 25)))

    return frozenset(holidays)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth occurrence of *weekday* (0=Mon) in *year*/*month*."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    first_occurrence = first + timedelta(days=delta)
    return first_occurrence + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of *weekday* (0=Mon) in *year*/*month*."""
    # Find the last day of the month
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    delta = (last.weekday() - weekday) % 7
    return last - timedelta(days=delta)


def _good_friday(year: int) -> date:
    """Compute Good Friday via the Anonymous Gregorian algorithm for Easter."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter = date(year, month, day)
    return easter - timedelta(days=2)


def _is_business_day(d: date, holidays: frozenset[date]) -> bool:
    """Return True if *d* is a weekday and not a US holiday."""
    return d.weekday() < 5 and d not in holidays


def _preceding_business_day(d: date, holidays: frozenset[date]) -> date:
    """Return d if it is a business day, else step backward until one is found."""
    while not _is_business_day(d, holidays):
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Expiry date generators
# ---------------------------------------------------------------------------

def _third_friday(year: int, month: int) -> date:
    """Return the 3rd Friday of the given month."""
    return _nth_weekday(year, month, 4, 3)  # weekday 4 = Friday


def _quarterly_expiries(year: int) -> list[date]:
    """Return the four quarterly expiry dates for *year* (3rd Fri Mar/Jun/Sep/Dec)."""
    return [_third_friday(year, m) for m in (3, 6, 9, 12)]


def _monthly_eom_expiry(year: int, month: int) -> date:
    """Return the last business day of *year*/*month* (EOM expiry).

    Holiday check uses a cached holiday set for the relevant year.
    """
    holidays = _us_holidays(year)
    if month == 12:
        last_cal = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_cal = date(year, month + 1, 1) - timedelta(days=1)
    return _preceding_business_day(last_cal, holidays)


def _weekly_expiry_for_day(year: int, month: int, day: int,
                            holidays: frozenset[date]) -> date:
    """Return the expiry date for a weekly that nominally falls on *day*,
    shifting to the preceding business day if it is a holiday.

    v0 rule — to be verified against CME holiday treatment.
    """
    d = date(year, month, day)
    if d not in holidays:
        return d
    return _preceding_business_day(d, holidays)


def _all_weekday_dates(year: int, month: int, weekday: int) -> list[date]:
    """Return all dates in *year*/*month* with the given weekday (0=Mon)."""
    first = date(year, month, 1)
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    results = []
    delta = (weekday - first.weekday()) % 7
    d = first + timedelta(days=delta)
    while d <= last:
        results.append(d)
        d += timedelta(weeks=1)
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_TZ_CT = ZoneInfo("America/Chicago")


def exercise_style(kind: ExpiryKind, expiry_date: date) -> str:
    """Return 'A' (American) or 'E' (European) for the given kind and expiry date.

    Rules (v0):
    * QUARTERLY: always 'A' (unchanged by 2021 conversion).
    * MONTHLY_EOM, WEEKLY_FRI: 'A' before STYLE_CONVERSION_DATE, 'E' on/after.
    * WEEKLY_MON/TUE/WED/THU: always 'E' (launched after conversion).
    """
    if kind == ExpiryKind.QUARTERLY:
        return "A"
    if kind in (ExpiryKind.WEEKLY_MON, ExpiryKind.WEEKLY_TUE,
                ExpiryKind.WEEKLY_WED, ExpiryKind.WEEKLY_THU):
        return "E"
    # MONTHLY_EOM and WEEKLY_FRI converted on STYLE_CONVERSION_DATE
    if expiry_date >= STYLE_CONVERSION_DATE:
        return "E"
    return "A"


def fixing_datetime_utc(expiry_date: date) -> datetime:
    """Return the 15:00 CT fixing datetime in UTC for *expiry_date*.

    DST-correct via zoneinfo America/Chicago.

    Examples
    --------
    2025-01-17 (CST, UTC-6): 15:00 CT → 21:00 UTC
    2025-06-20 (CDT, UTC-5): 15:00 CT → 20:00 UTC
    """
    naive = datetime(expiry_date.year, expiry_date.month, expiry_date.day, 15, 0, 0)
    aware_ct = naive.replace(tzinfo=_TZ_CT)
    return aware_ct.astimezone(ZoneInfo("UTC"))


def expiries_between(
    start_date: date,
    end_date: date,
    kinds: tuple[ExpiryKind, ...] | list[ExpiryKind] | None = None,
) -> list[tuple[date, ExpiryKind]]:
    """Return all expiry dates in [start_date, end_date] for the given kinds.

    Parameters
    ----------
    start_date : inclusive start date
    end_date   : inclusive end date
    kinds      : subset of ExpiryKind values to include; None means all.

    Returns
    -------
    List of (date, ExpiryKind) sorted ascending by date.  If multiple kinds
    fall on the same date, each appears as a separate entry.

    Notes
    -----
    QUARTERLY takes precedence: a 3rd Friday in Mar/Jun/Sep/Dec is classified
    as QUARTERLY, not WEEKLY_FRI, even when both would apply.

    Holiday shifts can cause two kinds to map to the same calendar date
    (e.g., a Thursday weekly and the preceding-business-day shift of a Friday
    holiday).  Each is included separately; deduplication is left to callers.
    """
    if kinds is None:
        all_kinds: tuple[ExpiryKind, ...] = tuple(ExpiryKind)
    else:
        all_kinds = tuple(kinds)

    kind_set = set(all_kinds)

    results: list[tuple[date, ExpiryKind]] = []

    # Collect all (year, month) pairs spanned
    years = range(start_date.year, end_date.year + 1)

    # Pre-cache holidays per year
    holiday_cache: dict[int, frozenset[date]] = {}
    for y in years:
        holiday_cache[y] = _us_holidays(y)

    # Build the set of quarterly dates for quick lookup
    quarterly_dates: set[date] = set()
    for y in years:
        for qd in _quarterly_expiries(y):
            quarterly_dates.add(qd)

    def _add(d: date, kind: ExpiryKind) -> None:
        if start_date <= d <= end_date and kind in kind_set:
            results.append((d, kind))

    for y in years:
        holidays = holiday_cache[y]
        months = range(1, 13)

        for m in months:
            # --- QUARTERLY ---
            if ExpiryKind.QUARTERLY in kind_set:
                if m in (3, 6, 9, 12):
                    qd = _third_friday(y, m)
                    # Quarterlies rarely fall on holidays but apply the rule.
                    qd_adj = _preceding_business_day(qd, holidays)
                    _add(qd_adj, ExpiryKind.QUARTERLY)

            # --- MONTHLY_EOM ---
            if ExpiryKind.MONTHLY_EOM in kind_set:
                eom = _monthly_eom_expiry(y, m)
                # EOM should not duplicate a quarterly — skip if same date.
                # (In practice they rarely coincide, but guard it.)
                if eom not in quarterly_dates:
                    _add(eom, ExpiryKind.MONTHLY_EOM)

            # --- WEEKLY_FRI (non-quarterly Fridays) ---
            if ExpiryKind.WEEKLY_FRI in kind_set:
                for fri in _all_weekday_dates(y, m, 4):  # 4 = Friday
                    if fri in quarterly_dates:
                        continue  # classified as QUARTERLY
                    adj = _weekly_expiry_for_day(y, m, fri.day, holidays)
                    if adj >= WEEKLY_FRI_STARTS_ON:
                        _add(adj, ExpiryKind.WEEKLY_FRI)

            # --- WEEKLY_WED ---
            if ExpiryKind.WEEKLY_WED in kind_set:
                for wed in _all_weekday_dates(y, m, 2):  # 2 = Wednesday
                    adj = _weekly_expiry_for_day(y, m, wed.day, holidays)
                    if adj >= WEEKLY_WED_STARTS_ON:
                        _add(adj, ExpiryKind.WEEKLY_WED)

            # --- WEEKLY_MON ---
            if ExpiryKind.WEEKLY_MON in kind_set:
                for mon in _all_weekday_dates(y, m, 0):  # 0 = Monday
                    adj = _weekly_expiry_for_day(y, m, mon.day, holidays)
                    if adj >= WEEKLY_MON_STARTS_ON:
                        _add(adj, ExpiryKind.WEEKLY_MON)

            # --- WEEKLY_TUE ---
            if ExpiryKind.WEEKLY_TUE in kind_set:
                for tue in _all_weekday_dates(y, m, 1):  # 1 = Tuesday
                    adj = _weekly_expiry_for_day(y, m, tue.day, holidays)
                    if adj >= WEEKLY_TUE_STARTS_ON:
                        _add(adj, ExpiryKind.WEEKLY_TUE)

            # --- WEEKLY_THU ---
            if ExpiryKind.WEEKLY_THU in kind_set:
                for thu in _all_weekday_dates(y, m, 3):  # 3 = Thursday
                    adj = _weekly_expiry_for_day(y, m, thu.day, holidays)
                    if adj >= WEEKLY_THU_STARTS_ON:
                        _add(adj, ExpiryKind.WEEKLY_THU)

    results.sort(key=lambda x: (x[0], x[1].value))
    return results
