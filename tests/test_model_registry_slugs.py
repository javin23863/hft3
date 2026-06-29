"""Model registry slug parity tests."""
from __future__ import annotations

import warnings

import pytest

from features_engine.src.model_registry import (
    all_slugs,
    get_hyp_id_for_slug,
    get_slug_for_hyp_id,
    legacy_to_slug,
    load_model_registry,
    resolve_model_id,
)


def test_seventy_two_unique_slugs() -> None:
    slugs = all_slugs()
    assert len(slugs) == 72
    assert len(set(slugs)) == 72


def test_legacy_to_slug_bijection() -> None:
    l2s = legacy_to_slug()
    assert len(l2s) == 61
    assert len(set(l2s.values())) == 61


def test_hyp_id_round_trip() -> None:
    for hyp_id in range(1, 51):
        slug = get_slug_for_hyp_id(hyp_id)
        assert get_hyp_id_for_slug(slug) == hyp_id


def test_resolve_legacy_warns() -> None:
    with pytest.warns(DeprecationWarning, match="legacy"):
        assert resolve_model_id("HYP_5") == "SPREAD_BLOWOUT_RECOMPRESSION"


def test_registry_entries_complete() -> None:
    models = load_model_registry()["models"]
    hyp_count = sum(1 for e in models.values() if e.get("kind") == "hypothesis")
    pdf_count = sum(1 for e in models.values() if e.get("kind") == "pdf_structural")
    assert hyp_count == 50
    assert pdf_count == 11
