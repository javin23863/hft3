"""Continuous CME candidate generation (Phase 5 stub).

Candidates are derived from relationship-graph edges and registry param ranges
only — no flat symbol-list expansion from cme_universe.yaml.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any, Iterator

from features_engine.src.model_registry import (
    continuous_eligible_slugs,
    get_continuous_model_entry,
)
from workbench.src.core.params import param_hash_from_dict

CANDIDATES_SCHEMA_VERSION = "1"

MODEL_FAMILY_TO_FEATURE_FAMILY: dict[str, str] = {
    "cross_market_lead_lag": "cross_market",
    "cross_asset_flow": "cross_market",
    "liquidity_resiliency": "queue_dynamics",
    "queue_dynamics": "queue_dynamics",
    "hidden_liquidity": "queue_dynamics",
    "order_flow_toxicity": "toxicity",
    "term_structure": "calendar_curve",
    "relative_value_spreads": "spread_depth",
    "seasonality_conditioning": "seasonal_state",
    "hawkes_point_process": "order_flow",
    "execution_overlay": "execution_cost",
}


def continuous_candidates_dir(repo_root: Path, rithmic_week: str) -> Path:
    safe_week = rithmic_week.replace("-", "_")
    return repo_root / "runtime" / "continuous_cme" / "candidates" / safe_week


def continuous_candidates_path(repo_root: Path, rithmic_week: str) -> Path:
    return continuous_candidates_dir(repo_root, rithmic_week) / "candidates.json"


def load_relationship_graph(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"relationship graph not found: {path}")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"expected mapping in {path}")
    return loaded


def feature_family_for_model(entry: dict[str, Any]) -> str:
    family = str(entry.get("model_family") or "")
    mapped = MODEL_FAMILY_TO_FEATURE_FAMILY.get(family)
    if mapped:
        return mapped
    return "order_flow"


def session_scope_for_week(rithmic_week: str) -> str:
    return f"{rithmic_week}:all_sessions"


def _param_values(lo: float, hi: float) -> list[float]:
    if hi <= lo:
        return [round(lo, 6)]
    mid = (lo + hi) / 2.0
    return sorted({round(lo, 6), round(mid, 6), round(hi, 6)})


def iter_param_combos(
    param_ranges: dict[str, list[float]], *, max_combos: int
) -> Iterator[dict[str, float]]:
    if not param_ranges:
        yield {}
        return
    keys = sorted(param_ranges)
    grids = [_param_values(float(param_ranges[k][0]), float(param_ranges[k][1])) for k in keys]
    count = 0
    for values in itertools.product(*grids):
        if count >= max_combos:
            break
        yield {key: float(val) for key, val in zip(keys, values)}
        count += 1


def edge_relationship_id(edge: dict[str, Any]) -> str:
    edge_id = edge.get("edge_id")
    if isinstance(edge_id, str) and edge_id.strip():
        return edge_id.strip()
    family = str(edge.get("family_id") or "unknown")
    source = str(edge.get("source_root") or "?")
    target = str(edge.get("target_root") or "?")
    return f"{family}:{source}->{target}"


def edge_matches_model(entry: dict[str, Any], edge: dict[str, Any]) -> bool:
    valid_types = entry.get("valid_relationship_types") or []
    if not valid_types:
        return False
    family_id = str(edge.get("family_id") or "")
    rel_type = str(edge.get("relationship_type") or "")
    allowed = {str(t) for t in valid_types}
    return family_id in allowed or rel_type in allowed


def _standalone_relationship_id(model_id: str) -> str:
    return f"standalone:{model_id}"


def _candidate_id(model_id: str, relationship_id: str, params: dict[str, float]) -> str:
    payload = {"model_id": model_id, "relationship_id": relationship_id, **params}
    return param_hash_from_dict(model_id, payload)


def generate_continuous_candidates(
    graph: dict[str, Any],
    *,
    max_candidates: int = 5000,
    model_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build candidate records from graph edges and registry default_param_ranges."""
    if max_candidates <= 0:
        return []

    rithmic_week = str(graph.get("rithmic_week") or "")
    session_scope = session_scope_for_week(rithmic_week) if rithmic_week else "unknown:all_sessions"
    edges = [e for e in graph.get("edges") or [] if isinstance(e, dict)]
    slugs = model_ids if model_ids is not None else continuous_eligible_slugs()

    candidates: list[dict[str, Any]] = []
    for slug in slugs:
        if len(candidates) >= max_candidates:
            break
        entry = get_continuous_model_entry(slug)

        param_ranges_raw = entry.get("default_param_ranges") or {}
        param_ranges: dict[str, list[float]] = {}
        if isinstance(param_ranges_raw, dict):
            for key, value in param_ranges_raw.items():
                if isinstance(value, (list, tuple)) and len(value) >= 2:
                    param_ranges[str(key)] = [float(value[0]), float(value[1])]

        feature_family = feature_family_for_model(entry)
        requires_graph = entry.get("requires_relationship_graph") is True
        valid_types = entry.get("valid_relationship_types") or []

        matched_edges = [e for e in edges if edge_matches_model(entry, e)]

        relationship_targets: list[tuple[str, dict[str, Any] | None]]
        if matched_edges:
            relationship_targets = [(edge_relationship_id(e), e) for e in matched_edges]
        elif not valid_types:
            relationship_targets = [(_standalone_relationship_id(slug), None)]
        elif requires_graph:
            continue
        else:
            continue

        per_model_budget = max(1, max_candidates - len(candidates))
        per_rel_budget = max(1, per_model_budget // max(len(relationship_targets), 1))

        for relationship_id, edge in relationship_targets:
            if len(candidates) >= max_candidates:
                break
            for params in iter_param_combos(param_ranges, max_combos=per_rel_budget):
                if len(candidates) >= max_candidates:
                    break
                scoring_ready = graph.get("summary", {}).get("scoring_ready") is True
                record: dict[str, Any] = {
                    "candidate_id": _candidate_id(slug, relationship_id, params),
                    "model_id": slug,
                    "relationship_id": relationship_id,
                    "feature_family": feature_family,
                    "session_scope": session_scope,
                    "model_family": str(entry.get("model_family") or ""),
                    "strategy_params": params,
                    "lane": "continuous_microstructure",
                    "pit_bounds_status": "ready" if scoring_ready else "unset",
                }
                if edge is not None:
                    record["edge_family_id"] = edge.get("family_id")
                    record["source_root"] = edge.get("source_root")
                    record["target_root"] = edge.get("target_root")
                candidates.append(record)

    return candidates


def build_continuous_candidates_artifact(
    *,
    repo_root: Path,
    graph: dict[str, Any],
    relationship_graph_path: Path,
    max_candidates: int = 5000,
) -> dict[str, Any]:
    candidates = generate_continuous_candidates(graph, max_candidates=max_candidates)
    model_ids = sorted({str(c["model_id"]) for c in candidates})
    relationship_ids = sorted({str(c["relationship_id"]) for c in candidates})
    return {
        "schema_version": CANDIDATES_SCHEMA_VERSION,
        "lane": "continuous_microstructure",
        "rithmic_week": graph.get("rithmic_week"),
        "universe_profile": graph.get("universe_profile"),
        "relationship_graph_path": str(relationship_graph_path),
        "candidates": candidates,
        "summary": {
            "candidate_count": len(candidates),
            "model_count": len(model_ids),
            "relationship_count": len(relationship_ids),
            "source": "graph_edges_and_registry_ranges",
        },
    }


def write_continuous_candidates(repo_root: Path, artifact: dict[str, Any]) -> Path:
    week = str(artifact.get("rithmic_week") or "")
    path = continuous_candidates_path(repo_root, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def scan_continuous_candidates(
    *,
    repo_root: Path,
    rithmic_week: str,
    universe_profile: str,
    relationship_graph_path: Path | None = None,
    max_candidates: int = 5000,
    build_graph_if_missing: bool = False,
) -> dict[str, Any]:
    """Scan graph edges + registry ranges; optionally build graph stub first."""
    from research_pipeline.relationship_graph import (
        build_relationship_graph_stub,
        relationship_graph_path as default_graph_path,
        write_relationship_graph,
    )

    graph_path = relationship_graph_path
    if graph_path is None:
        graph_path = default_graph_path(repo_root, rithmic_week)

    if not graph_path.is_file():
        if not build_graph_if_missing:
            raise FileNotFoundError(
                f"relationship graph missing at {graph_path}; "
                "pass --build-relationship-graph or an existing graph path"
            )
        graph = build_relationship_graph_stub(
            repo_root=repo_root,
            rithmic_week=rithmic_week,
            universe_profile=universe_profile,
        )
        graph_path = write_relationship_graph(repo_root, graph)
    else:
        graph = load_relationship_graph(graph_path)

    artifact = build_continuous_candidates_artifact(
        repo_root=repo_root,
        graph=graph,
        relationship_graph_path=graph_path,
        max_candidates=max_candidates,
    )
    out_path = write_continuous_candidates(repo_root, artifact)
    artifact["candidates_path"] = str(out_path)
    return artifact
