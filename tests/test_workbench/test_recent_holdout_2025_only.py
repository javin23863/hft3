"""Recent holdout must be calendar 2025 only (B4)."""

from __future__ import annotations

from pathlib import Path

import pytest

import workbench.src.data.event_catalog as event_catalog
from workbench.src.data.event_catalog import campaign_preview, load_periods

REPO = Path(__file__).resolve().parents[2]


def test_recent_holdout_period_is_2025_only():
    periods = {p.name: p for p in load_periods(REPO)}
    rh = periods["Recent holdout"]
    assert rh.start_year == 2025
    assert rh.end_year == 2025


def test_promotion_preview_excludes_2026_rows(monkeypatch: pytest.MonkeyPatch):
    def _events(model_id, period, symbol, repo_root, **kwargs):
        if period.name != "Recent holdout":
            return []
        return [
            event_catalog.EventSpec(
                event_id="CPI_2025_01_15_TIGHT",
                event_type="CPI",
                release_date="2025-01-15",
                event_context="CPI_TIGHT",
                symbol=symbol,
                npz_path=repo_root / "data" / "npz" / "fixture.npz",
                npz_present=True,
                start_utc=None,
                end_utc=None,
                npz_symbol_used=symbol,
            )
        ]

    monkeypatch.setattr(event_catalog, "list_campaign_events", _events)
    monkeypatch.setattr(event_catalog, "catalog_years_available", lambda *a, **kw: 1)

    preview = campaign_preview("HYP_5", "MES.v.0", REPO)
    rh = preview["periods"]["Recent holdout"]
    assert rh["end_year"] == 2025
    for ev in rh["events"]:
        year = int(ev["release_date"][:4])
        assert year == 2025
