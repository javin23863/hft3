"""Recent holdout must be calendar 2025 only (B4)."""

from __future__ import annotations

from pathlib import Path

from workbench.src.data.event_catalog import campaign_preview, load_periods

REPO = Path(__file__).resolve().parents[2]


def test_recent_holdout_period_is_2025_only():
    periods = {p.name: p for p in load_periods(REPO)}
    rh = periods["Recent holdout"]
    assert rh.start_year == 2025
    assert rh.end_year == 2025


def test_promotion_preview_excludes_2026_rows():
    preview = campaign_preview("HYP_5", "MES.v.0", REPO)
    rh = preview["periods"]["Recent holdout"]
    assert rh["end_year"] == 2025
    for ev in rh["events"]:
        year = int(ev["release_date"][:4])
        assert year == 2025
