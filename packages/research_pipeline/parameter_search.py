"""Deterministic candidate parameter search for the autoresearch pipeline.

The search layer proposes parameter sets before VectorBT screens them. It does
not promote models and it does not replace robustness or HftBacktest gates.
Advanced methods use stdlib optimizers and record that backend explicitly.
"""

from __future__ import annotations

import itertools
import json
import math
import random
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from features_engine.src.model_registry import load_model_registry, resolve_model_id
from hft3.hbt_parameter_sets import (
    HBT_PARAMETER_SET_PRE_HBT_STATUS,
    HBT_PARAMETER_SET_SCHEMA_VERSION,
    HBT_PARAMETER_SET_SOURCE,
)

from research_pipeline.types import CandidateModel, ParsedHypothesis

DEFAULT_HOLDING_PERIODS_BARS = [15, 30, 60]
DEFAULT_THRESHOLDS = [0.10, 0.15, 0.20, 0.25]
SUPPORTED_SEARCH_METHODS = {"grid", "bayesian", "evolutionary"}
HBT_PARAMETER_SET_AUTHORITY_REFS = (
    "packages/research_pipeline/parameter_search.py",
    "packages/research_pipeline/model_generation.py",
    "packages/research_pipeline/elite_refinement.py",
    "packages/research_pipeline/generation_loop.py",
    "docs/project/HFTBACKTEST_ONLY_EVIDENCE_PARAMETER_SURFACE_PLAN.md",
    "vault:decisions/2026-06-29 HBT-only all-model uniform-flow rule.md",
    "docs/human/RESEARCH_SYSTEM_EXECUTION_ORDER.md",
    "tests/test_workbench/test_self_learning_loop_contract.py",
)
RANGE_ALIASES = {
    "holding_bars": "holding_period_bars",
    "stop_loss": "stop_loss_pct",
    "take_profit": "take_profit_pct",
}
_HBT_PARAMETER_FAMILY_BY_METHOD = {
    "grid": "grid",
    "bayesian": "bayesian-prior",
    "evolutionary": "evolutionary-prior",
}


@dataclass(frozen=True)
class SearchSelection:
    params: dict[str, Any]
    metadata: dict[str, Any]


def model_ids_for_search(parsed: ParsedHypothesis, *, hybrid_models: bool = True) -> list[str]:
    """Return primary model plus registry-approved adjacent hypothesis model ids."""
    models = [parsed.primary_model_id]
    if hybrid_models:
        registry = load_model_registry().get("models", {})
        for feature_id in parsed.feature_list:
            entry = registry.get(feature_id) if feature_id else None
            if (
                isinstance(entry, Mapping)
                and entry.get("kind") == "hypothesis"
                and feature_id not in models
            ):
                models.append(feature_id)
    return models[:3]


