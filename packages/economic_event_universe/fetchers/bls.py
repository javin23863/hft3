"""BLS schedule proposal fetcher (one series per call — no date cross-product)."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.base import write_proposal

_SERIES = {
    "CPI": ("https://www.bls.gov/schedule/news_release/cpi.htm", "08:30:00"),
    "NFP": ("https://www.bls.gov/schedule/news_release/empsit.htm", "08:30:00"),
    "PPI": ("https://www.bls.gov/schedule/news_release/ppi.htm", "08:30:00"),
    "JOLTS": ("https://www.bls.gov/schedule/news_release/jolts.htm", "10:00:00"),
}


def parse_bls_html(html: str, event_type: str) -> list[dict[str, Any]]:
    if event_type not in _SERIES:
        return []
    if event_type not in html and event_type.lower() not in html.lower():
        return []
    url, rt = _SERIES[event_type]
    dates = sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", html)))
    return [
        {
            "release_date": d,
            "event_type": event_type,
            "source": "BLS",
            "source_url": url,
            "timezone": "America/New_York",
            "release_time": rt,
        }
        for d in dates
    ]


def propose(*, html: str | None = None, event_type: str = "CPI", dry_run: bool = True) -> list[dict[str, Any]]:
    rows = parse_bls_html(html or "", event_type)
    if not dry_run and rows:
        write_proposal("bls", rows)
    return rows
