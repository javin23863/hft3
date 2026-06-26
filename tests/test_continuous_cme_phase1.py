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


def _write_events(path: Path, lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join('{"ev":1}' for _ in range(lines)) + "\n", encoding="utf-8")


def test_build_coverage_manifest_stub_keys() -> None:
    from research_pipeline.continuous_data_manifest import (
        build_coverage_manifest,
        empty_contract_row,
    )

    manifest = build_coverage_manifest(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    assert manifest["lane"] == "continuous"
    assert manifest["rithmic_week"] == "2026-W27"
    assert manifest["universe_profile"] == "full_cme_research"
    assert "roots" in manifest
    assert isinstance(manifest["roots"], list)
    assert manifest["roots"]
    assert "data_types" in manifest
    assert manifest["data_types"] == ["mbo", "quotes", "trades"]
    assert "contract_rows" in manifest
    assert "summary" in manifest
    assert manifest["summary"]["expected_trading_days"] == 5
    row = empty_contract_row(contract="ES")
    assert row["contract"] == "ES"
    assert "missing_ratio" in row
    assert "liquidity_score" in row
    assert "eligible" in row
    assert "row_count" in row


def test_discover_rithmic_weekly_roots_from_fixture(tmp_path: Path) -> None:
    from research_pipeline.continuous_data_manifest import (
        build_coverage_manifest,
        discover_rithmic_weekly_roots,
    )

    week_root = tmp_path / "data" / "raw" / "rithmic_continuous" / "2026-W27" / "ESM6"
    _write_events(week_root / "2026-07-01" / "events.ndjson", 2)
    _write_events(week_root / "2026-07-02" / "events.ndjson", 3)

    roots = discover_rithmic_weekly_roots(tmp_path, "2026-W27")
    assert any(root["exists"] for root in roots)

    manifest = build_coverage_manifest(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    assert manifest["contracts"] == ["ESM6"]
    row = manifest["contract_rows"][0]
    assert row["contract"] == "ESM6"
    assert row["row_count"] == 5
    assert row["missing_ratio"] == pytest.approx(0.6)
    assert row["liquidity_score"] == pytest.approx(5 / 10_000)
    assert row["eligible"] is False
    assert manifest["summary"]["total_rows"] == 5
    assert manifest["summary"]["eligible_contracts"] == 0


def test_date_first_layout_aggregates_multi_day(tmp_path: Path) -> None:
    from research_pipeline.continuous_data_manifest import build_coverage_manifest

    week_root = tmp_path / "data" / "raw" / "rithmic_continuous" / "2026-W27"
    _write_events(week_root / "2026-07-01" / "ESM6" / "events.ndjson", 2)
    _write_events(week_root / "2026-07-02" / "ESM6" / "events.ndjson", 3)
    _write_events(week_root / "2026-07-03" / "MESU6" / "events.ndjson", 10)

    manifest = build_coverage_manifest(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    assert manifest["contracts"] == ["ESM6", "MESU6"]
    es_row = next(row for row in manifest["contract_rows"] if row["contract"] == "ESM6")
    assert es_row["row_count"] == 5
    assert es_row["missing_ratio"] == pytest.approx(0.6)
    assert manifest["summary"]["total_rows"] == 15


def test_flat_contract_events_without_date_dirs_uses_none_missing_ratio(
    tmp_path: Path,
) -> None:
    from research_pipeline.continuous_data_manifest import build_coverage_manifest

    week_root = tmp_path / "data" / "raw" / "rithmic_continuous" / "2026-W27" / "MESU6"
    _write_events(week_root / "events.ndjson", 100)

    manifest = build_coverage_manifest(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    row = manifest["contract_rows"][0]
    assert row["row_count"] == 100
    assert row["missing_ratio"] is None
    assert row["eligible"] is False


def test_data_types_derived_from_present_files(tmp_path: Path) -> None:
    from research_pipeline.continuous_data_manifest import build_coverage_manifest

    week_root = tmp_path / "data" / "raw" / "rithmic_continuous" / "2026-W27" / "ESM6"
    _write_events(week_root / "2026-07-01" / "mbo.ndjson", 1)
    _write_events(week_root / "2026-07-01" / "trades.ndjson", 1)

    manifest = build_coverage_manifest(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    assert manifest["data_types"] == ["mbo", "trades"]


def test_invalid_rithmic_week_fails_closed(tmp_path: Path) -> None:
    from research_pipeline.continuous_data_manifest import build_coverage_manifest

    with pytest.raises(ValueError, match="invalid rithmic_week"):
        build_coverage_manifest(
            repo_root=tmp_path,
            rithmic_week="not-a-week",
            universe_profile="full_cme_research",
        )


def test_pilot_profile_filters_contract_roots(tmp_path: Path) -> None:
    from research_pipeline.continuous_data_manifest import build_coverage_manifest

    week_root = tmp_path / "data" / "raw" / "rithmic_weekly" / "2026-W27"
    for day in ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"):
        _write_events(week_root / "ESM6" / day / "events.ndjson", 3000)
    _write_events(week_root / "ZCZ6" / "events.ndjson", 12_000)

    manifest = build_coverage_manifest(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="pilot_liquidity_top",
    )
    assert manifest["contracts"] == ["ESM6"]
    assert manifest["contract_rows"][0]["eligible"] is True
    assert manifest["summary"]["eligible_contracts"] == 1


def test_continuous_universe_filters() -> None:
    from research_pipeline.continuous_universe import (
        compute_eligibility,
        contract_root_symbol,
        filter_contracts_for_profile,
        is_active_for_profile,
        passes_coverage_filter,
        passes_liquidity_filter,
    )

    assert contract_root_symbol("ESM6") == "ES"
    assert contract_root_symbol("MESZ5") == "MES"
    assert is_active_for_profile("ESM6", "full_cme_research")
    assert is_active_for_profile("ESM6", "pilot_liquidity_top")
    assert not is_active_for_profile("ZCZ6", "pilot_liquidity_top")
    assert filter_contracts_for_profile(["ESM6", "ZCZ6"], "pilot_liquidity_top") == ["ESM6"]
    assert passes_coverage_filter(0.2)
    assert not passes_coverage_filter(0.9)
    assert passes_liquidity_filter(0.2)
    assert not passes_liquidity_filter(0.01)
    assert compute_eligibility(
        contract="ESM6",
        missing_ratio=0.1,
        liquidity_score=0.5,
        universe_profile="pilot_liquidity_top",
    ) is True


def test_write_coverage_manifest(tmp_path: Path) -> None:
    from research_pipeline.continuous_data_manifest import (
        build_coverage_manifest,
        coverage_manifest_path,
        write_coverage_manifest,
    )

    manifest = build_coverage_manifest(
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
    week_root = tmp_path / "data" / "raw" / "rithmic_continuous" / "2026-W27" / "MESU6"
    _write_events(week_root / "events.ndjson", 100)

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
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert loaded["contracts"] == ["MESU6"]
    assert loaded["summary"]["total_rows"] == 100
    assert loaded["contract_rows"][0]["missing_ratio"] is None
    assert loaded["contract_rows"][0]["eligible"] is False


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


def test_empty_date_partition_events_do_not_inflate_days_with_data(
    tmp_path: Path,
) -> None:
    from research_pipeline.continuous_data_manifest import build_coverage_manifest

    week_root = tmp_path / "data" / "raw" / "rithmic_continuous" / "2026-W27" / "ESM6"
    # ISO 2026-W27 trading days: Mon 2026-06-29 .. Fri 2026-07-03
    for day in ("2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"):
        (week_root / day).mkdir(parents=True)
        (week_root / day / "events.ndjson").write_text("\n", encoding="utf-8")
    _write_events(week_root / "2026-07-03" / "events.ndjson", 50)

    manifest = build_coverage_manifest(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    row = manifest["contract_rows"][0]
    assert row["row_count"] == 50
    assert row["missing_ratio"] == pytest.approx(0.8)


def test_typed_only_flat_capture_uses_none_missing_ratio(tmp_path: Path) -> None:
    from research_pipeline.continuous_data_manifest import build_coverage_manifest

    week_root = tmp_path / "data" / "raw" / "rithmic_continuous" / "2026-W27" / "ESM6"
    _write_events(week_root / "mbo.ndjson", 25)
    _write_events(week_root / "trades.ndjson", 25)

    manifest = build_coverage_manifest(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    row = manifest["contract_rows"][0]
    assert row["row_count"] == 50
    assert row["missing_ratio"] is None
    assert row["eligible"] is False
