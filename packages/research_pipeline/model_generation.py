"""Generate candidate models from a parsed hypothesis."""

from __future__ import annotations

import itertools
from typing import Iterator, List

from workbench.src.core.params import DEFAULT_STRATEGY_PARAMS, param_hash_from_dict

from research_pipeline.types import CandidateModel, ParsedHypothesis

_DEFAULT_THRESHOLDS = [0.10, 0.15, 0.20, 0.25]


def _threshold_grid(parsed: ParsedHypothesis) -> List[float]:
    pr = parsed.param_ranges.get("signal_threshold")
    if not pr or len(pr) < 2:
        return list(_DEFAULT_THRESHOLDS)
    lo, hi = float(pr[0]), float(pr[1])
    if hi <= lo:
        return [lo]
    mid = (lo + hi) / 2.0
    return sorted({round(lo, 4), round(mid, 4), round(hi, 4)})


def generate_candidates(
    parsed: ParsedHypothesis,
    *,
    max_candidates: int = 20,
) -> Iterator[CandidateModel]:
    """Yield param variants for primary model and keyword-adjacent slugs."""
    models = [parsed.primary_model_id]
    for feat in parsed.feature_list:
        if feat not in models and feat.isupper():
            models.append(feat)
    models = models[:3]

    thresholds = _threshold_grid(parsed)
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
            metadata={"source_model": parsed.primary_model_id},
        )
        count += 1
