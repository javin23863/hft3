from pathlib import Path

import pytest

from economic_event_universe.service import (
    inventory,
    list_calendar_rows,
    list_event_types,
    list_runnable_events,
    snapshot_artifacts_for_event,
)


REPO = Path(__file__).resolve().parents[2]


def test_unified_event_universe_exposes_all_catalog_types():
    rows = list_event_types()
    types = {row["event_type"] for row in rows}
    assert len(types) == 45
    for event_type in {
        "FOMC_STATEMENT",
        "FOMC_PRESS",
        "PCE",
        "PPI",
        "GDP_ADVANCE",
        "UNEMPLOYMENT_CLAIMS",
        "ISM_MANUFACTURING",
        "TREASURY_AUCTION",
        "EIA_CRUDE",
    }:
        assert event_type in types


def test_seed_rows_are_visible_but_not_runnable():
    calendar_rows = list_calendar_rows(REPO, include_seed=True)
    seed_types = {row["event_type"] for row in calendar_rows if row["row_status"] == "SEED"}
    runnable_types = {row["event_type"] for row in list_runnable_events(REPO)}
    assert "FOMC_STATEMENT" in seed_types
    assert "PCE" in seed_types
    assert "FOMC_STATEMENT" not in runnable_types
    assert "PCE" not in runnable_types
    assert {"CPI", "NFP", "PROP_FLATTEN_TOPSTEP"}.issubset(runnable_types)


def test_inventory_reports_defined_and_runnable_counts():
    data = inventory(REPO)
    assert data["event_type_count"] == 45
    assert data["runnable_event_count"] == 55
    assert data["row_status_counts"]["SOURCED"] == 55
    assert data["row_status_counts"]["SEED"] > 0


def test_snapshot_artifact_lookup_is_event_id_agnostic():
    artifacts = snapshot_artifacts_for_event("CPI_2024_09_11_TIGHT", REPO)
    assert all(row["event_id"] == "CPI_2024_09_11_TIGHT" for row in artifacts)


def test_seed_row_in_sourced_dir_fails_loudly(tmp_path):
    sourced = tmp_path / "packages" / "economic_event_universe" / "config" / "calendars" / "sourced"
    sourced.mkdir(parents=True)
    (sourced / "bad_seed.csv").write_text(
        "release_date,event_type,source,source_url,timezone,release_time,row_status\n"
        "2024-06-12,FOMC_STATEMENT,Fed,https://fed.gov,America/New_York,14:00:00,SEED\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="SEED row belongs"):
        list_calendar_rows(tmp_path, include_seed=True)
