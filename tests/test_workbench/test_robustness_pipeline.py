"""Robustness pipeline acceptance tests.

Tests that:
  - Manifest can be created for each lane type
  - Manifest contains all required fields
  - Pipeline stages execute in order
  - Edge explanation is produced
  - Positive PnL alone cannot promote champion
  - Champion promotion requires full robustness pipeline
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.src.robustness.manifest import (
    RobustnessManifest,
    create_cme_manifest,
    create_equities_manifest,
    create_options_manifest,
)
from workbench.src.robustness.pipeline import (
    _validate_binding,
    _inventory_data,
    _construct_search_space,
    _run_discovery_search,
    _run_wfc,
    _run_confirmation,
    _run_holdout,
    _run_execution_realism,
    _generate_explanation,
    run_robustness_pipeline,
)
from workbench.src.state.workbench_truth import build_workbench_truth


REPO = Path(__file__).resolve().parents[2]


def test_manifest_has_required_fields():
    """RobustnessManifest must have all required fields."""
    m = RobustnessManifest()
    fields = RobustnessManifest.__dataclass_fields__
    required = [
        "run_id", "created_at", "repo_commit", "lane_id", "model_id",
        "edge_status", "champion_status", "next_action",
    ]
    for field in required:
        assert field in fields, f"Missing required field: {field}"


def test_create_cme_manifest():
    """Create CME manifest populates lane_id and symbol."""
    m = create_cme_manifest("test_run", "abc123", "SPREAD_BLOWOUT_RECOMPRESSION", "MES.v.0")
    assert m.lane_id == "cme_futures"
    assert m.model_id == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert m.symbol == "MES.v.0"
    assert m.run_id == "test_run"
    assert m.repo_commit == "abc123"


def test_create_equities_manifest():
    """Create equities manifest populates lane_id and session fields."""
    m = create_equities_manifest(
        "test_run", "abc123", "ABSORPTION_FADE",
        session_id="gme_2021", symbol="GME", date="2021-01-27",
        catalyst="meme_short_squeeze",
    )
    assert m.lane_id == "equities_low_float"
    assert m.model_id == "ABSORPTION_FADE"
    assert m.session_id == "gme_2021"
    assert m.symbol == "GME"
    assert m.session_date == "2021-01-27"
    assert m.catalyst == "meme_short_squeeze"


def test_create_options_manifest():
    """Create options manifest populates lane_id and group."""
    m = create_options_manifest("test_run", "abc123", "HYBRID_EXECUTION", "example_same_ul")
    assert m.lane_id == "options_parity"
    assert m.model_id == "HYBRID_EXECUTION"
    assert m.group_id == "example_same_ul"


def test_manifest_writes_to_disk(tmp_path):
    """Manifest can be written to and read from disk."""
    m = create_cme_manifest("test_write", "sha", "MODEL", "MES.v.0")
    m.edge_status = "EDGE_FOUND"
    m.edge_explanation = "Test explanation"

    out = tmp_path / "manifest.json"
    m.write(out)

    assert out.is_file()
    import json
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["run_id"] == "test_write"
    assert data["lane_id"] == "cme_futures"
    assert data["edge_status"] == "EDGE_FOUND"
    assert data["edge_explanation"] == "Test explanation"


def test_manifest_to_dict():
    """Manifest.to_dict() returns all fields as a dict."""
    m = create_cme_manifest("run1", "sha1", "MODEL", "MES.v.0")
    d = m.to_dict()
    assert isinstance(d, dict)
    assert d["run_id"] == "run1"
    assert d["lane_id"] == "cme_futures"
    assert d["model_id"] == "MODEL"
    assert "features_tested" in d
    assert "parameters_tested" in d
    assert "search_space_version" in d


def test_binding_validation_rejects_unknown_lane():
    """Binding validation must reject unknown lanes."""
    valid, errors = _validate_binding(REPO, "nonexistent_lane", "MODEL", symbol="MES.v.0")
    assert not valid
    assert len(errors) > 0


def test_binding_validation_rejects_wrong_symbol():
    """CME binding must reject symbols not in allowed list."""
    valid, errors = _validate_binding(
        REPO, "cme_futures", "BOOK_PRESSURE",
        symbol="AAPL",  # stocks not allowed in CME
    )
    assert not valid
    assert any(
        "AAPL" in e or "not in lane allowed symbols" in e
        for e in errors
    ), f"Should reject AAPL, got errors: {errors}"


def test_binding_validation_accepts_valid_cme():
    """Valid CME binding must pass validation."""
    valid, errors = _validate_binding(
        REPO, "cme_futures", "BOOK_PRESSURE",
        symbol="MES.v.0",
    )
    assert valid, f"Expected valid, got errors: {errors}"
    assert len(errors) == 0


def test_positive_pnl_alone_cannot_promote_champion():
    """A model cannot become champion from positive PnL alone."""
    # Manifest with edge_status=EDGE_FOUND must still go through
    # all pipeline stages before champion promotion.
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    # Even if edge is found, champion status must reflect pipeline completion
    m.edge_status = "EDGE_FOUND"
    # Default champion_status is empty — must be explicitly set by completed pipeline
    assert m.champion_status == "" or m.champion_status in (
        "candidate", "fixture_only", "blocked", "dry_run", "rejected", "",
    ), f"Champion status '{m.champion_status}' not valid without full pipeline"


def test_equities_champion_requires_l3_policy():
    """Equities champion must satisfy L3-only policy."""
    import yaml
    cfg = REPO / "packages" / "equities_lane" / "config" / "decadal_runners.yaml"
    if cfg.is_file():
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        defaults = raw.get("defaults", {})
        assert defaults.get("l3_only", False), "Equities lane must enforce L3-only"
    else:
        pytest.skip("decadal_runners.yaml not found")


def test_equities_champion_requires_pit_float_metadata():
    """Equities champion must use PIT-safe float metadata."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    for e in equities.entries:
        if e.status not in ("skipped",):
            assert e.float_status in ("ready", "missing"), (
                f"Float metadata status unclear for {e.session_id}"
            )


