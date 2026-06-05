"""Workbench status snapshot producer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workbench.src.registry.unified_registry import list_models
from workbench.src.setup import check_graphify, scan_npz

STATUS_RESPONSE_CONTRACT = {
    "required": ["models_registered", "npz_files", "npz_total_mb", "graph_ready", "promoted_candidates"],
    "properties": {
        "models_registered": "integer",
        "npz_files": "integer",
        "npz_total_mb": "number",
        "graph_ready": "boolean",
        "promoted_candidates": "integer",
    },
}


def build_status_snapshot(repo: Path) -> dict[str, Any]:
    npz = scan_npz(repo)
    graph = check_graphify(repo)
    models = list_models()
    promo_dir = repo / "research_cards" / "promotion"
    promoted = list(promo_dir.glob("*.json")) if promo_dir.is_dir() else []
    return {
        "models_registered": len(models),
        "npz_files": npz["npz_count"],
        "npz_total_mb": npz["npz_total_size_mb"],
        "graph_ready": graph["graph_present"],
        "promoted_candidates": len(promoted),
    }
