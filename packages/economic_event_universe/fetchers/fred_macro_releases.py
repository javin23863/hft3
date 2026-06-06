"""FRED bootstrap rows for BLS/BEA/Census macro release types."""

from __future__ import annotations

from typing import Any

from economic_event_universe.fetchers.calendar_rows import calendar_row
from economic_event_universe.fetchers.fred_client import FRED_RELEASE_IDS, release_dates


def fetch_fred_rows_for_type(
    event_type: str,
    *,
    start_year: int = 2018,
    end_year: int = 2026,
) -> list[dict[str, Any]]:
    release_id = FRED_RELEASE_IDS.get(event_type)
    if release_id is None:
        return []
    try:
        dates = release_dates(
            release_id,
            start=f"{start_year}-01-01",
            end=f"{end_year}-12-31",
        )
    except Exception:
        return []
    return [
        calendar_row(event_type, d, source="FRED")
        for d in dates
        if start_year <= int(d[:4]) <= end_year
    ]


def fetch_fred_macro_rows(
    event_types: list[str],
    *,
    start_year: int = 2018,
    end_year: int = 2026,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for et in event_types:
        rows.extend(
            fetch_fred_rows_for_type(et, start_year=start_year, end_year=end_year)
        )
    return rows
