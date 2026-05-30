"""Ensure catalog downloads target event windows only."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

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
