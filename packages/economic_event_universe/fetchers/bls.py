"""BLS schedule fetcher — live HTML + FRED bootstrap."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.base import write_proposal
from economic_event_universe.fetchers.calendar_merge import merge_calendar_rows
from economic_event_universe.fetchers.calendar_rows import (
    calendar_row,
    derive_core_from_parent,
)
from economic_event_universe.fetchers.fred_macro_releases import fetch_fred_rows_for_type
from economic_event_universe.fetchers.http_util import fetch_text

_SERIES: dict[str, tuple[str, str]] = {
    "CPI": ("https://www.bls.gov/schedule/news_release/cpi.htm", "08:30:00"),
    "NFP": ("https://www.bls.gov/schedule/news_release/empsit.htm", "08:30:00"),
    "PPI": ("https://www.bls.gov/schedule/news_release/ppi.htm", "08:30:00"),
    "JOLTS": ("https://www.bls.gov/schedule/news_release/jolts.htm", "10:00:00"),
    "PRODUCTIVITY": ("https://www.bls.gov/schedule/news_release/prod2.htm", "08:30:00"),
    "ECI": ("https://www.bls.gov/schedule/news_release/eci.htm", "08:30:00"),
    "IMPORT_PRICES": ("https://www.bls.gov/schedule/news_release/ximpim.htm", "08:30:00"),
    "EXPORT_PRICES": ("https://www.bls.gov/schedule/news_release/ximpim.htm", "08:30:00"),
}


def parse_bls_html(html: str, event_type: str) -> list[dict[str, Any]]:
    if event_type not in _SERIES:
        return []
    url, rt = _SERIES[event_type]
    dates = sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", html)))
    if not dates:
        return []
    return [calendar_row(event_type, d, source="BLS", source_url=url, release_time=rt) for d in dates]


def fetch_bls_rows_for_type(
    event_type: str,
    *,
    start_year: int = 2018,
    end_year: int = 2026,
) -> list[dict[str, Any]]:
    if event_type not in _SERIES:
        return []
    url, _ = _SERIES[event_type]
    agency: list[dict[str, Any]] = []
    try:
        html = fetch_text(url)
        agency = parse_bls_html(html, event_type)
    except Exception:
        pass
    fred = fetch_fred_rows_for_type(event_type, start_year=start_year, end_year=end_year)
    rows = merge_calendar_rows(fred, agency)
    return [r for r in rows if start_year <= int(r["release_date"][:4]) <= end_year]


def fetch_all_bls_rows(*, start_year: int = 2018, end_year: int = 2026) -> dict[str, list[dict[str, Any]]]:
    """Return rows grouped by output CSV filename."""
    by_file: dict[str, list[dict[str, Any]]] = {}
    cpi = fetch_bls_rows_for_type("CPI", start_year=start_year, end_year=end_year)
    cpi.extend(derive_core_from_parent(cpi, "CPI", "CORE_CPI"))
    by_file["bls_cpi.csv"] = cpi

    nfp = fetch_bls_rows_for_type("NFP", start_year=start_year, end_year=end_year)
    by_file["bls_nfp.csv"] = nfp

    ppi = fetch_bls_rows_for_type("PPI", start_year=start_year, end_year=end_year)
    ppi.extend(derive_core_from_parent(ppi, "PPI", "CORE_PPI"))
    by_file["bls_ppi.csv"] = ppi

    by_file["bls_jolts.csv"] = fetch_bls_rows_for_type("JOLTS", start_year=start_year, end_year=end_year)
    by_file["bls_productivity.csv"] = fetch_bls_rows_for_type(
        "PRODUCTIVITY", start_year=start_year, end_year=end_year
    )
    by_file["bls_eci.csv"] = fetch_bls_rows_for_type("ECI", start_year=start_year, end_year=end_year)

    imp = fetch_bls_rows_for_type("IMPORT_PRICES", start_year=start_year, end_year=end_year)
    exp = fetch_bls_rows_for_type("EXPORT_PRICES", start_year=start_year, end_year=end_year)
    by_file["bls_trade_prices.csv"] = merge_calendar_rows(imp, exp)

    return by_file


def propose(*, html: str | None = None, event_type: str = "CPI", dry_run: bool = True) -> list[dict[str, Any]]:
    rows = parse_bls_html(html or "", event_type)
    if not dry_run and rows:
        write_proposal("bls", rows)
    return rows
