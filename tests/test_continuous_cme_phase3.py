"""Phase 3 continuous CME feature store and PIT leakage tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "packages") not in sys.path:
    sys.path.insert(0, str(REPO / "packages"))


def test_feature_group_taxonomy_matches_plan() -> None:
    from research_pipeline.continuous_feature_store import FEATURE_GROUP_KEYS

    assert FEATURE_GROUP_KEYS == (
        "order_flow",
        "queue_dynamics",
        "spread_depth",
        "cross_market",
        "calendar_curve",
        "seasonal_state",
        "toxicity",
        "execution_cost",
    )


def test_assert_no_timestamp_leakage_accepts_causal_order() -> None:
    from research_pipeline.continuous_feature_store import assert_no_timestamp_leakage

    assert_no_timestamp_leakage(
        decision_timestamp="2026-07-03T15:00:00",
        source_timestamps=["2026-07-03T14:59:00", "2026-07-03T15:00:00"],
    )


def test_assert_no_timestamp_leakage_rejects_future_source() -> None:
    from research_pipeline.continuous_feature_store import assert_no_timestamp_leakage

    with pytest.raises(ValueError, match="timestamp_leakage"):
        assert_no_timestamp_leakage(
            decision_timestamp="2026-07-03T15:00:00",
            source_timestamps=["2026-07-03T15:00:01"],
        )


def test_assert_no_forbidden_feature_names() -> None:
    from research_pipeline.continuous_feature_store import assert_no_forbidden_feature_names

    assert_no_forbidden_feature_names(["book_imbalance", "ofi_1s"])
    with pytest.raises(ValueError, match="forbidden_lookahead_feature"):
        assert_no_forbidden_feature_names(["spread_z_future_bar"])


def test_validate_feature_row_pit_missing_decision() -> None:
    from research_pipeline.continuous_feature_store import validate_feature_row_pit

    errors = validate_feature_row_pit({"feature_names": [], "source_timestamps": []})
    assert "missing_decision_timestamp" in errors


def test_build_feature_store_stub_and_write(tmp_path: Path) -> None:
    from research_pipeline.continuous_feature_store import (
        build_continuous_feature_store_stub,
        feature_store_path,
        write_continuous_feature_store,
    )

    matrix = build_continuous_feature_store_stub(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    assert matrix["lane"] == "continuous"
    assert matrix["summary"]["group_count"] == 8
    assert matrix["summary"]["pit_validated"] is False
    assert matrix["summary"]["data_loaded"] is False

    out = write_continuous_feature_store(tmp_path, matrix)
    assert out == feature_store_path(tmp_path, "2026-W27")
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "1"
    assert len(loaded["feature_groups"]) == 8


def test_write_rejects_leaky_row(tmp_path: Path) -> None:
    from research_pipeline.continuous_feature_store import (
        build_continuous_feature_store_stub,
        write_continuous_feature_store,
    )

    matrix = build_continuous_feature_store_stub(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    matrix["rows"] = [
        {
            "decision_timestamp": "2026-07-03T15:00:00",
            "feature_names": ["ofi"],
            "source_timestamps": ["2026-07-03T15:00:05"],
        }
    ]
    with pytest.raises(ValueError, match="feature_matrix_pit_invalid"):
        write_continuous_feature_store(tmp_path, matrix)


def test_assert_no_timestamp_leakage_normalizes_mixed_tz() -> None:
    from research_pipeline.continuous_feature_store import assert_no_timestamp_leakage

    assert_no_timestamp_leakage(
        decision_timestamp="2026-07-03T15:00:00+00:00",
        source_timestamps=["2026-07-03T14:59:00"],
    )


def test_write_rejects_forbidden_group_feature_name(tmp_path: Path) -> None:
    from research_pipeline.continuous_feature_store import (
        build_continuous_feature_store_stub,
        write_continuous_feature_store,
    )

    matrix = build_continuous_feature_store_stub(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    matrix["feature_groups"][0]["feature_names"] = ["spread_z_future_bar"]
    with pytest.raises(ValueError, match="feature_matrix_pit_invalid"):
        write_continuous_feature_store(tmp_path, matrix)


def test_validate_feature_group_missingness_requires_ratio_when_rows() -> None:
    from research_pipeline.continuous_feature_store import validate_feature_group_missingness

    assert validate_feature_group_missingness({"row_count": 0, "missingness_ratio": None}) == []
    assert validate_feature_group_missingness({"row_count": 10, "missingness_ratio": None}) == [
        "missing_missingness_ratio"
    ]
    assert validate_feature_group_missingness({"row_count": 10, "missingness_ratio": 0.05}) == []
    assert "missingness_out_of_range" in validate_feature_group_missingness(
        {"row_count": 10, "missingness_ratio": 1.5}
    )


def test_write_sets_pit_validated_true(tmp_path: Path) -> None:
    from research_pipeline.continuous_feature_store import (
        build_continuous_feature_store_stub,
        write_continuous_feature_store,
    )

    matrix = build_continuous_feature_store_stub(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    write_continuous_feature_store(tmp_path, matrix)
    loaded = json.loads(
        (tmp_path / "runtime" / "continuous_cme" / "feature_store" / "2026_W27" / "feature_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    assert loaded["summary"]["pit_validated"] is True


def test_write_rejects_group_with_rows_but_no_missingness(tmp_path: Path) -> None:
    from research_pipeline.continuous_feature_store import (
        build_continuous_feature_store_stub,
        write_continuous_feature_store,
    )

    matrix = build_continuous_feature_store_stub(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    matrix["feature_groups"][0]["row_count"] = 5
    with pytest.raises(ValueError, match="missing_missingness_ratio"):
        write_continuous_feature_store(tmp_path, matrix)


def test_run_pipeline_rejects_build_feature_store_on_event_lane() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "run_pipeline.py"),
            "--lane",
            "event",
            "--build-feature-store",
            "--thesis",
            "test",
            "--event-id",
            "CPI_2024_09_11_TIGHT",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "build-feature-store" in proc.stderr


def test_run_pipeline_rejects_build_graph_on_event_lane() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "run_pipeline.py"),
            "--lane",
            "event",
            "--build-relationship-graph",
            "--thesis",
            "test",
            "--event-id",
            "CPI_2024_09_11_TIGHT",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "build-relationship-graph" in proc.stderr


def test_run_pipeline_build_relationship_graph_flag(tmp_path: Path) -> None:
    config_src = REPO / "packages" / "features_engine" / "config"
    config_dst = tmp_path / "packages" / "features_engine" / "config"
    config_dst.mkdir(parents=True)
    for name in ("cme_relationship_graph.yaml", "cme_universe.yaml"):
        shutil.copy(config_src / name, config_dst / name)

    week_root = tmp_path / "data" / "raw" / "rithmic_continuous" / "2026-W27" / "MESU6"
    week_root.mkdir(parents=True)
    (week_root / "events.ndjson").write_text('{"ev":1}\n', encoding="utf-8")

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
            "--build-relationship-graph",
            "--repo-root",
            str(tmp_path),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    graph_path = (
        tmp_path
        / "runtime"
        / "continuous_cme"
        / "relationship_graph"
        / "2026_W27"
        / "graph.json"
    )
    assert graph_path.is_file()
    loaded = json.loads(graph_path.read_text(encoding="utf-8"))
    assert loaded["summary"]["edge_count"] > 0


def test_run_pipeline_build_feature_store_flag(tmp_path: Path) -> None:
    config_src = REPO / "packages" / "features_engine" / "config"
    config_dst = tmp_path / "packages" / "features_engine" / "config"
    config_dst.mkdir(parents=True)
    for name in ("cme_relationship_graph.yaml", "cme_universe.yaml"):
        shutil.copy(config_src / name, config_dst / name)

    week_root = tmp_path / "data" / "raw" / "rithmic_continuous" / "2026-W27" / "MESU6"
    week_root.mkdir(parents=True)
    (week_root / "events.ndjson").write_text('{"ev":1}\n', encoding="utf-8")

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
            "--build-feature-store",
            "--repo-root",
            str(tmp_path),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    fs_path = (
        tmp_path
        / "runtime"
        / "continuous_cme"
        / "feature_store"
        / "2026_W27"
        / "feature_matrix.json"
    )
    assert fs_path.is_file()
    loaded = json.loads(fs_path.read_text(encoding="utf-8"))
    assert loaded["summary"]["pit_validated"] is True
    assert loaded["summary"]["group_count"] == 8