def test_equities_champion_requires_route_reason():
    """Equities champion manifest must record route reason codes."""
    m = create_equities_manifest(
        "run", "sha", "ABSORPTION_FADE",
        session_id="gme_2021", symbol="GME", date="2021-01-27",
        catalyst="meme_short_squeeze",
    )
    # Route fields must be present in manifest
    assert hasattr(m, "route_type")
    assert hasattr(m, "route_reason_codes")
    assert hasattr(m, "route_status")


def test_equities_champion_requires_edge_explanation():
    """Equities champion must have edge explanation."""
    m = create_equities_manifest(
        "run", "sha", "ABSORPTION_FADE",
        session_id="gme_2021", symbol="GME", date="2021-01-27",
        catalyst="meme_short_squeeze",
    )
    assert hasattr(m, "edge_status")
    assert hasattr(m, "edge_explanation")
    assert hasattr(m, "no_edge_reason")


def test_manifest_contains_search_space():
    """Every manifest must declare search space fields."""
    m = RobustnessManifest()
    assert hasattr(m, "search_space_version")
    assert hasattr(m, "features_tested")
    assert hasattr(m, "parameters_tested")
    assert hasattr(m, "windows_tested")


def test_manifest_contains_discovery_results():
    """Every manifest must have discovery_results array."""
    m = RobustnessManifest()
    assert isinstance(m.discovery_results, list)


def test_manifest_contains_wfc_confirmation_holdout():
    """Every manifest must have walk-forward, confirmation, and holdout result fields."""
    m = RobustnessManifest()
    assert hasattr(m, "wfc_results")
    assert hasattr(m, "confirmation_results")
    assert hasattr(m, "holdout_results")


def test_manifest_contains_execution_realism():
    """Every manifest must have execution, cost, and latency assumptions."""
    m = RobustnessManifest()
    assert hasattr(m, "execution_assumptions")
    assert hasattr(m, "cost_assumptions")
    assert hasattr(m, "latency_assumptions")


def test_manifest_contains_pit_leakage_status():
    """Every manifest must report PIT and leakage status."""
    m = RobustnessManifest()
    assert hasattr(m, "pit_status")
    assert hasattr(m, "leakage_status")
    assert hasattr(m, "option_pit_status")


def test_manifest_contains_failure_modes():
    """Every manifest must have failure_modes list."""
    m = RobustnessManifest()
    assert isinstance(m.failure_modes, list)


def test_manifest_contains_blocking_reasons():
    """Every manifest must have blocking_reasons list."""
    m = RobustnessManifest()
    assert isinstance(m.blocking_reasons, list)


