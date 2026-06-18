"""Deterministic elite neighbor expansion for autoresearch Gen N+1."""

from __future__ import annotations

import copy
import itertools
from typing import Any, Mapping

from workbench.src.core.params import DEFAULT_STRATEGY_PARAMS, param_hash_from_dict

from research_pipeline.feature_family_proposals import propose_family_variant_candidates
from research_pipeline.feature_recipe import attach_feature_recipe_to_candidate, candidate_identity_hash
from research_pipeline.model_generation import generate_candidates
from research_pipeline.types import CandidateModel, ParsedHypothesis

_THRESHOLD_STEPS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]
_HOLDING_STEPS = [5, 15, 30, 60, 120]


def _neighbor_values(value: float, grid: list[float | int], *, steps: int = 1) -> list[float]:
    if value in grid:
        idx = grid.index(value)
    else:
        idx = min(range(len(grid)), key=lambda i: abs(float(grid[i]) - value))
    lo = max(0, idx - steps)
    hi = min(len(grid) - 1, idx + steps)
    return [float(grid[i]) for i in range(lo, hi + 1)]


def _elite_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in summary.get("candidates") or []:
        if not isinstance(row, Mapping):
            continue
        if row.get("elite") is True:
            rows.append(dict(row))
    return rows


def propose_next_candidates(
    *,
    parsed: ParsedHypothesis,
    generation_summary: Mapping[str, Any],
    tested_hashes: set[str],
    max_candidates: int,
    exploration_fraction: float,
    target_event_id: str | None = None,
    target_symbol: str = "MES",
    research_clock: str = "scheduled_event",
    family_search_enabled: bool = True,
    family_search_fraction: float = 0.4,
) -> list[CandidateModel]:
    """Build Gen N+1 candidates from validated elites plus exploration slice."""
    elites = _elite_rows(generation_summary)
    out: list[CandidateModel] = []
    seen: set[str] = set()

    explore_budget = 0
    if exploration_fraction > 0 and max_candidates > 1:
        explore_budget = min(
            max(1, int(max_candidates * exploration_fraction)),
            max_candidates - 1,
        )
    family_budget = 0
    if (
        family_search_enabled
        and family_search_fraction > 0
        and elites
        and max_candidates > 1
    ):
        family_budget = min(
            max(1, int(max_candidates * family_search_fraction)),
            max(0, max_candidates - explore_budget - 1),
        )
    exec_cap = max(1, max_candidates - explore_budget - family_budget)

    def _add(model: CandidateModel) -> None:
        model = attach_feature_recipe_to_candidate(
            model,
            parsed=parsed,
            target_event_id=target_event_id,
            target_symbol=target_symbol,
            research_clock=research_clock,
        )
        key = candidate_identity_hash(model)
        if key in tested_hashes or key in seen:
            return
        seen.add(key)
        out.append(model)

    for elite in elites:
        model_id = str(elite.get("model_id") or parsed.primary_model_id)
        params = dict(elite.get("strategy_params") or DEFAULT_STRATEGY_PARAMS)
        threshold = float(params.get("signal_threshold", DEFAULT_STRATEGY_PARAMS["signal_threshold"]))
        holding = int(params.get("holding_period_bars", 15))
        for th, hp in itertools.product(
            _neighbor_values(threshold, _THRESHOLD_STEPS),
            _neighbor_values(float(holding), _HOLDING_STEPS),
        ):
            if len(out) >= exec_cap:
                break
            p = copy.deepcopy(DEFAULT_STRATEGY_PARAMS)
            p.update(params)
            p["signal_threshold"] = th
            p["holding_period_bars"] = int(hp)
            _add(
                CandidateModel(
                    candidate_id=param_hash_from_dict(model_id, p),
                    model_id=model_id,
                    strategy_params=p,
                    thesis=parsed.thesis,
                    metadata={
                        "source_model": parsed.primary_model_id,
                        "strategy_family": model_id,
                        "elite_parent": elite.get("candidate_id"),
                        "refinement": "neighbor",
                    },
                )
            )
        if len(out) >= exec_cap:
            break

    if family_budget and len(out) < max_candidates:
        for cand in propose_family_variant_candidates(
            elites=elites,
            parsed=parsed,
            tested_hashes=tested_hashes,
            max_candidates=min(family_budget, max_candidates - len(out)),
            target_event_id=target_event_id,
            target_symbol=target_symbol,
            research_clock=research_clock,
        ):
            _add(cand)
            if len(out) >= max_candidates:
                break

    if len(out) < max_candidates and explore_budget:
        for cand in generate_candidates(
            parsed,
            max_candidates=explore_budget,
            expand_for_vectorbt=True,
            target_event_id=target_event_id,
            target_symbol=target_symbol,
            research_clock=research_clock,
        ):
            _add(cand)
            if len(out) >= max_candidates:
                break

    return out[:max_candidates]
