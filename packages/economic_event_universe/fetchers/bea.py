"""BEA release schedule fetcher — title-aware parsing + FRED bootstrap."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.base import write_proposal
from economic_event_universe.fetchers.calendar_merge import merge_calendar_rows
from economic_event_universe.fetchers.calendar_rows import calendar_row
from economic_event_universe.fetchers.fred_macro_releases import fetch_fred_rows_for_type
from economic_event_universe.fetchers.http_util import fetch_text

_URL = "https://www.bea.gov/news/schedule"


def parse_bea_html(html: str) -> list[dict[str, Any]]:
    """Match release titles to event types instead of cross-producting all dates."""
    rows: list[dict[str, Any]] = []
    # Table rows often: date + title text nearby
    for m in re.finditer(
        r"(\d{4}-\d{2}-\d{2})[^<]{0,200}?(Personal Income|Gross Domestic Product|PCE|GDP)",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        date, title = m.group(1), m.group(2).lower()
        block = html[max(0, m.start() - 50) : m.end() + 200].lower()
        if "personal income" in block or "pce" in block:
            if "core" in block:
                rows.append(calendar_row("CORE_PCE", date, source="BEA", source_url=_URL))
            else:
                rows.append(calendar_row("PCE", date, source="BEA", source_url=_URL))
        if "gross domestic product" in block or "gdp" in block:
            if "second" in block:
                rows.append(calendar_row("GDP_SECOND", date, source="BEA", source_url=_URL))
            elif "third" in block or "final" in block:
                rows.append(calendar_row("GDP_FINAL", date, source="BEA", source_url=_URL))
            elif "advance" in block or "first" in block:
                rows.append(calendar_row("GDP_ADVANCE", date, source="BEA", source_url=_URL))
            else:
                rows.append(calendar_row("GDP_ADVANCE", date, source="BEA", source_url=_URL))
    # Fallback: any ISO dates on page → PCE only if nothing matched
    if not rows:
        for d in sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", html))):
            rows.append(calendar_row("PCE", d, source="BEA", source_url=_URL))
    return rows


def _merge_type(event_type: str, agency: list[dict], *, start_year: int, end_year: int) -> list[dict]:
    fred = fetch_fred_rows_for_type(event_type, start_year=start_year, end_year=end_year)
    type_agency = [r for r in agency if r["event_type"] == event_type]
    merged = merge_calendar_rows(fred, type_agency)
    return [r for r in merged if start_year <= int(r["release_date"][:4]) <= end_year]


def fetch_all_bea_rows(*, start_year: int = 2018, end_year: int = 2026) -> dict[str, list[dict[str, Any]]]:
    agency: list[dict[str, Any]] = []
    try:
        agency = parse_bea_html(fetch_text(_URL))
    except Exception:
        pass
    pce_types = ("PCE", "CORE_PCE")
    gdp_types = ("GDP_ADVANCE", "GDP_SECOND", "GDP_FINAL")
    pce_rows: list[dict] = []
    gdp_rows: list[dict] = []
    for et in pce_types:
        pce_rows.extend(_merge_type(et, agency, start_year=start_year, end_year=end_year))
    for et in gdp_types:
        gdp_rows.extend(_merge_type(et, agency, start_year=start_year, end_year=end_year))
    return {
        "bea_pce.csv": merge_calendar_rows(pce_rows),
        "bea_gdp.csv": merge_calendar_rows(gdp_rows),
    }


def propose(*, html: str | None = None, dry_run: bool = True) -> list[dict[str, Any]]:
    rows = parse_bea_html(html or "")
    if not dry_run and rows:
        write_proposal("bea", rows)
    return rows
