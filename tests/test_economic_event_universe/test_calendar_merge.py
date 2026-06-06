"""Tests for calendar merge and co-release derivations."""

from __future__ import annotations

from economic_event_universe.fetchers.calendar_merge import merge_calendar_rows
from economic_event_universe.fetchers.calendar_rows import (
    calendar_row,
    derive_core_from_parent,
)


def test_merge_agency_wins_over_fred():
    fred = [calendar_row("CPI", "2024-01-11", source="FRED")]
    agency = [calendar_row("CPI", "2024-01-11", source="BLS")]
    merged = merge_calendar_rows(fred, agency)
    assert len(merged) == 1
    assert merged[0]["source"] == "BLS"


def test_derive_core_cpi_from_cpi():
    cpi = [calendar_row("CPI", "2024-01-11", source="BLS")]
    core = derive_core_from_parent(cpi, "CPI", "CORE_CPI")
    assert len(core) == 1
    assert core[0]["event_type"] == "CORE_CPI"
    assert core[0]["release_date"] == "2024-01-11"
