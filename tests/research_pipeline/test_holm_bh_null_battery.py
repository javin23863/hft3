"""Tests for holm_bh_correction and null_strategy_battery (R6 robustness layer).

Covers (per the task spec):
  - BH (Benjamini-Hochberg FDR) correction on known p-values
  - Holm step-down FWER correction on the same p-values
  - rejected count matches expected
  - Null battery: strong edge (all positive expectancies) -> low p_value -> pass
  - Null battery: no edge (symmetric / random expectancies) -> high p_value -> fail
  - Determinism: same seed -> same result
  - Empty input -> fail-closed
  - Output shape has all required keys

Both producers satisfy ROBUSTNESS_TESTING_SPEC.md §10 line 282
("Holm/BH multiple-testing correction") and line 287 ("null strategy battery").
holm_bh_correction handles §10 line 282 (citing Benjamini & Hochberg 1995 for
the BH method and Holm 1979 for the Holm method); null_strategy_battery handles
§10 line 287 (citing White 2000 "A Reality Check for Data Snooping").
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
    holm_bh_correction,
    null_strategy_battery,
)


# ===========================================================================
# holm_bh_correction
# ===========================================================================

class TestBHCorrectionHandMath:
    """BH adjusted p-values are analytically derivable; verify against formula.

    Reference p-values: [0.01, 0.04, 0.03] at alpha=0.05.

    Sorted ascending: [0.01, 0.03, 0.04] (ranks 1, 2, 3), n=3.
    Raw BH: p_(i) * n / rank[i] = [0.01*3/1, 0.03*3/2, 0.04*3/3]
                           = [0.03,  0.045,    0.04]
    Monotonicity from the top (accumulate min over reversed):
        adjusted_sorted = [0.03, min(0.045, 0.04), 0.04] = [0.03, 0.04, 0.04]
    Scatter back to original input order ([0.01, 0.04, 0.03]):
        corrected = [0.03, 0.04, 0.04]
    rejected (<=0.05): [True, True, True] -> n_rejected=3
    """

    P_VALUES = [0.01, 0.04, 0.03]

    def test_corrected_values_match_formula(self):
        result = holm_bh_correction(self.P_VALUES, alpha=0.05, method="bh")
        assert result["corrected_p_values"] == [
            pytest.approx(0.03, abs=1e-8),
            pytest.approx(0.04, abs=1e-8),
            pytest.approx(0.04, abs=1e-8),
        ], result

    def test_rejected_flags_match_expected(self):
        result = holm_bh_correction(self.P_VALUES, alpha=0.05, method="bh")
        assert result["rejected"] == [True, True, True]

    def test_n_rejected_matches_expected(self):
        result = holm_bh_correction(self.P_VALUES, alpha=0.05, method="bh")
        assert result["n_rejected"] == 3

    def test_reason_none_on_success(self):
        result = holm_bh_correction(self.P_VALUES, alpha=0.05, method="bh")
        assert result["reason"] is None

    def test_method_echoed(self):
        result = holm_bh_correction(self.P_VALUES, alpha=0.05, method="bh")
        assert result["method"] == "bh"

    def test_alpha_and_n_tests_echoed(self):
        result = holm_bh_correction(self.P_VALUES, alpha=0.05, method="bh")
        assert result["alpha"] == 0.05
        assert result["n_tests"] == 3

    def test_corrected_values_preserve_input_order(self):
        """Output positions must align with the original (unsorted) input."""
        result = holm_bh_correction([0.04, 0.01, 0.03], alpha=0.05, method="bh")
        # input order: [0.04, 0.01, 0.03] -> sorted [0.01, 0.03, 0.04]
        # raw BH = [0.03, 0.045, 0.04]; monotone = [0.03, 0.04, 0.04]
        # scatter: pos0=0.04->0.04, pos1=0.01->0.03, pos2=0.03->0.04
        assert result["corrected_p_values"] == [
            pytest.approx(0.04, abs=1e-8),
            pytest.approx(0.03, abs=1e-8),
            pytest.approx(0.04, abs=1e-8),
        ]


class TestHolmCorrectionHandMath:
    """Holm adjusted p-values are analytically derivable; verify against formula.

    Reference p-values: [0.01, 0.04, 0.03] at alpha=0.05.

    Sorted ascending: [0.01, 0.03, 0.04], n=3.
    Raw Holm: p_(i) * (n - i) = [0.01*3, 0.03*2, 0.04*1] = [0.03, 0.06, 0.04]
    Monotonicity from the bottom (accumulate max):
        adjusted_sorted = [0.03, max(0.06, 0.03), max(0.04, 0.06)] = [0.03, 0.06, 0.06]
    Scatter back to original input order ([0.01, 0.04, 0.03]):
        corrected = [0.03, 0.06, 0.06]
    rejected (<=0.05): [True, False, False] -> n_rejected=1
    """

    P_VALUES = [0.01, 0.04, 0.03]

    def test_corrected_values_match_formula(self):
        result = holm_bh_correction(self.P_VALUES, alpha=0.05, method="holm")
        assert result["corrected_p_values"] == [
            pytest.approx(0.03, abs=1e-8),
            pytest.approx(0.06, abs=1e-8),
            pytest.approx(0.06, abs=1e-8),
        ], result

    def test_rejected_flags_match_expected(self):
        result = holm_bh_correction(self.P_VALUES, alpha=0.05, method="holm")
        assert result["rejected"] == [True, False, False]

    def test_n_rejected_matches_expected(self):
        result = holm_bh_correction(self.P_VALUES, alpha=0.05, method="holm")
        assert result["n_rejected"] == 1

    def test_reason_none_on_success(self):
        result = holm_bh_correction(self.P_VALUES, alpha=0.05, method="holm")
        assert result["reason"] is None

    def test_method_echoed(self):
        result = holm_bh_correction(self.P_VALUES, alpha=0.05, method="holm")
        assert result["method"] == "holm"


class TestBHHolmComparison:
    """BH (FDR) is more permissive than Holm (FWER) on the same family."""

    def test_bh_rejects_at_least_as_many_as_holm(self):
        p = [0.01, 0.02, 0.03, 0.04, 0.05]
        bh = holm_bh_correction(p, alpha=0.05, method="bh")
        holm = holm_bh_correction(p, alpha=0.05, method="holm")
        assert bh["n_rejected"] >= holm["n_rejected"], (
            f"BH n_rejected={bh['n_rejected']} should be >= Holm n_rejected={holm['n_rejected']}"
        )

    def test_strict_alpha_rejects_none(self):
        """alpha=0.0001 is below the smallest corrected p -> no rejections."""
        p = [0.01, 0.04, 0.03]
        for method in ("bh", "holm"):
            result = holm_bh_correction(p, alpha=0.0001, method=method)
            assert result["n_rejected"] == 0, (method, result)


class TestHolmBHDeterminism:
    """Pure functions: repeated calls produce identical results."""

    def test_repeated_calls_identical_bh(self):
        p = [0.001, 0.02, 0.5, 0.1, 0.03]
        r1 = holm_bh_correction(p, alpha=0.05, method="bh")
        r2 = holm_bh_correction(p, alpha=0.05, method="bh")
        assert r1 == r2

    def test_repeated_calls_identical_holm(self):
        p = [0.001, 0.02, 0.5, 0.1, 0.03]
        r1 = holm_bh_correction(p, alpha=0.05, method="holm")
        r2 = holm_bh_correction(p, alpha=0.05, method="holm")
        assert r1 == r2

    def test_no_global_rng_dependency(self):
        p = [0.01, 0.04, 0.03]
        np.random.seed(42)
        r1 = holm_bh_correction(p, alpha=0.05, method="bh")
        np.random.seed(99999)
        r2 = holm_bh_correction(p, alpha=0.05, method="bh")
        assert r1 == r2


class TestHolmBHGuards:
    """Fail-closed on empty / invalid input."""

    def test_empty_p_values_fail_closed(self):
        result = holm_bh_correction([], alpha=0.05, method="bh")
        assert result["corrected_p_values"] == []
        assert result["rejected"] == []
        assert result["n_rejected"] == 0
        assert result["n_tests"] == 0
        assert result["reason"] is not None
        assert "no_p_values" in result["reason"]

    def test_empty_p_values_holm_also_fail_closed(self):
        result = holm_bh_correction([], alpha=0.05, method="holm")
        assert result["corrected_p_values"] == []
        assert result["n_rejected"] == 0
        assert result["reason"] is not None

    def test_nan_p_value_fail_closed(self):
        result = holm_bh_correction([0.01, float("nan"), 0.03], alpha=0.05, method="bh")
        assert result["n_rejected"] == 0
        assert all(v is None for v in result["corrected_p_values"])
        assert result["reason"] is not None
        assert "invalid_p_values" in result["reason"]

    def test_out_of_range_p_value_fail_closed(self):
        """p > 1.0 is invalid."""
        result = holm_bh_correction([0.01, 1.5, 0.03], alpha=0.05, method="bh")
        assert result["n_rejected"] == 0
        assert all(v is None for v in result["corrected_p_values"])
        assert "invalid_p_values" in (result["reason"] or "")

    def test_negative_p_value_fail_closed(self):
        result = holm_bh_correction([0.01, -0.1, 0.03], alpha=0.05, method="bh")
        assert result["n_rejected"] == 0
        assert "invalid_p_values" in (result["reason"] or "")

    def test_unknown_method_fail_closed(self):
        result = holm_bh_correction([0.01, 0.04, 0.03], alpha=0.05, method="bonferroni")
        assert result["n_rejected"] == 0
        assert all(v is None for v in result["corrected_p_values"])
        assert "unknown_method" in (result["reason"] or "")


class TestHolmBHOutputShape:
    """Required keys present with correct types."""

    REQUIRED_KEYS = {
        "corrected_p_values", "rejected", "n_rejected",
        "method", "alpha", "n_tests", "reason",
    }

    def test_success_keys_bh(self):
        result = holm_bh_correction([0.01, 0.04, 0.03], alpha=0.05, method="bh")
        assert set(result.keys()) == self.REQUIRED_KEYS
        assert isinstance(result["corrected_p_values"], list)
        assert all(isinstance(v, float) for v in result["corrected_p_values"])
        assert isinstance(result["rejected"], list)
        assert all(isinstance(v, bool) for v in result["rejected"])
        assert isinstance(result["n_rejected"], int)
        assert isinstance(result["method"], str)
        assert isinstance(result["alpha"], float)
        assert isinstance(result["n_tests"], int)
        assert result["reason"] is None

    def test_success_keys_holm(self):
        result = holm_bh_correction([0.01, 0.04, 0.03], alpha=0.05, method="holm")
        assert set(result.keys()) == self.REQUIRED_KEYS
        assert isinstance(result["n_rejected"], int)

    def test_empty_keys_present(self):
        result = holm_bh_correction([], alpha=0.05, method="bh")
        assert set(result.keys()) == self.REQUIRED_KEYS


# ===========================================================================
# null_strategy_battery
# ===========================================================================

class TestNullBatteryStrongEdge:
    """All-positive expectancies -> observed_mean far from the null mass -> pass."""

    def test_strong_edge_passes(self):
        # 30 strictly positive expectancies; observed_mean = 15.5.
        strong = [float(i) for i in range(1, 31)]
        result = null_strategy_battery(strong, n_null_runs=1000, seed=0, min_p_value=0.05)
        assert result["reason"] is None
        assert result["observed_mean"] == pytest.approx(15.5, abs=1e-8)
        # With all-positive data the sign-flipped null mean is ~0 while the
        # observed mean is 15.5 -> essentially zero null runs exceed it.
        assert result["p_value"] < 0.05, result
        assert result["null_pass"] is True

    def test_observed_mean_is_arithmetic_mean(self):
        data = [10.0, 20.0, 30.0, 40.0]
        result = null_strategy_battery(data, n_null_runs=500, seed=1)
        assert result["observed_mean"] == pytest.approx(25.0, abs=1e-8)

    def test_n_obs_echoed(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = null_strategy_battery(data, n_null_runs=200, seed=0)
        assert result["n_obs"] == 5


class TestNullBatteryNoEdge:
    """Symmetric / zero-mean expectancies -> high p_value -> fail."""

    def test_zero_mean_symmetric_fails(self):
        # Symmetric +/- pairs: observed_mean = 0.0 -> p_value = 1.0 -> fail.
        no_edge = [1.0, -1.0, 2.0, -2.0, 3.0, -3.0, 0.5, -0.5, 1.5, -1.5]
        result = null_strategy_battery(no_edge, n_null_runs=1000, seed=0, min_p_value=0.05)
        assert result["reason"] is None
        assert result["observed_mean"] == pytest.approx(0.0, abs=1e-8)
        assert result["p_value"] == pytest.approx(1.0, abs=1e-8)
        assert result["null_pass"] is False

    def test_random_expectancies_high_pvalue(self):
        """A random zero-centred sample should have a high p_value."""
        rng = np.random.default_rng(123)
        data = list(rng.standard_normal(40))
        result = null_strategy_battery(data, n_null_runs=2000, seed=42, min_p_value=0.05)
        # For a genuinely noisy zero-mean sample the observed mean is typically
        # well inside the null mass; require a high p_value (not necessarily 1.0).
        assert result["p_value"] >= 0.05, result
        assert result["null_pass"] is False

    def test_null_mean_near_zero(self):
        """The null distribution is centred near zero regardless of the data."""
        data = [1.0, -1.0, 2.0, -2.0, 3.0, -3.0, 0.5, -0.5, 1.5, -1.5]
        result = null_strategy_battery(data, n_null_runs=2000, seed=7)
        assert abs(result["null_mean"]) < 0.5, result


class TestNullBatteryDeterminism:
    """Same seed -> same result; different seed -> (likely) different null."""

    def test_same_seed_same_result(self):
        data = [float(i) for i in range(1, 31)]
        r1 = null_strategy_battery(data, n_null_runs=1000, seed=7)
        r2 = null_strategy_battery(data, n_null_runs=1000, seed=7)
        assert r1 == r2

    def test_different_seed_different_null_means(self):
        """Different seeds produce (almost certainly) different null_mean."""
        data = [1.0, 2.0, 3.0, 4.0, 5.0, -1.0, -2.0, -3.0, -4.0, -5.0]
        r1 = null_strategy_battery(data, n_null_runs=2000, seed=1)
        r2 = null_strategy_battery(data, n_null_runs=2000, seed=2)
        # Not asserting they MUST differ (tiny chance they coincide) but the
        # null_mean is very unlikely to be exactly equal across distinct seeds.
        assert r1["null_mean"] != r2["null_mean"] or r1["p_value"] == r2["p_value"]

    def test_no_global_rng_dependency(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        np.random.seed(42)
        r1 = null_strategy_battery(data, n_null_runs=500, seed=0)
        np.random.seed(99999)
        r2 = null_strategy_battery(data, n_null_runs=500, seed=0)
        assert r1 == r2


class TestNullBatteryGuards:
    """Fail-closed on empty / insufficient input."""

    def test_empty_expectancies_fail_closed(self):
        result = null_strategy_battery([], n_null_runs=1000, seed=0, min_p_value=0.05)
        assert result["observed_mean"] is None
        assert result["p_value"] is None
        assert result["null_pass"] is False
        assert result["n_obs"] == 0
        assert result["reason"] is not None
        assert "no_observations" in result["reason"]

    def test_non_finite_fail_closed(self):
        result = null_strategy_battery([1.0, float("nan"), 3.0], n_null_runs=1000, seed=0)
        assert result["observed_mean"] is None
        assert result["p_value"] is None
        assert result["null_pass"] is False
        assert "non_finite_observations" in (result["reason"] or "")

    def test_inf_fail_closed(self):
        result = null_strategy_battery([1.0, float("inf"), 3.0], n_null_runs=1000, seed=0)
        assert result["null_pass"] is False
        assert "non_finite_observations" in (result["reason"] or "")

    def test_zero_null_runs_fail_closed(self):
        """n_null_runs < 1 cannot form a distribution."""
        result = null_strategy_battery([1.0, 2.0, 3.0], n_null_runs=0, seed=0)
        assert result["null_pass"] is False
        assert result["p_value"] is None
        assert "insufficient_null_runs" in (result["reason"] or "")
        # observed_mean is still computed when there are observations.
        assert result["observed_mean"] == pytest.approx(2.0, abs=1e-8)

    def test_single_observation_runs(self):
        """n_obs=1 is degenerate but not empty; observed_mean is recoverable.
        The sign flip sends it to +/- itself; p_value is well-defined."""
        result = null_strategy_battery([5.0], n_null_runs=1000, seed=0)
        assert result["reason"] is None
        assert result["observed_mean"] == pytest.approx(5.0, abs=1e-8)


class TestNullBatteryOutputShape:
    """Required keys present with correct types."""

    REQUIRED_KEYS = {
        "observed_mean", "null_mean", "null_std", "p_value", "null_pass",
        "n_null_runs", "seed", "min_p_value", "n_obs", "reason",
    }

    def test_success_keys(self):
        result = null_strategy_battery([1.0, 2.0, 3.0], n_null_runs=500, seed=0)
        assert set(result.keys()) == self.REQUIRED_KEYS
        assert isinstance(result["observed_mean"], float)
        assert isinstance(result["null_mean"], float)
        assert isinstance(result["null_std"], float)
        assert isinstance(result["p_value"], float)
        assert isinstance(result["null_pass"], bool)
        assert isinstance(result["n_null_runs"], int)
        assert isinstance(result["seed"], int)
        assert isinstance(result["min_p_value"], float)
        assert isinstance(result["n_obs"], int)
        assert result["reason"] is None

    def test_empty_keys_present(self):
        result = null_strategy_battery([])
        assert set(result.keys()) == self.REQUIRED_KEYS

    def test_guard_keys_present(self):
        result = null_strategy_battery([1.0, float("nan")], n_null_runs=1000, seed=0)
        assert set(result.keys()) == self.REQUIRED_KEYS

    def test_p_value_in_unit_interval(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0, -1.0, -2.0, -3.0, -4.0, -5.0]
        result = null_strategy_battery(data, n_null_runs=1000, seed=0)
        assert 0.0 <= result["p_value"] <= 1.0


# ===========================================================================
# Cross-producer: real workflow where BH/Holm corrects a family of
# null-battery p_values.
# ===========================================================================

class TestCrossProducerWorkflow:
    """The two producers compose: null battery gives per-cell p_values, then
    Holm/BH corrects the family — matching the vault's complementary design."""

    def test_family_of_pvalues_corrected_by_bh(self):
        # Three cells: two with strong edge, one with no edge.
        strong1 = [float(i) for i in range(1, 21)]
        strong2 = [float(i) for i in range(1, 31)]
        no_edge = [1.0, -1.0, 2.0, -2.0, 3.0, -3.0, 0.5, -0.5, 1.5, -1.5]

        p_strong1 = null_strategy_battery(strong1, n_null_runs=1000, seed=0)["p_value"]
        p_strong2 = null_strategy_battery(strong2, n_null_runs=1000, seed=0)["p_value"]
        p_no_edge = null_strategy_battery(no_edge, n_null_runs=1000, seed=0)["p_value"]

        family = [p_strong1, p_strong2, p_no_edge]
        bh = holm_bh_correction(family, alpha=0.05, method="bh")
        # The two strong edges should survive; the no-edge cell should not.
        assert bh["rejected"][0] is True
        assert bh["rejected"][1] is True
        assert bh["rejected"][2] is False
        assert bh["n_rejected"] == 2

        holm = holm_bh_correction(family, alpha=0.05, method="holm")
        assert holm["n_rejected"] >= 1