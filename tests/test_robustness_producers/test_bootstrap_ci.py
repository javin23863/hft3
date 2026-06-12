"""Tests for bootstrap_ci.

Covers:
  - determinism: same seed → same result
  - different seeds → may differ
  - CI contains sample mean (percentile bootstrap always includes mean in [lo, hi])
  - CI widens with higher variance
  - edge cases: n=0, n=1
  - n specified correctly in output
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO / "packages"))

from hft3_bootstrap import setup_repo_paths
setup_repo_paths()

from research_pipeline.src.robustness_producers import bootstrap_ci


class TestDeterminism:
    def test_same_seed_same_result(self):
        expecs = [0.1, 0.2, 0.3, 0.4, 0.15, 0.25, 0.12]
        r1 = bootstrap_ci(expecs, n_boot=500, seed=0)
        r2 = bootstrap_ci(expecs, n_boot=500, seed=0)
        assert r1["mean"] == r2["mean"]
        assert r1["ci_lo_95"] == r2["ci_lo_95"]
        assert r1["ci_hi_95"] == r2["ci_hi_95"]

    def test_different_seed_may_differ(self):
        rng = np.random.default_rng(42)
        expecs = list(rng.normal(1.0, 0.5, 50))
        r0 = bootstrap_ci(expecs, n_boot=1000, seed=0)
        r1 = bootstrap_ci(expecs, n_boot=1000, seed=1)
        # With 50 observations and 1000 bootstrap draws, different seeds
        # almost certainly produce slightly different CI bounds
        # (not guaranteed mathematically, but overwhelmingly likely)
        # We just check the structure is correct for both
        assert r0["ci_lo_95"] <= r0["mean"] <= r0["ci_hi_95"]
        assert r1["ci_lo_95"] <= r1["mean"] <= r1["ci_hi_95"]


class TestCIContainsMean:
    """The 95% percentile bootstrap CI must contain the sample mean."""

    def test_ci_contains_mean_positive(self):
        expecs = [0.5, 0.6, 0.7, 0.55, 0.65, 0.58, 0.62]
        result = bootstrap_ci(expecs, seed=42)
        assert result["ci_lo_95"] <= result["mean"] <= result["ci_hi_95"], (
            f"mean={result['mean']} not in CI [{result['ci_lo_95']}, {result['ci_hi_95']}]"
        )

    def test_ci_contains_mean_mixed_signs(self):
        expecs = [-0.3, 0.4, -0.1, 0.2, 0.1, -0.05, 0.15]
        result = bootstrap_ci(expecs, seed=0)
        assert result["ci_lo_95"] <= result["mean"] <= result["ci_hi_95"]

    def test_ci_contains_mean_large_n(self):
        rng = np.random.default_rng(7)
        expecs = list(rng.normal(2.5, 0.3, 200))
        result = bootstrap_ci(expecs, n_boot=2000, seed=0)
        assert result["ci_lo_95"] <= result["mean"] <= result["ci_hi_95"]

    def test_mean_matches_numpy_mean(self):
        expecs = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = bootstrap_ci(expecs, seed=0)
        expected_mean = float(np.mean(expecs))
        assert abs(result["mean"] - expected_mean) < 1e-9


class TestCIWidensWithVariance:
    """Higher variance → wider CI (all else equal)."""

    def test_wider_ci_for_higher_variance(self):
        rng_base = np.random.default_rng(77)
        base_expecs = list(rng_base.normal(1.0, 0.1, 50))  # low variance
        high_expecs = list(rng_base.normal(1.0, 5.0, 50))  # high variance

        r_low = bootstrap_ci(base_expecs, n_boot=2000, seed=0)
        r_high = bootstrap_ci(high_expecs, n_boot=2000, seed=0)

        ci_width_low = r_low["ci_hi_95"] - r_low["ci_lo_95"]
        ci_width_high = r_high["ci_hi_95"] - r_high["ci_lo_95"]

        assert ci_width_high > ci_width_low, (
            f"High-variance CI width={ci_width_high:.4f} should exceed "
            f"low-variance width={ci_width_low:.4f}"
        )


class TestEdgeCases:
    def test_empty_list_returns_none_ci(self):
        result = bootstrap_ci([])
        assert result["mean"] is None
        assert result["ci_lo_95"] is None
        assert result["ci_hi_95"] is None
        assert result["n"] == 0

    def test_single_observation_returns_none_ci(self):
        result = bootstrap_ci([1.5])
        assert result["mean"] == pytest.approx(1.5)
        assert result["ci_lo_95"] is None
        assert result["ci_hi_95"] is None
        assert result["n"] == 1

    def test_two_observations_returns_valid_ci(self):
        result = bootstrap_ci([1.0, 3.0], n_boot=500, seed=0)
        assert result["mean"] == pytest.approx(2.0)
        assert result["ci_lo_95"] is not None
        assert result["ci_hi_95"] is not None
        assert result["ci_lo_95"] <= result["mean"] <= result["ci_hi_95"]

    def test_n_field_correct(self):
        expecs = [0.1, 0.2, 0.3, 0.4, 0.5]
        result = bootstrap_ci(expecs)
        assert result["n"] == 5

    def test_ci_orientation(self):
        """lo must be <= hi."""
        rng = np.random.default_rng(0)
        expecs = list(rng.normal(0.0, 1.0, 30))
        result = bootstrap_ci(expecs, seed=0)
        assert result["ci_lo_95"] <= result["ci_hi_95"]
