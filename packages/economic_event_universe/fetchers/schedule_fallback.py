"""Schedule-based fallback rows when agency HTML and FRED both return empty."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from economic_event_universe.fetchers.calendar_rows import calendar_row


def _first_business_day(year: int, month: int) -> date:
    d = date(year, month, 1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _third_business_day(year: int, month: int) -> date:
    d = date(year, month, 1)
    count = 0
    while d.month == month:
        if d.weekday() < 5:
            count += 1
            if count == 3:
                return d
        d += timedelta(days=1)
    return date(year, month, 5)


def ism_fallback_rows(*, start_year: int, end_year: int) -> list[dict[str, Any]]:
    rows: list[dict] = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            mfg = _first_business_day(y, m)
            svc = _third_business_day(y, m)
            rows.append(
                calendar_row(
                    "ISM_MANUFACTURING",
                    mfg.isoformat(),
                    source="ISM",
                    source_url="https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/pmi/",
                    release_time="10:00:00",
                )
            )
            rows.append(
                calendar_row(
                    "ISM_SERVICES",
                    svc.isoformat(),
                    source="ISM",
                    source_url="https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/services/",
                    release_time="10:00:00",
                )
            )
    return rows


def adp_from_nfp_rows(nfp_rows: list[dict], *, lag_business_days: int = 2) -> list[dict[str, Any]]:
    """ADP typically releases ~2 business days before NFP."""
    out: list[dict] = []
    for row in nfp_rows:
        if row["event_type"] != "NFP":
            continue
        d = date.fromisoformat(row["release_date"])
        lag = 0
        while lag < lag_business_days:
            d -= timedelta(days=1)
            if d.weekday() < 5:
                lag += 1
        out.append(
            calendar_row(
                "ADP_EMPLOYMENT",
                d.isoformat(),
                source="ADP",
                source_url="https://adpemploymentreport.com/",
                release_time="08:15:00",
            )
        )
    return out
