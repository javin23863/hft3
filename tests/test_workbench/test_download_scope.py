"""Ensure catalog downloads target event windows only."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from workbench.src.data.catalog_backfill import events_for_scope
from workbench.src.data.event_catalog import list_campaign_events
from decision_engine.python.src.walk_forward import ValidationPeriod

REPO = Path(__file__).resolve().parents[2]


def test_event_window_span_under_one_hour():
    period = ValidationPeriod("Discovery", 2018, 2020)
    events = list_campaign_events("HYP_5", period, "MES.v.0", REPO)
    assert events
    for ev in events:
        delta = ev.end_utc - ev.start_utc
        assert delta <= timedelta(hours=1)


def test_full_universe_download_scope_lists_seed_catalog_rows():
    full_rows = events_for_scope(REPO, "HYP_5", "MES.v.0", universe_scope="full_universe")
    campaign_rows = events_for_scope(REPO, "HYP_5", "MES.v.0", universe_scope="promotion_campaign")

    full_types = {ev.event_type for _, ev in full_rows}
    campaign_types = {ev.event_type for _, ev in campaign_rows}

    assert {"FOMC_STATEMENT", "PCE", "PPI", "GDP_ADVANCE", "TREASURY_AUCTION"}.issubset(full_types)
    assert {
        "ADP_EMPLOYMENT",
        "BAKER_HUGHES_RIG",
        "CPI",
        "DURABLE_GOODS_ADVANCE",
        "NFP",
        "UNEMPLOYMENT_CLAIMS",
    }.issubset(campaign_types)
    assert full_types <= campaign_types
    assert any(ev.row_status == "SEED" for _, ev in full_rows)
    assert all(ev.row_status == "SOURCED" for _, ev in campaign_rows)
    assert all(Path(ev.source_file).name == "events.csv" for _, ev in campaign_rows)
