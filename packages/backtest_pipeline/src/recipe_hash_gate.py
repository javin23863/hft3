"""VectorBT ↔ HftBacktest feature_recipe_hash equality gate (Phase 6)."""

from __future__ import annotations

from typing import Any, Mapping


def extract_feature_recipe_hash_from_promoted_row(row: Mapping[str, Any]) -> str | None:
    """Read feature_recipe_hash from a promoted screening row."""
    direct = row.get("feature_recipe_hash")
    if direct:
        return str(direct).strip() or None

    vbt = row.get("vectorbt_results")
    if isinstance(vbt, Mapping) and vbt.get("feature_recipe_hash"):
        return str(vbt["feature_recipe_hash"]).strip() or None

    meta = row.get("base_candidate_metadata")
    if isinstance(meta, Mapping) and meta.get("feature_recipe_hash"):
        return str(meta["feature_recipe_hash"]).strip() or None

    recipe = row.get("feature_recipe")
    if isinstance(recipe, Mapping) and recipe.get("feature_recipe_hash"):
        return str(recipe["feature_recipe_hash"]).strip() or None

    return None


def validate_feature_recipe_hash_handoff(
    *,
    scenario_feature_recipe_hash: str,
    promoted_row: Mapping[str, Any],
) -> list[str]:
    """Fail closed when hashes disagree; skip when neither side declares a hash."""
    upstream = extract_feature_recipe_hash_from_promoted_row(promoted_row)
    scenario_hash = str(scenario_feature_recipe_hash or "").strip()

    if not scenario_hash and not upstream:
        return []

    reasons: list[str] = []
    if not scenario_hash and upstream:
        reasons.append("scenario_feature_recipe_hash_missing")
    elif scenario_hash and not upstream:
        reasons.append("promoted_feature_recipe_hash_missing")
    elif scenario_hash != upstream:
        reasons.append("feature_recipe_hash_mismatch")
    return reasons
