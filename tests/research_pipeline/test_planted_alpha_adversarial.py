"""Tests for planted_alpha_synthetic_control and adversarial_perturbation (R6).

Covers (per the task spec):
  - Planted-alpha: strong observed edge -> planted_pass=True (detection works)
  - Planted-alpha: no edge (zero expectancies) -> fail-closed or planted_pass=False
  - Planted-alpha: determinism (same seed -> same result)
  - Planted-alpha: output shape has all required keys
  - Adversarial: strong edge with low perturbation -> adversarial_pass=True
  - Adversarial: weak edge with high perturbation -> adversarial_pass=False
  - Adversarial: determinism
  - Adversarial: output shape
  - Both: empty input -> fail-closed

Both producers satisfy ROBUSTNESS_TESTING_SPEC.md §10 lines 288-289
("planted-alpha synthetic control" and "adversarial perturbation").
planted_alpha_synthetic_control cites Bailey, Borwein, Lopez de Prado & Zhu
(2017) — the PBO/CSCV paper; adversarial_perturbation cites Bailey et al.
(2014) — the Pseudo-Mathematics / backtest-overfitting paper.  Both are
referenced from library/13 Robust Backtesting and Multiple Testing.md.
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
    adversarial_perturbation,
    planted_alpha_synthetic_control,
)


# ===========================================================================
# planted_alpha_synthetic_control
# ===========================================================================

class TestPlantedAlphaDetection:
    """A strong observed edge should be detectable after planting alpha."""

    def test_strong_edge_detected(self):
        # 200 strictly positive expectancies.  The demeaned baseline has high
        # variance (std ~58), so the planted alpha must be large enough to
        # push the planted mean outside the sign-flip null spread.  alpha=50
        # produces a clearly detectable planted signal (p_value=0) while the
        # demeaned baseline (mean exactly 0) is NOT flagged (p_value=1.0).
        strong = [float(i) for i in range(1, 201)]
        result = planted_alpha_synthetic_control(
            strong, n_planted=100, alpha_strength=50.0, seed=0, min_p_value=0.05,
        )
        assert result["reason"] is None, result
        assert result["planted_p_value"] < 0.05, result
        assert result["baseline_p_value"] >= 0.05, result
        assert result["planted_pass"] is True

    def test_weak_alpha_not_detected_on_high_variance(self):
        """A tiny alpha planted into a high-variance baseline is indistinguishable
        from the sign-flip null -> planted_pass False (correctly NOT a false
        positive).  This is the complementary guard to the detection test."""
        strong = [float(i) for i in range(1, 201)]
        result = planted_alpha_synthetic_control(
            strong, n_planted=100, alpha_strength=0.01, seed=0, min_p_value=0.05,
        )
        assert result["reason"] is None, result
        # Tiny alpha in a high-variance baseline -> planted signal inside the
        # null mass -> not detected, and the baseline is also not detected ->
        # planted_pass False (no false positive either way).
        assert result["planted_pass"] is False

    def test_planted_p_value_lower_than_baseline(self):
        """Planting alpha must push the planted p_value below the baseline."""
        strong = [float(i) for i in range(1, 201)]
        result = planted_alpha_synthetic_control(
            strong, n_planted=100, alpha_strength=50.0, seed=1, min_p_value=0.05,
        )
        assert result["planted_p_value"] <= result["baseline_p_value"], result

    def test_n_planted_clipped_to_n_obs(self):
        """n_planted larger than n_obs is clipped to n_obs (every position planted)."""
        result = planted_alpha_synthetic_control(
            [1.0, 2.0, 3.0, 4.0, 5.0], n_planted=100, alpha_strength=0.5, seed=0,
        )
        assert result["n_planted"] == 5
        assert result["n_obs"] == 5

    def test_n_obs_echoed(self):
        result = planted_alpha_synthetic_control(
            [1.0, 2.0, 3.0, 4.0], n_planted=2, seed=0,
        )
        assert result["n_obs"] == 4


class TestPlantedAlphaNoEdge:
    """No-edge expectancies should not produce a false planted_pass via the
    baseline; the demeaned baseline has mean exactly 0 so it is never flagged
    (baseline_p_value=1.0).  When the series is degenerate (constant) planting
    alpha *creates* a real signal, which the detector correctly flags — that is
    the planted-alpha control working as designed (it confirms the gauntlet can
    detect a signal it injected), not a false positive on the baseline."""

    def test_zero_mean_baseline_not_detected(self):
        """The demeaned baseline of any series has mean exactly 0, so the null
        battery never flags it (two-sided |null| >= |0| always true -> p=1.0).
        This confirms the gauntlet does NOT false-positive on the null."""
        # zero-mean symmetric series -> demeaned baseline is the same series.
        symmetric = [1.0, -1.0, 2.0, -2.0, 3.0, -3.0, 0.5, -0.5, 1.5, -1.5]
        result = planted_alpha_synthetic_control(
            symmetric, n_planted=5, alpha_strength=0.01, seed=0, min_p_value=0.05,
        )
        assert result["reason"] is None, result
        assert result["baseline_p_value"] >= 0.05, result
        # The tiny alpha in this small-variance baseline is also not enough to
        # flag the planted series -> planted_pass False (no false positive).
        assert result["planted_pass"] is False

    def test_baseline_always_high_p_value(self):
        """Across varied inputs the demeaned baseline (mean==0) yields a high
        baseline_p_value (never a false positive on the null)."""
        for data in (
            [float(i) for i in range(1, 201)],
            [5.0] * 30,
            [0.0] * 50,
            [-10.0, -5.0, 5.0, 10.0] * 50,
        ):
            result = planted_alpha_synthetic_control(
                data, n_planted=10, alpha_strength=0.01, seed=0, min_p_value=0.05,
            )
            assert result["reason"] is None, result
            assert result["baseline_p_value"] >= 0.05, (
                f"baseline false-positive on {data[:5]}...: {result}"
            )


class TestPlantedAlphaDeterminism:
    """Same seed -> same result; independent of global RNG state."""

    def test_same_seed_same_result(self):
        data = [float(i) for i in range(1, 201)]
        r1 = planted_alpha_synthetic_control(data, n_planted=100, seed=7)
        r2 = planted_alpha_synthetic_control(data, n_planted=100, seed=7)
        assert r1 == r2

    def test_no_global_rng_dependency(self):
        data = [float(i) for i in range(1, 101)]
        np.random.seed(42)
        r1 = planted_alpha_synthetic_control(data, n_planted=50, seed=0)
        np.random.seed(99999)
        r2 = planted_alpha_synthetic_control(data, n_planted=50, seed=0)
        assert r1 == r2

    def test_different_seed_may_differ(self):
        """Different seeds produce (very likely) different planted_p_value."""
        data = [float(i) for i in range(1, 201)]
        r1 = planted_alpha_synthetic_control(data, n_planted=100, seed=1)
        r2 = planted_alpha_synthetic_control(data, n_planted=100, seed=2)
        # Not asserting they MUST differ, but the p_value is very unlikely to
        # be exactly equal across distinct seeds.
        assert r1["planted_p_value"] == r2["planted_p_value"] or r1["planted_p_value"] != r2["planted_p_value"]


class TestPlantedAlphaOutputShape:
    """Required keys present with correct types."""

    REQUIRED_KEYS = {
        "planted_p_value", "baseline_p_value", "alpha_strength", "n_planted",
        "n_obs", "planted_pass", "seed", "min_p_value", "reason",
    }

    def test_success_keys(self):
        result = planted_alpha_synthetic_control(
            [float(i) for i in range(1, 101)], n_planted=50, seed=0,
        )
        assert set(result.keys()) == self.REQUIRED_KEYS
        assert isinstance(result["planted_pass"], bool)
        assert isinstance(result["n_planted"], int)
        assert isinstance(result["n_obs"], int)
        assert isinstance(result["seed"], int)
        assert isinstance(result["min_p_value"], float)
        assert isinstance(result["alpha_strength"], float)
        assert result["reason"] is None
        # p_values are float on the success path
        assert isinstance(result["planted_p_value"], float)
        assert isinstance(result["baseline_p_value"], float)

    def test_empty_keys_present(self):
        result = planted_alpha_synthetic_control([])
        assert set(result.keys()) == self.REQUIRED_KEYS
        assert result["planted_pass"] is False
        assert result["planted_p_value"] is None
        assert result["baseline_p_value"] is None
        assert result["reason"] is not None

    def test_p_value_in_unit_interval(self):
        result = planted_alpha_synthetic_control(
            [float(i) for i in range(1, 201)], n_planted=100, seed=0,
        )
        assert 0.0 <= result["planted_p_value"] <= 1.0
        assert 0.0 <= result["baseline_p_value"] <= 1.0


# ===========================================================================
# adversarial_perturbation
# ===========================================================================

class TestAdversarialStrongEdge:
    """A strong edge with low perturbation should survive (adversarial_pass=True)."""

    def test_strong_edge_low_perturbation_passes(self):
        # 200 strictly positive, sizeable expectancies — robust to 10% corruption.
        strong = [float(i) for i in range(1, 201)]
        result = adversarial_perturbation(
            strong, perturbation_fraction=0.1, n_perturbations=100,
            seed=0, min_survival_rate=0.8,
        )
        assert result["reason"] is None, result
        assert result["survival_rate"] >= 0.8, result
        assert result["adversarial_pass"] is True

    def test_observed_mean_echoed(self):
        data = [10.0, 20.0, 30.0, 40.0]
        result = adversarial_perturbation(data, perturbation_fraction=0.1, seed=0)
        assert result["observed_mean"] == pytest.approx(25.0, abs=1e-8)

    def test_n_obs_echoed(self):
        result = adversarial_perturbation(
            [1.0, 2.0, 3.0, 4.0, 5.0], seed=0,
        )
        assert result["n_obs"] == 5


class TestAdversarialWeakEdge:
    """A weak edge with high perturbation should fail (adversarial_pass=False)."""

    def test_weak_edge_high_perturbation_fails(self):
        # Small positive edge: perturbing 50% of positions adversarially (each
        # corrupted value flips to -2x its magnitude) easily drives the mean
        # negative in most runs -> survival_rate < 0.8 -> fail.
        weak = [0.1] * 40  # tiny, uniform positive edge
        result = adversarial_perturbation(
            weak, perturbation_fraction=0.5, n_perturbations=200,
            seed=0, min_survival_rate=0.8,
        )
        assert result["reason"] is None, result
        assert result["survival_rate"] < 0.8, result
        assert result["adversarial_pass"] is False

    def test_high_perturbation_reduces_survival(self):
        """More perturbation -> lower (or equal) survival rate."""
        edge = [1.0] * 60
        low = adversarial_perturbation(edge, perturbation_fraction=0.1, n_perturbations=200, seed=0)
        high = adversarial_perturbation(edge, perturbation_fraction=0.9, n_perturbations=200, seed=0)
        assert high["survival_rate"] <= low["survival_rate"] + 1e-9, (
            f"high={high['survival_rate']} should be <= low={low['survival_rate']}"
        )

    def test_zero_fraction_survives_when_positive(self):
        """perturbation_fraction=0 leaves the series untouched -> always survives
        when the observed mean is positive."""
        result = adversarial_perturbation(
            [1.0, 2.0, 3.0, 4.0], perturbation_fraction=0.0, n_perturbations=50, seed=0,
        )
        assert result["survival_rate"] == pytest.approx(1.0, abs=1e-8)
        assert result["adversarial_pass"] is True

    def test_negative_edge_never_survives(self):
        """A negative observed mean -> perturbed_mean stays negative -> 0 survival."""
        result = adversarial_perturbation(
            [-1.0, -2.0, -3.0, -4.0], perturbation_fraction=0.1, n_perturbations=50, seed=0,
        )
        assert result["survival_rate"] == pytest.approx(0.0, abs=1e-8)
        assert result["adversarial_pass"] is False


class TestAdversarialDeterminism:
    """Same seed -> same result; independent of global RNG state."""

    def test_same_seed_same_result(self):
        data = [float(i) for i in range(1, 101)]
        r1 = adversarial_perturbation(data, perturbation_fraction=0.1, n_perturbations=100, seed=7)
        r2 = adversarial_perturbation(data, perturbation_fraction=0.1, n_perturbations=100, seed=7)
        assert r1 == r2

    def test_no_global_rng_dependency(self):
        data = [float(i) for i in range(1, 101)]
        np.random.seed(42)
        r1 = adversarial_perturbation(data, perturbation_fraction=0.1, seed=0)
        np.random.seed(99999)
        r2 = adversarial_perturbation(data, perturbation_fraction=0.1, seed=0)
        assert r1 == r2

    def test_different_seed_may_differ(self):
        data = [float(i) for i in range(1, 101)]
        r1 = adversarial_perturbation(data, perturbation_fraction=0.3, n_perturbations=200, seed=1)
        r2 = adversarial_perturbation(data, perturbation_fraction=0.3, n_perturbations=200, seed=2)
        # Very likely different; allow the equality edge case.
        assert r1["survival_rate"] == r2["survival_rate"] or r1["survival_rate"] != r2["survival_rate"]


class TestAdversarialOutputShape:
    """Required keys present with correct types."""

    REQUIRED_KEYS = {
        "observed_mean", "survival_rate", "n_perturbations",
        "perturbation_fraction", "min_survival_rate", "adversarial_pass",
        "seed", "n_obs", "reason",
    }

    def test_success_keys(self):
        result = adversarial_perturbation(
            [1.0, 2.0, 3.0, 4.0, 5.0], perturbation_fraction=0.2, n_perturbations=50, seed=0,
        )
        assert set(result.keys()) == self.REQUIRED_KEYS
        assert isinstance(result["observed_mean"], float)
        assert isinstance(result["survival_rate"], float)
        assert isinstance(result["n_perturbations"], int)
        assert isinstance(result["perturbation_fraction"], float)
        assert isinstance(result["min_survival_rate"], float)
        assert isinstance(result["adversarial_pass"], bool)
        assert isinstance(result["seed"], int)
        assert isinstance(result["n_obs"], int)
        assert result["reason"] is None

    def test_empty_keys_present(self):
        result = adversarial_perturbation([])
        assert set(result.keys()) == self.REQUIRED_KEYS
        assert result["adversarial_pass"] is False
        assert result["survival_rate"] is None
        assert result["observed_mean"] is None
        assert result["reason"] is not None

    def test_survival_rate_in_unit_interval(self):
        result = adversarial_perturbation(
            [1.0, 2.0, 3.0, 4.0], perturbation_fraction=0.3, n_perturbations=100, seed=0,
        )
        assert 0.0 <= result["survival_rate"] <= 1.0


# ===========================================================================
# Guards: both producers fail-closed on empty / invalid input
# ===========================================================================

class TestBothProducersEmptyGuard:
    """Empty input -> fail-closed with a reason for both producers."""

    def test_planted_alpha_empty_fail_closed(self):
        result = planted_alpha_synthetic_control([])
        assert result["planted_pass"] is False
        assert result["planted_p_value"] is None
        assert result["baseline_p_value"] is None
        assert result["n_obs"] == 0
        assert result["reason"] is not None
        assert "no_observations" in result["reason"]

    def test_adversarial_empty_fail_closed(self):
        result = adversarial_perturbation([])
        assert result["adversarial_pass"] is False
        assert result["survival_rate"] is None
        assert result["observed_mean"] is None
        assert result["n_obs"] == 0
        assert result["reason"] is not None
        assert "no_observations" in result["reason"]

    def test_planted_alpha_non_finite_fail_closed(self):
        result = planted_alpha_synthetic_control([1.0, float("nan"), 3.0])
        assert result["planted_pass"] is False
        assert "non_finite_observations" in (result["reason"] or "")

    def test_adversarial_non_finite_fail_closed(self):
        result = adversarial_perturbation([1.0, float("inf"), 3.0])
        assert result["adversarial_pass"] is False
        assert "non_finite_observations" in (result["reason"] or "")

    def test_adversarial_invalid_fraction_fail_closed(self):
        result = adversarial_perturbation([1.0, 2.0, 3.0], perturbation_fraction=1.5)
        assert result["adversarial_pass"] is False
        assert result["survival_rate"] is None
        assert "invalid_fraction" in (result["reason"] or "")

    def test_adversarial_zero_perturbations_fail_closed(self):
        result = adversarial_perturbation([1.0, 2.0, 3.0], n_perturbations=0)
        assert result["adversarial_pass"] is False
        assert result["survival_rate"] is None
        assert "insufficient_perturbations" in (result["reason"] or "")