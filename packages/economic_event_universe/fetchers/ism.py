"""ISM PMI release fetcher — manufacturing vs services split."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.base import write_proposal
from economic_event_universe.fetchers.calendar_rows import calendar_row
from economic_event_universe.fetchers.http_util import fetch_text
from economic_event_universe.fetchers.schedule_fallback import ism_fallback_rows

_MFG_URL = "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/pmi/"
_SVC_URL = "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/services/"


def parse_ism_page(html: str, event_type: str, url: str) -> list[dict[str, Any]]:
    dates = sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", html)))
    return [
        calendar_row(event_type, d, source="ISM", source_url=url, release_time="10:00:00") for d in dates
    ]


def fetch_all_ism_rows(*, start_year: int = 2018, end_year: int = 2026) -> dict[str, list[dict[str, Any]]]:
    rows: list[dict] = []
    for et, url in (
        ("ISM_MANUFACTURING", _MFG_URL),
        ("ISM_SERVICES", _SVC_URL),
    ):
        try:
            rows.extend(parse_ism_page(fetch_text(url), et, url))
        except Exception:
            pass
    if not rows:
        rows = ism_fallback_rows(start_year=start_year, end_year=end_year)
    filtered = [r for r in rows if start_year <= int(r["release_date"][:4]) <= end_year]
    return {"ism_pmi.csv": filtered}


def parse_ism_html(html: str) -> list[dict[str, Any]]:
    rows = parse_ism_page(html, "ISM_MANUFACTURING", _MFG_URL)
    rows.extend(parse_ism_page(html, "ISM_SERVICES", _SVC_URL))
    return rows


def propose(*, html: str | None = None, dry_run: bool = True) -> list[dict[str, Any]]:
    rows = parse_ism_html(html or "")
    if not dry_run and rows:
        write_proposal("ism", rows)
    return rows
