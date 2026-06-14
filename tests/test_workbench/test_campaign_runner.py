"""Tests for walk-forward campaign runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from workbench.src.data.coverage_check import CoverageSummary
from workbench.src.run.campaign_runner import run_campaign

REPO = Path(__file__).resolve().parents[2]


def _fixture_coverage(model_name: str = "HYP_5") -> CoverageSummary:
    return CoverageSummary(
        model_name=model_name,
        data_type="CME MBO Level 3",
        required_symbols=["MES"],
        available_start_date="2018-01-01",
        available_end_date="2025-12-31",
        valid_trading_days=250,
        minimum_required_days=250,
        target_days=750,
        coverage_status="MINIMUM_ONLY",
        missing_date_ranges=[],
        action_taken="fixture coverage for dry-run contract test",
    )


@pytest.fixture
def dry_run_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    import workbench.src.data.event_catalog as event_catalog
    import workbench.src.run.campaign_runner as campaign_runner

    monkeypatch.setattr(
        campaign_runner,
        "compute_model_coverage",
        lambda repo_root, model_id, symbol: _fixture_coverage(model_id),
    )
    monkeypatch.setattr(campaign_runner, "catalog_years_available", lambda *a, **kw: 10.0)
    monkeypatch.setattr(
        event_catalog,
        "campaign_preview",
        lambda *a, **kw: {"periods": [], "missing": [], "runnable": []},
    )


def test_dry_run_returns_preview(tmp_path, monkeypatch, dry_run_fixtures):
    monkeypatch.chdir(REPO)
    result = run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        dry_run=True,
        allow_partial=True,
    )
    assert result.status == "DRY_RUN"
    preview = REPO / "artifacts" / "research_cards" / "workbench_runs" / result.campaign_id / "dry_run_preview.json"
    assert preview.is_file()


def test_dry_run_persists_composition_in_manifest(dry_run_fixtures):
    from workbench.src.core.composition import DefensiveStub, ModelComposition

    comp = ModelComposition(
        primary_model_id="HYP_5",
        defensive_stubs=[DefensiveStub("PDF_MODEL_9", "before", 50.0)],
    )
    result = run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        dry_run=True,
        allow_partial=True,
        composition=comp,
    )
    manifest = REPO / "artifacts" / "research_cards" / "workbench_runs" / result.campaign_id / "campaign.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["composition"]["primary_model_id"] == "HYP_5"


@patch("workbench.src.run.campaign_runner.load_wfc_config")
@patch("workbench.src.run.engine.WorkbenchEngine")
@patch("workbench.src.run.campaign_runner.list_campaign_events")
def test_sequential_gate_stops_after_discovery_fail(mock_list, MockEngine, mock_wfc, monkeypatch):
    import workbench.src.run.campaign_runner as campaign_runner

    monkeypatch.setattr(campaign_runner, "compute_model_coverage", lambda *a, **kw: _fixture_coverage())
    monkeypatch.setattr(campaign_runner, "catalog_years_available", lambda *a, **kw: 10.0)
    mock_wfc.return_value = {"enabled": False}
    from workbench.src.data.event_catalog import EventSpec

    ev = EventSpec(
        event_id="CPI_2018_01_11_TIGHT",
        event_type="CPI",
        release_date="2018-01-11",
        event_context="CPI_TIGHT",
        symbol="MES.v.0",
        npz_path=REPO / "data" / "npz" / "MES.v.0_CPI_2018_01_11_TIGHT_mbo.npz",
        npz_present=True,
        start_utc=None,
        end_utc=None,
    )

    def _events(model_id, period, symbol, repo_root, **kw):
        if period.name == "Discovery":
            return [ev]
        return []

    mock_list.side_effect = _events
    mock_engine = MagicMock()
    MockEngine.return_value = mock_engine
    mock_engine.run.return_value = {
        "run_id": "x",
        "artifact_dir": str(REPO / "artifacts" / "research_cards" / "workbench_runs" / "dummy"),
        "report": {
            "net_pnl": -1.0,
            "num_trades": 1,
            "survives_cpp_execution_delay": False,
        },
    }
    (REPO / "artifacts" / "research_cards" / "workbench_runs" / "dummy").mkdir(parents=True, exist_ok=True)

    result = run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        allow_partial=True,
        audit_grade=False,
    )
    assert result.status == "FAIL"
    assert len(result.periods) == 1
    assert result.periods[0].name == "Discovery"
    assert result.periods[0].gate_pass is False


def test_hyp_29_dry_run_no_cpi_events(dry_run_fixtures):
    result = run_campaign(REPO, "HYP_29", "MES.v.0", dry_run=True, allow_partial=True)
    assert result.status == "DRY_RUN"


@patch("workbench.src.run.engine.WorkbenchEngine")
@patch("workbench.src.run.campaign_runner.compute_model_coverage")
def test_audit_grade_blocks_before_robustness_when_coverage_under_250(mock_coverage, MockEngine):
    mock_coverage.return_value = CoverageSummary(
        model_name="HYP_5",
        data_type="CME MBO Level 3",
        required_symbols=["MES"],
        available_start_date="",
        available_end_date="",
        valid_trading_days=249,
        minimum_required_days=250,
        target_days=750,
        coverage_status="BELOW_MINIMUM",
        missing_date_ranges=["2023-01-03..2023-12-29"],
        action_taken="fill missing data before robustness",
    )

    result = run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        audit_grade=True,
        allow_partial=False,
        campaign_id="pytest_coverage_blocks",
    )

    assert result.status == "DATA_INSUFFICIENT"
    MockEngine.assert_not_called()


@patch("workbench.src.run.campaign_runner.list_campaign_events")
@patch("workbench.src.run.campaign_runner.load_wfc_config")
@patch("workbench.src.run.campaign_runner.catalog_years_available")
@patch("workbench.src.run.campaign_runner.compute_model_coverage")
def test_valid_day_coverage_replaces_old_year_gate(mock_coverage, mock_years, mock_wfc, mock_events):
    mock_coverage.return_value = CoverageSummary(
        model_name="HYP_5",
        data_type="CME MBO Level 3",
        required_symbols=["MES"],
        available_start_date="2023-01-03",
        available_end_date="2023-12-29",
        valid_trading_days=250,
        minimum_required_days=250,
        target_days=750,
        coverage_status="MINIMUM_ONLY",
        missing_date_ranges=["none"],
        action_taken="proceed to robustness (minimum coverage only)",
    )
    mock_years.return_value = 0
    mock_wfc.return_value = {"enabled": False}
    mock_events.return_value = []

    result = run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        audit_grade=True,
        allow_partial=False,
        campaign_id="pytest_coverage_replaces_year_gate",
    )

    assert result.status != "DATA_INSUFFICIENT"
