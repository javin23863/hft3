"""NAR existing home sales."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.calendar_merge import merge_calendar_rows
from economic_event_universe.fetchers.calendar_rows import calendar_row
from economic_event_universe.fetchers.fred_macro_releases import fetch_fred_rows_for_type
from economic_event_universe.fetchers.http_util import fetch_text

_URL = "https://www.nar.realtor/research-and-statistics"


def parse_nar_html(html: str) -> list[dict[str, Any]]:
    dates = sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", html)))
    return [
        calendar_row(
            "EXISTING_HOME_SALES",
            d,
            source="NAR",
            source_url=_URL,
            release_time="10:00:00",
        )
        for d in dates
    ]


def fetch_all_nar_rows(*, start_year: int = 2018, end_year: int = 2026) -> dict[str, list[dict[str, Any]]]:
    agency: list[dict] = []
    try:
        agency = parse_nar_html(fetch_text(_URL))
    except Exception:
        pass
    fred = fetch_fred_rows_for_type("EXISTING_HOME_SALES", start_year=start_year, end_year=end_year)
    rows = merge_calendar_rows(fred, agency)
    filtered = [r for r in rows if start_year <= int(r["release_date"][:4]) <= end_year]
    return {"nar_existing_home.csv": filtered}
