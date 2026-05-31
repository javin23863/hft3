"""ISM PMI release proposal fetcher."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.base import write_proposal

_URL = "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/"


def parse_ism_html(html: str) -> list[dict[str, Any]]:
    dates = sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", html)))
    rows = []
    for d in dates:
        for et in ("ISM_MANUFACTURING", "ISM_SERVICES"):
            rows.append(
                {
                    "release_date": d,
                    "event_type": et,
                    "source": "ISM",
                    "source_url": _URL,
                    "timezone": "America/New_York",
                    "release_time": "10:00:00",
                }
            )
    return rows


def propose(*, html: str | None = None, dry_run: bool = True) -> list[dict[str, Any]]:
    rows = parse_ism_html(html or "")
    if not dry_run and rows:
        write_proposal("ism", rows)
    return rows
