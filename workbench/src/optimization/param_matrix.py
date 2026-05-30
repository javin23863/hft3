"""Parameter matrix generation from model bounds."""

from __future__ import annotations

import itertools
from typing import Any, Dict, List

import numpy as np

from workbench.src.core.params import param_hash_from_dict
from workbench.src.registry.unified_registry import build_models_config


def load_parameter_bounds(model_id: str) -> Dict[str, List[float]]:
    cfg = build_models_config().get(model_id)
    if cfg is None:
        return {}
    return dict(cfg.parameter_bounds or {})


def _linspace_values(lo: float, hi: float, n: int) -> List[float]:
    if n <= 1:
        return [float(lo)]
    return [float(x) for x in np.linspace(lo, hi, n)]


def generate_param_grid(
    model_id: str,
    *,
    min_combinations: int = 100,
) -> List[Dict[str, Any]]:
    bounds = load_parameter_bounds(model_id)
    if not bounds:
        return []

    keys = sorted(bounds.keys())
    lists: List[List[float]] = []
    for key in keys:
        vals = bounds[key]
        if len(vals) >= 2 and all(isinstance(v, (int, float)) for v in vals[:2]):
            lo, hi = float(vals[0]), float(vals[-1])
            per_dim = max(2, int(round(min_combinations ** (1.0 / len(keys)))))
            lists.append(_linspace_values(lo, hi, per_dim))
        else:
            lists.append([float(v) for v in vals])

    combos = [dict(zip(keys, prod)) for prod in itertools.product(*lists)]
    if len(combos) < min_combinations and len(keys) == 1:
        key = keys[0]
        lo, hi = float(bounds[key][0]), float(bounds[key][-1])
        combos = [{key: v} for v in _linspace_values(lo, hi, min_combinations)]

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for params in combos:
        ph = param_hash_from_dict(model_id, params)
        if ph in seen:
            continue
        seen.add(ph)
        out.append({"parameter_hash": ph, "params": params})
    return out
