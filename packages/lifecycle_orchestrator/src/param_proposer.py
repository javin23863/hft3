"""Deterministic parameter proposer (the param-tweak route's generator).

Re-optimizes an EXISTING certified hypothesis's params within bounded ranges.
No LM, no new code — enumerate the family's sweep grid, clamp every combo to the
certified envelope's param bounds, return the survivors in deterministic order.
(The LM proposer F3 is reserved for genuinely novel hypothesis variants.)
"""
from __future__ import annotations

import itertools
from typing import Any, Optional


def _in_bounds(name: str, value: Any, bounds: Optional[dict]) -> bool:
    if not bounds or name not in bounds:
        return True
    lo, hi = bounds[name]
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


def propose_params(family_spec: dict, *, envelope_bounds: Optional[dict] = None, max_candidates: int = 16) -> list[dict]:
    """Enumerate the clamped grid. Deterministic (sorted params, sorted values)."""
    sweep = family_spec.get("sweep") or {}
    names = sorted(sweep.keys())
    grids = []
    for name in names:
        spec = sweep[name] or {}
        grid = spec.get("grid")
        if not grid:
            lo, hi = spec.get("min"), spec.get("max")
            grid = [lo, hi] if lo is not None and hi is not None else []
        grids.append(sorted(g for g in grid if g is not None))

    out: list[dict] = []
    for combo in itertools.product(*grids):
        params = dict(zip(names, combo))
        if all(_in_bounds(n, v, envelope_bounds) for n, v in params.items()):
            out.append(params)
        if len(out) >= max_candidates:
            break
    return out
