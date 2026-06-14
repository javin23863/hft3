"""Campaign runner hot-memory telemetry hook."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.src.data.coverage_check import CoverageSummary
from workbench.src.run.campaign_runner import run_campaign

REPO = Path(__file__).resolve().parents[2]


def _fixture_coverage() -> CoverageSummary:
    return CoverageSummary(
        model_name="HYP_5",
        data_type="CME MBO Level 3",
        required_symbols=["MES"],
        available_start_date="2018-01-01",
        available_end_date="2025-12-31",
        valid_trading_days=250,
        minimum_required_days=250,
        target_days=750,
        coverage_status="MINIMUM_ONLY",
        missing_date_ranges=[],
        action_taken="fixture coverage for dry-run telemetry test",
    )


@pytest.fixture
def dry_run_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    import workbench.src.data.event_catalog as event_catalog
    import workbench.src.run.campaign_runner as campaign_runner

    monkeypatch.setattr(campaign_runner, "compute_model_coverage", lambda *a, **kw: _fixture_coverage())
    monkeypatch.setattr(campaign_runner, "catalog_years_available", lambda *a, **kw: 10.0)
    monkeypatch.setattr(
        event_catalog,
        "campaign_preview",
        lambda *a, **kw: {"periods": [], "missing": [], "runnable": []},
    )


def test_dry_run_includes_hot_memory_telemetry(dry_run_fixtures):
    result = run_campaign(REPO, "HYP_5", "MES.v.0", dry_run=True, allow_partial=True)
    assert result.status == "DRY_RUN"
    preview_path = (
        REPO / "artifacts" / "research_cards" / "workbench_runs" / result.campaign_id / "dry_run_preview.json"
    )
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    telemetry = preview["diagnostics"]["hot_memory_telemetry"]
    assert "hot_executable" in telemetry
    assert "ES" in telemetry["hot_executable"]
    assert "resident" in telemetry
    assert "core_protected_symbols" in telemetry
