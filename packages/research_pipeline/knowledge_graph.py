"""Knowledge graph helpers — query file-backed KG and persist document slices."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import networkx as nx

from data_layer.kg.store import append_edges, append_nodes


def _kg_paths(repo_root: Path) -> tuple[Path, Path]:
    base = repo_root / "research_cards" / "kg"
    return base / "nodes.jsonl", base / "edges.jsonl"


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_graph(repo_root: Path) -> nx.DiGraph:
    nodes_path, edges_path = _kg_paths(repo_root)
    g = nx.DiGraph()
    for node in _read_jsonl(nodes_path):
        nid = node.get("id")
        if nid:
            attrs = {k: v for k, v in node.items() if k != "id"}
            g.add_node(str(nid), **attrs)
    for edge in _read_jsonl(edges_path):
        src, dst = edge.get("from"), edge.get("to")
        if src and dst:
            rel = {k: v for k, v in edge.items() if k not in ("from", "to")}
            g.add_edge(str(src), str(dst), **rel)
    return g


def persist_graph_slice(repo_root: Path, g: nx.DiGraph) -> tuple[int, int]:
    from research_pipeline.document_ingestion import graph_to_kg_records
    from data_layer.openfoundry_bridge import validate_connector

    validation = validate_connector(repo_root)
    if not validation["upstream"]["core_pack_present"]:
        raise RuntimeError(
            "vendor/openfoundry not initialized — run: "
            "git submodule update --init vendor/openfoundry vendor/alphageometry"
        )

    records = graph_to_kg_records(g)
    n_nodes = append_nodes(repo_root, records["nodes"])
    n_edges = append_edges(repo_root, records["edges"])
    return n_nodes, n_edges


def get_exposures(repo_root: Path, entity_id: str) -> List[Dict[str, Any]]:
    """Return nodes reachable from entity via exposure/supplier/mentions edges."""
    g = load_graph(repo_root)
    eid = entity_id if entity_id.startswith("entity:") else f"entity:{entity_id}"
    if eid not in g:
        return []
    exposures: List[Dict[str, Any]] = []
    for _, target, attrs in g.out_edges(eid, data=True):
        rel = attrs.get("relation", "")
        if rel in ("exposure", "supplier", "mentions", "contains"):
            node = dict(g.nodes[target])
            node["id"] = target
            node["relation"] = rel
            exposures.append(node)
    return exposures


def get_related_events(
    repo_root: Path,
    entity_id: str,
    window: Optional[timedelta] = None,
) -> List[Dict[str, Any]]:
    """Return macro-event nodes linked to entity (window reserved for future event-time filter)."""
    _ = window
    g = load_graph(repo_root)
    eid = entity_id if ":" in entity_id else f"entity:{entity_id}"
    if eid not in g:
        eid = f"entity:{entity_id}"
    events: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _collect(node: str) -> None:
        if node in seen:
            return
        seen.add(node)
        attrs = g.nodes.get(node, {})
        if attrs.get("type") == "macro-event":
            rec = dict(attrs)
            rec["id"] = node
            events.append(rec)
        for nbr in g.neighbors(node):
            _collect(nbr)

    if eid in g:
        _collect(eid)
    return events
