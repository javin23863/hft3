"""PDF_MODEL_5 options lane fixture campaign."""

from __future__ import annotations

from pathlib import Path

from workbench.src.data.event_catalog import load_model_binding
from workbench.src.run.campaign_runner import run_campaign

REPO = Path(__file__).resolve().parents[2]


def test_pdf_model_5_binding_options_lane():
    binding = load_model_binding(REPO, "PDF_MODEL_5")
    assert binding["campaign_mode"] == "options_lane"
    assert "options_chain" in binding["required_datasets"]


def test_pdf_model_5_dry_run_campaign():
    result = run_campaign(REPO, "PDF_MODEL_5", "MES.v.0", dry_run=True, allow_partial=True)
    assert result.status == "DRY_RUN"
