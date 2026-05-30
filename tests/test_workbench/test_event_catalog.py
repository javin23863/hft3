"""Tests for campaign event catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from decision_engine.python.src.walk_forward import ValidationPeriod
from workbench.src.data.event_catalog import (
    list_campaign_events,
    load_model_binding,
    row_to_event_context,
)

REPO = Path(__file__).resolve().parents[2]


def test_row_to_event_context_cpi():
    assert row_to_event_context("CPI", "TIGHT") == "CPI_TIGHT"


def test_row_to_event_context_topstep():
    assert row_to_event_context("PROP_FLATTEN_TOPSTEP", "MAIN") == "PROP_FLATTEN_TOPSTEP"


def test_hyp_5_discovery_lists_cpi_events():
    period = ValidationPeriod("Discovery", 2018, 2020)
    events = list_campaign_events("HYP_5", period, "MES.v.0", REPO)
    ids = [e.event_id for e in events]
    assert "CPI_2018_01_11_TIGHT" in ids
    for e in events:
        if e.event_id.startswith("CPI_"):
            assert e.event_context == "CPI_TIGHT"
        elif e.event_id.startswith("NFP_"):
            assert e.event_context == "NFP_TIGHT"


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


def test_pdf_model_5_options_lane():
    binding = load_model_binding(REPO, "PDF_MODEL_5")
    assert binding["campaign_mode"] == "options_lane"
    assert "options_chain" in binding["required_datasets"]
