"""Census release schedule proposal fetcher."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.base import write_proposal

_URL = "https://www.census.gov/economic-indicators/"


def parse_census_html(html: str) -> list[dict[str, Any]]:
    dates = sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", html)))
    rows = []
    for d in dates:
        for et, rt in (
            ("RETAIL_SALES", "08:30:00"),
            ("DURABLE_GOODS_ADVANCE", "08:30:00"),
            ("DURABLE_GOODS_FULL", "10:00:00"),
            ("HOUSING_STARTS", "08:30:00"),
            ("TRADE_BALANCE", "08:30:00"),
        ):
            rows.append(
                {
                    "release_date": d,
                    "event_type": et,
                    "source": "Census",
                    "source_url": _URL,
                    "timezone": "America/New_York",
                    "release_time": rt,
                }
            )
    return rows


def propose(*, html: str | None = None, dry_run: bool = True) -> list[dict[str, Any]]:
    rows = parse_census_html(html or "")
    if not dry_run and rows:
        write_proposal("census", rows)
    return rows
