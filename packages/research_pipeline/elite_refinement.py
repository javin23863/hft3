"""Deterministic elite neighbor expansion for autoresearch Gen N+1."""

from __future__ import annotations

import copy
import itertools
from typing import Any, Mapping

from workbench.src.core.params import DEFAULT_STRATEGY_PARAMS, param_hash_from_dict

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
) -> list[CandidateModel]:
    """Build Gen N+1 candidates from validated elites plus exploration slice."""
    elites = _elite_rows(generation_summary)
    out: list[CandidateModel] = []
    seen: set[str] = set()

    def _add(model: CandidateModel) -> None:
        phash = param_hash_from_dict(model.model_id, model.strategy_params)
        if phash in tested_hashes or phash in seen:
            return
        seen.add(phash)
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
            if len(out) >= max_candidates:
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
        if len(out) >= max_candidates:
            break

    explore_budget = 0
    if exploration_fraction > 0 and max_candidates:
        explore_budget = max(1, int(max_candidates * exploration_fraction))
    if len(out) < max_candidates and explore_budget:
        for cand in generate_candidates(parsed, max_candidates=explore_budget, expand_for_vectorbt=True):
            _add(cand)
            if len(out) >= max_candidates:
                break

    return out[:max_candidates]