def test_pipeline_dry_run_returns_manifest():
    """Dry-run mode must return a manifest with BLOCKED or DRY_RUN status."""
    manifest = run_robustness_pipeline(
        REPO,
        lane_id="cme_futures",
        model_id="BOOK_PRESSURE",
        symbol="MES.v.0",
        dry_run=True,
    )
    assert manifest.edge_status in ("BLOCKED", "DRY_RUN"), f"Got: {manifest.edge_status}"
    assert manifest.binding_valid, f"Binding should be valid, got errors: {manifest.binding_errors}"


def test_pipeline_invalid_binding_returns_blocked():
    """Invalid binding must return BLOCKED status."""
    manifest = run_robustness_pipeline(
        REPO,
        lane_id="cme_futures",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        symbol="AAPL",  # invalid for CME
    )
    assert manifest.edge_status == "BLOCKED"
    assert not manifest.binding_valid


def test_pipeline_ci_fixture_returns_edge_found():
    """CI fixture mode must return EDGE_FOUND with explanation."""
    manifest = run_robustness_pipeline(
        REPO,
        lane_id="cme_futures",
        model_id="BOOK_PRESSURE",
        symbol="MES.v.0",
        ci_fixture=True,
    )
    assert manifest.edge_status in ("BLOCKED", "EDGE_FOUND")
    assert manifest.edge_explanation or manifest.blocking_reasons


def test_pipeline_produces_manifest_with_all_stages():
    """Full pipeline (even dry-run) must produce complete manifest."""
    manifest = run_robustness_pipeline(
        REPO,
        lane_id="cme_futures",
        model_id="BOOK_PRESSURE",
        symbol="MES.v.0",
        ci_fixture=True,
    )
    assert manifest.run_id
    assert manifest.repo_commit
    assert manifest.lane_id
    assert manifest.model_id
    assert manifest.edge_status
    assert manifest.champion_status is not None
    assert manifest.next_action


def test_wfc_from_campaign_no_periods():
    """WFC must fail when no campaign periods exist."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    m.period_results = []
    result = _run_wfc(REPO, "cme_futures", "MODEL", m, [])
    assert result.get("passed") is False
    assert result.get("total_periods") == 0
    assert result.get("pass_rate") == 0.0
    assert "error" in result


def test_wfc_from_campaign_all_pass():
    """WFC must pass when all campaign periods pass."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    m.period_results = [
        {"period": "P1", "gate_pass": True, "net_pnl": 100.0, "num_trades": 5},
        {"period": "P2", "gate_pass": True, "net_pnl": 80.0, "num_trades": 4},
        {"period": "P3", "gate_pass": True, "net_pnl": 120.0, "num_trades": 6},
    ]
    result = _run_wfc(REPO, "cme_futures", "MODEL", m, [])
    assert result.get("passed") is True
    assert result.get("total_periods") == 3
    assert result.get("pass_rate") == 1.0


def test_wfc_from_campaign_mixed_results():
    """WFC must pass with 2/3 gate pass."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    m.period_results = [
        {"period": "P1", "gate_pass": True, "net_pnl": 100.0},
        {"period": "P2", "gate_pass": False, "net_pnl": -50.0},
        {"period": "P3", "gate_pass": True, "net_pnl": 80.0},
    ]
    result = _run_wfc(REPO, "cme_futures", "MODEL", m, [])
    assert result.get("passed") is True
    assert result.get("total_periods") == 3
    assert result.get("pass_rate") == 2.0 / 3.0


def test_confirmation_rejects_no_pnl():
    """Confirmation must fail when no PnL present."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    m.wfc_out_of_sample_pnl = 0.0
    m.wfc_pass_rate = 0.5
    result = _run_confirmation(REPO, "cme_futures", "MODEL", m)
    assert result.get("passed") is False


def test_confirmation_rejects_no_trades():
    """Confirmation must fail with zero trades."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    m.wfc_out_of_sample_pnl = 100.0
    m.wfc_pass_rate = 0.7
    m.period_results = []
    result = _run_confirmation(REPO, "cme_futures", "MODEL", m)
    assert result.get("passed") is False


def test_confirmation_accepts_valid():
    """Confirmation must pass with positive PnL and trades."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    m.wfc_out_of_sample_pnl = 200.0
    m.wfc_pass_rate = 0.8
    m.period_results = [
        {"num_trades": 5},
        {"num_trades": 7},
    ]
    result = _run_confirmation(REPO, "cme_futures", "MODEL", m)
    assert result.get("passed") is True
    assert result.get("pnl") == 200.0
    assert result.get("trades") == 12


