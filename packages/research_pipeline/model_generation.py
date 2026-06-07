"""Generate candidate models from a parsed hypothesis."""

from __future__ import annotations

import copy
import itertools
import random
from typing import Iterator, List

from workbench.src.core.params import DEFAULT_STRATEGY_PARAMS, param_hash_from_dict

from research_pipeline.types import CandidateModel, ParsedHypothesis

_DEFAULT_THRESHOLDS = [0.10, 0.15, 0.20, 0.25]
_DEFAULT_RANDOM_THRESHOLD_RANGE = (0.05, 0.50)
_DEFAULT_HOLDING_PERIODS_BARS = [15, 30, 60]


def _threshold_grid(
    parsed: ParsedHypothesis,
    *,
    mode: str = "grid",
    n_samples: int = 5,
) -> List[float]:
    pr = parsed.param_ranges.get("signal_threshold")
    if not pr or len(pr) < 2:
        if mode == "random":
            lo, hi = _DEFAULT_RANDOM_THRESHOLD_RANGE
            samples = max(1, int(n_samples))
            return [round(random.uniform(lo, hi), 4) for _ in range(samples)]
        if mode != "grid":
            raise ValueError(f"unsupported search mode: {mode}")
        return list(_DEFAULT_THRESHOLDS)
    lo, hi = float(pr[0]), float(pr[1])
    if hi <= lo:
        return [lo]
    if mode == "random":
        samples = max(1, int(n_samples))
        return [round(random.uniform(lo, hi), 4) for _ in range(samples)]
    if mode != "grid":
        raise ValueError(f"unsupported search mode: {mode}")
    mid = (lo + hi) / 2.0
    return sorted({round(lo, 4), round(mid, 4), round(hi, 4)})


def generate_candidates(
    parsed: ParsedHypothesis,
    *,
    max_candidates: int = 20,
    expand_for_vectorbt: bool = False,
    search_mode: str = "grid",
    num_samples: int = 5,
    max_iterations: int = 1,
) -> Iterator[CandidateModel]:
    """Yield param variants for primary model and keyword-adjacent slugs.

    When expand_for_vectorbt is True, generates a richer grid across
    signal_threshold, holding_period, and stop_loss/take_profit for
    VectorBT's cheap parameter filtering.
    """
    models = [parsed.primary_model_id]
    for feat in parsed.feature_list:
        if feat not in models and feat.isupper():
            models.append(feat)
    models = models[:3]

    sample_count = max(1, int(num_samples)) * max(1, int(max_iterations))
    thresholds = _threshold_grid(parsed, mode=search_mode, n_samples=sample_count)
    count = 0
    for model_id, threshold in itertools.product(models, thresholds):
        if count >= max_candidates:
            break
        params = dict(DEFAULT_STRATEGY_PARAMS)
        params["signal_threshold"] = threshold
        cid = param_hash_from_dict(model_id, params)
        yield CandidateModel(
            candidate_id=cid,
            model_id=model_id,
            strategy_params=params,
            thesis=parsed.thesis,
            metadata={"source_model": parsed.primary_model_id, "strategy_family": model_id},
        )
        count += 1

    if expand_for_vectorbt:
        for model_id in models[:1]:
            for threshold, holding in itertools.product(thresholds, _DEFAULT_HOLDING_PERIODS_BARS):
                if count >= max_candidates:
                    break
                params = copy.deepcopy(DEFAULT_STRATEGY_PARAMS)
                params["signal_threshold"] = threshold
                params["holding_period_bars"] = holding
                cid = param_hash_from_dict(model_id, params)
                yield CandidateModel(
                    candidate_id=cid,
                    model_id=model_id,
                    strategy_params=params,
                    thesis=parsed.thesis,
                    metadata={
                        "source_model": parsed.primary_model_id,
                        "strategy_family": model_id,
                        "vectorbt_grid": True,
                    },
                )
                count += 1
