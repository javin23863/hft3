"""DOL unemployment claims — FRED bootstrap + weekly Thursday pattern fallback."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from economic_event_universe.fetchers.calendar_merge import merge_calendar_rows
from economic_event_universe.fetchers.calendar_rows import calendar_row
from economic_event_universe.fetchers.fred_macro_releases import fetch_fred_rows_for_type

_URL = "https://www.dol.gov/ui/data.pdf"


def _thursday_claims_dates(start_year: int, end_year: int) -> list[dict[str, Any]]:
    """Fallback when FRED unavailable — raw Thursday anchors (holiday shift applied downstream)."""
    rows: list[dict] = []
    d = date(start_year, 1, 1)
    while d.weekday() != 3:
        d += timedelta(days=1)
    end = date(end_year, 12, 31)
    while d <= end:
        rows.append(
            calendar_row(
                "UNEMPLOYMENT_CLAIMS",
                d.isoformat(),
                source="DOL",
                source_url=_URL,
                release_time="08:30:00",
            )
        )
        d += timedelta(days=7)
    return rows


def fetch_claims_rows(*, start_year: int = 2018, end_year: int = 2026) -> list[dict[str, Any]]:
    fred = fetch_fred_rows_for_type("UNEMPLOYMENT_CLAIMS", start_year=start_year, end_year=end_year)
    fallback = _thursday_claims_dates(start_year, end_year)
    return merge_calendar_rows(fred, fallback)


def fetch_all_dol_rows(*, start_year: int = 2018, end_year: int = 2026) -> dict[str, list[dict[str, Any]]]:
    return {"dol_claims.csv": fetch_claims_rows(start_year=start_year, end_year=end_year)}
