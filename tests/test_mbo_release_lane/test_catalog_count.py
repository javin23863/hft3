"""Catalog size must not conflate with events.csv."""

from __future__ import annotations

from pathlib import Path

from economic_event_universe.catalog_report import events_csv_summary
from economic_event_universe.registry import catalog_event_type_count
from hft3_bootstrap import repo_root


def test_catalog_types_exceed_events_csv_types():
    root = repo_root()
    _, csv_types, _ = events_csv_summary(root)
    catalog_n = catalog_event_type_count()
    assert catalog_n >= 44
    assert csv_types <= catalog_n
    assert csv_types == 3  # research-ready subset today
