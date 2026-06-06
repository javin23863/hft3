"""ADP employment report — FRED/NFP-adjacent monthly pattern."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.bls import fetch_bls_rows_for_type
from economic_event_universe.fetchers.calendar_rows import calendar_row
from economic_event_universe.fetchers.http_util import fetch_text
from economic_event_universe.fetchers.schedule_fallback import adp_from_nfp_rows

_URL = "https://adpemploymentreport.com/"


def parse_adp_html(html: str) -> list[dict[str, Any]]:
    dates = sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", html)))
    return [
        calendar_row("ADP_EMPLOYMENT", d, source="ADP", source_url=_URL, release_time="08:15:00")
        for d in dates
    ]


def fetch_all_adp_rows(*, start_year: int = 2018, end_year: int = 2026) -> dict[str, list[dict[str, Any]]]:
    rows: list[dict] = []
    try:
        rows = parse_adp_html(fetch_text(_URL))
    except Exception:
        pass
    if not rows:
        nfp = fetch_bls_rows_for_type("NFP", start_year=start_year, end_year=end_year)
        rows = adp_from_nfp_rows(nfp)
    filtered = [r for r in rows if start_year <= int(r["release_date"][:4]) <= end_year]
    return {"adp_employment.csv": filtered}
