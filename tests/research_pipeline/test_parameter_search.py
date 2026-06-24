from __future__ import annotations

import pytest

from research_pipeline.model_generation import generate_candidates
from research_pipeline.parameter_search import parameter_grid, select_parameters
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
