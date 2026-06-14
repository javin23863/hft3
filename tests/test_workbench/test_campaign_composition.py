"""Campaign composition manifest and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.src.core.composition import DefensiveStub, ModelComposition
from workbench.src.data.coverage_check import CoverageSummary
from workbench.src.run.campaign_runner import run_campaign
from workbench.src.run.composition_cli import load_composition, parse_defensive_flag

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
        action_taken="fixture coverage for dry-run contract test",
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


def test_parse_defensive_flag():
    stubs = parse_defensive_flag("PDF_MODEL_9:before:50,PDF_MODEL_11:during")
    assert len(stubs) == 2
    assert stubs[0].model_id == "PDF_MODEL_9"
    assert stubs[0].phase == "before"


def test_dry_run_campaign_with_composition(dry_run_fixtures):
    composition = ModelComposition(
        primary_model_id="HYP_5",
        defensive_stubs=[DefensiveStub("PDF_MODEL_9", "before", 50.0)],
    )
    result = run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        dry_run=True,
        allow_partial=True,
        composition=composition,
    )
    assert result.status == "DRY_RUN"
    manifest = REPO / "artifacts" / "research_cards" / "workbench_runs" / result.campaign_id / "campaign.json"
    assert manifest.is_file()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["composition"]["primary_model_id"] == "HYP_5"
    assert len(data["composition"]["defensive_stubs"]) == 1


def test_load_composition_from_json(tmp_path):
    path = tmp_path / "comp.json"
    path.write_text(
        json.dumps(
            {
                "primary_model_id": "HYP_5",
                "defensive_stubs": [{"model_id": "PDF_MODEL_3", "phase": "continuous", "budget_us": 2500}],
            }
        ),
        encoding="utf-8",
    )
    comp = load_composition("HYP_5", composition_path=path)
    assert comp.primary_model_id == "HYP_5"
    assert comp.defensive_stubs[0].model_id == "PDF_MODEL_3"
