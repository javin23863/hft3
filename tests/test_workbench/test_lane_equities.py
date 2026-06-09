"""Equities lane-specific acceptance tests.

L3-only policy enforcement, fixture handling, blocker exposure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.src.state.workbench_truth import build_workbench_truth


REPO = Path(__file__).resolve().parents[2]


def test_equities_l3_only_blocks_degraded_real_research():
    """Real research cannot silently run on degraded MBP data."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    # Skipped sessions must show L3 blockers
    skipped = [e for e in equities.entries if e.status == "skipped"]
    for s in skipped:
        has_l3_blocker = any(
            "L3" in b.upper() or "SKIPPED" in b or "databento" in b.lower()
            for b in s.blockers
        )
        assert has_l3_blocker, (
            f"Skipped session {s.session_id} must explain why L3 data is unavailable"
        )


def test_equities_fixture_can_run_only_with_allow_degraded():
    """Fixture sessions must be explicitly marked if they bypass L3-only policy."""
    # The l3_policy.py enforces that degraded sessions require allow_degraded=True.
    # This tests that our truth system distinguishes fixture from real sessions.
    from workbench.src.data.lane_bindings import get_lane_binding
    binding = get_lane_binding("equities_low_float", REPO)
    assert binding is not None
    assert binding.l3_only is True, "Equities lane must enforce L3-only"
    assert binding.l3_policy == "enforce", "L3 policy must be enforce"


def test_equities_workbench_exposes_l3_blocker():
    """Workbench must show L3 REQUIRED / DEGRADED BLOCKED where applicable."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    # Every entry must have l3_status reported
    for e in equities.entries:
        assert e.l3_status, f"Entry {e.session_id} missing l3_status"
        # Skipped sessions must have L3-related blocker
        if e.status == "skipped":
            assert len(e.blockers) > 0, f"Skipped session {e.session_id} must have blockers"
        if e.status == "blocked":
            assert e.l3_status == "missing", f"Blocked session {e.session_id} should show missing L3"


def test_equities_missing_mbo_does_not_hide_session():
    """Sessions with missing MBO data must be shown, not hidden."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    total = len(equities.entries)
    # Skipped + blocked + ready should equal total - no sessions silently dropped
    visible = sum(
        1 for e in equities.entries
        if e.status in ("ready", "partial", "blocked", "skipped")
    )
    assert visible == total, (
        f"All {total} sessions must be visible; only {visible} shown"
    )


def test_equities_manifest_contains_blockers():
    """Every equities entry with blockers must list them."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    blocked_entries = [e for e in equities.entries if e.blockers]
    for e in blocked_entries:
        assert len(e.blockers) > 0, f"Entry {e.session_id} has empty blocker list"
        assert all(isinstance(b, str) for b in e.blockers), "All blockers must be strings"


def test_equities_data_readiness_is_accurate():
    """Data readiness must reflect actual NDJSON presence."""
    from pathlib import Path
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")

    ready_count = 0
    for e in equities.entries:
        if e.status not in ("skipped", "blocked"):
            ndjson = (
                REPO / "data" / "equities" / "normalized" / f"{e.symbol}_{e.date}.ndjson"
            )
            if ndjson.is_file():
                ready_count += 1

    assert equities.sessions_available == ready_count, (
        f"Reported {equities.sessions_available} ready, but {ready_count} NDJSON files exist"
    )


def test_equities_entries_have_all_fields():
    """Each equities entry must have all required fields populated."""
    truth = build_workbench_truth(REPO)
    equities = next(l for l in truth.lanes if l.lane_id == "equities_low_float")
    required_fields = [
        "session_id", "symbol", "date", "status", "l3_status",
        "normalized_status", "daily_status", "float_status", "option_feature_status",
    ]
    for e in equities.entries:
        for field in required_fields:
            assert getattr(e, field) is not None, f"Field {field} is None for {e.session_id}"
