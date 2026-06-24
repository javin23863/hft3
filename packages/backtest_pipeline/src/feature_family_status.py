"""Feature-family paid-screen readiness (Phase 9).

Loads ``FEATURE_FAMILY_STATUS_MANIFEST.yaml`` and evaluates pilot screening
artifacts against ``paid_screen_gate.required_pilot_fields``.

``paid_screen_gate.allowed`` remains false until an operator flips it after a
real pilot run; this module fail-closes expensive compute regardless.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from backtest_pipeline.src.recipe_hash_gate import (
    extract_feature_recipe_hash_from_promoted_row,
    validate_feature_recipe_hash_handoff,
)

MANIFEST_REL = Path("docs/project/FEATURE_FAMILY_STATUS_MANIFEST.yaml")

PILOT_FIELD_ARTIFACT_KEYS: dict[str, str] = {
    "macro_context_ablation_status": "context_ablation_status",
}

PILOT_SCOPE_ALLOWS_NOT_RUN: frozenset[str] = frozenset({"robustness_result"})


def manifest_path(repo_root: Path) -> Path:
    return repo_root / MANIFEST_REL


def load_feature_family_status_manifest(repo_root: Path) -> dict[str, Any]:
    import yaml

    path = manifest_path(repo_root)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise ValueError("feature_family_status_manifest_not_mapping")
    return dict(raw)


def _promoted_rows(artifact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = artifact.get("promoted") or []
    return [row for row in rows if isinstance(row, Mapping)]


def _resolve_feature_recipe_hash(artifact: Mapping[str, Any]) -> str | None:
    for row in _promoted_rows(artifact):
        recipe_hash = extract_feature_recipe_hash_from_promoted_row(row)
        if recipe_hash:
            return recipe_hash
    direct = artifact.get("feature_recipe_hash")
    return str(direct).strip() if direct else None


def _resolve_vectorbt_result(artifact: Mapping[str, Any]) -> str | None:
    if str(artifact.get("screening_backend") or "") != "vectorbt":
        return None
    promoted = _promoted_rows(artifact)
    if not promoted:
        if int(artifact.get("trials_run") or 0) > 0:
            return "trials_completed"
        return None
    for row in promoted:
        vbt = row.get("vectorbt_results")
        if isinstance(vbt, Mapping):
            paid_compute_eval = vbt.get("paid_compute_gate_evaluation")
            if (
                isinstance(paid_compute_eval, Mapping)
                and paid_compute_eval.get("failures") == []
            ):
                return "paid_compute_gate_pass"
        status = str(row.get("screening_status") or "").lower()
        if status == "pass":
            return "screen_pass"
        if isinstance(vbt, Mapping):
            pilot_eval = vbt.get("pilot_gate_evaluation")
            if isinstance(pilot_eval, Mapping) and pilot_eval.get("failures") == []:
                return "pilot_gate_pass"
    if int(artifact.get("trials_run") or 0) > 0:
        return "trials_completed"
    return None


def _resolve_robustness_result(artifact: Mapping[str, Any]) -> str | None:
    explicit = artifact.get("robustness_result")
    if explicit not in (None, ""):
        return str(explicit)
    scope = str(artifact.get("screening_scope") or "").lower()
    if scope in {"pilot", "pilot-scope", "pilot_scope"}:
        return "not_run_pilot_scope"
    for row in _promoted_rows(artifact):
        vbt = row.get("vectorbt_results")
        if isinstance(vbt, Mapping) and vbt.get("robustness_evidence"):
            return "robustness_evidence_present"
    return None


def _resolve_hftbacktest_handoff_status(artifact: Mapping[str, Any]) -> str | None:
    explicit = artifact.get("hftbacktest_handoff_status")
    if explicit not in (None, ""):
        return str(explicit)
    promoted = _promoted_rows(artifact)
    if not promoted:
        return None
    hashes: list[str] = []
    for row in promoted:
        recipe_hash = extract_feature_recipe_hash_from_promoted_row(row)
        if not recipe_hash:
            return None
        if validate_feature_recipe_hash_handoff(
            scenario_feature_recipe_hash=recipe_hash,
            promoted_row=row,
        ):
            return None
        hashes.append(recipe_hash)
    if hashes:
        return "recipe_hash_handoff_ready"
    return None


def _resolve_pilot_field(field: str, artifact: Mapping[str, Any]) -> str | None:
    if field == "feature_recipe_hash":
        return _resolve_feature_recipe_hash(artifact)

    if field == "vectorbt_result":
        return _resolve_vectorbt_result(artifact)

    if field == "robustness_result":
        return _resolve_robustness_result(artifact)

    if field == "hftbacktest_handoff_status":
        return _resolve_hftbacktest_handoff_status(artifact)

    artifact_key = PILOT_FIELD_ARTIFACT_KEYS.get(field, field)
    value = artifact.get(artifact_key)
    if value in (None, ""):
        return None
    if artifact_key == "feature_usage_manifest" and not isinstance(value, Mapping):
        return None
    return str(value) if artifact_key != "feature_usage_manifest" else "present"


def evaluate_feature_family_paid_gate(
    pilot_artifact: Mapping[str, Any],
    *,
    repo_root: Path,
    status_manifest: Mapping[str, Any] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Evaluate pilot artifact against feature-family paid-screen requirements."""
    errors: list[str] = []
    manifest = (
        dict(status_manifest)
        if status_manifest is not None
        else load_feature_family_status_manifest(repo_root)
    )
    gate = manifest.get("paid_screen_gate") or {}
    if not isinstance(gate, Mapping):
        errors.append("paid_screen_gate_missing")
        return errors, {"resolved_fields": {}}

    if not bool(gate.get("allowed")):
        reason = str(gate.get("reason") or "not_allowed")
        errors.append(f"paid_screen_gate_not_allowed:{reason}")

    required = [str(f) for f in (gate.get("required_pilot_fields") or []) if f]
    resolved: dict[str, str | None] = {}
    for field in required:
        value = _resolve_pilot_field(field, pilot_artifact)
        resolved[field] = value
        if value is None:
            if field in PILOT_SCOPE_ALLOWS_NOT_RUN:
                scope = str(pilot_artifact.get("screening_scope") or "").lower()
                if scope in {"pilot", "pilot-scope", "pilot_scope"}:
                    resolved[field] = "not_run_pilot_scope"
                    continue
            errors.append(f"pilot_missing:{field}")
        elif field in PILOT_SCOPE_ALLOWS_NOT_RUN and value == "not_run_pilot_scope":
            continue

    summary = {
        "manifest_path": str(manifest_path(repo_root)),
        "paid_screen_gate_allowed": bool(gate.get("allowed")),
        "required_pilot_fields": required,
        "resolved_fields": resolved,
    }
    return errors, summary
