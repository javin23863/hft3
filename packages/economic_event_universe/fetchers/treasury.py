"""Treasury auction and quarterly refunding calendars."""

from __future__ import annotations

from datetime import date
from typing import Any

from economic_event_universe.fetchers.calendar_rows import calendar_row

_AUCTION_URL = "https://www.treasurydirect.gov/auctions/"
_REFUNDING_URL = (
    "https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding"
)


def _monthly_first_business(start_year: int, end_year: int, event_type: str, url: str) -> list[dict]:
    rows: list[dict] = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            d = date(y, m, 1)
            while d.weekday() >= 5:
                d = date(y, m, d.day + 1) if d.day < 28 else d
                if d.month != m:
                    break
            if d.month == m:
                rows.append(
                    calendar_row(event_type, d.isoformat(), source="Treasury", source_url=url, release_time="13:00:00")
                )
    return rows


def _quarterly_refunding(start_year: int, end_year: int) -> list[dict]:
    rows: list[dict] = []
    for y in range(start_year, end_year + 1):
        for m, d in ((2, 5), (5, 4), (8, 3), (11, 2)):
            rows.append(
                calendar_row(
                    "TREASURY_REFUNDING",
                    date(y, m, d).isoformat(),
                    source="Treasury",
                    source_url=_REFUNDING_URL,
                    release_time="08:30:00",
                )
            )
    return rows


def fetch_all_treasury_rows(*, start_year: int = 2018, end_year: int = 2026) -> dict[str, list[dict[str, Any]]]:
    auctions = _monthly_first_business(start_year, end_year, "TREASURY_AUCTION", _AUCTION_URL)
    refunding = _quarterly_refunding(start_year, end_year)
    refunding = [r for r in refunding if start_year <= int(r["release_date"][:4]) <= end_year]
    return {
        "treasury_auctions.csv": auctions,
        "treasury_refunding.csv": refunding,
    }
