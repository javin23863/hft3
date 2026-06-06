"""Smoke tests for sync_all_calendars with mocked fetchers."""

from __future__ import annotations

from unittest.mock import patch

from economic_event_universe.fetchers.bls import fetch_all_bls_rows
from economic_event_universe.fetchers.schedule_fallback import ism_fallback_rows


def test_ism_fallback_produces_both_types():
    rows = ism_fallback_rows(start_year=2024, end_year=2024)
    types = {r["event_type"] for r in rows}
    assert "ISM_MANUFACTURING" in types
    assert "ISM_SERVICES" in types
    assert len(rows) == 24


def test_bls_fetch_returns_cpi_rows():
    fake_cpi = [
        {
            "release_date": "2024-01-11",
            "event_type": "CPI",
            "source": "BLS",
            "source_url": "https://www.bls.gov/schedule/news_release/cpi.htm",
            "timezone": "America/New_York",
            "release_time": "08:30:00",
        }
    ]
    with patch(
        "economic_event_universe.fetchers.bls.fetch_bls_rows_for_type",
        side_effect=lambda event_type, **kw: fake_cpi if event_type == "CPI" else [],
    ):
        out = fetch_all_bls_rows(start_year=2024, end_year=2024)
    assert "bls_cpi.csv" in out
    assert any(r["event_type"] == "CORE_CPI" for r in out["bls_cpi.csv"])
