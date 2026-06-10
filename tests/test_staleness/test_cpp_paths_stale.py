"""Tests for CORRECTNESS §2 row 11: C++ source trees in CORE_BACKTESTER_PATHS.

Verifies that rithmic_gateway/, risk_engine/, packages/decision_engine/cpp/, and
packages/features_engine/cpp/ appear in CORE_BACKTESTER_PATHS so that any C++
change stales T3 certification stamps.

Also verifies the staleness checker detects a simulated change under each tree.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hft3.validation.certification_registry import CertificationRecord, save_registry
from hft3.validation.certification_staleness import assess_staleness
from hft3.validation.core_engine_paths import CORE_BACKTESTER_PATHS


# ---------------------------------------------------------------------------
# Row 11 — membership assertions
# ---------------------------------------------------------------------------

CPP_TREES = [
    "rithmic_gateway/",
    "risk_engine/",
    "packages/decision_engine/cpp/",
    "packages/features_engine/cpp/",
]


@pytest.mark.parametrize("tree", CPP_TREES)
def test_cpp_tree_present_in_core_backtester_paths(tree: str) -> None:
    """CORE_BACKTESTER_PATHS must contain each C++ source tree (CORRECTNESS row 11)."""
    assert tree in CORE_BACKTESTER_PATHS, (
        f"{tree!r} missing from CORE_BACKTESTER_PATHS — "
        "C++ changes will not stale T3 certification stamps (CORRECTNESS §2 row 11 / defect f)"
    )


# ---------------------------------------------------------------------------
# Staleness detection — simulate a change under each C++ tree
# ---------------------------------------------------------------------------

_REPRESENTATIVE_FILES = {
    "rithmic_gateway/": "rithmic_gateway/src/rithmic_adapter.cpp",
    "risk_engine/": "risk_engine/src/risk_manager.cpp",
    "packages/decision_engine/cpp/": "packages/decision_engine/cpp/src/decision_runtime.cpp",
    "packages/features_engine/cpp/": "packages/features_engine/cpp/src/feature_extractor.cpp",
}


def _make_green_registry(tmp_path: Path) -> None:
    reg = CertificationRecord(
        latest_certification_run_id="CERT-cpp-test",
        latest_certification_commit="base_sha",
        latest_certification_status="GREEN",
        backtester_version="v1",
    )
    save_registry(reg, tmp_path)


@pytest.mark.parametrize("tree", CPP_TREES)
def test_cpp_change_stales_certification(tree: str, tmp_path: Path) -> None:
    """A file change under each C++ tree must cause assess_staleness to report stale."""
    _make_green_registry(tmp_path)
    changed_file = _REPRESENTATIVE_FILES[tree]

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


@pytest.mark.parametrize("tree", CPP_TREES)
def test_no_cpp_change_does_not_stale(tree: str, tmp_path: Path) -> None:
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
