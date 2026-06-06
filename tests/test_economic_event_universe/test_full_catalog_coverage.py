"""All 45 catalog types must appear in events.csv after full sync build."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def events_types() -> set[str]:
    import csv

    path = REPO / "packages" / "data_system" / "config" / "events.csv"
    with path.open(newline="", encoding="utf-8") as f:
        return {str(row["event_type"]) for row in csv.DictReader(f)}


def test_all_45_types_in_events_csv(events_types: set[str]) -> None:
    from economic_event_universe.registry import catalog_event_types

    all_types = set(catalog_event_types())
    missing = sorted(all_types - events_types)
    assert not missing, f"missing from events.csv: {missing}"


def test_sourced_calendar_type_count(events_types: set[str]) -> None:
    from economic_event_universe.calendar_io import sourced_event_types_in_dir
    from economic_event_universe.registry import catalog_event_types

    cal_dir = REPO / "packages" / "data_system" / "config" / "release_calendars"
    sourced = sourced_event_types_in_dir(cal_dir)
    rule_based = {
        et
        for et, cfg in __import__(
            "economic_event_universe.registry", fromlist=["event_definitions"]
        ).event_definitions().items()
        if cfg.get("schedule") == "rule_based"
    }
    assert len(sourced) + len(rule_based) >= len(catalog_event_types()) - 0
    assert rule_based.issubset(events_types)
