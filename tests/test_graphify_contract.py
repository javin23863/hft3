"""Graphify workflow contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_graphifyignore_excludes_generated_and_external_heavy_dirs() -> None:
    repo = Path(__file__).resolve().parents[1]
    entries = {
        line.strip()
        for line in (repo / ".graphifyignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "artifacts/" in entries
    assert "build-msvc/" in entries
    assert "vendor/" in entries
    assert "graphify-out/" in entries


def test_graphify_graph_has_no_ignored_heavy_source_roots() -> None:
    repo = Path(__file__).resolve().parents[1]
    graph = repo / "graphify-out" / "graph.json"
    if not graph.exists():
        pytest.skip("graphify graph is not present in this checkout")

    payload = json.loads(graph.read_text(encoding="utf-8"))
    bad_roots = ("artifacts/", "build-msvc/", "vendor/")
    offenders = sorted(
        str(node.get("source_file", "")).replace("\\", "/")
        for node in payload.get("nodes", [])
        if str(node.get("source_file", "")).replace("\\", "/").startswith(bad_roots)
    )

    assert offenders[:10] == []
