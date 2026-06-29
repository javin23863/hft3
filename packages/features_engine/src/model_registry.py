"""Unified model slug registry - 50 HYP + 11 PDF + 11 continuous = 72 total."""

from __future__ import annotations

import warnings
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

import yaml

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "model_registry.yaml"


@lru_cache(maxsize=1)
def load_model_registry() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _models() -> dict:
    return load_model_registry().get("models", {})


@lru_cache(maxsize=1)
def slug_to_legacy() -> Dict[str, str]:
    return {
        slug: entry["legacy_id"]
        for slug, entry in _models().items()
        if "legacy_id" in entry
    }


@lru_cache(maxsize=1)
def legacy_to_slug() -> Dict[str, str]:
    return {
        entry["legacy_id"]: slug
        for slug, entry in _models().items()
        if "legacy_id" in entry
    }


def all_slugs() -> List[str]:
    return sorted(_models().keys())


def continuous_eligible_slugs() -> List[str]:
    """Return registry slugs marked continuous_eligible (Phase 4)."""
    return sorted(
        slug
        for slug, entry in _models().items()
        if entry.get("continuous_eligible") is True
        or entry.get("kind") == "continuous_microstructure"
    )


def get_continuous_model_entry(slug: str) -> dict:
    """Resolve continuous model metadata; raises KeyError if not continuous-eligible."""
    entry = _models().get(slug)
    if entry is None:
        raise KeyError(f"Unknown model id: {slug}")
    if not (
        entry.get("continuous_eligible") is True
        or entry.get("kind") == "continuous_microstructure"
    ):
        raise KeyError(f"{slug} is not continuous-eligible")
    return entry


def resolve_model_id(model_id: str) -> str:
    """Return canonical slug; accept legacy_id with deprecation warning."""
    if model_id in _models():
        return model_id
    slug = legacy_to_slug().get(model_id)
    if slug is not None:
        warnings.warn(
            f"legacy model id {model_id!r} is deprecated; use slug {slug!r}",
            DeprecationWarning,
            stacklevel=2,
        )
        return slug
    raise KeyError(f"Unknown model id: {model_id}")


def get_slug_for_hyp_id(hyp_id: int) -> str:
    for slug, entry in _models().items():
        if entry.get("kind") == "hypothesis" and entry.get("hyp_id") == hyp_id:
            return slug
    raise KeyError(f"Unknown hyp_id: {hyp_id}")


def get_hyp_id_for_slug(slug: str) -> int:
    entry = _models().get(slug)
    if entry is None:
        raise KeyError(f"Unknown slug: {slug}")
    if entry.get("kind") != "hypothesis":
        raise KeyError(f"{slug} is not a hypothesis model")
    hyp_id = entry.get("hyp_id")
    if hyp_id is None:
        raise KeyError(f"{slug} has no hyp_id")
    return int(hyp_id)
