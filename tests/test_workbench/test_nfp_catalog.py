"""NFP calendar coverage in campaign catalog."""

from __future__ import annotations

from pathlib import Path

from decision_engine.python.src.walk_forward import ValidationPeriod
from workbench.src.data.event_catalog import list_campaign_events, row_to_event_context

REPO = Path(__file__).resolve().parents[2]


def test_nfp_resolves_to_nfp_tight():
    assert row_to_event_context("NFP", "TIGHT") == "NFP_TIGHT"


def test_nfp_in_discovery_years():
    period = ValidationPeriod("Discovery", 2018, 2020)
    events = list_campaign_events("HYP_5", period, "MES.v.0", REPO)
    nfp = [e for e in events if e.event_context == "NFP_TIGHT"]
    assert nfp, "expected NFP_TIGHT events in Discovery for HYP_5"
    assert any("NFP_" in e.event_id for e in nfp)