def parameter_grid(
    parsed: ParsedHypothesis,
    *,
    expand_for_vectorbt: bool = False,
) -> dict[str, list[Any]]:
    """Build a finite parameter grid from parsed ranges and defaults."""
    grid: dict[str, list[Any]] = {
        "signal_threshold": _range_points(
            _param_range(parsed, "signal_threshold"),
            default=DEFAULT_THRESHOLDS,
        )
    }
    stop_loss = _range_points(_param_range(parsed, "stop_loss_pct", "stop_loss"), default=[])
    take_profit = _range_points(_param_range(parsed, "take_profit_pct", "take_profit"), default=[])
    if stop_loss:
        grid["stop_loss_pct"] = stop_loss
    if take_profit:
        grid["take_profit_pct"] = take_profit
    # Round trips per event window (requires the exit leg downstream); only
    # models whose registry ranges opt in explore it — no silent grid blowup.
    round_trips = _range_points(_param_range(parsed, "max_round_trips"), default=[])
    if round_trips:
        grid["max_round_trips"] = [int(value) for value in round_trips]
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
    """Select parameter dicts from ``grid`` with no silent method fallback."""
    if max_candidates <= 0:
        return []
    method = _normalise_method(search_method)
    normalised = _normalise_grid(grid)
    all_params = _grid_product(normalised)
    if method == "grid":
        selected_indexes = list(range(min(max_candidates, len(all_params))))
        iterations = len(selected_indexes)
    elif method == "bayesian":
        selected_indexes, iterations = _bayesian_prior_indexes(
            all_params,
            normalised,
            max_candidates=max_candidates,
        )
    else:
        selected_indexes, iterations = _evolutionary_indexes(
            all_params,
            normalised,
            max_candidates=max_candidates,
            seed=seed,
        )

    out: list[SearchSelection] = []
    for ordinal, index in enumerate(selected_indexes):
        out.append(
            SearchSelection(
                params=all_params[index],
                metadata={
                    "schema_version": "hft3_candidate_search_selection_v1",
                    "requested_method": method,
                    "selected_method": method,
                    "backend": "stdlib",
                    "method_status": "ok",
                    "optimizer_stage": "pre_vectorbt_candidate_generation",
                    "objective_evaluations": 0,
                    "seed": seed,
                    "iterations": iterations,
                    "grid_size": len(all_params),
                    "explored_parameter_count": len(selected_indexes),
                    "selected_index": index,
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
    hybrid_models: bool = True,
    seed: int = 42,
) -> Iterable[tuple[str, SearchSelection]]:
    """Yield ``(model_id, selection)`` pairs under a global candidate budget."""
    if max_candidates <= 0:
        return
    method = _normalise_method(search_method)
    models = model_ids_for_search(parsed, hybrid_models=hybrid_models)
    selections = select_parameters(
        parameter_grid(parsed, expand_for_vectorbt=expand_for_vectorbt),
        max_candidates=max_candidates,
        search_method=method,
        seed=seed,
    )
    count = 0
    if method == "grid":
        for model_id in models:
            for selection in selections:
                if count >= max_candidates:
                    return
                yield model_id, selection
                count += 1
        return

    for idx, selection in enumerate(selections):
        if count >= max_candidates:
            return
        yield models[idx % len(models)], selection
        count += 1


def hbt_parameter_set_from_candidate(candidate: CandidateModel) -> dict[str, Any]:
    """Convert an existing self-learning proposal into an HBT parameter-set spec."""
    canonical_model_id = resolve_model_id(candidate.model_id)
    metadata = dict(candidate.metadata or {})
    family = _hbt_parameter_family_from_metadata(metadata)
    strategy_params = dict(candidate.strategy_params or {})
    # Campaign runs must produce closed-trade realized PnL: default the exit
    # leg on (holding expiry closes the position) unless a proposal explicitly
    # disables it. Without this a run holds through the window and reports
    # cash-only PnL that can never pass the economic gate.
    strategy_params.setdefault("exit_at_holding", True)
    return {
        "schema_version": HBT_PARAMETER_SET_SCHEMA_VERSION,
        "source": HBT_PARAMETER_SET_SOURCE,
        "canonical_model_id": canonical_model_id,
        "parameter_family": family,
        "strategy_params": strategy_params,
        "parameter_proposal_status": HBT_PARAMETER_SET_PRE_HBT_STATUS,
        "objective_evaluations": 0,
        "optimizer_claim": False,
        "source_candidate_id": candidate.candidate_id,
        "authority_refs": list(HBT_PARAMETER_SET_AUTHORITY_REFS),
    }


def hbt_parameter_sets_from_candidates(
    candidates: Iterable[CandidateModel],
) -> list[dict[str, Any]]:
    """Return deduped HBT parameter-set specs from self-learning candidates."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        spec = hbt_parameter_set_from_candidate(candidate)
        key = _hbt_parameter_set_key(spec)
        if key in seen:
            continue
        seen.add(key)
        out.append(spec)
    return out


def hbt_parameter_sets_from_model_registry(
    *,
    search_methods: Sequence[str] = ("grid",),
    max_candidates_per_method: int = 1,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Declare pre-HBT self-learning parameter-set specs for every registry slug."""
    if max_candidates_per_method <= 0:
        return []
    methods = _normalise_search_methods(search_methods)
    models = load_model_registry().get("models", {})
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slug, entry in models.items():
        model_entry = entry if isinstance(entry, Mapping) else {}
        parsed = _parsed_from_registry_model(str(slug), model_entry)
        grid = _declared_parameter_grid(parsed)
        for method in methods:
            selections = select_parameters(
                grid,
                max_candidates=max_candidates_per_method,
                search_method=method,
                seed=seed,
            )
            for selection in selections:
                candidate = CandidateModel(
                    candidate_id=_registry_parameter_candidate_id(str(slug), selection),
                    model_id=str(slug),
                    strategy_params=dict(selection.params),
                    thesis=f"Declared pre-HBT self-learning parameter proposal for {slug}",
                    metadata={
                        "source_model": str(slug),
                        "strategy_family": str(slug),
                        "candidate_search": dict(selection.metadata),
                    },
                )
                spec = hbt_parameter_set_from_candidate(candidate)
                key = _hbt_parameter_set_key(spec)
                if key in seen:
                    continue
                seen.add(key)
                out.append(spec)
    return out


def _normalise_method(search_method: str) -> str:
    method = str(search_method or "grid").strip().lower().replace("-", "_")
    if method not in SUPPORTED_SEARCH_METHODS:
        raise ValueError(
            f"unknown search_method {search_method!r}; expected one of {sorted(SUPPORTED_SEARCH_METHODS)}"
        )
    return method


def _normalise_search_methods(search_methods: Sequence[str]) -> tuple[str, ...]:
    raw_methods = (
        (search_methods,)
        if isinstance(search_methods, str)
        else tuple(search_methods)
    )
    methods: list[str] = []
    for raw_method in raw_methods:
        method = _normalise_method(str(raw_method))
        if method not in methods:
            methods.append(method)
    if not methods:
        raise ValueError("search_methods must not be empty")
    return tuple(methods)


def _parsed_from_registry_model(slug: str, entry: Mapping[str, Any]) -> ParsedHypothesis:
    return ParsedHypothesis(
        thesis=f"Declared pre-HBT self-learning parameter proposal for {slug}",
        instrument_universe=[
            str(symbol) for symbol in entry.get("valid_instrument_universe") or []
        ],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=[slug],
        param_ranges=_registry_default_param_ranges(entry),
        primary_model_id=slug,
        source="model_registry",
    )


def _registry_default_param_ranges(entry: Mapping[str, Any]) -> dict[str, list[float]]:
    ranges = entry.get("default_param_ranges")
    if not isinstance(ranges, Mapping):
        return {}
    out: dict[str, list[float]] = {}
    for key, values in ranges.items():
        if isinstance(values, (list, tuple)) and values:
            out[str(key)] = [float(value) for value in values]
    return out


def _declared_parameter_grid(parsed: ParsedHypothesis) -> dict[str, list[Any]]:
    grid = parameter_grid(parsed)
    grid["holding_period_bars"] = _holding_points(
        _param_range(parsed, "holding_period_bars", "holding_bars")
    )
    return grid


def _registry_parameter_candidate_id(slug: str, selection: SearchSelection) -> str:
    method = str(selection.metadata.get("selected_method") or "grid")
    selected_index = int(selection.metadata.get("selected_index") or 0)
    return f"model_registry:{slug}:{method}:{selected_index}"


def _hbt_parameter_family_from_metadata(metadata: Mapping[str, Any]) -> str:
    search = metadata.get("candidate_search")
    if isinstance(search, Mapping):
        method = str(search.get("selected_method") or search.get("requested_method") or "grid")
        method = _normalise_method(method)
        return _HBT_PARAMETER_FAMILY_BY_METHOD[method]
    return "grid"


def _hbt_parameter_set_key(spec: Mapping[str, Any]) -> str:
    payload = {
        "canonical_model_id": spec["canonical_model_id"],
        "parameter_family": spec["parameter_family"],
        "strategy_params": spec["strategy_params"],
    }
    return _canonical_json(payload)


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _normalise_grid(grid: Mapping[str, Sequence[Any]]) -> dict[str, list[Any]]:
    normalised: dict[str, list[Any]] = {}
    for key in sorted(grid):
        values = _dedupe_values(list(grid[key]))
        if not values:
            continue
        normalised_key = RANGE_ALIASES.get(key, key)
        if normalised_key in normalised:
            raise ValueError(f"duplicate parameter range for {normalised_key!r}")
        normalised[normalised_key] = values
    if not normalised:
        raise ValueError("parameter grid must not be empty")
    return normalised


def _dedupe_values(values: Sequence[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = repr(value)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _param_range(parsed: ParsedHypothesis, key: str, *aliases: str) -> Sequence[float] | None:
    present = [candidate_key for candidate_key in (key, *aliases) if parsed.param_ranges.get(candidate_key)]
    if len(present) > 1:
        raise ValueError(f"duplicate parameter range for {RANGE_ALIASES.get(key, key)!r}")
    return parsed.param_ranges[present[0]] if present else None


def _grid_product(grid: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[key] for key in keys))]


def _range_points(values: Sequence[float] | None, *, default: Sequence[Any]) -> list[Any]:
    if not values:
        return list(default)
    if len(values) == 1:
        return [round(float(values[0]), 4)]
    lo = _finite_float(values[0], "range lower bound")
    hi = _finite_float(values[1], "range upper bound")
    if hi <= lo:
        return [round(lo, 4)]
    mid = (lo + hi) / 2.0
    return sorted({round(lo, 4), round(mid, 4), round(hi, 4)})


def _holding_points(values: Sequence[float] | None) -> list[int]:
    points = _range_points(values, default=DEFAULT_HOLDING_PERIODS_BARS)
    return sorted({max(1, int(round(float(point)))) for point in points})


def _finite_float(value: Any, name: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _vectors(params: Sequence[Mapping[str, Any]], grid: Mapping[str, Sequence[Any]]) -> list[tuple[float, ...]]:
    keys = list(grid.keys())
    positions = {
        key: {repr(value): idx for idx, value in enumerate(grid[key])}
        for key in keys
    }
    vectors: list[tuple[float, ...]] = []
    for row in params:
        coords = []
        for key in keys:
            values = list(grid[key])
            denom = max(1, len(values) - 1)
            coords.append(positions[key][repr(row[key])] / denom)
        vectors.append(tuple(coords))
    return vectors


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _bayesian_prior_indexes(
    all_params: Sequence[Mapping[str, Any]],
    grid: Mapping[str, Sequence[Any]],
    *,
    max_candidates: int,
) -> tuple[list[int], int]:
    """Space-filling prior acquisition used before any backtest observations."""
    vectors = _vectors(all_params, grid)
    target = tuple(0.5 for _ in range(len(next(iter(vectors), ()))))
    selected: list[int] = []
    remaining = set(range(len(all_params)))
    limit = min(max_candidates, len(all_params))
    while remaining and len(selected) < limit:
        scored = []
        for index in remaining:
            vec = vectors[index]
            if not selected:
                score = -_distance(vec, target)
            else:
                nearest = min(_distance(vec, vectors[chosen]) for chosen in selected)
                score = nearest - 0.05 * _distance(vec, target)
            scored.append((score, -index, index))
        _score, _neg_index, chosen = max(scored)
        selected.append(chosen)
        remaining.remove(chosen)
    return selected, len(selected)


def _evolutionary_indexes(
    all_params: Sequence[Mapping[str, Any]],
    grid: Mapping[str, Sequence[Any]],
    *,
    max_candidates: int,
    seed: int,
) -> tuple[list[int], int]:
    """Small deterministic genetic search over the finite parameter grid."""
    grid_size = len(all_params)
    limit = min(max_candidates, grid_size)
    if limit <= 0:
        return [], 0
    rng = random.Random(seed)
    vectors = _vectors(all_params, grid)
    index_by_signature = {tuple(row.items()): idx for idx, row in enumerate(all_params)}
    keys = list(grid.keys())
    population_size = min(grid_size, max(4, limit * 3))
    population = rng.sample(range(grid_size), population_size) if grid_size > population_size else list(range(grid_size))
    generations = max(4, limit * 3)

    for _generation in range(generations):
        ranked = sorted(
            set(population),
            key=lambda idx: (_evolutionary_fitness(vectors[idx], idx, seed), -idx),
            reverse=True,
        )
        survivors = ranked[: max(2, population_size // 2)]
        children: list[int] = []
        while len(survivors) + len(children) < population_size:
            parent_a = all_params[rng.choice(survivors)]
            parent_b = all_params[rng.choice(survivors)]
            child = {}
            for key in keys:
                values = list(grid[key])
                child[key] = parent_a[key] if rng.random() < 0.5 else parent_b[key]
                if rng.random() < 0.25:
                    child[key] = rng.choice(values)
            children.append(index_by_signature[tuple(child.items())])
        population = survivors + children

    ranked_final = sorted(
        set(population) | set(range(grid_size)),
        key=lambda idx: (_evolutionary_fitness(vectors[idx], idx, seed), -idx),
        reverse=True,
    )
    return ranked_final[:limit], generations


def _evolutionary_fitness(vector: Sequence[float], index: int, seed: int) -> float:
    center = tuple(0.5 for _ in vector)
    centeredness = 1.0 - _distance(vector, center)
    jitter = random.Random((seed + 1) * (index + 17)).random() * 1e-6
    # Keep the advisory smoke search near interior grid points while preserving
    # a smaller edge-exploration term so boundary hypotheses still surface.
    edge_exploration = sum(abs(coord - 0.5) for coord in vector) / max(1, len(vector))
    return 0.7 * centeredness + 0.3 * edge_exploration + jitter


__all__ = [
    "DEFAULT_HOLDING_PERIODS_BARS",
    "DEFAULT_THRESHOLDS",
    "SUPPORTED_SEARCH_METHODS",
    "SearchSelection",
    "HBT_PARAMETER_SET_AUTHORITY_REFS",
    "HBT_PARAMETER_SET_PRE_HBT_STATUS",
    "HBT_PARAMETER_SET_SCHEMA_VERSION",
    "HBT_PARAMETER_SET_SOURCE",
    "hbt_parameter_set_from_candidate",
    "hbt_parameter_sets_from_candidates",
    "hbt_parameter_sets_from_model_registry",
    "model_ids_for_search",
    "parameter_grid",
    "search_plan",
    "select_parameters",
]
