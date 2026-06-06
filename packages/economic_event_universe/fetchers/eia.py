"""EIA weekly petroleum and natural gas storage releases."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from economic_event_universe.fetchers.calendar_rows import calendar_row

_CRUDE_URL = "https://www.eia.gov/petroleum/supply/weekly/"
_NATGAS_URL = "https://www.eia.gov/naturalgas/storage/"


def _weekly_wednesdays(start_year: int, end_year: int, event_type: str, url: str, rt: str) -> list[dict]:
    rows: list[dict] = []
    d = date(start_year, 1, 1)
    while d.weekday() != 2:
        d += timedelta(days=1)
    end = date(end_year, 12, 31)
    while d <= end:
        rows.append(calendar_row(event_type, d.isoformat(), source="EIA", source_url=url, release_time=rt))
        d += timedelta(days=7)
    return rows


def _weekly_thursdays(start_year: int, end_year: int, event_type: str, url: str, rt: str) -> list[dict]:
    rows: list[dict] = []
    d = date(start_year, 1, 1)
    while d.weekday() != 3:
        d += timedelta(days=1)
    end = date(end_year, 12, 31)
    while d <= end:
        rows.append(calendar_row(event_type, d.isoformat(), source="EIA", source_url=url, release_time=rt))
        d += timedelta(days=7)
    return rows


def fetch_all_eia_rows(*, start_year: int = 2018, end_year: int = 2026) -> dict[str, list[dict[str, Any]]]:
    crude = _weekly_wednesdays(start_year, end_year, "EIA_CRUDE", _CRUDE_URL, "10:30:00")
    natgas = _weekly_thursdays(start_year, end_year, "EIA_NATGAS", _NATGAS_URL, "10:30:00")
    return {"eia_weekly.csv": crude + natgas}
