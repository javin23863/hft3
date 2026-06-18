"""Freeze candidate manifests before VectorBT evaluation."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from research_pipeline.feature_recipe import compute_feature_recipe_hash


def _git_commit(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode().strip()
    except Exception:
        return ""


def freeze_candidate_manifest(
    *,
    candidate: Any,
    repo_root: Path,
    generation_index: int,
    parent_candidate_id: str | None = None,
    proposal_reason: str | None = None,
    split_scheme: str = "discovery_holdout_per_walk_forward_yaml",
    engine_versions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build immutable manifest; caller must not mutate fields after freeze."""
    recipe = dict(getattr(candidate, "feature_recipe", None) or {})
    if not recipe:
        raise ValueError("candidate missing feature_recipe; attach before freeze")

    from research_pipeline.feature_recipe import validate_recipe_pit_timestamps

    pit_errors = validate_recipe_pit_timestamps(recipe)
    if pit_errors:
        raise ValueError(f"feature_recipe PIT validation failed: {','.join(pit_errors)}")

    recipe_hash = (
        getattr(candidate, "feature_recipe_hash", None)
        or recipe.get("feature_recipe_hash")
        or compute_feature_recipe_hash(recipe)
    )
    frozen_at = datetime.now(timezone.utc).isoformat()
    manifest: dict[str, Any] = {
        "manifest_schema": "candidate_manifest.v1",
        "candidate_id": candidate.candidate_id,
        "feature_recipe_hash": recipe_hash,
        "model_id": candidate.model_id,
        "target_symbol": getattr(candidate, "target_symbol", None) or recipe.get("target_symbol"),
        "research_clock": getattr(candidate, "research_clock", None) or recipe.get("research_clock"),
        "target_event_or_opportunity": getattr(candidate, "target_event_id", None)
        or recipe.get("target_event_id"),
        "feature_recipe": recipe,
        "execution_assumptions": dict(candidate.strategy_params),
        "source_data_hashes": dict((candidate.metadata or {}).get("source_data_hashes") or {}),
        "feature_data_hashes": dict((candidate.metadata or {}).get("feature_data_hashes") or {}),
        "split_scheme": split_scheme,
        "code_commit": _git_commit(repo_root),
        "engine_versions": dict(engine_versions or {}),
        "generation_id": generation_index,
        "parent_candidate_id": parent_candidate_id,
        "proposal_reason": proposal_reason,
        "frozen_at_utc": frozen_at,
    }
    manifest["manifest_hash"] = compute_feature_recipe_hash(
        {k: v for k, v in manifest.items() if k not in {"manifest_hash", "frozen_at_utc"}}
    )
    return manifest


def write_frozen_manifests(path: Path, manifests: list[Mapping[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(dict(m), sort_keys=True) + "\n" for m in manifests]
    path.write_text("".join(lines), encoding="utf-8")
    return path
