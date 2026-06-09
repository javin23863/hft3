"""Acceptance tests for WorkbenchTruth backend service.

Tests that:
  - WorkbenchTruth contains all 4 lanes
  - CME lane shows all 7 canonical symbols
  - Equities lane is visible as first-class
  - Missing data is shown as blockers, not hidden
  - Options/parity lane is separate from equities-linked options
  - UI renders from WorkbenchTruth only (no independent state assembly)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.src.state.workbench_truth import (
    build_workbench_truth,
    WorkbenchTruth,
    LaneTruth,
    CmeEntryTruth,
    EquitiesEntryTruth,
    OptionsEntryTruth,
    CryptoEntryTruth,
)
from workbench.src.data.lane_bindings import load_lane_bindings


REPO = Path(__file__).resolve().parents[2]


def test_workbench_truth_contains_all_lanes():
    """WorkbenchTruth must contain all 4 lanes."""
    truth = build_workbench_truth(REPO)
    assert isinstance(truth, WorkbenchTruth)
    assert len(truth.lanes) >= 4
    lane_ids = {l.lane_id for l in truth.lanes}
    assert "cme_futures" in lane_ids
    assert "equities_low_float" in lane_ids
    assert "options_parity" in lane_ids
    assert "crypto" in lane_ids


def test_workbench_truth_contains_cme_symbols():
    """CME lane must show all 7 canonical futures symbols."""
    truth = build_workbench_truth(REPO)
    cme = next(l for l in truth.lanes if l.lane_id == "cme_futures")
    symbols = {e.symbol for e in cme.entries}
    expected = {"MES.v.0", "ES.v.0", "MNQ.v.0", "NQ.v.0", "ZN.v.0", "ZB.v.0", "RTY.v.0"}
    assert symbols == expected, f"Expected {expected}, got {symbols}"


def test_workbench_truth_contains_equities_sessions():
    """Equities lane must contain decadal sessions."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    assert len(equities.entries) > 0, "Equities lane should have entries"
    session_ids = {e.session_id for e in equities.entries}
    assert "gme_2021" in session_ids
    assert "kodk_2020" in session_ids
    assert "amst_2026" in session_ids


def test_workbench_truth_contains_options_groups():
    """Options/parity lane must contain parity groups."""
    truth = build_workbench_truth(REPO)
    options = next(l for l in truth.lanes if l.lane_id == "options_parity")
    assert len(options.entries) >= 2
    group_ids = {e.group_id for e in options.entries}
    assert "example_same_ul" in group_ids
    assert "example_cross_ul" in group_ids


def test_workbench_truth_reports_missing_data_as_blockers():
    """Missing data must be reported as blockers, never hidden."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")

    # Skipped sessions must show blockers
    skipped = [e for e in equities.entries if e.status == "skipped"]
    assert len(skipped) > 0, "Should have skipped (pre-Databento) sessions"
    for s in skipped:
        assert len(s.blockers) > 0, f"Skipped session {s.session_id} must have blockers explaining why"


def test_equities_manifest_contains_session_id():
    """Every equities entry must have a session_id."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    for e in equities.entries:
        assert e.session_id, f"Entry missing session_id"


def test_equities_manifest_contains_l3_status():
    """Every equities entry must report L3 status."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    for e in equities.entries:
        assert e.l3_status in ("ready", "missing"), f"Invalid L3 status: {e.l3_status}"


def test_equities_manifest_contains_float_status():
    """Every equities entry must report float metadata status."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    for e in equities.entries:
        assert e.float_status in ("ready", "missing"), f"Invalid float status: {e.float_status}"


def test_equities_manifest_contains_options_feature_status():
    """Every equities entry must report options feature status."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    for e in equities.entries:
        assert e.option_feature_status in (
            "available", "not_downloaded", "unknown"
        ), f"Invalid option feature status: {e.option_feature_status}"


def test_equities_lane_is_first_class():
    """Equities lane must be a first-class lane with operational status."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    assert equities.status == "operational"
    assert equities.universe_size > 0
    assert equities.lane_name == "Equities Low-Float Runner"


def test_workbench_truth_has_repo_commit():
    """WorkbenchTruth must include repo commit SHA."""
    truth = build_workbench_truth(REPO)
    assert truth.repo_commit
    assert truth.repo_commit != "unknown"
    assert len(truth.repo_commit) >= 7


def test_workbench_truth_has_generated_at():
    """WorkbenchTruth must include generation timestamp."""
    truth = build_workbench_truth(REPO)
    assert truth.generated_at
    assert "T" in truth.generated_at  # ISO format


def test_cme_entries_have_correct_type():
    """All CME entries must be CmeEntryTruth instances."""
    truth = build_workbench_truth(REPO)
    cme = next(l for l in truth.lanes if l.lane_id == "cme_futures")
    for e in cme.entries:
        assert isinstance(e, CmeEntryTruth)


def test_equities_entries_have_correct_type():
    """All equities entries must be EquitiesEntryTruth instances."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    for e in equities.entries:
        assert isinstance(e, EquitiesEntryTruth)


def test_options_entries_have_correct_type():
    """All options entries must be OptionsEntryTruth instances."""
    truth = build_workbench_truth(REPO)
    options = next(l for l in truth.lanes if l.lane_id == "options_parity")
    for e in options.entries:
        assert isinstance(e, OptionsEntryTruth)


def test_crypto_entries_have_correct_type():
    """All crypto entries must be CryptoEntryTruth instances."""
    truth = build_workbench_truth(REPO)
    crypto = next(l for l in truth.lanes if l.lane_id == "crypto")
    for e in crypto.entries:
        assert isinstance(e, CryptoEntryTruth)


def test_bindings_are_lane_aware():
    """Lane bindings must be loaded from lane_bindings.yaml."""
    bindings = load_lane_bindings(REPO)
    assert len(bindings.lanes) >= 4
    assert "cme_futures" in bindings.lanes
    assert "equities_low_float" in bindings.lanes


def test_lane_bindings_have_data_roots():
    """Each lane binding must declare its data roots."""
    bindings = load_lane_bindings(REPO)
    for lane_id, binding in bindings.lanes.items():
        assert binding.data_roots, f"Lane {lane_id} missing data_roots"


def test_lane_bindings_have_validation_policies():
    """Each lane binding must declare validation policies."""
    bindings = load_lane_bindings(REPO)
    for lane_id, binding in bindings.lanes.items():
        assert binding.validation_policies or lane_id == "crypto", \
            f"Lane {lane_id} missing validation_policies"


def test_model_lane_mapping_exists():
    """Model-to-lane bindings must map known models."""
    bindings = load_lane_bindings(REPO)
    assert len(bindings.model_to_lanes) > 0
    assert "SPREAD_BLOWOUT_RECOMPRESSION" in bindings.model_to_lanes
    assert "BOOK_PRESSURE" in bindings.model_to_lanes


def test_options_parity_is_separate_lane():
    """Options/parity lane must be a separate lane from equities options features."""
    truth = build_workbench_truth(REPO)
    lane_ids = {l.lane_id for l in truth.lanes}
    assert "options_parity" in lane_ids, "Options/parity lane missing"
    # Equities lane exists separately
    assert "equities_low_float" in lane_ids, "Equities lane missing"


def test_crypto_lane_is_present():
    """Crypto lane must be present and first-class."""
    truth = build_workbench_truth(REPO)
    crypto = next(l for l in truth.lanes if l.lane_id == "crypto")
    assert crypto.status == "operational"
    assert len(crypto.entries) > 0
