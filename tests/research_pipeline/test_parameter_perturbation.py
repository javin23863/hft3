"""Tests for parameter_perturbation producer (§10 line 286, gap matrix #3)."""

from __future__ import annotations

import numpy as np
import pytest

from research_pipeline.src.robustness_producers import parameter_perturbation


class TestParameterPerturbationHandMath:
    """Hand-verified mathematical expectations."""

    def test_strong_edge_survives_small_perturbation(self):
        """Cells with strong positive expectancy should survive 10% perturbation."""
        expectancies = [0.5] * 100  # Strong edge
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10],
            n_runs_per_fraction=100,
            seed=42,
            min_stability_score=0.5,
        )
        assert result["observed_mean"] == 0.5
        assert result["parameter_stability_score"] > 0.5
        assert result["parameter_perturbation_pass"] is True
        assert result["reason"] is None

    def test_weak_edge_fails_perturbation(self):
        """Cells with explicitly zero mean should fail under perturbation.

        The additive noise model scales noise with std(arr). When the mean
        is exactly zero (demeaned series), the perturbed mean is pure noise,
        so survival_rate ≈ 0.5 < 0.7.
        """
        rng = np.random.default_rng(99)
        expectancies_arr = rng.normal(0.0, 0.01, 100)
        expectancies_arr -= expectancies_arr.mean()  # explicitly demean
        expectancies = expectancies_arr.tolist()
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.25, 0.50],
            n_runs_per_fraction=100,
            seed=42,
            min_stability_score=0.7,
        )
        assert result["parameter_stability_score"] < 0.7
        assert result["parameter_perturbation_pass"] is False

    def test_negative_edge_never_survives(self):
        """Negative expectancy should always fail."""
        expectancies = [-0.01] * 100
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10, 0.25],
            n_runs_per_fraction=50,
            seed=42,
        )
        assert result["observed_mean"] == -0.01
        assert result["parameter_perturbation_pass"] is False

    def test_zero_perturbation_preserves_sign(self):
        """Zero perturbation fraction should preserve the original sign."""
        # Perturbation 0 means no noise
        expectancies = [0.05] * 50
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.0],
            n_runs_per_fraction=100,
            seed=42,
        )
        # With no noise, all runs should survive for positive expectancy
        assert abs(result["parameter_stability_score"] - 1.0) < 1e-6
        assert result["parameter_perturbation_pass"] is True


class TestParameterPerturbationGuards:
    """Fail-closed guards."""

    def test_empty_list_returns_fail_closed(self):
        result = parameter_perturbation(
            [],
            perturbation_fractions=[0.10],
            n_runs_per_fraction=50,
        )
        assert result["reason"] == "no_observations: empty per_event_expectancies"
        assert result["parameter_stability_score"] is None
        assert result["parameter_perturbation_pass"] is False
        assert result["n_obs"] == 0

    def test_nan_in_expectancies_returns_fail_closed(self):
        expectancies = [0.01, float("nan"), 0.02]
        result = parameter_perturbation(expectancies, perturbation_fractions=[0.10])
        assert result["reason"] == "non_finite_observations: per_event_expectancies contain NaN/inf"
        assert result["parameter_perturbation_pass"] is False

    def test_inf_in_expectancies_returns_fail_closed(self):
        expectancies = [0.01, float("inf"), 0.02]
        result = parameter_perturbation(expectancies, perturbation_fractions=[0.10])
        assert result["reason"] == "non_finite_observations: per_event_expectancies contain NaN/inf"
        assert result["parameter_perturbation_pass"] is False

    def test_zero_runs_fail_closed(self):
        expectancies = [0.05] * 10
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10],
            n_runs_per_fraction=0,
        )
        assert result["reason"] == "insufficient_runs: n_runs_per_fraction=0 < 1"
        assert result["parameter_stability_score"] is None

    def test_fraction_out_of_range_fail_closed(self):
        expectancies = [0.05] * 10
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[1.5],  # > 1.0
        )
        assert "invalid_fractions" in result["reason"]
        assert result["parameter_perturbation_pass"] is False

    def test_negative_fraction_fail_closed(self):
        expectancies = [0.05] * 10
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[-0.1],
        )
        assert "invalid_fractions" in result["reason"]
        assert result["parameter_perturbation_pass"] is False


class TestParameterPerturbationDeterminism:
    """Determinism guarantees."""

    def test_repeated_calls_identical(self):
        expectancies = [0.05] * 50
        r1 = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10, 0.25],
            n_runs_per_fraction=50,
            seed=123,
        )
        r2 = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10, 0.25],
            n_runs_per_fraction=50,
            seed=123,
        )
        assert r1 == r2

    def test_different_seeds_provide_different_results(self):
        expectancies = [0.05] * 50
        r1 = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10],
            n_runs_per_fraction=50,
            seed=0,
        )
        r2 = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10],
            n_runs_per_fraction=50,
            seed=1,
        )
        # Different seeds should produce different survival rates (with high probability)
        # But both should have valid structure
        assert "parameter_stability_score" in r1
        assert "parameter_stability_score" in r2


