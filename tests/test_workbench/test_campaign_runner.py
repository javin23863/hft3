"""Tests for walk-forward campaign runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from workbench.src.run.campaign_runner import run_campaign

REPO = Path(__file__).resolve().parents[2]


def test_dry_run_returns_preview(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO)
    result = run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        dry_run=True,
        allow_partial=True,
    )
    assert result.status == "DRY_RUN"
    preview = REPO / "research_cards" / "workbench_runs" / result.campaign_id / "dry_run_preview.json"
    assert preview.is_file()


def test_dry_run_persists_composition_in_manifest():
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
    manifest = REPO / "research_cards" / "workbench_runs" / result.campaign_id / "campaign.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["composition"]["primary_model_id"] == "HYP_5"


@patch("workbench.src.run.campaign_runner.load_wfc_config")
@patch("workbench.src.run.engine.WorkbenchEngine")
@patch("workbench.src.run.campaign_runner.list_campaign_events")
def test_sequential_gate_stops_after_discovery_fail(mock_list, MockEngine, mock_wfc):
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
        "artifact_dir": str(REPO / "research_cards" / "workbench_runs" / "dummy"),
        "report": {
            "net_pnl": -1.0,
            "num_trades": 1,
            "survives_cpp_execution_delay": False,
        },
    }
    (REPO / "research_cards" / "workbench_runs" / "dummy").mkdir(parents=True, exist_ok=True)

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


def test_hyp_29_dry_run_no_cpi_events():
    result = run_campaign(REPO, "HYP_29", "MES.v.0", dry_run=True, allow_partial=True)
    assert result.status == "DRY_RUN"
