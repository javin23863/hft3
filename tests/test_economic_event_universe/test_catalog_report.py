"""Tests for 44-type macro catalog source of truth."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_catalog_has_full_event_type_set():
    from economic_event_universe.registry import catalog_event_type_count, catalog_event_types

    types = catalog_event_types()
    n = catalog_event_type_count()
    assert n == len(types)
    assert n >= 44
    assert "CPI" in types
    assert "NFP" in types
    assert "EIA_CRUDE" in types
    assert "FRIDAY_CLOSE" in types


def test_catalog_banner_lists_sourced_calendars():
    from economic_event_universe.catalog_report import format_catalog_banner
    from economic_event_universe.registry import catalog_event_type_count

    text = format_catalog_banner(REPO)
    assert f"{catalog_event_type_count()} event types" in text
    assert "SOURCED release calendars" in text
    assert "sync_all_calendars.py" in text


def test_events_csv_covers_full_catalog():
    from economic_event_universe.catalog_report import events_csv_summary
    from economic_event_universe.registry import catalog_event_types

    rows, type_count, csv_types = events_csv_summary(REPO)
    assert rows >= 1000
    assert type_count == len(catalog_event_types())
    assert csv_types == set(catalog_event_types())


def test_window_catalog_covers_most_types_with_seed():
    from economic_event_universe.registry import catalog_event_types
    from economic_event_universe.window_catalog import count_windows_by_type, iter_catalog_windows

    windows = iter_catalog_windows(REPO, include_seed=True, include_rule_based=False)
    by_type = count_windows_by_type(windows)
    covered = sum(1 for et in catalog_event_types() if by_type.get(et, 0) > 0)
    assert covered >= 35
    assert by_type.get("CPI", 0) > 0
    assert by_type.get("UNEMPLOYMENT_CLAIMS", 0) > 0
