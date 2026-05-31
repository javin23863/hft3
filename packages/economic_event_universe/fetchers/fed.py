"""Federal Reserve calendar proposal fetcher."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.base import write_proposal

_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"


def parse_fed_html(html: str) -> list[dict[str, Any]]:
    dates = sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", html)))
    rows = []
    for d in dates:
        rows.extend(
            [
                {
                    "release_date": d,
                    "event_type": "FOMC_STATEMENT",
                    "source": "Fed",
                    "source_url": _URL,
                    "timezone": "America/New_York",
                    "release_time": "14:00:00",
                },
                {
                    "release_date": d,
                    "event_type": "FOMC_PRESS",
                    "source": "Fed",
                    "source_url": _URL,
                    "timezone": "America/New_York",
                    "release_time": "14:30:00",
                },
            ]
        )
    return rows


def propose(*, html: str | None = None, dry_run: bool = True) -> list[dict[str, Any]]:
    rows = parse_fed_html(html or "")
    if not dry_run and rows:
        write_proposal("fed", rows)
    return rows
