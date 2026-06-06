"""Tests for campaign event catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from decision_engine.python.src.walk_forward import ValidationPeriod
from workbench.src.data.event_catalog import (
    list_campaign_events,
    list_universe_events,
    load_periods,
    load_model_binding,
    row_to_event_context,
)
from workbench.src.run.evidence_snapshot import load_run_evidence

REPO = Path(__file__).resolve().parents[2]


def test_row_to_event_context_cpi():
    assert row_to_event_context("CPI", "TIGHT") == "CPI_TIGHT"


def test_row_to_event_context_topstep():
    assert row_to_event_context("PROP_FLATTEN_TOPSTEP", "MAIN") == "PROP_FLATTEN_TOPSTEP"


def test_hyp_5_discovery_lists_cpi_events():
    period = ValidationPeriod("Discovery", 2018, 2020)
    events = list_campaign_events("HYP_5", period, "MES.v.0", REPO)
    ids = [e.event_id for e in events]
    contexts = {e.event_context for e in events}
    assert "CPI_2018_01_11_TIGHT" in ids
    assert contexts <= {"CPI_TIGHT", "NFP_TIGHT"}
    assert {"CPI_TIGHT", "NFP_TIGHT"}.issubset(contexts)
    for e in events:
        if e.event_id.startswith("CPI_"):
            assert e.event_context == "CPI_TIGHT"
        elif e.event_id.startswith("NFP_"):
            assert e.event_context == "NFP_TIGHT"


def test_hyp_5_campaign_contexts_are_macro_only():
    allowed = {"CPI_TIGHT", "NFP_TIGHT"}
    for period in load_periods(REPO):
        events = list_campaign_events("HYP_5", period, "MES.v.0", REPO)
        assert events, period.name
        assert {e.event_context for e in events} <= allowed


def test_hyp_29_does_not_list_cpi():
    period = ValidationPeriod("Holdout", 2023, 2024)
    events = list_campaign_events("HYP_29", period, "MES.v.0", REPO)
    assert all("CPI" not in e.event_id for e in events)
    if events:
        assert events[0].event_id.startswith("PROP_FLATTEN_TOPSTEP")
        assert events[0].event_context == "PROP_FLATTEN_TOPSTEP"


def test_period_year_filter_excludes_wrong_years():
    period = ValidationPeriod("Discovery", 2018, 2020)
    events = list_campaign_events("HYP_5", period, "MES.v.0", REPO)
    for e in events:
        year = int(e.release_date.split("-")[0])
        assert 2018 <= year <= 2020


def test_full_universe_view_is_not_limited_to_generated_campaign_csv():
    period = ValidationPeriod("Discovery", 2018, 2020)
    events = list_universe_events("HYP_5", period, "MES.v.0", REPO)
    types = {e.event_type for e in events}
    statuses = {e.row_status for e in events}

    assert {"SEED", "SOURCED"}.issubset(statuses)
    assert {
        "FOMC_STATEMENT",
        "PCE",
        "PPI",
        "GDP_ADVANCE",
        "TREASURY_AUCTION",
        "UNEMPLOYMENT_CLAIMS",
    }.issubset(types)
    assert {"CPI", "NFP"}.issubset(types)
    assert len(types) > 20


def test_pdf_model_5_options_lane():
    binding = load_model_binding(REPO, "PDF_MODEL_5")
    assert binding["campaign_mode"] == "options_lane"
    assert "options_chain" in binding["required_datasets"]


def test_workbench_snapshot_exposes_full_event_universe():
    snapshot = load_run_evidence(REPO, "all_lanes")
    universe = snapshot.registry.get("event_universe") or {}
    assert universe["event_type_count"] == 45
    types = {row["event_type"] for row in universe["event_types"]}
    assert {"FOMC_STATEMENT", "PCE", "PPI", "GDP_ADVANCE", "TREASURY_AUCTION"}.issubset(types)
    runnable_types = {row["event_type"] for row in universe["runnable_events"]}
    assert {"CPI", "NFP", "PROP_FLATTEN_TOPSTEP"}.issubset(runnable_types)
    assert "FOMC_STATEMENT" not in runnable_types
