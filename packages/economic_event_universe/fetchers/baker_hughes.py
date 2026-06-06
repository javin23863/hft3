"""Baker Hughes rig count — weekly Friday releases."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from economic_event_universe.fetchers.calendar_rows import calendar_row

_URL = "https://rigcount.bakerhughes.com/"


def fetch_all_baker_hughes_rows(
    *, start_year: int = 2018, end_year: int = 2026
) -> dict[str, list[dict[str, Any]]]:
    rows: list[dict] = []
    d = date(start_year, 1, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    end = date(end_year, 12, 31)
    while d <= end:
        rows.append(
            calendar_row(
                "BAKER_HUGHES_RIG",
                d.isoformat(),
                source="BakerHughes",
                source_url=_URL,
                release_time="13:00:00",
            )
        )
        d += timedelta(days=7)
    return {"baker_hughes_rig.csv": rows}
