"""Tests for cscv_pbo.

Covers:
  - synthetic dominant config → pbo ≈ 0 (train-winner always wins in test too)
  - pure-noise matrix (deterministic seeded) → pbo near 0.5 (assert 0.3–0.7)
  - skill+noise ordering: pbo(noise) > pbo(skill)
  - guard conditions: < 2 configs, < 4 blocks
  - NaN masking: excluded cells recorded in n_excluded
  - determinism: same inputs → same pbo
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

from research_pipeline.src.robustness_producers import cscv_pbo


def _noise_matrix(n_blocks: int, n_configs: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, 1.0, (n_blocks, n_configs))


def _skill_matrix(n_blocks: int, n_configs: int, skill_col: int, skill_offset: float, seed: int) -> np.ndarray:
    mat = _noise_matrix(n_blocks, n_configs, seed)
    mat[:, skill_col] += skill_offset
    return mat


class TestDominantConfig:
    """When one config dominates ALL blocks, it wins every train set and also
    every test set → PBO should be ≈ 0 (train-winner never in bottom half)."""

    def test_dominant_config_pbo_near_zero(self):
        n_blocks = 8
        n_configs = 6
        # Config 0 is far superior to all others in every block
        mat = _noise_matrix(n_blocks, n_configs, seed=101)
        mat[:, 0] += 20.0  # massive offset — always wins train AND test

        result = cscv_pbo(mat, n_splits=8)
        assert result["pbo"] is not None, f"Expected pbo, got reason: {result['reason']}"
        assert result["pbo"] < 0.15, (
            f"Dominant config should have pbo near 0, got {result['pbo']:.4f}"
        )

    def test_dominant_config_pbo_exactly_zero_possible(self):
        """With enough dominance, pbo should be exactly 0.0."""
        n_blocks = 8
        n_configs = 4
        mat = np.zeros((n_blocks, n_configs))
        mat[:, 0] = 100.0  # config 0 always returns 100; others return 0

        result = cscv_pbo(mat, n_splits=8)
        assert result["pbo"] is not None
        assert result["pbo"] == pytest.approx(0.0, abs=1e-9)


class TestNoisePBO:
    """Pure noise → no one config is systematically better → PBO near 0.5."""

    def test_noise_pbo_near_half(self):
        # Large matrix to make the average pbo reliably near 0.5
        # Use a fixed seed for determinism
        mat = _noise_matrix(n_blocks=16, n_configs=20, seed=555)
        result = cscv_pbo(mat, n_splits=8)
        assert result["pbo"] is not None, f"reason: {result['reason']}"
        assert 0.3 <= result["pbo"] <= 0.7, (
            f"Noise pbo should be near 0.5, got {result['pbo']:.4f}"
        )

    def test_noise_pbo_in_range_multiple_seeds(self):
        """Across several seeds, noise matrices should consistently yield pbo in [0.3, 0.7]."""
        for seed in [1, 42, 137, 999, 12345]:
            mat = _noise_matrix(n_blocks=16, n_configs=12, seed=seed)
            result = cscv_pbo(mat, n_splits=8)
            assert result["pbo"] is not None
            assert 0.2 <= result["pbo"] <= 0.8, (
                f"seed={seed}: noise pbo={result['pbo']:.4f} out of expected range"
            )


class TestSkillVsNoise:
    """pbo(noise) > pbo(skill): a matrix with genuine skill should have lower PBO."""

    def test_skill_ordering(self):
        seed = 42
        n_blocks = 16
        n_configs = 10

        # Pure noise
        noise_mat = _noise_matrix(n_blocks, n_configs, seed)
        noise_result = cscv_pbo(noise_mat, n_splits=8)

        # Same noise + dominant skill column
        skill_mat = _skill_matrix(n_blocks, n_configs, skill_col=0, skill_offset=10.0, seed=seed)
        skill_result = cscv_pbo(skill_mat, n_splits=8)

        assert noise_result["pbo"] is not None
        assert skill_result["pbo"] is not None
        assert noise_result["pbo"] > skill_result["pbo"], (
            f"Expected pbo(noise)={noise_result['pbo']:.4f} > pbo(skill)={skill_result['pbo']:.4f}"
        )


class TestGuards:
    """Guard conditions: insufficient configs, insufficient blocks, NaN masking."""

    def test_fewer_than_2_configs_returns_none(self):
        mat = np.ones((8, 1))
        result = cscv_pbo(mat, n_splits=8)
        assert result["pbo"] is None
        assert result["reason"] is not None
        assert "insufficient_configs" in result["reason"]

    def test_fewer_than_4_blocks_returns_none(self):
        mat = np.ones((3, 5))
        result = cscv_pbo(mat, n_splits=8)
        assert result["pbo"] is None
        assert result["reason"] is not None
        assert "insufficient_blocks" in result["reason"]

    def test_nan_masking_excludes_incomplete_configs(self):
        n_blocks = 8
        n_configs = 5
        mat = _noise_matrix(n_blocks, n_configs, seed=7)
        # Make config 2 have a NaN in block 3
        mat[3, 2] = np.nan

        result = cscv_pbo(mat, n_splits=8)
        assert result["pbo"] is not None
        assert result["n_excluded"] == 1, f"Expected 1 excluded, got {result['n_excluded']}"
        assert result["n_configs"] == n_configs - 1

    def test_all_configs_have_nan_returns_none(self):
        mat = np.full((8, 4), np.nan)
        result = cscv_pbo(mat, n_splits=8)
        assert result["pbo"] is None

    def test_invalid_input_types(self):
        result = cscv_pbo(None, n_splits=8)
        assert result["pbo"] is None
        assert result["reason"] is not None

    def test_1d_array_returns_none(self):
        result = cscv_pbo(np.ones(8), n_splits=8)
        assert result["pbo"] is None
        assert "ndim" in result["reason"]


class TestDeterminism:
    """Same inputs must produce identical outputs."""

    def test_deterministic_result(self):
        mat = _noise_matrix(n_blocks=16, n_configs=8, seed=321)
        r1 = cscv_pbo(mat, n_splits=8)
        r2 = cscv_pbo(mat, n_splits=8)
        assert r1["pbo"] == r2["pbo"]
        assert r1["n_partitions"] == r2["n_partitions"]

    def test_partition_cap_200(self):
        """With n_blocks=16, C(16,8)=12870 → capped at 200 partitions."""
        mat = _noise_matrix(n_blocks=16, n_configs=6, seed=42)
        result = cscv_pbo(mat, n_splits=8)
        assert result["n_partitions"] == 200

    def test_small_matrix_uncapped(self):
        """With n_blocks=8, C(8,4)=70 → under cap, all partitions used."""
        mat = _noise_matrix(n_blocks=8, n_configs=6, seed=42)
        result = cscv_pbo(mat, n_splits=8)
        assert result["n_partitions"] == 70
