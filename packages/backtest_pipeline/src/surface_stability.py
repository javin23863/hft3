"""Surface-stability producer for VBT-3.

Computes the six required in-sample surface-robustness checks defined in
``docs/project/ROBUSTNESS_TESTING_SPEC.md`` §4 (lines 130-144):

    "The system must not pick the highest point just because it is highest."

Required checks (per the spec):
  - plateau width
  - neighbor stability
  - cliff distance from loss regions
  - parameter perturbation sensitivity
  - peak-vs-plateau comparison
  - minimum sample size

Acceptance criterion (§4, line 143-144): "a sharp isolated peak near loss
regions is rejected or marked experimental even if its in-sample PnL is high."

The public entry point is :func:`compute_surface_stability`, which returns a
dict whose shape matches the ``surface_stability_metrics`` fixture used by the
VectorBT adapter screening-artifact validator
(``_is_surface_stability_defined``).
"""
from __future__ import annotations

import math
import statistics
from collections import deque
from typing import Any, Dict, Mapping

# ---------------------------------------------------------------------------
# Authority citation (single source of truth for the formulas below).
# ---------------------------------------------------------------------------
CITATION = "docs/project/ROBUSTNESS_TESTING_SPEC.md:130-144"

REQUIRED_CHECKS = (
    "plateau_width",
    "neighbor_stability",
    "cliff_distance_from_loss_regions",
    "parameter_perturbation_sensitivity",
    "peak_vs_plateau_comparison",
    "minimum_sample_size",
)

EVIDENCE_FIELDS = (
    "plateau_score",
    "plateau_width",
    "neighbor_stability",
    "cliff_distance_from_loss_regions",
    "parameter_perturbation_sensitivity",
    "peak_vs_plateau_comparison",
    "minimum_sample_size",
)

# Cap used when the peak is a positive outlier over a non-positive median.
_OUTLIER_RATIO_CAP = 999.0


def _normalise_key(key: Any) -> tuple:
    """Ensure every grid key is a tuple (1-D keys become 1-tuples)."""
    if isinstance(key, tuple):
        return key
    return (key,)


def _cell_performance(cell: Any, performance_metric: str) -> float:
    """Extract the performance metric from a cell value (dict or scalar)."""
    if isinstance(cell, Mapping):
        val = cell.get(performance_metric, 0.0)
    else:
        val = cell
    if val is None:
        return 0.0
    try:
        f = float(val)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def _cell_trade_count(cell: Any) -> int:
    """Extract ``trade_count`` from a cell value (dict only)."""
    if isinstance(cell, Mapping):
        val = cell.get("trade_count", 0)
    else:
        val = 0
    if val is None:
        return 0
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _within_tolerance(cell_perf: float, ref_perf: float, tolerance: float) -> bool:
    """Relative-tolerance comparison.

    When ``ref_perf`` is zero the comparison falls back to absolute tolerance
    so a flat-at-zero surface is still treated as a plateau.
    """
    delta = abs(cell_perf - ref_perf)
    if ref_perf == 0:
        return delta <= tolerance
    return delta <= tolerance * abs(ref_perf)


