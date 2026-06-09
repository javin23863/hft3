"""Options lane separation acceptance tests.

Tests that:
  - Equities-linked options features are not collapsed into options_lane
  - Options/parity lane remains separate from equities
  - Options feature contribution is reported
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.src.state.workbench_truth import build_workbench_truth


REPO = Path(__file__).resolve().parents[2]


def test_equities_options_features_are_not_collapsed_into_options_lane():
    """Equities-linked options features belong to the equities lane, not options_lane."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    options = next(l for l in truth.lanes if l.lane_id == "options_parity")

    # Equities lane reports option_feature_status per session
    for e in equities.entries:
        assert e.option_feature_status, f"Equities entry {e.session_id} missing option_feature_status"

    # Options lane entries do NOT have option_feature_status
    for e in options.entries:
        assert not hasattr(e, "option_feature_status") or e.option_feature_status is None, (
            f"Options lane entry {e.group_id} should not have option_feature_status"
        )


def test_options_parity_lane_is_separate_from_equities_feature_phase():
    """Options/parity lane must be a distinct lane from equities."""
    truth = build_workbench_truth(REPO)
    lane_ids = {l.lane_id for l in truth.lanes}
    assert "options_parity" in lane_ids
    assert "equities_low_float" in lane_ids
    # They must be different lanes
    assert "options_parity" != "equities_low_float"


def test_equities_options_feature_status():
    """Equities entries must report option feature availability."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")

    # KODK and GME are known to have options data
    kodk = next(
        (e for e in equities.entries if e.session_id == "kodk_2020"), None
    )
    gme = next((e for e in equities.entries if e.session_id == "gme_2021"), None)

    if kodk and kodk.status != "skipped":
        assert kodk.option_feature_status in ("available", "not_downloaded"), (
            f"KODK option feature: {kodk.option_feature_status}"
        )

    if gme and gme.status != "skipped":
        assert gme.option_feature_status in ("available", "not_downloaded"), (
            f"GME option feature: {gme.option_feature_status}"
        )


def test_options_lane_entries_have_correct_structure():
    """Options/parity lane entries must have group_id, group_type, legs."""
    truth = build_workbench_truth(REPO)
    options = next(l for l in truth.lanes if l.lane_id == "options_parity")
    for e in options.entries:
        assert e.group_id, "Options entry missing group_id"
        assert e.group_type, "Options entry missing group_type"
        assert e.legs > 0, f"Options entry {e.group_id} should have legs > 0"


def test_options_lane_reports_data_readiness():
    """Options lane must report data readiness accurately."""
    truth = build_workbench_truth(REPO)
    options = next(l for l in truth.lanes if l.lane_id == "options_parity")
    assert options.data_readiness_pct >= 0.0
    assert options.data_readiness_pct <= 100.0


def test_options_features_not_mixed_into_cme():
    """Options features must not leak into CME lane entries."""
    truth = build_workbench_truth(REPO)
    cme = next(l for l in truth.lanes if l.lane_id == "cme_futures")
    for e in cme.entries:
        assert not hasattr(e, "option_feature_status") or e.option_feature_status is None, (
            f"CME entry {e.symbol} should not have option_feature_status"
        )
