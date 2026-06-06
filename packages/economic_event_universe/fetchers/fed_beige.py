"""Federal Reserve Beige Book release calendar."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.calendar_rows import calendar_row
from economic_event_universe.fetchers.http_util import fetch_text

_BEIGE_INDEX = "https://www.federalreserve.gov/monetarypolicy/beigebook.htm"


def parse_beige_html(html: str, *, source_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    patterns = (
        r"BeigeBook_(\d{4})(\d{2})(\d{2})\.pdf",
        r"beigebook(\d{4})(\d{2})(\d{2})\.pdf",
    )
    for pat in patterns:
        for y, m, d in re.findall(pat, html, flags=re.IGNORECASE):
            rd = f"{y}-{m}-{d}"
            if rd in seen:
                continue
            seen.add(rd)
            rows.append(calendar_row("FED_BEIGE_BOOK", rd, source_url=source_url))
    return rows


def fetch_beige_rows(*, start_year: int = 2018, end_year: int = 2026) -> list[dict[str, Any]]:
    """Scrape per-year beigebook{YYYY}.htm pages (historical archive on Fed.gov)."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for year in range(start_year, end_year + 1):
        url = f"https://www.federalreserve.gov/monetarypolicy/beigebook{year}.htm"
        try:
            html = fetch_text(url)
        except Exception:
            continue
        for row in parse_beige_html(html, source_url=url):
            if row["release_date"] in seen:
                continue
            seen.add(row["release_date"])
            rows.append(row)

    # Near-term releases also appear on the rolling default page.
    for url in (
        "https://www.federalreserve.gov/monetarypolicy/beige-book-default.htm",
        "https://www.federalreserve.gov/monetarypolicy/publications/beige-book-default.htm",
    ):
        try:
            html = fetch_text(url)
        except Exception:
            continue
        for row in parse_beige_html(html, source_url=url):
            if row["release_date"] in seen:
                continue
            yr = int(row["release_date"][:4])
            if start_year <= yr <= end_year:
                seen.add(row["release_date"])
                rows.append(row)

    return sorted(rows, key=lambda r: r["release_date"])
