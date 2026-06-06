"""Census release schedule fetcher — per-series pages + FRED bootstrap."""

from __future__ import annotations

import re
from typing import Any

from economic_event_universe.fetchers.base import write_proposal
from economic_event_universe.fetchers.calendar_merge import merge_calendar_rows
from economic_event_universe.fetchers.calendar_rows import (
    calendar_row,
    derive_building_permits_from_housing,
)
from economic_event_universe.fetchers.fred_macro_releases import fetch_fred_rows_for_type
from economic_event_universe.fetchers.http_util import fetch_text

_CENSUS_PAGES: dict[str, tuple[str, str, str]] = {
    "RETAIL_SALES": (
        "https://www.census.gov/retail/marts.html",
        "08:30:00",
        "census_retail.csv",
    ),
    "DURABLE_GOODS_ADVANCE": (
        "https://www.census.gov/economic-indicators/",
        "08:30:00",
        "census_durable.csv",
    ),
    "DURABLE_GOODS_FULL": (
        "https://www.census.gov/economic-indicators/",
        "10:00:00",
        "census_durable.csv",
    ),
    "HOUSING_STARTS": (
        "https://www.census.gov/construction/nrc/index.html",
        "08:30:00",
        "census_housing.csv",
    ),
    "BUILDING_PERMITS": (
        "https://www.census.gov/construction/bps/index.html",
        "08:30:00",
        "census_housing.csv",
    ),
    "NEW_HOME_SALES": (
        "https://www.census.gov/construction/nrs/index.html",
        "10:00:00",
        "census_nhs.csv",
    ),
    "CONSTRUCTION_SPENDING": (
        "https://www.census.gov/construction/c30/index.html",
        "10:00:00",
        "census_c30.csv",
    ),
    "TRADE_BALANCE": (
        "https://www.census.gov/foreign-trade/Press-Release/current_press_release/index.html",
        "08:30:00",
        "census_trade.csv",
    ),
    "FACTORY_ORDERS": (
        "https://www.census.gov/manufacturing/m3/index.html",
        "10:00:00",
        "census_factory_orders.csv",
    ),
}


def parse_census_page(html: str, event_type: str) -> list[dict[str, Any]]:
    if event_type not in _CENSUS_PAGES:
        return []
    url, rt, _ = _CENSUS_PAGES[event_type]
    dates = sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", html)))
    return [
        calendar_row(event_type, d, source="Census", source_url=url, release_time=rt) for d in dates
    ]


def fetch_census_rows_for_type(
    event_type: str,
    *,
    start_year: int = 2018,
    end_year: int = 2026,
) -> list[dict[str, Any]]:
    if event_type not in _CENSUS_PAGES:
        return []
    url, _, _ = _CENSUS_PAGES[event_type]
    agency: list[dict] = []
    try:
        agency = parse_census_page(fetch_text(url), event_type)
    except Exception:
        pass
    fred = fetch_fred_rows_for_type(event_type, start_year=start_year, end_year=end_year)
    rows = merge_calendar_rows(fred, agency)
    return [r for r in rows if start_year <= int(r["release_date"][:4]) <= end_year]


def fetch_all_census_rows(*, start_year: int = 2018, end_year: int = 2026) -> dict[str, list[dict[str, Any]]]:
    by_file: dict[str, list[dict]] = {}
    for et, (_, _, fname) in _CENSUS_PAGES.items():
        by_file.setdefault(fname, []).extend(
            fetch_census_rows_for_type(et, start_year=start_year, end_year=end_year)
        )
    housing = by_file.get("census_housing.csv", [])
    if not any(r["event_type"] == "BUILDING_PERMITS" for r in housing):
        housing.extend(derive_building_permits_from_housing(housing))
        by_file["census_housing.csv"] = merge_calendar_rows(housing)
    return {k: merge_calendar_rows(v) for k, v in by_file.items()}


def parse_census_html(html: str) -> list[dict[str, Any]]:
    rows: list[dict] = []
    for et in _CENSUS_PAGES:
        rows.extend(parse_census_page(html, et))
    return rows


def propose(*, html: str | None = None, dry_run: bool = True) -> list[dict[str, Any]]:
    rows = parse_census_html(html or "")
    if not dry_run and rows:
        write_proposal("census", rows)
    return rows
