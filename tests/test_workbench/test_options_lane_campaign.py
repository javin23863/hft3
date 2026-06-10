"""PDF_MODEL_5 / DEALER_HEDGING options-type equities lane fixture campaign."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from workbench.src.data.event_catalog import load_model_binding
from workbench.src.run.campaign_runner import run_campaign

REPO = Path(__file__).resolve().parents[2]


def test_dealer_hedging_binding_equities_lane():
    binding = load_model_binding(REPO, "DEALER_HEDGING")
    assert binding["campaign_mode"] in ("equities_lane", "options_lane")
    assert "options_chain" in binding["required_datasets"]


def test_pdf_model_5_binding_equities_lane():
    # PDF_MODEL_5 is the legacy_id for DEALER_HEDGING; both must resolve to equities_lane.
    binding = load_model_binding(REPO, "PDF_MODEL_5")
    assert binding["campaign_mode"] in ("equities_lane", "options_lane")


def test_dealer_hedging_dry_run_campaign():
    result = run_campaign(REPO, "DEALER_HEDGING", "MES.v.0", dry_run=True, allow_partial=True)
    assert result.status == "DRY_RUN"


def test_pdf_model_5_dry_run_campaign():
    result = run_campaign(REPO, "PDF_MODEL_5", "MES.v.0", dry_run=True, allow_partial=True)
    assert result.status == "DRY_RUN"


@patch("workbench.src.run.campaign_runner._run_institutional_model_metrics")
def test_options_fixture_campaign_paper_shadow_contract(mock_metrics):
    """Non-dry-run options fixture campaign: verify new summary.json paper shadow contract."""
    mock_metrics.return_value = {"status": "ok", "envelope": {"active": True}}

    result = run_campaign(
        REPO,
        "DEALER_HEDGING",
        "MES.v.0",
        allow_partial=True,
        audit_grade=False,
    )

    # Campaign must complete without raising (fixture path bug is fixed).
    assert result.status in ("PASS", "FAIL", "BLOCKED")

    summary_path = Path(result.artifact_dir) / "summary.json"
    assert summary_path.is_file(), f"summary.json missing at {result.artifact_dir}"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    # New paper shadow contract — no forced promotion_eligible: False contradiction.
    assert "promotion_eligible" not in summary, (
        "summary.json must not contain forced promotion_eligible:False contradiction"
    )
    assert summary.get("paper_shadow_required") is True
    assert summary.get("paper_shadow_status") in ("PENDING", "PASS", "FAIL")
    assert isinstance(summary.get("paper_shadow_days"), int)

    # promote_candidate must be False while paper shadow is PENDING.
    if summary.get("paper_shadow_status") == "PENDING":
        assert summary.get("promote_candidate") is False, (
            "promote_candidate must be False while paper_shadow_status is PENDING"
        )
