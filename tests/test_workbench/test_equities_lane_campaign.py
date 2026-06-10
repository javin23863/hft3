"""Equities lane walk-forward campaign tests (stock models: EQUITY_/LOW_FLOAT_ prefixes).

Honesty-rule outcome: the fixture at packages/equities_lane/fixtures/low_float_session_v1.ndjson
covers only ONE session date (2024-03-15). The walk_forward config requires at minimum
train_days(60) + val_days(20) + test_days(20) = 100 calendar days to produce a single fold.
One session date cannot span that range, so generate_folds returns [] and the campaign
correctly returns DATA_INSUFFICIENT — no fabricated stage results are emitted.

The --record-paper-shadow equivalent (record_paper_shadow()) is tested separately: recording
PASS into a summary that has status != "PASS" must keep promote_candidate False; a summary
already at PASS flips it to True.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from workbench.src.run.campaign_runner import (
    _is_options_type_model,
    _run_equities_campaign,
    load_paper_shadow_config,
    record_paper_shadow,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "packages" / "equities_lane" / "fixtures"


# ---------------------------------------------------------------------------
# Model-type classifier
# ---------------------------------------------------------------------------

def test_is_options_type_model_positive():
    assert _is_options_type_model("DEALER_HEDGING")
    assert _is_options_type_model("PDF_MODEL_5")
    assert _is_options_type_model("OPTIONS_ARBS_V1")
    assert _is_options_type_model("PARITY_CHECKER")


def test_is_options_type_model_negative():
    assert not _is_options_type_model("EQUITY_RUNNER_V1")
    assert not _is_options_type_model("LOW_FLOAT_BREAKOUT")
    assert not _is_options_type_model("SPREAD_BLOWOUT_RECOMPRESSION")


# ---------------------------------------------------------------------------
# Paper shadow config
# ---------------------------------------------------------------------------

def test_load_paper_shadow_config_defaults():
    cfg = load_paper_shadow_config(REPO)
    assert isinstance(cfg["ibkr_days"], int)
    assert cfg["ibkr_days"] >= 1
    assert cfg["account_type"] == "IBKR_PAPER"
    assert cfg["lane"] == "equities"


# ---------------------------------------------------------------------------
# Stock model campaign — honesty rule: DATA_INSUFFICIENT expected
# ---------------------------------------------------------------------------

def test_stock_campaign_data_insufficient_with_fixture(tmp_path):
    """Single-session fixture cannot satisfy fold requirements — must return DATA_INSUFFICIENT."""
    from workbench.src.artifacts.paths import campaign_dir_for
    from workbench.src.core.params import param_hash_from_dict, DEFAULT_STRATEGY_PARAMS

    campaign_id = "pytest_stock_equity_runner_honesty"
    artifact_dir = campaign_dir_for(REPO, campaign_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    ph = param_hash_from_dict("EQUITY_RUNNER_V1", dict(DEFAULT_STRATEGY_PARAMS))

    result = _run_equities_campaign(
        REPO,
        "EQUITY_RUNNER_V1",
        "RUNNER",
        artifact_dir,
        ph,
    )

    # Honesty rule: must not fabricate stages; must return DATA_INSUFFICIENT.
    assert result.status == "DATA_INSUFFICIENT", (
        f"Expected DATA_INSUFFICIENT (fixture covers 1 session, cannot produce folds), got {result.status}"
    )
    assert result.periods == [] or all(
        not any(True for _ in result.periods)
    ), "No period results should be fabricated when DATA_INSUFFICIENT"

    summary_path = artifact_dir / "summary.json"
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["status"] == "DATA_INSUFFICIENT"
    assert summary["campaign_mode"] == "equities_lane"
    assert summary.get("paper_shadow_required") is True
    assert summary.get("paper_shadow_status") == "PENDING"
    assert isinstance(summary.get("paper_shadow_days"), int)
    assert summary.get("promote_candidate") is False

    # Blocking gate must describe the fold shortfall.
    gates = summary.get("blocking_gates") or []
    assert any(
        g.get("gate") == "equities_walk_forward_min_sessions" for g in gates
    ), f"Expected equities_walk_forward_min_sessions blocking gate; got: {gates}"

    # No forced promotion_eligible: False contradiction.
    assert "promotion_eligible" not in summary


def test_stock_campaign_dry_run(tmp_path):
    """Dry run must return DRY_RUN status without running any backtest."""
    from workbench.src.artifacts.paths import campaign_dir_for
    from workbench.src.core.params import param_hash_from_dict, DEFAULT_STRATEGY_PARAMS

    campaign_id = "pytest_stock_equity_dry_run"
    artifact_dir = campaign_dir_for(REPO, campaign_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    ph = param_hash_from_dict("LOW_FLOAT_BREAKOUT", dict(DEFAULT_STRATEGY_PARAMS))

    result = _run_equities_campaign(
        REPO,
        "LOW_FLOAT_BREAKOUT",
        "RUNNER",
        artifact_dir,
        ph,
        dry_run=True,
    )
    assert result.status == "DRY_RUN"
    assert (artifact_dir / "dry_run_preview.json").is_file()


# ---------------------------------------------------------------------------
# record_paper_shadow — promote_candidate flip semantics
# ---------------------------------------------------------------------------

def test_record_paper_shadow_pass_does_not_flip_when_stages_fail(tmp_path):
    """Paper shadow PASS must NOT flip promote_candidate when stage gates have not passed."""
    from workbench.src.artifacts.paths import campaign_dir_for

    campaign_id = "pytest_paper_shadow_no_flip"
    artifact_dir = campaign_dir_for(REPO, campaign_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Write a summary where status is FAIL (stage gates did not pass).
    summary = {
        "campaign_id": campaign_id,
        "status": "FAIL",
        "model_id": "EQUITY_RUNNER_V1",
        "symbol": "RUNNER",
        "param_hash": "x",
        "campaign_mode": "equities_lane",
        "periods": [{"name": "Discovery", "gate_pass": False, "evaluate_only": False}],
        "paper_shadow_required": True,
        "paper_shadow_status": "PENDING",
        "paper_shadow_days": 60,
        "promote_candidate": False,
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    record_paper_shadow(REPO, campaign_id, "PASS")

    updated = json.loads((artifact_dir / "summary.json").read_text(encoding="utf-8"))
    assert updated["paper_shadow_status"] == "PASS"
    # Stage gates failed → promote_candidate must stay False.
    assert updated["promote_candidate"] is False


def test_record_paper_shadow_pass_flips_when_stages_pass(tmp_path):
    """Paper shadow PASS must flip promote_candidate to True when stage gates all passed."""
    from workbench.src.artifacts.paths import campaign_dir_for

    campaign_id = "pytest_paper_shadow_flip"
    artifact_dir = campaign_dir_for(REPO, campaign_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Write a summary where status is PASS and all stages passed.
    summary = {
        "campaign_id": campaign_id,
        "status": "PASS",
        "model_id": "EQUITY_RUNNER_V1",
        "symbol": "RUNNER",
        "param_hash": "x",
        "campaign_mode": "equities_lane",
        "periods": [{"name": "Discovery", "gate_pass": True, "evaluate_only": False}],
        "paper_shadow_required": True,
        "paper_shadow_status": "PENDING",
        "paper_shadow_days": 60,
        "promote_candidate": False,
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    record_paper_shadow(REPO, campaign_id, "PASS")

    updated = json.loads((artifact_dir / "summary.json").read_text(encoding="utf-8"))
    assert updated["paper_shadow_status"] == "PASS"
    # All stage gates passed + paper shadow PASS → promote_candidate must be True.
    assert updated["promote_candidate"] is True


def test_record_paper_shadow_fail_keeps_promote_candidate_false(tmp_path):
    """Paper shadow FAIL must keep promote_candidate False regardless of stage gates."""
    from workbench.src.artifacts.paths import campaign_dir_for

    campaign_id = "pytest_paper_shadow_fail"
    artifact_dir = campaign_dir_for(REPO, campaign_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "campaign_id": campaign_id,
        "status": "PASS",
        "model_id": "EQUITY_RUNNER_V1",
        "symbol": "RUNNER",
        "param_hash": "x",
        "campaign_mode": "equities_lane",
        "periods": [{"name": "Discovery", "gate_pass": True, "evaluate_only": False}],
        "paper_shadow_required": True,
        "paper_shadow_status": "PENDING",
        "paper_shadow_days": 60,
        "promote_candidate": False,
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    record_paper_shadow(REPO, campaign_id, "FAIL")

    updated = json.loads((artifact_dir / "summary.json").read_text(encoding="utf-8"))
    assert updated["paper_shadow_status"] == "FAIL"
    assert updated["promote_candidate"] is False


# ---------------------------------------------------------------------------
# Fixture existence sanity
# ---------------------------------------------------------------------------

def test_equities_fixtures_present():
    assert (FIXTURES / "low_float_session_v1.ndjson").is_file(), "session fixture missing"
    assert (FIXTURES / "daily_bars.csv").is_file(), "daily_bars fixture missing"
    assert (FIXTURES / "float_metadata.csv").is_file(), "float_metadata fixture missing"
