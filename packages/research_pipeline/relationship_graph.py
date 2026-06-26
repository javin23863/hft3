"""CME relationship graph builder (Phase 2 stub)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from research_pipeline.continuous_universe import (
    is_root_active_for_profile,
    validate_universe_profile,
)

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

CAUSAL_BOUNDS_KEYS = (
    "pit_window_end",
    "as_of_event_time",
    "max_lag_sessions",
)
CAUSAL_BOUND_UNSET = "unset"
_EVENT_TIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?$"
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


def _empty_causal_bounds() -> dict[str, str]:
    return {key: CAUSAL_BOUND_UNSET for key in CAUSAL_BOUNDS_KEYS}


def assert_causal_bounds_ready(causal_bounds: dict[str, Any]) -> None:
    """Fail closed before edge scoring when PIT causal bounds are unset."""
    for key in ("pit_window_end", "as_of_event_time"):
        value = causal_bounds.get(key)
        if not isinstance(value, str) or not value.strip() or value == CAUSAL_BOUND_UNSET:
            raise ValueError(f"causal bound {key!r} not set for scoring")
        if not _EVENT_TIME_RE.match(value.strip()):
            raise ValueError(f"causal bound {key!r} must be ISO event-time string")
    max_lag = causal_bounds.get("max_lag_sessions")
    if not isinstance(max_lag, int) or max_lag <= 0:
        raise ValueError("causal bound 'max_lag_sessions' must be a positive int")


def _universe_root_symbols(universe_config: dict[str, Any]) -> set[str]:
    roots = universe_config.get("roots")
    if not isinstance(roots, dict):
        return set()
    return {str(symbol).upper() for symbol in roots}


def validate_edge_roots(
    edges: list[dict[str, Any]], universe_config: dict[str, Any]
) -> None:
    """Fail closed when graph pairs reference roots absent from cme_universe.yaml."""
    known = _universe_root_symbols(universe_config)
    missing: set[str] = set()
    for edge in edges:
        for key in ("source_root", "target_root"):
            root = str(edge.get(key) or "").upper()
            if root and root not in known:
                missing.add(root)
    if missing:
        raise ValueError(
            f"edge roots missing from cme_universe.yaml: {', '.join(sorted(missing))}"
        )


def filter_edges_for_profile(
    edges: list[dict[str, Any]], universe_profile: str
) -> list[dict[str, Any]]:
    """Keep edges whose endpoints are active for *universe_profile*."""
    validate_universe_profile(universe_profile)
    return [
        edge
        for edge in edges
        if is_root_active_for_profile(str(edge.get("source_root") or ""), universe_profile)
        and is_root_active_for_profile(str(edge.get("target_root") or ""), universe_profile)
    ]


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
                    "causal_bounds": _empty_causal_bounds(),
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
    universe_config_path: Path | None = None,
) -> dict[str, Any]:
    """Build Phase 2 relationship graph shell (no scoring yet)."""
    validate_universe_profile(universe_profile)
    config = load_relationship_graph_config(repo_root, config_path=config_path)
    universe = load_cme_universe_config(repo_root, config_path=universe_config_path)
    edges = enumerate_edges(config)
    validate_edge_roots(edges, universe)
    edges = filter_edges_for_profile(edges, universe_profile)
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
            "scoring_ready": False,
        },
    }


def require_edges_scorable(edges: list[dict[str, Any]]) -> None:
    """Block edge scoring until every edge has populated PIT causal bounds."""
    for edge in edges:
        assert_causal_bounds_ready(dict(edge.get("causal_bounds") or {}))


def write_relationship_graph(repo_root: Path, graph: dict[str, Any]) -> Path:
    week = str(graph["rithmic_week"])
    path = relationship_graph_path(repo_root, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
