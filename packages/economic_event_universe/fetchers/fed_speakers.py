"""Federal Reserve speaker events from newsevents HTML."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.calendar_rows import calendar_row
from economic_event_universe.fetchers.http_util import fetch_text

_SPEECHES_URL = "https://www.federalreserve.gov/newsevents/speeches.htm"


def parse_speeches_html(html: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for y, m, d in re.findall(r"/newsevents/speech/\w+?(\d{4})(\d{2})(\d{2})", html):
        rd = f"{y}-{m}-{d}"
        if rd in seen:
            continue
        seen.add(rd)
        rows.append(calendar_row("FED_SPEAKER", rd, source_url=_SPEECHES_URL))
    return sorted(rows, key=lambda r: r["release_date"])


def fetch_speaker_rows(*, start_year: int = 2018, end_year: int = 2026) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for year in range(start_year, end_year + 1):
        url = f"https://www.federalreserve.gov/newsevents/speech/{year}-speeches.htm"
        try:
            html = fetch_text(url)
        except Exception:
            continue
        for row in parse_speeches_html(html):
            key = (row["event_type"], row["release_date"])
            if key in seen:
                continue
            seen.add(key)
            yr = int(row["release_date"][:4])
            if start_year <= yr <= end_year:
                rows.append(row)
    return sorted(rows, key=lambda r: r["release_date"])
