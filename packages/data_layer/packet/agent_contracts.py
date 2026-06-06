"""Validation helpers for LLM-facing MBO agent contracts."""

from __future__ import annotations

import json
import math
from datetime import datetime
from functools import lru_cache
from numbers import Real
from pathlib import Path
from typing import Any, Dict, List

import jsonschema

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOC_SCHEMA_DIR = _REPO_ROOT / "docs" / "schemas"


@lru_cache(maxsize=16)
def _load_agent_schema(name: str) -> Dict[str, Any]:
    path = _DOC_SCHEMA_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def validate_agent_contract(schema_name: str, obj: Any) -> List[str]:
    """Return validation errors for LLM-facing docs/schemas contracts."""
    schema = _load_agent_schema(schema_name)
    validator = jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    errors = sorted(validator.iter_errors(obj), key=lambda err: list(err.path))
    out: List[str] = []
    for err in errors:
        path = ".".join(str(part) for part in err.path)
        out.append(f"{path}: {err.message}" if path else err.message)
    _append_non_finite_number_errors(out, obj, "")
    _append_datetime_format_errors(out, obj, "")
    if schema_name == "MODEL_CARD.schema.json":
        _append_model_card_feature_registry_errors(out, obj)
    return out


def _append_non_finite_number_errors(errors: List[str], value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            _append_non_finite_number_errors(errors, child, child_path)
        return
    if isinstance(value, list):
        for idx, child in enumerate(value):
            child_path = f"{path}[{idx}]" if path else f"[{idx}]"
            _append_non_finite_number_errors(errors, child, child_path)
        return
    if isinstance(value, Real) and not isinstance(value, bool):
        if isinstance(value, int):
            return
        if not math.isfinite(float(value)):
            errors.append(f"{path}: must be finite")


def _append_datetime_format_errors(errors: List[str], value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in {
                "timestamp",
                "exchange_timestamp",
                "receive_timestamp",
                "decision_timestamp",
            } and isinstance(child, str):
                if not _is_rfc3339_datetime(child):
                    errors.append(f"{child_path}: must be date-time")
            _append_datetime_format_errors(errors, child, child_path)
        return
    if isinstance(value, list):
        for idx, child in enumerate(value):
            child_path = f"{path}[{idx}]" if path else f"[{idx}]"
            _append_datetime_format_errors(errors, child, child_path)


def _is_rfc3339_datetime(value: str) -> bool:
    if "T" not in value:
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _append_model_card_feature_registry_errors(errors: List[str], obj: Any) -> None:
    if not isinstance(obj, dict):
        return
    card = obj.get("model_card")
    if not isinstance(card, dict):
        return
    feature_ids = card.get("feature_ids") or []
    if not isinstance(feature_ids, list):
        return
    try:
        from features_engine.src.features.registry import load_feature_registry
        from features_engine.src.model_registry import load_model_registry, resolve_model_id

        registry = load_feature_registry()
    except Exception as exc:
        errors.append(f"model_card.feature_ids: feature registry unavailable: {exc}")
        return
    model_kind = str(card.get("model_type") or "")
    try:
        slug = resolve_model_id(str(card.get("model_id") or ""))
        model_entry = (load_model_registry().get("models") or {}).get(slug, {})
        model_kind = str(model_entry.get("kind") or model_kind)
    except KeyError:
        pass
    if model_kind == "existing_hft3_candidate":
        model_kind = "hypothesis"
    for idx, feature_id in enumerate(feature_ids):
        if not isinstance(feature_id, str):
            continue
        try:
            spec = registry.resolve(feature_id)
        except KeyError:
            errors.append(f"model_card.feature_ids[{idx}]: unknown feature_id {feature_id}")
            continue
        acceptance = registry.accept(
            spec.feature_id,
            consumer_lane="all_lanes",
            source_lane=spec.lane_owner,
            model_kind=model_kind,
            pit_safe=True,
            source_tier=spec.source_tier_required,
            required_inputs_available=True,
        )
        if not acceptance.accepted:
            errors.append(
                f"model_card.feature_ids[{idx}]: feature_id {feature_id} not eligible for "
                f"model_kind {model_kind}: {';'.join(acceptance.reasons)}"
            )
