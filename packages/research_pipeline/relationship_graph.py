"""CME relationship graph builder (Phase 2 stub)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

GRAPH_SCHEMA_VERSION = "1"
_DEFAULT_GRAPH_CONFIG = Path("packages/features_engine/config/cme_relationship_graph.yaml")
_DEFAULT_UNIVERSE_CONFIG = Path("packages/features_engine/config/cme_universe.yaml")

EDGE_FEATURE_KEYS = (
    "lagged_correlation",
    "lagged_ofi_beta",
    "lead_lag_stability",
    "spread_z_score",
    "cointegration_residual",
    "volume_leadership",
    "queue_pressure_divergence",
    "impact_decay_half_life",
    "cost_feasibility",
)


def relationship_graph_dir(repo_root: Path, rithmic_week: str) -> Path:
    safe_week = rithmic_week.replace("-", "_")
    return repo_root / "runtime" / "continuous_cme" / "relationship_graph" / safe_week


def relationship_graph_path(repo_root: Path, rithmic_week: str) -> Path:
    return relationship_graph_dir(repo_root, rithmic_week) / "graph.json"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"expected mapping in {path}")
    return loaded


def load_relationship_graph_config(
    repo_root: Path,
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Load cme_relationship_graph.yaml."""
    path = config_path or (repo_root / _DEFAULT_GRAPH_CONFIG)
    return _load_yaml(path)


def load_cme_universe_config(
    repo_root: Path,
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Load cme_universe.yaml root metadata."""
    path = config_path or (repo_root / _DEFAULT_UNIVERSE_CONFIG)
    return _load_yaml(path)


def _empty_edge_features() -> dict[str, None]:
    return {key: None for key in EDGE_FEATURE_KEYS}


def enumerate_edges(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Enumerate stub edges from configured relationship families."""
    edges: list[dict[str, Any]] = []
    families = config.get("families")
    if not isinstance(families, dict):
        return edges
    for family_id, family in families.items():
        if not isinstance(family, dict):
            continue
        relationship_type = str(family.get("relationship_type") or family_id)
        pairs = family.get("pairs") or []
        if not isinstance(pairs, list):
            continue
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            source, target = str(pair[0]), str(pair[1])
            edge_id = f"{family_id}:{source}->{target}"
            edges.append(
                {
                    "edge_id": edge_id,
                    "family_id": str(family_id),
                    "relationship_type": relationship_type,
                    "source_root": source,
                    "target_root": target,
                    "features": _empty_edge_features(),
                    "score": None,
                }
            )
    return edges


def build_relationship_graph_stub(
    *,
    repo_root: Path,
    rithmic_week: str,
    universe_profile: str,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Build Phase 2 relationship graph shell (no scoring yet)."""
    config = load_relationship_graph_config(repo_root, config_path=config_path)
    edges = enumerate_edges(config)
    families = sorted(
        {
            str(edge["family_id"])
            for edge in edges
            if edge.get("family_id") is not None
        }
    )
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "lane": "continuous",
        "rithmic_week": rithmic_week,
        "universe_profile": universe_profile,
        "config_schema_version": str(config.get("schema_version") or ""),
        "families": families,
        "edges": edges,
        "summary": {
            "edge_count": len(edges),
            "family_count": len(families),
        },
    }


def write_relationship_graph(repo_root: Path, graph: dict[str, Any]) -> Path:
    week = str(graph["rithmic_week"])
    path = relationship_graph_path(repo_root, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
