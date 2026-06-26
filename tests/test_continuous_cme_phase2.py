"""Phase 2 continuous CME relationship graph scaffold tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "packages") not in sys.path:
    sys.path.insert(0, str(REPO / "packages"))


def test_load_relationship_graph_config_families() -> None:
    from research_pipeline.relationship_graph import load_relationship_graph_config

    config = load_relationship_graph_config(REPO)
    assert config["schema_version"] == "1"
    families = config["families"]
    assert set(families) >= {
        "micro_standard",
        "metals_complex",
        "energy_complex",
        "rates_curve",
    }
    assert families["micro_standard"]["pairs"][0] == ["MES", "ES"]


def test_load_cme_universe_config_roots() -> None:
    from research_pipeline.relationship_graph import load_cme_universe_config

    config = load_cme_universe_config(REPO)
    assert config["schema_version"] == "1"
    es = config["roots"]["ES"]
    assert es["asset_class"] == "equity_index"
    assert es["micro_standard_pair"] == "MES"


def test_enumerate_edges_stub() -> None:
    from research_pipeline.relationship_graph import (
        EDGE_FEATURE_KEYS,
        enumerate_edges,
        load_relationship_graph_config,
    )

    config = load_relationship_graph_config(REPO)
    edges = enumerate_edges(config)
    assert len(edges) >= 16
    families = {edge["family_id"] for edge in edges}
    assert families == {
        "micro_standard",
        "metals_complex",
        "energy_complex",
        "rates_curve",
    }
    sample = edges[0]
    assert sample["edge_id"]
    assert sample["source_root"]
    assert sample["target_root"]
    assert sample["score"] is None
    assert set(sample["features"]) == set(EDGE_FEATURE_KEYS)
    assert all(value is None for value in sample["features"].values())
    assert "causal_bounds" in sample
    assert sample["causal_bounds"]["pit_window_end"] == "unset"


def test_build_relationship_graph_stub_and_write(tmp_path: Path) -> None:
    from research_pipeline.relationship_graph import (
        build_relationship_graph_stub,
        relationship_graph_path,
        write_relationship_graph,
    )

    graph = build_relationship_graph_stub(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    assert graph["lane"] == "continuous"
    assert graph["rithmic_week"] == "2026-W27"
    assert graph["summary"]["edge_count"] == len(graph["edges"])
    assert graph["summary"]["family_count"] == 4

    out = write_relationship_graph(tmp_path, graph)
    assert out == relationship_graph_path(tmp_path, "2026-W27")
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "1"
    assert loaded["summary"]["edge_count"] > 0


def test_enumerate_edges_rejects_malformed_pairs() -> None:
    from research_pipeline.relationship_graph import enumerate_edges

    config = {
        "families": {
            "bad": {
                "pairs": [["ONLY_ONE"], ("A", "B", "C"), ["OK", "PAIR"]],
            }
        }
    }
    edges = enumerate_edges(config)
    assert len(edges) == 1
    assert edges[0]["source_root"] == "OK"
    assert edges[0]["target_root"] == "PAIR"


def test_load_relationship_graph_config_missing_raises(tmp_path: Path) -> None:
    from research_pipeline.relationship_graph import load_relationship_graph_config

    with pytest.raises(FileNotFoundError):
        load_relationship_graph_config(tmp_path)


def test_build_graph_includes_causal_bounds_and_validates_roots() -> None:
    from research_pipeline.relationship_graph import (
        CAUSAL_BOUNDS_KEYS,
        CAUSAL_BOUND_UNSET,
        build_relationship_graph_stub,
        require_edges_scorable,
    )

    graph = build_relationship_graph_stub(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    sample = graph["edges"][0]
    assert set(sample["causal_bounds"]) == set(CAUSAL_BOUNDS_KEYS)
    assert all(value == CAUSAL_BOUND_UNSET for value in sample["causal_bounds"].values())
    assert graph["summary"]["scoring_ready"] is False
    with pytest.raises(ValueError, match="not set for scoring"):
        require_edges_scorable(graph["edges"])


def test_assert_causal_bounds_ready_rejects_porous_values() -> None:
    from research_pipeline.relationship_graph import assert_causal_bounds_ready

    with pytest.raises(ValueError, match="not set for scoring"):
        assert_causal_bounds_ready(
            {
                "pit_window_end": "",
                "as_of_event_time": "2026-07-03T15:00:00",
                "max_lag_sessions": 3,
            }
        )
    with pytest.raises(ValueError, match="must be ISO event-time"):
        assert_causal_bounds_ready(
            {
                "pit_window_end": "not-a-timestamp",
                "as_of_event_time": "2026-07-03T15:00:00",
                "max_lag_sessions": 3,
            }
        )
    with pytest.raises(ValueError, match="positive int"):
        assert_causal_bounds_ready(
            {
                "pit_window_end": "2026-07-03T21:00:00",
                "as_of_event_time": "2026-07-03T15:00:00",
                "max_lag_sessions": 0,
            }
        )


def test_assert_causal_bounds_ready_accepts_valid_bounds() -> None:
    from research_pipeline.relationship_graph import assert_causal_bounds_ready

    assert_causal_bounds_ready(
        {
            "pit_window_end": "2026-07-03T21:00:00",
            "as_of_event_time": "2026-07-03T15:00:00",
            "max_lag_sessions": 5,
        }
    )


def test_build_graph_filters_edges_for_pilot_profile() -> None:
    from research_pipeline.relationship_graph import build_relationship_graph_stub

    full = build_relationship_graph_stub(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="full_cme_research",
    )
    pilot = build_relationship_graph_stub(
        repo_root=REPO,
        rithmic_week="2026-W27",
        universe_profile="pilot_liquidity_top",
    )
    assert pilot["summary"]["edge_count"] <= full["summary"]["edge_count"]
    assert pilot["summary"]["edge_count"] > 0
    for edge in pilot["edges"]:
        assert edge["source_root"] in {
            "MES", "ES", "MNQ", "NQ", "MGC", "GC", "MCL", "CL", "SI", "HG",
            "RB", "HO", "NG", "ZT", "ZF", "ZN", "ZB", "UB",
        }


def test_validate_edge_roots_raises_for_unknown_root() -> None:
    from research_pipeline.relationship_graph import (
        enumerate_edges,
        load_cme_universe_config,
        validate_edge_roots,
    )

    universe = load_cme_universe_config(REPO)
    edges = enumerate_edges(
        {"families": {"x": {"pairs": [["ZZZ", "YYY"]]}}}
    )
    with pytest.raises(ValueError, match="missing from cme_universe.yaml"):
        validate_edge_roots(edges, universe)


def test_energy_complex_excludes_micro_standard_mcl_cl_duplicate() -> None:
    from research_pipeline.relationship_graph import (
        enumerate_edges,
        load_relationship_graph_config,
    )

    config = load_relationship_graph_config(REPO)
    edges = enumerate_edges(config)
    mcl_cl = [
        edge
        for edge in edges
        if {edge["source_root"], edge["target_root"]} == {"MCL", "CL"}
    ]
    assert len(mcl_cl) == 1
    assert mcl_cl[0]["family_id"] == "micro_standard"
