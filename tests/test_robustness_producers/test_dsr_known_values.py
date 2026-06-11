"""Tests for deflated_sharpe_for_cell.

Covers:
  - monotone: higher observed Sharpe → higher dsr_cdf
  - multiplicity: n_trials↑ → dsr_cdf↓ (stiffer penalty for more trials)
  - exact match: output matches crypto_lane.src.ml.walk_forward_runner.deflated_sharpe_cdf
    on a fixed vector (both import paths, compare)
  - guard: n < 3 returns None fields with reason
  - dsr_pass flag and one_sided_p semantics
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

from research_pipeline.src.robustness_producers import (
    deflated_sharpe_for_cell,
    _deflated_sharpe_cdf_inline,
)


def _make_expecs(n: int, mean: float, std: float, seed: int = 42) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(rng.normal(mean, std, n))


class TestDSRMonotone:
    """dsr_cdf must be monotone in observed Sharpe (all else equal)."""

    def test_monotone_in_sharpe(self):
        # Keep n_trials and n_obs fixed; vary the mean → varies Sharpe
        n = 30
        n_trials = 50
        std = 1.0
        means = [0.0, 0.5, 1.0, 2.0, 4.0]
        prev_cdf = -1.0
        for mu in means:
            expecs = _make_expecs(n, mu, std)
            result = deflated_sharpe_for_cell(expecs, n_trials=n_trials)
            assert result["dsr_cdf"] is not None
            cdf = result["dsr_cdf"]
            assert cdf >= prev_cdf - 1e-9, (
                f"dsr_cdf not monotone: mean={mu} → cdf={cdf:.6f} < prev={prev_cdf:.6f}"
            )
            prev_cdf = cdf


class TestDSRNTrials:
    """n_trials↑ → dsr_cdf↓ (higher multiplicity = stiffer penalty)."""

    def test_more_trials_lower_cdf(self):
        expecs = _make_expecs(40, mean=2.0, std=1.0, seed=7)
        n_trials_list = [1, 5, 20, 100, 500]
        prev_cdf = 2.0  # start above 1 so first entry always passes
        for n_trials in n_trials_list:
            result = deflated_sharpe_for_cell(expecs, n_trials=n_trials)
            cdf = result["dsr_cdf"]
            assert cdf is not None
            assert cdf <= prev_cdf + 1e-9, (
                f"dsr_cdf increased with n_trials: n_trials={n_trials} → {cdf:.6f} > prev={prev_cdf:.6f}"
            )
            prev_cdf = cdf


class TestDSRExactMatch:
    """Output of deflated_sharpe_for_cell must match crypto_lane implementation exactly."""

    def test_matches_crypto_lane_implementation(self):
        # Import both: the canonical crypto_lane version and our producer
        try:
            from crypto_lane.src.ml.walk_forward_runner import deflated_sharpe_cdf as crypto_dsr
        except Exception as exc:
            pytest.skip(f"crypto_lane not importable: {exc}")

        expecs = _make_expecs(25, mean=1.5, std=0.8, seed=99)
        n_trials = 30

        result = deflated_sharpe_for_cell(expecs, n_trials=n_trials)
        assert result["dsr_cdf"] is not None

        arr = np.array(expecs)
        sharpe = float(np.mean(arr) / np.std(arr, ddof=1))
        skew = float(np.mean((arr - np.mean(arr)) ** 3) / np.std(arr, ddof=1) ** 3)
        kurt = float(np.mean((arr - np.mean(arr)) ** 4) / np.std(arr, ddof=1) ** 4)

        expected_cdf = crypto_dsr(
            observed_sharpe=sharpe,
            n_trials=n_trials,
            n_obs=len(expecs),
            skew=skew,
            kurt=kurt,
        )

        # dsr_cdf is rounded to 8 decimal places before storage; allow 5e-9 tolerance
        assert abs(result["dsr_cdf"] - expected_cdf) < 5e-9, (
            f"dsr_cdf mismatch: producer={result['dsr_cdf']:.12f} vs crypto={expected_cdf:.12f}"
        )

    def test_inline_copy_matches_crypto_lane(self):
        """The inline fallback copy must be numerically identical to crypto_lane."""
        try:
            from crypto_lane.src.ml.walk_forward_runner import deflated_sharpe_cdf as crypto_dsr
        except Exception as exc:
            pytest.skip(f"crypto_lane not importable: {exc}")

        for obs_sharpe, n_trials, n_obs, skew, kurt in [
            (1.5, 20, 50, 0.0, 3.0),
            (0.5, 100, 30, -0.3, 4.0),
            (3.0, 5, 200, 0.1, 3.5),
            (0.0, 10, 10, 0.0, 3.0),
        ]:
            expected = crypto_dsr(obs_sharpe, n_trials, n_obs, skew, kurt)
            got = _deflated_sharpe_cdf_inline(obs_sharpe, n_trials, n_obs, skew, kurt)
            assert abs(expected - got) < 1e-12, (
                f"inline copy mismatch at sharpe={obs_sharpe}: expected={expected} got={got}"
            )


class TestDSRGuards:
    """Guard conditions: n < 3, zero variance, n_trials edge cases."""

    def test_n_less_than_3_returns_none_fields(self):
        for n in (0, 1, 2):
            expecs = [0.5] * n
            result = deflated_sharpe_for_cell(expecs, n_trials=10)
            assert result["sharpe"] is None
            assert result["dsr_cdf"] is None
            assert result["dsr_pass"] is None
            assert result["reason"] is not None
            assert "insufficient_events" in result["reason"]

    def test_zero_variance_returns_none_fields(self):
        expecs = [1.0, 1.0, 1.0, 1.0, 1.0]
        result = deflated_sharpe_for_cell(expecs, n_trials=10)
        assert result["sharpe"] is None
        assert "zero_variance" in result["reason"]

    def test_dsr_pass_flag_semantics(self):
        # High signal → should pass
        expecs = _make_expecs(50, mean=5.0, std=0.5, seed=3)
        r_pass = deflated_sharpe_for_cell(expecs, n_trials=1)
        assert r_pass["dsr_cdf"] is not None
        assert r_pass["dsr_pass"] == (r_pass["dsr_cdf"] >= 0.95)

        # Low signal → should fail
        expecs_low = _make_expecs(10, mean=0.01, std=2.0, seed=4)
        r_fail = deflated_sharpe_for_cell(expecs_low, n_trials=500)
        if r_fail["dsr_cdf"] is not None:
            assert r_fail["dsr_pass"] == (r_fail["dsr_cdf"] >= 0.95)

    def test_one_sided_p_is_complement(self):
        expecs = _make_expecs(20, mean=1.0, std=0.5, seed=5)
        result = deflated_sharpe_for_cell(expecs, n_trials=10)
        if result["dsr_cdf"] is not None:
            assert abs(result["one_sided_p"] - (1.0 - result["dsr_cdf"])) < 1e-9
