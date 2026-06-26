"""Phase 1 continuous CME lane scaffold tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "packages") not in sys.path:
    sys.path.insert(0, str(REPO / "packages"))


def test_build_coverage_manifest_stub_keys() -> None:
    from research_pipeline.continuous_data_manifest import (
        build_coverage_manifest_stub,
        empty_contract_row,
    )

    manifest = build_coverage_manifest_stub(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    assert manifest["lane"] == "continuous"
    assert manifest["rithmic_week"] == "2026-W27"
    assert manifest["universe_profile"] == "full_cme_research"
    assert "roots" in manifest
    assert "data_types" in manifest
    assert manifest["data_types"] == []
    assert "contract_rows" in manifest
    assert "summary" in manifest
    row = empty_contract_row(contract="ES")
    assert row["contract"] == "ES"
    assert "missing_ratio" in row
    assert "liquidity_score" in row
    assert "eligible" in row
    assert row["missing_ratio"] is None
    assert row["liquidity_score"] is None
    assert row["eligible"] is None


def test_write_coverage_manifest(tmp_path: Path) -> None:
    from research_pipeline.continuous_data_manifest import (
        build_coverage_manifest_stub,
        coverage_manifest_path,
        write_coverage_manifest,
    )

    manifest = build_coverage_manifest_stub(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    out = write_coverage_manifest(tmp_path, manifest)
    assert out == coverage_manifest_path(tmp_path, "2026-W27")
    assert out.is_file()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "1"


def test_validate_universe_profile_rejects_unknown() -> None:
    from research_pipeline.continuous_universe import validate_universe_profile

    assert validate_universe_profile("full_cme_research") == "full_cme_research"
    with pytest.raises(ValueError, match="unknown universe profile"):
        validate_universe_profile("not_a_profile")


def test_run_pipeline_continuous_lane_writes_manifest(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "run_pipeline.py"),
            "--lane",
            "continuous",
            "--rithmic-week",
            "2026-W27",
            "--universe-profile",
            "full_cme_research",
            "--repo-root",
            str(tmp_path),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    manifest_path = tmp_path / "runtime" / "continuous_cme" / "coverage_manifest_2026_W27.json"
    assert manifest_path.is_file()


def test_run_pipeline_event_lane_requires_thesis_and_event_id() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "run_pipeline.py"),
            "--lane",
            "event",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "thesis" in proc.stderr.lower() or "event-id" in proc.stderr.lower()
