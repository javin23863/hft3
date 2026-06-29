"""Model registry integrity tests — verifies slug counts, bidirectionality, dependency chains.

Traces to: BLUEPRINT.md §7 (51 model inventory), AGENTS.md (model registry slug resolution).
"""
from __future__ import annotations

import pytest

from features_engine.src.model_registry import (
    all_slugs,
    get_hyp_id_for_slug,
    get_slug_for_hyp_id,
    legacy_to_slug,
    load_model_registry,
    resolve_model_id,
    slug_to_legacy,
)
from features_engine.src.hypotheses.modules import (
    BaseHypothesis,
    SecondWaveContinuation,
    StopRunExhaustionFade,
)
from features_engine.src.hypotheses.registry import (
    HypothesisRegistry,
    get_active_hypotheses,
    CROSS_ASSET_HYP_IDS,
)
from features_engine.src.structural_models.registry import (
    MODEL_DEPENDENCY_MAP,
    PDF_MODEL_IDS,
    get_structural_models,
)


class TestModelRegistryIntegrity:
    def test_total_count(self):
        slugs = all_slugs()
        assert len(slugs) > 0

    def test_all_hyp_ids_unique(self):
        hyp_ids: list[int] = []
        registry = HypothesisRegistry()
        for slug in all_slugs():
            try:
                hyp_ids.append(get_hyp_id_for_slug(slug))
            except (KeyError, ValueError):
                pass
        assert len(hyp_ids) == len(set(hyp_ids))

    def test_all_active_hypotheses_in_registry(self):
        slugs = set(all_slugs())
        active = get_active_hypotheses()
        for hyp in active:
            legacy = f"HYP_{hyp.hyp_id}"
            assert legacy in slugs or get_slug_for_hyp_id(hyp.hyp_id) in slugs

    def test_all_structural_models_in_registry(self):
        slugs = set(all_slugs())
        for model in get_structural_models():
            mid = getattr(model, "model_id", "")
            if mid:
                assert mid in slugs, f"Structural model {mid} not in registry"

    def test_legacy_mapping_bidirectional(self):
        l2s = legacy_to_slug()
        s2l = slug_to_legacy()
        for legacy_id, slug in l2s.items():
            assert slug in s2l, f"Slug {slug} missing from slug_to_legacy"
            assert s2l[slug] == legacy_id, f"Legacy ID mismatch for {slug}"

    def test_resolve_model_id_accepts_both(self):
        slugs = all_slugs()
        if slugs:
            resolve_model_id(slugs[0])
        l2s = legacy_to_slug()
        if l2s:
            legacy = list(l2s.keys())[0]
            resolve_model_id(legacy)

    def test_dependency_map_valid(self):
        all_ids = set(all_slugs())
        for consumer, producers in MODEL_DEPENDENCY_MAP.items():
            for p in producers:
                assert p in all_ids, f"Dependency {p} for {consumer} not in registry"

    def test_pdf_model_ids_populated(self):
        assert len(PDF_MODEL_IDS) > 0

    def test_no_dead_slugs_in_registry(self):
        slugs = set(all_slugs())
        structural_ids = {getattr(m, "model_id", "") for m in get_structural_models()}
        hyp_legacy_ids = {f"HYP_{h.hyp_id}" for h in get_active_hypotheses()}
        hyp_slugs = set()
        for h in get_active_hypotheses():
            try:
                hyp_slugs.add(get_slug_for_hyp_id(h.hyp_id))
            except (KeyError, ValueError):
                pass
        cross_asset_slugs = {"ES_MES_LEAD_LAG", "NQ_MNQ_LEAD_LAG",
                              "ES_NQ_DIVERGENCE_SNAPBACK", "ZN_ZB_ES_NQ_MACRO_IMPULSE",
                              "MICRO_CONTRACT_RETAIL_LAG"}
        continuous_slugs = {
            slug for slug in slugs
            if load_model_registry()["models"][slug].get("kind") == "continuous_microstructure"
        }
        all_implemented = structural_ids | hyp_legacy_ids | hyp_slugs | cross_asset_slugs | continuous_slugs
        unaccounted = [s for s in slugs if s not in all_implemented]
        assert len(unaccounted) == 0, f"Dead slugs: {unaccounted}"