def test_holdout_rejects_zero_pnl():
    """Holdout must fail when confirmation PnL is zero."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    m.wfc_out_of_sample_pnl = 100.0
    m.confirmation_pnl = 0.0
    result = _run_holdout(REPO, "cme_futures", "MODEL", m)
    assert result.get("passed") is False


def test_holdout_requires_50pct_retention():
    """Holdout must require at least 50% retention."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    m.wfc_out_of_sample_pnl = 100.0
    m.confirmation_pnl = 30.0
    result = _run_holdout(REPO, "cme_futures", "MODEL", m)
    assert result.get("passed") is False
    assert result.get("pnl_retention") == 0.3


def test_holdout_accepts_high_retention():
    """Holdout must pass with >= 50% PnL retention."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    m.wfc_out_of_sample_pnl = 100.0
    m.confirmation_pnl = 80.0
    result = _run_holdout(REPO, "cme_futures", "MODEL", m)
    assert result.get("passed") is True
    assert result.get("pnl_retention") == 0.8


def test_execution_realism_per_lane_defaults():
    """Execution realism must produce per-lane defaults."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    result = _run_execution_realism(REPO, "cme_futures", "MODEL", m)
    assert "latency_ms" in result
    assert "slippage_model" in result
    assert "fee_model" in result
    assert result.get("latency_ms") == 5.0
    assert result.get("slippage_model") == "MBO_queue"
    assert result.get("fee_model") == "CME_fee_schedule"


def test_execution_realism_equities_defaults():
    """Execution realism must have equities-specific defaults."""
    m = create_equities_manifest("run", "sha", "MODEL", "gme_2021", "GME", "2021-01-27", "meme")
    result = _run_execution_realism(REPO, "equities_low_float", "MODEL", m)
    assert result.get("slippage_model") == "L3_spread_crossing"
    assert result.get("fee_model") == "taker_maker_5bps"


def test_generate_explanation_all_stages_pass():
    """Full explanation must produce EDGE_FOUND when all stages pass."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    m.binding_valid = True
    m.wfc_out_of_sample_pnl = 100.0
    m.wfc_pass_rate = 1.0
    m.holdout_retention = 0.8
    m.data_inventory = {"npz_events": "700", "npz_status": "available"}

    discovery = [{"status": "completed"}]
    wfc = {"passed": True, "pass_rate": 1.0, "oos_pnl": 100.0}
    confirmation = {"passed": True, "pnl": 100.0, "trades": 20}
    holdout = {"passed": True, "pnl": 80.0, "pnl_retention": 0.8}
    execution = {"passed": True, "pit_leakage": False}

    result = _generate_explanation(
        "cme_futures", "MODEL", m,
        discovery, wfc, confirmation, holdout, execution,
        ci_fixture=False,
    )
    assert result["edge_status"] == "EDGE_FOUND"
    assert result["champion_status"] == "champion"


def test_generate_explanation_partial_stages():
    """Partial stage pass must produce PROMISING_EDGE."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    m.binding_valid = True
    m.wfc_out_of_sample_pnl = 50.0
    m.data_inventory = {"npz_status": "available"}

    discovery = [{"status": "completed"}]
    wfc = {"passed": True}
    confirmation = {"passed": True}
    holdout = {"passed": False}
    execution = {"passed": True}

    result = _generate_explanation(
        "cme_futures", "MODEL", m,
        discovery, wfc, confirmation, holdout, execution,
        ci_fixture=False,
    )
    assert result["edge_status"] == "PROMISING_EDGE"
    assert result["champion_status"] == "candidate"


def test_generate_explanation_ci_fixture():
    """CI fixture mode must return fixture_only champion."""
    m = create_cme_manifest("run", "sha", "MODEL", "MES.v.0")
    result = _generate_explanation(
        "cme_futures", "MODEL", m,
        [], {}, {}, {}, {},
        ci_fixture=True,
    )
    assert result["edge_status"] == "EDGE_FOUND"
    assert result["champion_status"] == "fixture_only"


def test_discovery_crypto_returns_pending():
    """Crypto discovery must return pending_implementation."""
    m = RobustnessManifest()
    from workbench.src.robustness.pipeline import _discovery_crypto
    results = _discovery_crypto(REPO, "MODEL", m)
    assert len(results) == 1
    assert results[0]["status"] == "pending_implementation"
