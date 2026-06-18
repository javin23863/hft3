"""Tests for Phase 7 feature-family recipe proposals."""

from __future__ import annotations

from research_pipeline.elite_refinement import propose_next_candidates
from research_pipeline.feature_family_proposals import (
    apply_family_variant_to_recipe,
    list_family_variant_ids,
    propose_family_variant_candidates,
)
from research_pipeline.feature_recipe import attach_feature_recipe_to_candidate, build_feature_recipe
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


def test_list_family_variant_ids_non_empty() -> None:
    assert list_family_variant_ids()
    assert "cross_asset_es_leader" in list_family_variant_ids()


def test_apply_family_variant_changes_recipe_hash() -> None:
    base = build_feature_recipe(
        model_id="HYP_5",
        strategy_params={"signal_threshold": 0.15},
        feature_list=["HYP_5"],
        target_event_id="CPI_2024_09_11_TIGHT",
    ).to_dict()
    base_hash = base["feature_recipe_hash"]
    variant = apply_family_variant_to_recipe(
        base,
        variant_id="cross_asset_es_leader",
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    assert variant["feature_recipe_hash"] != base_hash
    cross = variant["feature_families"]["cross_asset_futures"]
    assert cross["model_consumption_state"] == "not_measured"
    assert "es_leader_momentum" in cross["selected_features"]


def test_family_variants_dedup_by_recipe_hash() -> None:
    parsed = _parsed()
    attached = attach_feature_recipe_to_candidate(
        CandidateModel(
            candidate_id="c1",
            model_id="HYP_5",
            strategy_params={"signal_threshold": 0.15, "holding_period_bars": 15},
            thesis="t",
        ),
        parsed=parsed,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    elite = {
        "elite": True,
        "candidate_id": "c1",
        "model_id": "HYP_5",
        "strategy_params": dict(attached.strategy_params),
        "feature_recipe": dict(attached.feature_recipe or {}),
        "feature_recipe_hash": attached.feature_recipe_hash,
    }
    recipe_hash = str(attached.feature_recipe_hash)
    tested = {recipe_hash}
    out = propose_family_variant_candidates(
        elites=[elite],
        parsed=parsed,
        tested_hashes=tested,
        max_candidates=10,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    assert out
    assert all(c.feature_recipe_hash != recipe_hash for c in out)
    assert all(c.metadata.get("refinement") == "family_variant" for c in out)


def test_elite_refinement_emits_family_variants() -> None:
    parsed = _parsed()
    attached = attach_feature_recipe_to_candidate(
        CandidateModel(
            candidate_id="c1",
            model_id="HYP_5",
            strategy_params={"signal_threshold": 0.15, "holding_period_bars": 15},
            thesis="t",
        ),
        parsed=parsed,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    summary = {
        "candidates": [
            {
                "elite": True,
                "candidate_id": "c1",
                "model_id": "HYP_5",
                "strategy_params": dict(attached.strategy_params),
                "feature_recipe": dict(attached.feature_recipe or {}),
                "feature_recipe_hash": attached.feature_recipe_hash,
            }
        ]
    }
    out = propose_next_candidates(
        parsed=parsed,
        generation_summary=summary,
        tested_hashes=set(),
        max_candidates=8,
        exploration_fraction=0.0,
        target_event_id="CPI_2024_09_11_TIGHT",
        family_search_enabled=True,
        family_search_fraction=0.5,
    )
    family_variants = [c for c in out if c.metadata.get("refinement") == "family_variant"]
    neighbors = [c for c in out if c.metadata.get("refinement") == "neighbor"]
    assert family_variants
    assert neighbors
    hashes = {c.feature_recipe_hash for c in out if c.feature_recipe_hash}
    assert len(hashes) == len(out)
