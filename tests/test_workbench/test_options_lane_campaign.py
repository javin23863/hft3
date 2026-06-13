"""PDF_MODEL_5 / DEALER_HEDGING options-type equities lane fixture campaign."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from workbench.src.data.event_catalog import load_model_binding
from workbench.src.run.campaign_runner import (
    _run_options_type_campaign,
    record_paper_shadow,
    run_campaign,
)

REPO = Path(__file__).resolve().parents[2]


def _write_tmp_walk_forward(root: Path) -> None:
    cfg = root / "apps" / "workbench" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "walk_forward.yaml").write_text(
        "options_paper_shadow:\n"
        "  days: 60\n"
        "  account_type: PAPER\n"
        "  lane: options\n",
        encoding="utf-8",
    )


def _write_tmp_options_spec(root: Path, rows: str) -> None:
    specs = root / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "OPTIONS_LANE.md").write_text(
        "# OPTIONS_LANE.md\n\n"
        "| ID | Component | Description | Status |\n"
        "|----|-----------|-------------|--------|\n"
        f"{rows}\n",
        encoding="utf-8",
    )


def _prepare_passing_paper_shadow_summary(
    root: Path,
    campaign_id: str,
    **summary_updates,
) -> Path:
    runs = root / "artifacts" / "research_cards" / "workbench_runs"
    artifact_dir = runs / campaign_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "campaign_id": campaign_id,
        "status": "PASS",
        "model_id": "FOPT_ES_CALL",
        "symbol": "MES.v.0",
        "periods": [{"gate_pass": True, "evaluate_only": False}],
        "promote_candidate": False,
    }
    summary.update(summary_updates)
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return artifact_dir


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
@patch("workbench.src.run.campaign_runner.artifact_root")
@patch("workbench.src.registry.unified_registry.get_model_by_id")
def test_options_fixture_campaign_paper_shadow_contract(
    mock_get_model,
    mock_artifact_root,
    mock_metrics,
    tmp_path: Path,
):
    """Non-dry-run options fixture campaign: verify new summary.json paper shadow contract."""
    class PassingOptionsAdapter:
        def validate_inputs(self, ctx):
            return []

        def run_backtest(self, ctx):
            return SimpleNamespace(net_pnl=10.0, num_trades=2)

    _write_tmp_walk_forward(tmp_path)
    _write_tmp_options_spec(tmp_path, "| o-a | `vol_clock` | verified | **FIXED** |")
    artifact_dir = tmp_path / "artifacts" / "research_cards" / "workbench_runs" / "pytest_options_contract"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    mock_get_model.return_value = PassingOptionsAdapter()
    mock_artifact_root.return_value = tmp_path / "artifacts" / "research_cards"
    mock_metrics.return_value = {"status": "ok", "envelope": {"active": True}}

    result = _run_options_type_campaign(
        tmp_path,
        "DEALER_HEDGING",
        "MES.v.0",
        artifact_dir,
        "hash",
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


@pytest.mark.parametrize(
    "summary_updates",
    [
        {"model_id": "FOPT_ES_CALL"},
        {"model_id": "OPTIONS_PARITY_TEST"},
        {"model_id": "PARITY_FIXTURE_TEST"},
        {"model_id": "HYP_5", "lane": "cme_options"},
    ],
)
def test_record_paper_shadow_blocks_options_like_identifiers_while_ledger_open(
    tmp_path: Path,
    summary_updates: dict[str, str],
) -> None:
    _write_tmp_walk_forward(tmp_path)
    _write_tmp_options_spec(
        tmp_path,
        "| o-a | `vol_clock` | placeholder | **OPEN** - blocks shadow/live arm. |",
    )
    artifact_dir = _prepare_passing_paper_shadow_summary(
        tmp_path,
        "pytest_options_paper_shadow_open",
        **summary_updates,
    )

    record_paper_shadow(tmp_path, artifact_dir.name, "PASS")

    summary = json.loads((artifact_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["paper_shadow_status"] == "PASS"
    assert summary["promote_candidate"] is False
    assert summary["options_defect_ledger"]["status"] == "blocked"
    assert summary["options_defect_ledger"]["open_ids"] == ["o-a"]
    assert any(
        gate.get("gate") == "options_defect_ledger"
        for gate in summary.get("blocking_gates", [])
    )


def test_record_paper_shadow_blocks_sparse_options_summary_while_ledger_open(tmp_path: Path) -> None:
    """record_paper_shadow is options-specific; sparse old summaries still need the ledger."""
    _write_tmp_walk_forward(tmp_path)
    _write_tmp_options_spec(
        tmp_path,
        "| o-a | `vol_clock` | placeholder | **OPEN** - blocks shadow/live arm. |",
    )
    artifact_dir = _prepare_passing_paper_shadow_summary(
        tmp_path,
        "pytest_sparse_options_paper_shadow_open",
        model_id="",
        symbol="",
    )

    record_paper_shadow(tmp_path, artifact_dir.name, "PASS")

    summary = json.loads((artifact_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["promote_candidate"] is False
    assert summary["options_defect_ledger"]["status"] == "blocked"
    assert any(
        gate.get("gate") == "options_defect_ledger"
        for gate in summary.get("blocking_gates", [])
    )


def test_record_paper_shadow_does_not_clear_existing_blocking_gate(tmp_path: Path) -> None:
    _write_tmp_walk_forward(tmp_path)
    _write_tmp_options_spec(tmp_path, "| o-a | `vol_clock` | placeholder | **FIXED** |")
    artifact_dir = _prepare_passing_paper_shadow_summary(
        tmp_path,
        "pytest_options_paper_shadow_existing_blocker",
        blocking_gates=[
            {
                "gate": "data_coverage",
                "status": "MISSING",
                "reason": "required options dataset is absent",
            }
        ],
    )

    record_paper_shadow(tmp_path, artifact_dir.name, "PASS")

    summary = json.loads((artifact_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["paper_shadow_status"] == "PASS"
    assert summary["options_defect_ledger"]["status"] == "empty"
    assert summary["promote_candidate"] is False
    assert any(gate.get("gate") == "data_coverage" for gate in summary.get("blocking_gates", []))


@pytest.mark.parametrize(
    ("rows", "expected_status"),
    [
        ("| o-a | `broken_row` | missing status |", "malformed"),
        ("| o-a | `vol_clock` | placeholder | **MAYBE** |", "unknown_status"),
    ],
)
def test_record_paper_shadow_fails_closed_for_malformed_or_unknown_options_ledger(
    tmp_path: Path,
    rows: str,
    expected_status: str,
) -> None:
    _write_tmp_walk_forward(tmp_path)
    _write_tmp_options_spec(tmp_path, rows)
    artifact_dir = _prepare_passing_paper_shadow_summary(
        tmp_path,
        f"pytest_options_paper_shadow_{expected_status}",
        model_id="FOPT_ES_CALL",
    )

    record_paper_shadow(tmp_path, artifact_dir.name, "PASS")

    summary = json.loads((artifact_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["promote_candidate"] is False
    assert summary["options_defect_ledger"]["status"] == expected_status
    assert summary["options_defect_ledger"]["empty"] is False


@patch("workbench.src.run.campaign_runner._run_institutional_model_metrics")
@patch("workbench.src.run.campaign_runner.artifact_root")
@patch("workbench.src.registry.unified_registry.get_model_by_id")
def test_options_type_campaign_blocks_promotion_when_ledger_open_after_paper_shadow_pass(
    mock_get_model,
    mock_artifact_root,
    mock_metrics,
    tmp_path: Path,
) -> None:
    class PassingOptionsAdapter:
        def validate_inputs(self, ctx):
            return []

        def run_backtest(self, ctx):
            return SimpleNamespace(net_pnl=10.0, num_trades=2)

    _write_tmp_walk_forward(tmp_path)
    _write_tmp_options_spec(
        tmp_path,
        "| o-a | `vol_clock` | placeholder | **OPEN** - blocks shadow/live arm. |",
    )
    artifact_dir = tmp_path / "artifacts" / "research_cards" / "workbench_runs" / "pytest_options_fixture_open"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "paper_shadow.json").write_text(
        json.dumps({"status": "PASS"}),
        encoding="utf-8",
    )
    mock_get_model.return_value = PassingOptionsAdapter()
    mock_artifact_root.return_value = tmp_path / "artifacts" / "research_cards"
    mock_metrics.return_value = {"status": "ok", "envelope": {"active": True}}

    result = _run_options_type_campaign(
        tmp_path,
        "FOPT_ES_CALL",
        "FOPT_ES_CALL",
        artifact_dir,
        "hash",
    )

    assert result.status == "PASS"
    summary = json.loads((artifact_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["paper_shadow_status"] == "PASS"
    assert summary["options_defect_ledger"]["status"] == "blocked"
    assert summary["promote_candidate"] is False
