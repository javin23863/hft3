"""Federal Reserve FOMC calendar fetcher."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.base import write_proposal
from economic_event_universe.fetchers.calendar_rows import calendar_row
from economic_event_universe.fetchers.http_util import fetch_text

_FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"


def _iso(y: str, m: str, d: str) -> str:
    return f"{y}-{m}-{d}"


def parse_fomc_html(html: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(event_type: str, release_date: str) -> None:
        key = (event_type, release_date)
        if key in seen:
            return
        seen.add(key)
        rows.append(calendar_row(event_type, release_date, source_url=_FOMC_URL))

    for y, m, d in re.findall(r"monetary(\d{4})(\d{2})(\d{2})a\d+\.pdf", html):
        add("FOMC_STATEMENT", _iso(y, m, d))

    for y, m, d in re.findall(r"minutes(\d{4})(\d{2})(\d{2})\.pdf", html):
        add("FOMC_MINUTES", _iso(y, m, d))

    for block in re.split(r'<div class="row fomc-meeting"', html)[1:]:
        if "press conference" not in block.lower():
            continue
        match = re.search(r"monetary(\d{4})(\d{2})(\d{2})a\d+\.pdf", block)
        if match:
            y, m, d = match.groups()
            add("FOMC_PRESS", _iso(y, m, d))

    return sorted(rows, key=lambda r: (r["event_type"], r["release_date"]))


def fetch_fomc_rows(*, start_year: int = 2018, end_year: int = 2026) -> list[dict[str, Any]]:
    html = fetch_text(_FOMC_URL)
    rows = parse_fomc_html(html)
    out: list[dict[str, Any]] = []
    for row in rows:
        yr = int(row["release_date"][:4])
        if start_year <= yr <= end_year:
            out.append(row)
    return out


def propose(*, html: str | None = None, dry_run: bool = True) -> list[dict[str, Any]]:
    if html is None:
        html = fetch_text(_FOMC_URL)
    rows = parse_fomc_html(html)
    if not dry_run and rows:
        write_proposal("fed_fomc", rows)
    return rows
