from __future__ import annotations

import json

import pytest

from features_engine.src.model_registry import load_model_registry
from research_pipeline.model_generation import generate_candidates
import research_pipeline.parameter_search as parameter_search
from research_pipeline.parameter_search import (
    HBT_PARAMETER_SET_PRE_HBT_STATUS,
    HBT_PARAMETER_SET_SCHEMA_VERSION,
    HBT_PARAMETER_SET_SOURCE,
    hbt_parameter_sets_from_candidates,
    hbt_parameter_sets_from_model_registry,
    parameter_grid,
    select_parameters,
)
from research_pipeline.types import ParsedHypothesis


def _parsed() -> ParsedHypothesis:
    return ParsedHypothesis(
        thesis="Fade spread blowout after CPI",
        instrument_universe=["MES"],
        entry_rules=["enter_spread"],
        exit_rules=["exit_revert"],
        indicators=["BOOK_PRESSURE"],
        feature_list=["BOOK_PRESSURE"],
        param_ranges={
            "signal_threshold": [0.05, 0.35],
            "holding_period_bars": [10, 30],
            "stop_loss_pct": [0.002, 0.006],
            "take_profit_pct": [0.004, 0.012],
        },
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )


def test_parameter_search_methods_are_explicit_and_nonempty():
    grid = parameter_grid(_parsed(), expand_for_vectorbt=True)

    for method in ("grid", "bayesian", "evolutionary"):
        selections = select_parameters(grid, max_candidates=4, search_method=method, seed=7)

        assert len(selections) == 4
        assert {row.metadata["requested_method"] for row in selections} == {method}
        assert {row.metadata["selected_method"] for row in selections} == {method}
        assert {row.metadata["backend"] for row in selections} == {"stdlib"}
        assert {row.metadata["method_status"] for row in selections} == {"ok"}
        assert all(row.metadata["grid_size"] >= 4 for row in selections)


def test_parameter_search_rejects_unknown_method_instead_of_fallback():
    grid = parameter_grid(_parsed(), expand_for_vectorbt=True)

    with pytest.raises(ValueError, match="unknown search_method"):
        select_parameters(grid, max_candidates=2, search_method="seeded")


def test_generate_candidates_records_search_receipt():
    candidates = list(
        generate_candidates(
            _parsed(),
            max_candidates=3,
            expand_for_vectorbt=True,
            search_method="bayesian",
            search_seed=11,
        )
    )

    assert len(candidates) == 3
    assert all("holding_period_bars" in candidate.strategy_params for candidate in candidates)
    for candidate in candidates:
        receipt = candidate.metadata["candidate_search"]
        assert receipt["requested_method"] == "bayesian"
        assert receipt["selected_method"] == "bayesian"
        assert receipt["backend"] == "stdlib"
        assert receipt["seed"] == 11
        assert receipt["optimizer_stage"] == "pre_vectorbt_candidate_generation"


def test_hbt_parameter_sets_export_self_learning_proposals() -> None:
    candidates = []
    for method in ("grid", "bayesian", "evolutionary"):
        candidates.extend(
            generate_candidates(
                _parsed(),
                max_candidates=1,
                expand_for_vectorbt=True,
                search_method=method,
                search_seed=11,
            )
        )

    specs = hbt_parameter_sets_from_candidates(candidates)

    assert [spec["parameter_family"] for spec in specs] == [
        "grid",
        "bayesian-prior",
        "evolutionary-prior",
    ]
    assert {spec["canonical_model_id"] for spec in specs} == {
        "SPREAD_BLOWOUT_RECOMPRESSION"
    }
    assert {spec["source"] for spec in specs} == {HBT_PARAMETER_SET_SOURCE}
    assert {spec["parameter_proposal_status"] for spec in specs} == {
        HBT_PARAMETER_SET_PRE_HBT_STATUS
    }
    assert {spec["objective_evaluations"] for spec in specs} == {0}
    assert {spec["optimizer_claim"] for spec in specs} == {False}
    assert all(
        "packages/research_pipeline/parameter_search.py" in spec["authority_refs"]
        for spec in specs
    )
    assert all(
        "packages/research_pipeline/generation_loop.py" in spec["authority_refs"]
        for spec in specs
    )


def test_hbt_registry_parameter_sets_cover_all_canonical_slugs() -> None:
    registry_models = load_model_registry()["models"]

    specs = hbt_parameter_sets_from_model_registry()

    assert len(specs) == len(registry_models)
    assert [spec["canonical_model_id"] for spec in specs] == list(registry_models)
    legacy_ids = {
        str(entry["legacy_id"])
        for entry in registry_models.values()
        if entry.get("legacy_id")
    }
    assert {spec["canonical_model_id"] for spec in specs}.isdisjoint(legacy_ids)


def test_hbt_registry_parameter_sets_dedupe_normalized_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        parameter_search,
        "load_model_registry",
        lambda: {"models": {"BOOK_PRESSURE": {}, "BOOK_PRESSURE_ALIAS": {}}},
    )
    monkeypatch.setattr(
        parameter_search,
        "resolve_model_id",
        lambda model_id: "BOOK_PRESSURE",
    )

    specs = parameter_search.hbt_parameter_sets_from_model_registry()

    assert len(specs) == 1
    assert specs[0]["canonical_model_id"] == "BOOK_PRESSURE"


def test_hbt_registry_parameter_sets_use_valid_pre_hbt_fields() -> None:
    specs = hbt_parameter_sets_from_model_registry()

    assert {spec["schema_version"] for spec in specs} == {
        HBT_PARAMETER_SET_SCHEMA_VERSION
    }
    assert {spec["source"] for spec in specs} == {HBT_PARAMETER_SET_SOURCE}
    assert {spec["parameter_family"] for spec in specs} == {"grid"}
    assert {spec["parameter_proposal_status"] for spec in specs} == {
        HBT_PARAMETER_SET_PRE_HBT_STATUS
    }
    assert {spec["objective_evaluations"] for spec in specs} == {0}
    assert {spec["optimizer_claim"] for spec in specs} == {False}
    assert all(spec["strategy_params"] for spec in specs)
    assert all(
        "packages/research_pipeline/parameter_search.py" in spec["authority_refs"]
        for spec in specs
    )


def test_hbt_registry_parameter_sets_support_explicit_prior_methods() -> None:
    search_methods = ("grid", "bayesian", "evolutionary")
    specs = hbt_parameter_sets_from_model_registry(
        search_methods=search_methods
    )
    registry_models = load_model_registry()["models"]
    families_by_slug = {slug: [] for slug in registry_models}
    for spec in specs:
        families_by_slug[spec["canonical_model_id"]].append(spec["parameter_family"])

    assert len(specs) == len(registry_models) * len(search_methods)
    assert all(
        families == ["grid", "bayesian-prior", "evolutionary-prior"]
        for families in families_by_slug.values()
    )
    assert {spec["objective_evaluations"] for spec in specs} == {0}
    assert {spec["optimizer_claim"] for spec in specs} == {False}


def test_hbt_registry_parameter_sets_do_not_reference_screening_artifacts() -> None:
    serialized = json.dumps(
        hbt_parameter_sets_from_model_registry(
            search_methods=("grid", "bayesian", "evolutionary")
        ),
        sort_keys=True,
    ).lower()

    assert "vectorbt" not in serialized
    assert "stage_a" not in serialized
    assert "stage a" not in serialized
    assert "screening" not in serialized