def compute_surface_stability(
    surface_grid: Dict[Any, Dict[str, Any]],
    performance_metric: str = "net_return",
    tolerance: float = 0.1,
    loss_threshold: float = 0.0,
    min_sample_size: int = 30,
) -> Dict[str, Any]:
    """Compute the six §4 surface-stability checks for a parameter-surface grid.

    Parameters
    ----------
    surface_grid:
        Dict mapping parameter-value tuples to a dict of per-cell metrics.
        Each cell dict must include at least ``performance_metric`` and
        ``trade_count``.  Non-tuple keys are treated as 1-D parameters.

        .. warning::
            Every cell's metrics MUST be in-sample (IS) only.  This producer
            performs no lookahead guard — callers are responsible for ensuring
            no out-of-sample or future-leaking data is present in the grid.
            Contaminating the grid with OOS metrics would invalidate all six
            §4 checks.
    performance_metric:
        Key in each cell dict whose value is the performance measure.
    tolerance:
        Relative tolerance (0-1) used to define the plateau around the peak.
    loss_threshold:
        Performance below this value marks a "loss region" cell.
    min_sample_size:
        Minimum ``trade_count`` required across the plateau cells.

    Returns
    -------
    dict with keys matching ``SURFACE_STABILITY_EVIDENCE_FIELDS`` plus
    ``status``, ``formula_authority_status``, ``literature_or_ontology_citation``,
    and ``required_checks``.

    Raises
    ------
    ValueError
        If ``surface_grid`` is empty or not a dict.
    """
    if not isinstance(surface_grid, dict):
        raise ValueError("surface_grid must be a non-empty dict")
    if len(surface_grid) == 0:
        raise ValueError("surface_grid must be a non-empty dict")

    # ------------------------------------------------------------------
    # Normalise keys → tuples and extract per-cell performance / trade count.
    # ------------------------------------------------------------------
    grid: Dict[tuple, Any] = {}
    for key, value in surface_grid.items():
        grid[_normalise_key(key)] = value

    all_keys = list(grid.keys())
    perf_cache: Dict[tuple, float] = {
        k: _cell_performance(grid[k], performance_metric) for k in all_keys
    }
    trade_cache: Dict[tuple, int] = {k: _cell_trade_count(grid[k]) for k in all_keys}
    all_perfs = [perf_cache[k] for k in all_keys]

    # ------------------------------------------------------------------
    # Locate the peak cell (highest performance).  Ties resolve to the first
    # key in insertion order — deterministic given a stable dict ordering.
    # ------------------------------------------------------------------
    peak_key = max(all_keys, key=lambda k: perf_cache[k])
    peak_perf = perf_cache[peak_key]

    # ------------------------------------------------------------------
    # Build the integer-indexed grid structure.
    # ------------------------------------------------------------------
    ndim = len(peak_key)
    # Sorted unique parameter values per dimension → deterministic indexing.
    dim_values: list = []
    for d in range(ndim):
        unique_vals = sorted({k[d] for k in all_keys})
        dim_values.append(unique_vals)

    key_to_idx: Dict[tuple, tuple] = {}
    for key in all_keys:
        key_to_idx[key] = tuple(dim_values[d].index(key[d]) for d in range(ndim))
    idx_to_key: Dict[tuple, tuple] = {v: k for k, v in key_to_idx.items()}

    grid_diameter = sum(len(dv) - 1 for dv in dim_values)

    def _neighbor_keys(key: tuple) -> list:
        """Immediate grid neighbours (±1 index step in exactly one dimension)."""
        idx = key_to_idx[key]
        neighbours = []
        for d in range(ndim):
            for offset in (-1, 1):
                new_idx = list(idx)
                new_idx[d] += offset
                new_idx_t = tuple(new_idx)
                if new_idx_t in idx_to_key:
                    neighbours.append(idx_to_key[new_idx_t])
        return neighbours

    # ------------------------------------------------------------------
    # 1. plateau_width — cells within relative tolerance of the peak.
    # ------------------------------------------------------------------
    plateau_keys = [
        k for k in all_keys if _within_tolerance(perf_cache[k], peak_perf, tolerance)
    ]
    plateau_width = len(plateau_keys)

    # ------------------------------------------------------------------
    # 2. neighbor_stability — fraction of peak's immediate neighbours
    #    that are within tolerance of the peak.  No neighbours → 0.0.
    # ------------------------------------------------------------------
    peak_neighbours = _neighbor_keys(peak_key)
    if len(peak_neighbours) == 0:
        neighbor_stability = 0.0
    else:
        within = sum(
            1
            for n in peak_neighbours
            if _within_tolerance(perf_cache[n], peak_perf, tolerance)
        )
        neighbor_stability = within / len(peak_neighbours)

    # ------------------------------------------------------------------
    # 3. cliff_distance_from_loss_regions — BFS (Manhattan grid steps) from
    #    the peak to the nearest loss cell.  If none exists, use the grid
    #    diameter.
    # ------------------------------------------------------------------
    if peak_perf < loss_threshold:
        cliff_distance = 0
    else:
        loss_keys = {k for k in all_keys if perf_cache[k] < loss_threshold}
        if not loss_keys:
            cliff_distance = grid_diameter
        else:
            visited = {peak_key}
            queue: deque = deque([(peak_key, 0)])
            cliff_distance = grid_diameter  # fallback (should not be reached)
            while queue:
                cell, dist = queue.popleft()
                if perf_cache[cell] < loss_threshold:
                    cliff_distance = dist
                    break
                for n in _neighbor_keys(cell):
                    if n not in visited:
                        visited.add(n)
                        queue.append((n, dist + 1))

    # ------------------------------------------------------------------
    # 4. parameter_perturbation_sensitivity — maximum absolute performance
    #    delta under ±1 step from the peak, normalised by |peak_perf|,
    #    capped to [0, 1].
    # ------------------------------------------------------------------
    if len(peak_neighbours) == 0 or peak_perf == 0:
        parameter_perturbation_sensitivity = 0.0
    else:
        max_delta = max(abs(perf_cache[n] - peak_perf) for n in peak_neighbours)
        parameter_perturbation_sensitivity = min(max_delta / abs(peak_perf), 1.0)

    # ------------------------------------------------------------------
    # 5. peak_vs_plateau_comparison — ratio of peak performance to the
    #    median performance across the *entire* surface.  Values near 1.0
    #    mean the peak is not an outlier; values > 1.3 indicate a sharp,
    #    isolated peak (per §4 acceptance criterion).
    #
    #    Note: "plateau cells" here refers to the full parameter surface
    #    (the landscape/plateau being evaluated).  Using only cells within
    #    tolerance would cap the ratio at ~1/(1-tolerance) and could never
    #    exceed 1.3, making the sharp-peak detection required by §4
    #    impossible.
    # ------------------------------------------------------------------
    median_perf = statistics.median(all_perfs)
    if median_perf > 0:
        peak_vs_plateau_comparison = peak_perf / median_perf
    elif peak_perf > 0:
        # Peak positive, median ≤ 0 → extreme outlier.
        peak_vs_plateau_comparison = _OUTLIER_RATIO_CAP
    else:
        peak_vs_plateau_comparison = 1.0

    # ------------------------------------------------------------------
    # 6. minimum_sample_size — minimum trade_count across plateau cells.
    # ------------------------------------------------------------------
    if plateau_keys:
        minimum_sample_size_value = min(trade_cache[k] for k in plateau_keys)
    else:
        minimum_sample_size_value = 0

    # ------------------------------------------------------------------
    # Composite plateau_score (weighted, range 0-1).
    #
    # Implementation defaults — NOT spec-mandated values.  ROBUSTNESS_TESTING_SPEC
    # §4 lists the six required checks but defines no weighted composite; these
    # weights (0.3 / 0.2 / 0.2 / 0.2 / 0.1) are implementation defaults chosen to
    # satisfy the §4 acceptance criterion ("a sharp isolated peak near loss
    # regions is rejected").  The weighting emphasizes neighbor stability most,
    # then perturbation insensitivity, cliff safety, and peak-vs-plateau evenness
    # equally, with the smallest weight on meeting the minimum-sample floor.
    #
    # Per Codex review finding 7: these composite weights and the pass/fail
    # thresholds below are implementation defaults pending a vault waiver.  They
    # are not ratified by ROBUSTNESS_TESTING_SPEC and must be re-confirmed against
    # VECTORBT_SCREENING_ENGINE_SPEC.md lines 571-574 before being treated as
    # contract-stable.  See VECTORBT_SCREENING_ENGINE_SPEC.md (lines 571-574) for
    # the screening-engine composite contract that this surface-stability
    # composite is expected to converge with.
    # ------------------------------------------------------------------
    if peak_vs_plateau_comparison > 0:
        peak_vs_plateau_inv = min(1.0 / peak_vs_plateau_comparison, 1.0)
    else:
        peak_vs_plateau_inv = 0.0

    if grid_diameter > 0:
        cliff_ratio = min(cliff_distance / grid_diameter, 1.0)
    else:
        cliff_ratio = 0.0

    plateau_score = (
        0.3 * neighbor_stability
        + 0.2 * (1.0 - parameter_perturbation_sensitivity)
        + 0.2 * cliff_ratio
        + 0.2 * peak_vs_plateau_inv
        + 0.1 * (1.0 if minimum_sample_size_value >= min_sample_size else 0.0)
    )
    plateau_score = min(max(plateau_score, 0.0), 1.0)

    # ------------------------------------------------------------------
    # Status rule (§4 acceptance).
    #
    # Implementation defaults — NOT spec-mandated thresholds.  ROBUSTNESS_TESTING_SPEC
    # §4 requires the six checks but does not define pass/fail cutoffs.  The
    # thresholds below are implementation defaults chosen to satisfy the §4
    # acceptance criterion ("a sharp isolated peak near loss regions is rejected
    # or marked experimental even if its in-sample PnL is high"):
    #   neighbor_stability            < 0.5   (peak poorly supported by neighbours)
    #   parameter_perturbation_sensitivity > 0.3  (performance swings >30% per step)
    #   cliff_distance                < 2     (loss region within two grid steps)
    #   peak_vs_plateau_comparison    > 1.3   (sharp isolated peak)
    #   minimum_sample_size_value      < min_sample_size  (too few trades)
    # Any single trip → "fail".
    # ------------------------------------------------------------------
    status = "fail" if any(
        (
            neighbor_stability < 0.5,
            parameter_perturbation_sensitivity > 0.3,
            cliff_distance < 2,
            peak_vs_plateau_comparison > 1.3,
            minimum_sample_size_value < min_sample_size,
        )
    ) else "pass"

    # ------------------------------------------------------------------
    # Assemble the result dict matching the fixture shape.
    # ------------------------------------------------------------------
    return {
        "status": status,
        "formula_authority_status": "defined",
        "literature_or_ontology_citation": CITATION,
        "required_checks": list(REQUIRED_CHECKS),
        "plateau_score": round(plateau_score, 6),
        "plateau_width": plateau_width,
        "neighbor_stability": round(neighbor_stability, 6),
        "cliff_distance_from_loss_regions": cliff_distance,
        "parameter_perturbation_sensitivity": round(
            parameter_perturbation_sensitivity, 6
        ),
        "peak_vs_plateau_comparison": round(peak_vs_plateau_comparison, 6),
        "minimum_sample_size": minimum_sample_size_value,
    }