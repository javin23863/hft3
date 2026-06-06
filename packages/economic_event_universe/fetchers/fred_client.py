"""FRED API client for macro release calendar dates."""

from __future__ import annotations

from urllib.parse import urlencode

from economic_event_universe.fetchers.env import fred_api_key
from economic_event_universe.fetchers.http_util import fetch_json

FRED_BASE = "https://api.stlouisfed.org/fred"

# release_id from FRED /releases catalog
FRED_RELEASE_IDS: dict[str, int] = {
    "INDUSTRIAL_PRODUCTION": 13,  # G.17 Industrial Production and Capacity Utilization
    "FED_H41": 20,  # H.4.1 Factors Affecting Reserve Balances
}


def _get(path: str, **params: str | int) -> dict:
    key = fred_api_key()
    if not key:
        raise RuntimeError("FRED_API_KEY missing; set in hft3/.env or ~/Desktop/keys.env")
    q = {"api_key": key, "file_type": "json", **params}
    url = f"{FRED_BASE}{path}?{urlencode(q)}"
    return fetch_json(url)


def release_dates(
    release_id: int,
    *,
    start: str = "2018-01-01",
    end: str = "2026-12-31",
    limit: int = 1000,
) -> list[str]:
    """Return YYYY-MM-DD release dates for a FRED release_id."""
    out: list[str] = []
    offset = 0
    while True:
        data = _get(
            "/release/dates",
            release_id=release_id,
            realtime_start=start,
            realtime_end=end,
            limit=limit,
            offset=offset,
            sort_order="asc",
        )
        batch = data.get("release_dates") or []
        if not batch:
            break
        for row in batch:
            d = str(row.get("date", ""))[:10]
            if d:
                out.append(d)
        if len(batch) < limit:
            break
        offset += len(batch)
    return sorted(set(out))
