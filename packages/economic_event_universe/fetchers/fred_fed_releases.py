"""FRED-backed Fed release calendars (G.17 INDPRO, H.4.1)."""

from __future__ import annotations

from typing import Any

from economic_event_universe.fetchers.calendar_rows import calendar_row
from economic_event_universe.fetchers.fred_client import FRED_RELEASE_IDS, release_dates


def fetch_fred_fed_rows(
    event_type: str,
    *,
    start_year: int = 2018,
    end_year: int = 2026,
) -> list[dict[str, Any]]:
    release_id = FRED_RELEASE_IDS.get(event_type)
    if release_id is None:
        raise ValueError(f"No FRED release_id for {event_type}")
    dates = release_dates(
        release_id,
        start=f"{start_year}-01-01",
        end=f"{end_year}-12-31",
    )
    return [
        calendar_row(event_type, d)
        for d in dates
        if start_year <= int(d[:4]) <= end_year
    ]


def fetch_indpro_rows(**kwargs: int) -> list[dict[str, Any]]:
    return fetch_fred_fed_rows("INDUSTRIAL_PRODUCTION", **kwargs)


def fetch_h41_rows(**kwargs: int) -> list[dict[str, Any]]:
    return fetch_fred_fed_rows("FED_H41", **kwargs)
