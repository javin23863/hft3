"""Phase 5 continuous CME candidate generation tests."""

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


def _copy_continuous_configs(tmp_path: Path) -> None:
    config_src = REPO / "packages" / "features_engine" / "config"
    config_dst = tmp_path / "packages" / "features_engine" / "config"
    config_dst.mkdir(parents=True)
    for name in ("cme_relationship_graph.yaml", "cme_universe.yaml", "model_registry.yaml"):
        shutil.copy(config_src / name, config_dst / name)


def _seed_rithmic_week(tmp_path: Path) -> None:
    week_root = tmp_path / "data" / "raw" / "rithmic_continuous" / "2026-W27" / "MESU6"
    week_root.mkdir(parents=True)
    (week_root / "events.ndjson").write_text('{"ev":1}\n', encoding="utf-8")


def test_generate_candidates_from_graph_edges_and_registry_ranges() -> None:
    from research_pipeline.continuous_model_generation import generate_continuous_candidates
    from research_pipeline.relationship_graph import build_relationship_graph_stub

    graph = build_relationship_graph_stub(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    candidates = generate_continuous_candidates(
        graph,
        max_candidates=500,
        model_ids=["MICRO_STANDARD_FLOW_TRANSFER"],
    )
    assert candidates
    for row in candidates:
        assert row["relationship_id"].startswith("micro_standard:")
        assert row["feature_family"] == "cross_market"
        assert row["session_scope"] == "2026-W27:all_sessions"
        assert "source_root" in row
        assert "target_root" in row
        assert row["strategy_params"]
    rel_ids = {c["relationship_id"] for c in candidates}
    assert "micro_standard:MES->ES" in rel_ids


def test_candidates_exclude_flat_symbol_list_expansion() -> None:
    from research_pipeline.continuous_model_generation import generate_continuous_candidates
    from research_pipeline.relationship_graph import build_relationship_graph_stub

    graph = build_relationship_graph_stub(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="pilot_liquidity_top",
    )
    edge_roots = {
        root
        for edge in graph["edges"]
        for root in (edge["source_root"], edge["target_root"])
    }
    candidates = generate_continuous_candidates(graph, max_candidates=2000)
    for row in candidates:
        if row["relationship_id"].startswith("standalone:"):
            continue
        assert row.get("source_root") in edge_roots
        assert row.get("target_root") in edge_roots


def test_rl_overlay_standalone_relationship_id() -> None:
    from research_pipeline.continuous_model_generation import generate_continuous_candidates
    from research_pipeline.relationship_graph import build_relationship_graph_stub

    graph = build_relationship_graph_stub(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    candidates = generate_continuous_candidates(
        graph,
        max_candidates=10,
        model_ids=["RL_EXECUTION_OVERLAY"],
    )
    assert len(candidates) >= 1
    assert all(c["relationship_id"] == "standalone:RL_EXECUTION_OVERLAY" for c in candidates)
    assert all(c["feature_family"] == "execution_cost" for c in candidates)
    assert all(c["pit_bounds_status"] == "unset" for c in candidates)


def test_calendar_curve_matches_rates_curve_edges() -> None:
    from research_pipeline.continuous_model_generation import generate_continuous_candidates
    from research_pipeline.relationship_graph import build_relationship_graph_stub

    graph = build_relationship_graph_stub(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    candidates = generate_continuous_candidates(
        graph,
        max_candidates=50,
        model_ids=["CALENDAR_CURVE_MICRO_IMPULSE"],
    )
    assert candidates
    assert any(c["relationship_id"].startswith("rates_curve:") for c in candidates)


def test_candidates_tag_pit_bounds_unset_on_stub_graph() -> None:
    from research_pipeline.continuous_model_generation import generate_continuous_candidates
    from research_pipeline.relationship_graph import build_relationship_graph_stub

    graph = build_relationship_graph_stub(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    assert graph["summary"]["scoring_ready"] is False
    candidates = generate_continuous_candidates(
        graph, max_candidates=5, model_ids=["MICRO_STANDARD_FLOW_TRANSFER"]
    )
    assert candidates
    assert all(c["pit_bounds_status"] == "unset" for c in candidates)


def test_build_and_write_candidates_artifact(tmp_path: Path) -> None:
    from research_pipeline.continuous_model_generation import (
        build_continuous_candidates_artifact,
        continuous_candidates_path,
        write_continuous_candidates,
    )
    from research_pipeline.relationship_graph import (
        build_relationship_graph_stub,
        write_relationship_graph,
    )

    _copy_continuous_configs(tmp_path)
    graph = build_relationship_graph_stub(
        repo_root=tmp_path,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    graph_path = write_relationship_graph(tmp_path, graph)
    artifact = build_continuous_candidates_artifact(
        repo_root=tmp_path,
        graph=graph,
        relationship_graph_path=graph_path,
        max_candidates=100,
    )
    assert artifact["summary"]["candidate_count"] > 0
    assert artifact["summary"]["source"] == "graph_edges_and_registry_ranges"
    sample = artifact["candidates"][0]
    assert {"relationship_id", "feature_family", "session_scope"} <= set(sample)

    out = write_continuous_candidates(tmp_path, artifact)
    assert out == continuous_candidates_path(tmp_path, "2026-W27")
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "1"


def test_scan_continuous_candidates_requires_graph(tmp_path: Path) -> None:
    from research_pipeline.continuous_model_generation import scan_continuous_candidates

    _copy_continuous_configs(tmp_path)
    with pytest.raises(FileNotFoundError, match="relationship graph missing"):
        scan_continuous_candidates(
            repo_root=tmp_path,
            rithmic_week="2026-W27",
            universe_profile="full_cme_research",
            build_graph_if_missing=False,
        )


def test_run_pipeline_scan_continuous_candidates_flag(tmp_path: Path) -> None:
    _copy_continuous_configs(tmp_path)
    _seed_rithmic_week(tmp_path)

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
            "--scan-continuous-candidates",
            "--continuous-scan-max",
            "200",
            "--repo-root",
            str(tmp_path),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    candidates_path = (
        tmp_path
        / "runtime"
        / "continuous_cme"
        / "candidates"
        / "2026_W27"
        / "candidates.json"
    )
    assert candidates_path.is_file()
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    assert payload["summary"]["candidate_count"] > 0


def test_scan_without_graph_returns_error(tmp_path: Path) -> None:
    _copy_continuous_configs(tmp_path)
    _seed_rithmic_week(tmp_path)

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "run_pipeline.py"),
            "--lane",
            "continuous",
            "--rithmic-week",
            "2026-W27",
            "--scan-continuous-candidates",
            "--repo-root",
            str(tmp_path),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "relationship graph missing" in proc.stderr


def test_run_pipeline_scan_flag_rejects_event_lane() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "run_pipeline.py"),
            "--lane",
            "event",
            "--scan-continuous-candidates",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "scan-continuous-candidates" in proc.stderr


def test_cross_market_feature_shell_and_pair_validation() -> None:
    from research_pipeline.cross_market_features import (
        CROSS_MARKET_FEATURE_NAMES,
        assert_cross_market_feature_names,
        empty_cross_market_feature_shell,
        validate_cross_market_pair,
    )

    shell = empty_cross_market_feature_shell(relationship_id="metals_complex:GC->SI")
    assert shell["group_id"] == "cross_market"
    assert shell["feature_names"] == list(CROSS_MARKET_FEATURE_NAMES)
    assert_cross_market_feature_names(["lagged_ofi_beta"])
    validate_cross_market_pair("GC", "SI")
    with pytest.raises(ValueError, match="cross_market_self_pair"):
        validate_cross_market_pair("GC", "GC")
    with pytest.raises(ValueError, match="unknown_cross_market_feature"):
        assert_cross_market_feature_names(["book_imbalance_future_bar"])


def test_commodity_structure_shell_validation() -> None:
    from research_pipeline.commodity_structure import (
        COMMODITY_COMPLEX_FAMILY_IDS,
        empty_commodity_structure_shell,
        validate_commodity_complex_id,
    )

    assert "rates_curve" in COMMODITY_COMPLEX_FAMILY_IDS
    shell = empty_commodity_structure_shell(complex_id="energy_complex")
    assert shell["group_id"] == "calendar_curve"
    assert shell["complex_id"] == "energy_complex"
    with pytest.raises(ValueError, match="unknown_commodity_complex"):
        validate_commodity_complex_id("micro_standard")


def test_seasonal_state_feature_shell() -> None:
    from research_pipeline.seasonal_state import (
        SEASONAL_STATE_FEATURE_NAMES,
        assert_seasonal_state_feature_names,
        empty_seasonal_state_shell,
    )

    shell = empty_seasonal_state_shell()
    assert shell["group_id"] == "seasonal_state"
    assert shell["feature_names"] == list(SEASONAL_STATE_FEATURE_NAMES)
    assert_seasonal_state_feature_names(["seasonal_state_weight"])
    with pytest.raises(ValueError, match="unknown_seasonal_state_feature"):
        assert_seasonal_state_feature_names(["lagged_ofi_beta"])


def test_disambiguate_relationship_family_from_thesis_keywords() -> None:
    from research_pipeline.hypothesis_parser import disambiguate_relationship_family

    assert disambiguate_relationship_family(
        "Cross-market OFI impact on GC to SI",
        ["metals_complex", "energy_complex", "rates_curve"],
    ) == "metals_complex"
    assert disambiguate_relationship_family(
        "Crude CL RB energy complex OFI",
        ["metals_complex", "energy_complex", "rates_curve"],
    ) == "energy_complex"
    assert disambiguate_relationship_family(
        "Treasury ZN ZB curve impulse",
        ["rates_curve", "calendar_front_second"],
    ) == "rates_curve"


def test_disambiguate_relationship_family_uses_graph_context() -> None:
    from research_pipeline.hypothesis_parser import disambiguate_relationship_family

    graph = {
        "edges": [
            {"family_id": "metals_complex", "source_root": "GC", "target_root": "SI"},
        ],
        "families": ["metals_complex"],
    }
    result = disambiguate_relationship_family(
        "Cross-market OFI impact",
        ["metals_complex", "energy_complex", "rates_curve"],
        relationship_graph=graph,
    )
    assert result == "metals_complex"


def test_parse_continuous_lane_profile_disambiguates_with_graph() -> None:
    from research_pipeline.hypothesis_parser import parse_continuous_lane_profile
    from research_pipeline.relationship_graph import build_relationship_graph_stub

    graph = build_relationship_graph_stub(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    profile = parse_continuous_lane_profile(
        "Structural spread dislocation on MES ES micro",
        relationship_graph=graph,
        use_llm=False,
    )
    assert profile.primary_model_id == "STRUCTURAL_SPREAD_MICRO_DISLOCATION"
    assert profile.relationship_family == "micro_standard"


def test_parse_continuous_lane_profile_calendar_front_second() -> None:
    from research_pipeline.hypothesis_parser import parse_continuous_lane_profile

    profile = parse_continuous_lane_profile(
        "Calendar curve front second roll impulse",
        use_llm=False,
    )
    assert profile.primary_model_id == "CALENDAR_CURVE_MICRO_IMPULSE"
    assert profile.relationship_family == "calendar_front_second"

