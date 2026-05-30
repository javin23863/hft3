"""Campaign composition manifest and CLI."""

from __future__ import annotations

import json
from pathlib import Path

from workbench.src.core.composition import DefensiveStub, ModelComposition
from workbench.src.run.campaign_runner import run_campaign
from workbench.src.run.composition_cli import load_composition, parse_defensive_flag

REPO = Path(__file__).resolve().parents[2]


def test_parse_defensive_flag():
    stubs = parse_defensive_flag("PDF_MODEL_9:before:50,PDF_MODEL_11:during")
    assert len(stubs) == 2
    assert stubs[0].model_id == "PDF_MODEL_9"
    assert stubs[0].phase == "before"


def test_dry_run_campaign_with_composition():
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
    manifest = REPO / "research_cards" / "workbench_runs" / result.campaign_id / "campaign.json"
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
