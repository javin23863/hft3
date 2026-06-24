"""Bounded deterministic parameter search for autoresearch candidates."""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from features_engine.src.model_registry import all_slugs
from research_pipeline.types import ParsedHypothesis

DEFAULT_HOLDING_PERIODS_BARS = [15, 30, 60]
DEFAULT_THRESHOLDS = [0.10, 0.15, 0.20, 0.25]
RANGE_ALIASES = {
    "holding_bars": "holding_period_bars",
    "stop_loss": "stop_loss_pct",
    "take_profit": "take_profit_pct",
}


@dataclass(frozen=True)
class SearchSelection:
    params: dict[str, Any]
    metadata: dict[str, Any]


def model_ids_for_search(parsed: ParsedHypothesis, *, hybrid: bool = True) -> list[str]:
    """Return primary model plus optional adjacent model ids from the registry."""
    registry_models = set(all_slugs())
    models = [parsed.primary_model_id]
    if hybrid:
        for feat in parsed.feature_list:
            if feat in registry_models and feat not in models:
                models.append(feat)
    return models[:3]


def parameter_grid(
    parsed: ParsedHypothesis,
    *,
    expand_for_vectorbt: bool = False,
) -> dict[str, list[Any]]:
    """Build a deterministic parameter grid from parsed registry ranges."""
    grid: dict[str, list[Any]] = {
        "signal_threshold": _range_points(
            _param_range(parsed, "signal_threshold"),
            default=DEFAULT_THRESHOLDS,
        )
    }
    stop_loss = _range_points(_param_range(parsed, "stop_loss_pct", "stop_loss"), default=[])
    take_profit = _range_points(_param_range(parsed, "take_profit_pct", "take_profit"), default=[])
    grid["stop_loss_pct"] = stop_loss
    grid["take_profit_pct"] = take_profit
    if expand_for_vectorbt:
        grid["holding_period_bars"] = _holding_points(
            _param_range(parsed, "holding_period_bars", "holding_bars")
        )
    return grid


def select_parameters(
    grid: Mapping[str, Sequence[Any]],
    *,
    max_candidates: int,
    search_method: str = "grid",
    seed: int = 42,
) -> list[SearchSelection]:
    """Select parameter dicts from ``grid`` with explicit method status metadata."""
    if max_candidates <= 0:
        return []
    normalised = _normalise_grid(grid)
    all_params = _grid_product(normalised)
    method = search_method.lower().replace("-", "_")
    method_status = "ok"
    fallback_method: str | None = None
    selected_method = method
    if method in {"bayesian", "evolutionary"}:
        method_status = "method_unavailable"
        fallback_method = "seeded"
        selected_method = "seeded"
    elif method not in {"grid", "seeded", "hybrid"}:
        raise ValueError(f"unknown search_method {search_method!r}")

    if selected_method == "grid":
        selected_indexes = list(range(min(max_candidates, len(all_params))))
    elif selected_method == "hybrid":
        head_count = min(max_candidates, max(1, max_candidates // 2), len(all_params))
        head = list(range(head_count))
        tail_budget = max_candidates - len(head)
        tail_pool = [idx for idx in range(len(all_params)) if idx not in set(head)]
        rng = random.Random(seed)
        rng.shuffle(tail_pool)
        selected_indexes = head + sorted(tail_pool[:tail_budget])
    else:
        selected_indexes = list(range(len(all_params)))
        rng = random.Random(seed)
        rng.shuffle(selected_indexes)
        selected_indexes = sorted(selected_indexes[:max_candidates])

    out: list[SearchSelection] = []
    for ordinal, idx in enumerate(selected_indexes):
        out.append(
            SearchSelection(
                params=all_params[idx],
                metadata={
                    "search_method": method,
                    "method_status": method_status,
                    "fallback_method": fallback_method,
                    "selected_method": selected_method,
                    "seed": seed,
                    "grid_size": len(all_params),
                    "selected_index": idx,
                    "selection_ordinal": ordinal,
                    "max_candidates": max_candidates,
                },
            )
        )
    return out


def search_plan(
    parsed: ParsedHypothesis,
    *,
    max_candidates: int,
    expand_for_vectorbt: bool = False,
    search_method: str = "grid",
    hybrid: bool = True,
    seed: int = 42,
) -> Iterable[tuple[str, SearchSelection]]:
    """Yield ``(model_id, selection)`` pairs under the global candidate budget."""
    models = model_ids_for_search(parsed, hybrid=hybrid)
    grid = parameter_grid(parsed, expand_for_vectorbt=expand_for_vectorbt)
    per_model = select_parameters(
        grid,
        max_candidates=max_candidates,
        search_method=search_method,
        seed=seed,
    )
    count = 0
    yielded_pairs: set[tuple[int, str]] = set()
    primary_budget = max_candidates if len(models) == 1 else (max_candidates + 1) // 2
    for selection_index, selection in enumerate(per_model[:primary_budget]):
        yield models[0], selection
        yielded_pairs.add((selection_index, models[0]))
        count += 1

    secondary_models = models[1:] or models
    for selection_index in range(primary_budget, len(per_model)):
        if count >= max_candidates:
            return
        selection = per_model[selection_index]
        model_id = secondary_models[(selection_index - primary_budget) % len(secondary_models)]
        yield model_id, selection
        yielded_pairs.add((selection_index, model_id))
        count += 1

    for selection_index, selection in enumerate(per_model):
        for model_id in models:
            if (selection_index, model_id) in yielded_pairs:
                continue
            if count >= max_candidates:
                return
            yield model_id, selection
            yielded_pairs.add((selection_index, model_id))
            count += 1


def _normalise_grid(grid: Mapping[str, Sequence[Any]]) -> dict[str, list[Any]]:
    normalised: dict[str, list[Any]] = {}
    for key in sorted(grid):
        values = list(grid[key])
        if not values:
            continue
        normalised_key = _normalised_range_key(key)
        if normalised_key in normalised:
            raise ValueError(f"duplicate parameter range for {normalised_key!r}")
        normalised[normalised_key] = values
    if not normalised:
        raise ValueError("parameter grid must not be empty")
    return normalised


def _param_range(parsed: ParsedHypothesis, key: str, *aliases: str) -> Sequence[float] | None:
    present = [
        candidate_key
        for candidate_key in (key, *aliases)
        if parsed.param_ranges.get(candidate_key)
    ]
    if len(present) > 1:
        normalised_key = _canonical_range_key(key, *aliases)
        raise ValueError(f"duplicate parameter range for {normalised_key!r}")
    if present:
        return parsed.param_ranges[present[0]]
    return None


def _normalised_range_key(key: str) -> str:
    return RANGE_ALIASES.get(key, key)


def _canonical_range_key(key: str, *aliases: str) -> str:
    for candidate_key in (key, *aliases):
        normalised_key = _normalised_range_key(candidate_key)
        if normalised_key == key:
            return key
    return _normalised_range_key(key)


def _grid_product(grid: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[key] for key in keys))]


def _range_points(values: Sequence[float] | None, *, default: Sequence[Any]) -> list[Any]:
    if not values:
        return list(default)
    if len(values) == 1:
        return [round(float(values[0]), 4)]
    lo = float(values[0])
    hi = float(values[1])
    if hi <= lo:
        return [round(lo, 4)]
    mid = (lo + hi) / 2.0
    return sorted({round(lo, 4), round(mid, 4), round(hi, 4)})


def _holding_points(values: Sequence[float] | None) -> list[int]:
    points = _range_points(values, default=DEFAULT_HOLDING_PERIODS_BARS)
    return sorted({max(1, int(round(float(point)))) for point in points})