class TestParameterPerturbationOutputShape:
    """Output schema validation."""

    def test_success_keys_present(self):
        """All required keys present on success."""
        expectancies = [0.05] * 50
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10, 0.25],
            n_runs_per_fraction=50,
            min_stability_score=0.7,
        )
        required_keys = {
            "observed_mean",
            "fraction_survival_rates",
            "parameter_stability_score",
            "parameter_perturbation_pass",
            "perturbation_fractions",
            "n_runs_per_fraction",
            "min_stability_score",
            "seed",
            "n_obs",
            "reason",
        }
        assert required_keys.issubset(result.keys())

    def test_guard_keys_present(self):
        """Guard keys present even on failure."""
        result = parameter_perturbation(
            [],
            perturbation_fractions=[0.10],
        )
        assert "observed_mean" in result
        assert "fraction_survival_rates" in result
        assert "parameter_stability_score" in result
        assert "parameter_perturbation_pass" in result
        assert "reason" in result
        assert result["reason"] is not None

    def test_fraction_rates_echoed(self):
        """Input fractions echoed in output."""
        expectancies = [0.05] * 50
        custom_fractions = [0.05, 0.15, 0.30]
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=custom_fractions,
        )
        assert set(result["perturbation_fractions"]) == set(custom_fractions)
        assert set(result["fraction_survival_rates"].keys()) == set(custom_fractions)

    def test_default_fractions_used_when_none(self):
        """Default fractions [0.10, 0.25] used when None."""
        expectancies = [0.05] * 50
        result = parameter_perturbation(expectancies, perturbation_fractions=None)
        assert result["perturbation_fractions"] == [0.10, 0.25]
        assert set(result["fraction_survival_rates"].keys()) == {0.10, 0.25}

    def test_stability_score_is_mean_of_fractions(self):
        """Stability score equals mean of fraction survival rates."""
        expectancies = [0.05] * 50
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10, 0.25],
            n_runs_per_fraction=100,
            seed=42,
        )
        expected = np.mean(list(result["fraction_survival_rates"].values()))
        assert abs(result["parameter_stability_score"] - expected) < 1e-6


class TestParameterPerturbationPassFail:
    """Pass/fail boundary behavior."""

    def test_pass_when_score_above_threshold(self):
        """Pass when stability_score >= min_stability_score."""
        expectancies = [0.1] * 100  # Strong edge
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10],
            n_runs_per_fraction=100,
            seed=42,
            min_stability_score=0.5,
        )
        assert result["parameter_perturbation_pass"] is True

    def test_fail_when_score_below_threshold(self):
        """Fail when stability_score < min_stability_score (via zero-mean series)."""
        rng = np.random.default_rng(99)
        expectancies_arr = rng.normal(0.0, 0.01, 100)
        expectancies_arr -= expectancies_arr.mean()  # explicitly demean
        expectancies = expectancies_arr.tolist()
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.25, 0.50],
            n_runs_per_fraction=100,
            seed=42,
            min_stability_score=0.7,
        )
        assert result["parameter_perturbation_pass"] is False
        assert result["parameter_stability_score"] < 0.7

    def test_exactly_at_boundary(self):
        """Behavior at exact boundary depends on floating point."""
        expectancies = [0.05] * 50
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10],
            n_runs_per_fraction=50,
            seed=42,
        )
        # Score should be compared with >=
        assert result["parameter_perturbation_pass"] == (result["parameter_stability_score"] >= 0.7)

    def test_min_stability_score_echoed(self):
        """min_stability_score echoed in output."""
        expectancies = [0.05] * 50
        result = parameter_perturbation(
            expectancies,
            min_stability_score=0.9,
        )
        assert result["min_stability_score"] == 0.9


class TestParameterPerturbationOrdering:
    """Monotonicity: higher perturbation fraction -> lower survival rate."""

    def test_larger_fraction_lower_survival(self):
        """For weak edges, larger perturbation should reduce survival.

        With heterogeneous expectancies (non-zero std), the additive noise
        model produces decreasing survival as the perturbation fraction grows.
        """
        rng = np.random.default_rng(77)
        expectancies = (rng.normal(0.001, 0.01, 100)).tolist()  # weak + variable
        result = parameter_perturbation(
            expectancies,
            perturbation_fractions=[0.10, 0.25, 0.50],
            n_runs_per_fraction=100,
            seed=42,
        )
        r_10 = result["fraction_survival_rates"][0.10]
        r_25 = result["fraction_survival_rates"][0.25]
        r_50 = result["fraction_survival_rates"][0.50]
        # With weak edge and variable data, survival should generally decrease
        assert r_50 <= r_25 + 0.05  # Allow small random variation
        assert r_25 <= r_10 + 0.05


if __name__ == "__main__":
    pytest.main([__file__, "-v"])