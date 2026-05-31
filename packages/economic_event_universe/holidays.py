"""US federal holiday adjustments for macro release schedules."""

from __future__ import annotations

from datetime import date, timedelta

from economic_event_universe.registry import get_event_def

# Fixed + observed federal holidays (simplified; extend via YAML if needed)
_FIXED: list[tuple[int, int]] = [
    (1, 1),
    (6, 19),
    (7, 4),
    (11, 11),
    (12, 25),
]

# N-th weekday rules: (month, weekday, n) weekday 0=Mon
_WEEKDAY_RULES: list[tuple[int, int, int]] = [
    (1, 0, 3),  # MLK third Monday Jan
    (2, 0, 3),  # Presidents Day
    (5, 0, 0),  # Memorial last Monday May — n=0 => last
    (9, 0, 1),  # Labor first Monday Sep
    (10, 0, 1),  # Columbus second Monday Oct — approx
    (11, 3, 4),  # Thanksgiving fourth Thursday Nov
]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    if n == 0:
        d = date(year, month + 1, 1) - timedelta(days=1) if month == 12 else date(year, month + 1, 1)
        if month < 12:
            d = date(year, month + 1, 1) - timedelta(days=1)
        else:
            d = date(year, 12, 31)
        while d.weekday() != weekday:
            d -= timedelta(days=1)
        return d
    d = date(year, month, 1)
    count = 0
    while d.month == month:
        if d.weekday() == weekday:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)
    raise ValueError(f"no nth weekday for {year}-{month}")


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def federal_holidays(year: int) -> set[date]:
    out: set[date] = set()
    for m, d in _FIXED:
        if m == 6 and d == 19 and year < 2021:
            continue
        out.add(_observed(date(year, m, d)))
    out.add(_nth_weekday(year, 1, 0, 3))
    out.add(_nth_weekday(year, 2, 0, 3))
    # Memorial last Monday May
    d = date(year, 5, 31)
    while d.weekday() != 0:
        d -= timedelta(days=1)
    out.add(d)
    out.add(_nth_weekday(year, 9, 0, 1))
    out.add(_nth_weekday(year, 11, 3, 4))
    return out


def is_federal_holiday(d: date) -> bool:
    return d in federal_holidays(d.year)


def apply_holiday_adjustment(event_type: str, nominal: date) -> date:
    """Shift release date per event_universe holiday_rule."""
    rule = get_event_def(event_type).get("holiday_rule", "none")
    if rule == "none":
        return nominal
    if rule == "claims_thursday_to_wednesday":
        if nominal.weekday() == 3 and is_federal_holiday(nominal):
            return nominal - timedelta(days=1)
    if rule == "skip_if_holiday":
        while is_federal_holiday(nominal):
            nominal += timedelta(days=1)
    return nominal
