"""Generate candidate models from a parsed hypothesis."""

from __future__ import annotations

from typing import Iterator

from workbench.src.core.params import DEFAULT_STRATEGY_PARAMS, param_hash_from_dict

from research_pipeline.feature_recipe import attach_feature_recipe_to_candidate
from research_pipeline.parameter_search import search_plan
from research_pipeline.types import CandidateModel, ParsedHypothesis


def generate_candidates(
    parsed: ParsedHypothesis,
    *,
    max_candidates: int = 20,
    expand_for_vectorbt: bool = False,
    target_event_id: str | None = None,
    target_symbol: str = "MES",
    research_clock: str = "scheduled_event",
    search_method: str = "grid",
    hybrid: bool = True,
    search_seed: int = 42,
) -> Iterator[CandidateModel]:
    """Yield param variants for primary model and keyword-adjacent slugs.

    When expand_for_vectorbt is True, generates a richer grid across
    signal_threshold, holding_period, and stop_loss/take_profit for
    VectorBT's cheap parameter filtering.
    """
    count = 0
    for model_id, selection in search_plan(
        parsed,
        max_candidates=max_candidates,
        expand_for_vectorbt=expand_for_vectorbt,
        search_method=search_method,
        hybrid_models=hybrid,
        seed=search_seed,
    ):
        if count >= max_candidates:
            break
        params = dict(DEFAULT_STRATEGY_PARAMS)
        params.update(selection.params)
        cid = param_hash_from_dict(model_id, params)
        yield attach_feature_recipe_to_candidate(
            CandidateModel(
                candidate_id=cid,
                model_id=model_id,
                strategy_params=params,
                thesis=parsed.thesis,
                metadata={
                    "source_model": parsed.primary_model_id,
                    "strategy_family": model_id,
                    "candidate_search": dict(selection.metadata),
                },
            ),
            parsed=parsed,
            target_event_id=target_event_id,
            target_symbol=target_symbol,
            research_clock=research_clock,
        )
        count += 1
