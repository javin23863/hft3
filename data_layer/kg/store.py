"""Append-only JSONL knowledge graph under research_cards/kg/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def kg_dir(repo_root: Path) -> Path:
    return repo_root / "research_cards" / "kg"


def _append_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
    return n


def append_nodes(repo_root: Path, nodes: List[Dict[str, Any]]) -> int:
    path = kg_dir(repo_root) / "nodes.jsonl"
    existing_ids = _existing_ids(path)
    fresh = [n for n in nodes if n.get("id") not in existing_ids]
    return _append_jsonl(path, fresh)


def append_edges(repo_root: Path, edges: List[Dict[str, Any]]) -> int:
    path = kg_dir(repo_root) / "edges.jsonl"
    existing = {_edge_key(e) for e in _read_jsonl(path)}
    fresh = [e for e in edges if _edge_key(e) not in existing]
    return _append_jsonl(path, fresh)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _existing_ids(path: Path) -> set:
    return {str(r.get("id")) for r in _read_jsonl(path) if r.get("id")}


def _edge_key(edge: Dict[str, Any]) -> str:
    return f"{edge.get('from')}|{edge.get('to')}|{edge.get('relation')}"
