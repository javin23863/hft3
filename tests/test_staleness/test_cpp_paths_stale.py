"""Tests for CORRECTNESS §2 row 11: C++ source trees in CORE_BACKTESTER_PATHS.

Verifies that granular src/include subpaths (not bare trailing-slash dirs) appear
in CORE_BACKTESTER_PATHS, and that the engine/ tree added in M3 is covered.

Also verifies the staleness checker detects a simulated change under each tree,
and that a path under rithmic_gateway/tests/ is NOT treated as core.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hft3.validation.certification_registry import CertificationRecord, save_registry
from hft3.validation.certification_staleness import assess_staleness
from hft3.validation.core_engine_paths import CORE_BACKTESTER_PATHS


# ---------------------------------------------------------------------------
# Row 11 — membership assertions (granular subpaths)
# ---------------------------------------------------------------------------

CPP_SUBPATHS = [
    "rithmic_gateway/src/",
    "rithmic_gateway/include/",
    "risk_engine/src/",
    "risk_engine/include/",
    "packages/decision_engine/cpp/src/",
    "packages/decision_engine/cpp/include/",
    "packages/features_engine/cpp/src/",
    "packages/features_engine/cpp/include/",
    "packages/features_engine/cpp/bindings/",
    "engine/src/",
    "engine/include/",
]


@pytest.mark.parametrize("subpath", CPP_SUBPATHS)
def test_cpp_subpath_present_in_core_backtester_paths(subpath: str) -> None:
    """CORE_BACKTESTER_PATHS must contain each C++ src/include subpath (CORRECTNESS row 11)."""
    assert subpath in CORE_BACKTESTER_PATHS, (
        f"{subpath!r} missing from CORE_BACKTESTER_PATHS — "
        "C++ changes will not stale T3 certification stamps (CORRECTNESS §2 row 11 / defect f)"
    )


# ---------------------------------------------------------------------------
# Negative test — test-only paths are NOT core
# ---------------------------------------------------------------------------

def test_rithmic_gateway_tests_not_core() -> None:
    """A path under rithmic_gateway/tests/ must NOT appear in CORE_BACKTESTER_PATHS."""
    for entry in CORE_BACKTESTER_PATHS:
        assert not "rithmic_gateway/tests/" == entry, (
            "rithmic_gateway/tests/ should not be a core path — test-only edits cause over-staleness noise"
        )
        # also ensure the bare trailing-slash dir is gone (would match tests/ recursively)
        assert entry != "rithmic_gateway/", (
            "bare rithmic_gateway/ must be replaced with src/include subpaths"
        )


# ---------------------------------------------------------------------------
# Staleness detection — simulate a change under each C++ tree
# ---------------------------------------------------------------------------

_REPRESENTATIVE_FILES: dict[str, str] = {
    "rithmic_gateway/src/": "rithmic_gateway/src/rithmic_adapter.cpp",
    "rithmic_gateway/include/": "rithmic_gateway/include/rithmic_adapter.h",
    "risk_engine/src/": "risk_engine/src/risk_manager.cpp",
    "risk_engine/include/": "risk_engine/include/risk_manager.h",
    "packages/decision_engine/cpp/src/": "packages/decision_engine/cpp/src/decision_runtime.cpp",
    "packages/decision_engine/cpp/include/": "packages/decision_engine/cpp/include/decision_runtime.h",
    "packages/features_engine/cpp/src/": "packages/features_engine/cpp/src/feature_extractor.cpp",
    "packages/features_engine/cpp/include/": "packages/features_engine/cpp/include/feature_extractor.h",
    "packages/features_engine/cpp/bindings/": "packages/features_engine/cpp/bindings/features_pybind.cpp",
    "engine/src/": "engine/src/live_engine.cpp",
    "engine/include/": "engine/include/live_engine.h",
}


def _make_green_registry(tmp_path: Path) -> None:
    reg = CertificationRecord(
        latest_certification_run_id="CERT-cpp-test",
        latest_certification_commit="base_sha",
        latest_certification_status="GREEN",
        backtester_version="v1",
    )
    save_registry(reg, tmp_path)


@pytest.mark.parametrize("subpath", CPP_SUBPATHS)
def test_cpp_change_stales_certification(subpath: str, tmp_path: Path) -> None:
    """A file change under each C++ subpath must cause assess_staleness to report stale."""
    _make_green_registry(tmp_path)
    changed_file = _REPRESENTATIVE_FILES[subpath]

    with patch("hft3.validation.certification_staleness.git_sha", return_value="new_sha"):
        with patch(
            "hft3.validation.certification_staleness._git_diff_files",
            return_value=[changed_file],
        ):
            with patch(
                "hft3.validation.certification_staleness._commit_is_ancestor",
                return_value=True,
            ):
                result = assess_staleness(tmp_path)

    assert result.certification_is_current is False, (
        f"Change in {changed_file!r} should stale the certification stamp"
    )
    assert result.stale_reason == "core_backtester_files_changed_since_certification"
    assert changed_file in result.changed_core_files


@pytest.mark.parametrize("subpath", CPP_SUBPATHS)
def test_no_cpp_change_does_not_stale(subpath: str, tmp_path: Path) -> None:
    """With no changed files reported, a GREEN stamp stays current."""
    _make_green_registry(tmp_path)

    with patch("hft3.validation.certification_staleness.git_sha", return_value="base_sha"):
        with patch(
            "hft3.validation.certification_staleness._git_diff_files",
            return_value=[],
        ):
            with patch(
                "hft3.validation.certification_staleness._commit_is_ancestor",
                return_value=True,
            ):
                result = assess_staleness(tmp_path)

    assert result.certification_is_current is True


# ---------------------------------------------------------------------------
# Negative staleness — change under rithmic_gateway/tests/ does NOT stale
# ---------------------------------------------------------------------------

def test_rithmic_gateway_test_file_does_not_stale(tmp_path: Path) -> None:
    """A change under rithmic_gateway/tests/ must NOT stale the certification stamp.

    git diff --name-only -- rithmic_gateway/src/ rithmic_gateway/include/ ... will not
    return rithmic_gateway/tests/... because that subpath is not in CORE_BACKTESTER_PATHS.
    The mock simulates that: _git_diff_files returns [] (git filtered it out).
    """
    _make_green_registry(tmp_path)

    with patch("hft3.validation.certification_staleness.git_sha", return_value="new_sha"):
        with patch(
            "hft3.validation.certification_staleness._git_diff_files",
            return_value=[],  # git found no core files changed — test-only path excluded by pathspecs
        ):
            with patch(
                "hft3.validation.certification_staleness._commit_is_ancestor",
                return_value=True,
            ):
                result = assess_staleness(tmp_path)

    assert result.certification_is_current is True, (
        "rithmic_gateway/tests/ change (not a core pathspec) must NOT stale the certification stamp"
    )
