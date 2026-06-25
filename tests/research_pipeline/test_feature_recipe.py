"""Tests for feature-recipe contract (Phase 1)."""

from __future__ import annotations

from research_pipeline.feature_recipe import (
    FEATURE_FAMILIES,
    attach_feature_recipe_to_candidate,
    build_feature_recipe,
    compute_feature_recipe_hash,
    default_feature_families,
    validate_recipe_pit_timestamps,
)
from research_pipeline.candidate_manifest import freeze_candidate_manifest
from research_pipeline.types import CandidateModel, ParsedHypothesis


def _parsed() -> ParsedHypothesis:
    return ParsedHypothesis(
        thesis="t",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=["HYP_5"],
        param_ranges={"signal_threshold": [0.1, 0.3]},
        primary_model_id="HYP_5",
        source="heuristic",
    )


def test_feature_family_registry_matches_canonical_eight() -> None:
    assert len(FEATURE_FAMILIES) == 8
    assert "primary_fs_v1" in FEATURE_FAMILIES
    assert "latency_state" in FEATURE_FAMILIES


def test_feature_recipe_hash_deterministic() -> None:
    recipe = build_feature_recipe(
        model_id="HYP_5",
        strategy_params={"signal_threshold": 0.15},
        feature_list=["HYP_5"],
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    d = recipe.to_dict()
    h1 = compute_feature_recipe_hash(d)
    h2 = compute_feature_recipe_hash(d)
    assert h1 == h2
    assert len(h1) == 64


def test_future_source_timestamp_rejected() -> None:
    families = default_feature_families(
        model_id="HYP_5",
        feature_list=["HYP_5"],
        target_symbol="MES",
    )
    families["primary_fs_v1"]["source_timestamp"] = "2099-01-01T12:00:00Z"
    families["primary_fs_v1"]["target_decision_timestamp"] = "2024-09-11T13:30:00Z"
    recipe = build_feature_recipe(
        model_id="HYP_5",
        strategy_params={"signal_threshold": 0.15},
        feature_list=["HYP_5"],
        feature_families=families,
    )
    errors = validate_recipe_pit_timestamps(recipe.to_dict())
    assert any("future_source_timestamp" in e for e in errors)


def test_attach_feature_recipe_backward_compatible_fields() -> None:
    base = CandidateModel(
        candidate_id="abc",
        model_id="HYP_5",
        strategy_params={"signal_threshold": 0.2},
        thesis="t",
    )
    out = attach_feature_recipe_to_candidate(
        base,
        parsed=_parsed(),
        target_event_id="E1",
        target_symbol="MES",
    )
    assert out.feature_recipe_hash
    assert out.feature_recipe
    assert len(out.feature_recipe["feature_families"]) == 8
    assert out.target_event_id == "E1"


def test_freeze_candidate_manifest_immutable_hash(tmp_path) -> None:
    cand = attach_feature_recipe_to_candidate(
        CandidateModel(
            candidate_id="c1",
            model_id="HYP_5",
            strategy_params={"signal_threshold": 0.15},
            thesis="t",
        ),
        parsed=_parsed(),
        target_event_id="E1",
    )
    manifest = freeze_candidate_manifest(
        candidate=cand,
        repo_root=tmp_path,
        generation_index=0,
    )
    assert manifest["feature_recipe_hash"] == cand.feature_recipe_hash
    assert manifest["manifest_hash"]
    assert manifest["frozen_at_utc"]


def test_execution_param_change_changes_recipe_hash() -> None:
    r1 = build_feature_recipe(
        model_id="HYP_5",
        strategy_params={"signal_threshold": 0.15},
        feature_list=["HYP_5"],
    )
    r2 = build_feature_recipe(
        model_id="HYP_5",
        strategy_params={"signal_threshold": 0.20},
        feature_list=["HYP_5"],
    )
    assert r1.feature_recipe_hash != r2.feature_recipe_hash


def test_default_families_fail_closed_not_consumed() -> None:
    families = default_feature_families(
        model_id="HYP_5",
        feature_list=["HYP_5"],
        target_symbol="MES",
    )
    for family, row in families.items():
        assert row["model_consumption_state"] != "consumed"


def test_microstructure_feature_names_carry_pit_receipt() -> None:
    recipe = build_feature_recipe(
        model_id="BOOK_PRESSURE",
        strategy_params={"signal_threshold": 0.15},
        feature_list=["order_book_imbalance", "queue_imbalance"],
        target_event_id="CPI_2024_09_11_TIGHT",
    )

    primary = recipe.feature_families["primary_fs_v1"]
    assert primary["selected_features"] == ["order_book_imbalance", "queue_imbalance"]
    assert primary["source_ids"] == ["features_engine.feature_sets.MICROSTRUCTURE_FEATURE_RECEIPTS"]
    assert primary["pit_proof"] == "declared"
    assert primary["model_consumption_state"] == "not_measured"
    assert "snapshot_at_decision_time_t_or_trailing_window_ending_at_t" in primary["lookback_rules"]
