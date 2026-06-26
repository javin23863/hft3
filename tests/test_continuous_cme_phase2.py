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
