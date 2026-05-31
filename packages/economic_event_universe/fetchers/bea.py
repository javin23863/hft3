"""BEA release schedule proposal fetcher."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.base import write_proposal

_URL = "https://www.bea.gov/news/schedule"


def parse_bea_html(html: str) -> list[dict[str, Any]]:
    dates = sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", html)))
    rows = []
    for d in dates:
        for et in ("PCE", "CORE_PCE", "GDP_ADVANCE", "GDP_SECOND", "GDP_FINAL"):
            rows.append(
                {
                    "release_date": d,
                    "event_type": et,
                    "source": "BEA",
                    "source_url": _URL,
                    "timezone": "America/New_York",
                    "release_time": "08:30:00",
                }
            )
    return rows


def propose(*, html: str | None = None, dry_run: bool = True) -> list[dict[str, Any]]:
    rows = parse_bea_html(html or "")
    if not dry_run and rows:
        write_proposal("bea", rows)
    return rows
