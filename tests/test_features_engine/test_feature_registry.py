from __future__ import annotations

from features_engine.src.features.feature_index import FEATURE_NAME_TO_INDEX, REGIME_INDEX_MAP, FeatureIndex
from features_engine.src.features.registry import (
    feature_ids_for_model,
    load_feature_registry,
    validate_feature_registry,
    validate_model_feature_map,
)
from features_engine.src.model_registry import all_slugs


def test_feature_registry_validates_and_resolves_allowed_aliases() -> None:
    assert validate_feature_registry() == []
    assert validate_model_feature_map() == []

    registry = load_feature_registry()
    assert registry.canonical_id("normalized_ofi_10") == "mbo.flow.normalized_ofi_10ms"
    assert registry.canonical_id("regime_spread_stress") == "mbo.regime.spread_stress"


def test_model_ids_do_not_resolve_as_feature_aliases() -> None:
    registry = load_feature_registry()

    for model_id in all_slugs():
        try:
            registry.resolve(model_id)
        except KeyError:
            continue
        raise AssertionError(f"model_id resolved as feature_id: {model_id}")


def test_feature_registry_acceptance_fail_closed() -> None:
    registry = load_feature_registry()

    assert registry.accept(
        "mbo.flow.normalized_ofi_10ms",
        consumer_lane="cme_futures",
        source_lane="cme_futures",
        model_kind="hypothesis",
        pit_safe=True,
        source_tier="tier_1_primary",
    ).accepted

    assert registry.accept(
        "not_registered",
        consumer_lane="cme_futures",
    ).reasons == ("UNREGISTERED_FEATURE",)

    pit_reject = registry.accept(
        "mbo.flow.normalized_ofi_10ms",
        consumer_lane="cme_futures",
        source_lane="cme_futures",
        model_kind="hypothesis",
        pit_safe=False,
        source_tier="tier_1_primary",
    )
    assert not pit_reject.accepted
    assert "PIT_UNSAFE" in pit_reject.reasons

    tier_reject = registry.accept(
        "mbo.flow.normalized_ofi_10ms",
        consumer_lane="cme_futures",
        source_lane="cme_futures",
        model_kind="hypothesis",
        source_tier="tier_4_untrusted_context",
    )
    assert not tier_reject.accepted
    assert "SOURCE_TIER_INSUFFICIENT" in tier_reject.reasons

    kind_reject = registry.accept(
        "pdf.ofi_pca.ofi_value",
        consumer_lane="cme_futures",
        source_lane="cme_futures",
        model_kind="hypothesis",
        source_tier="tier_2_vendor_normalized",
    )
    assert not kind_reject.accepted
    assert "WRONG_MODEL_KIND" in kind_reject.reasons


def test_model_feature_map_resolves_registered_features() -> None:
    registry = load_feature_registry()

    for model_id in all_slugs():
        feature_ids = feature_ids_for_model(model_id)
        assert feature_ids, model_id
        assert all(registry.resolve(feature_id).feature_id == feature_id for feature_id in feature_ids)


def test_feature_index_slots_are_registered_without_reordering() -> None:
    registry = load_feature_registry()
    slot_to_feature_id = {
        spec.feature_index_slot: spec.feature_id
        for spec in registry.all_specs()
        if spec.feature_index_slot is not None
    }
    expected_slots = {int(slot) for slot in FeatureIndex}

    assert set(slot_to_feature_id) == expected_slots
    assert slot_to_feature_id[int(FEATURE_NAME_TO_INDEX["spread_stress"])] == "mbo.depth.spread_stress"
    assert slot_to_feature_id[int(REGIME_INDEX_MAP["spread_stress"])] == "mbo.regime.spread_stress"
