"""Tests for slippage_stress_for_cell and latency_stress_for_cell (R6).

Covers (per the task spec):
  - pass / fail scenarios for both producers
  - missing-data fail-closed (decomposition fields absent → stress_data_available=False)
  - determinism (repeated calls return identical results; no randomness)
  - output shape (required keys present with correct types)

Both producers satisfy ROBUSTNESS_TESTING_SPEC.md §10 lines 283-285
("fee multiplier stress" / "slippage multiplier stress" / "latency stress").
slippage_stress_for_cell handles §10 line 284; latency_stress_for_cell
handles §10 line 285.  The fee multiplier check (§10 line 283) is already
covered by fee_stress_for_cell (see tests/test_robustness_producers/).
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
    latency_stress_for_cell,
    slippage_stress_for_cell,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_records(
    n_events: int,
    net_expectancy_per_event: float,
    fee_per_rt: float,
    tick_value: float,
    n_trades_per_event: int = 5,
) -> tuple[list[float], list[int], list[float], list[float]]:
    """Return (expectancies, n_trades, fee_per_rt_list, tick_value_list)."""
    expectancies  = [net_expectancy_per_event] * n_events
    n_trades_list = [n_trades_per_event] * n_events
    fee_list      = [fee_per_rt] * n_events
    tv_list       = [tick_value] * n_events
    return expectancies, n_trades_list, fee_list, tv_list


# ===========================================================================
# slippage_stress_for_cell
# ===========================================================================

class TestSlippageHandMath:
    """Slippage stress values are analytically derivable; verify against formula."""

    def test_multiplier_scenarios_match_formula(self):
        """slip_xN: net_exp_at_m = gross - fee - tick_value * (m - 1.0).

        With base_slip_ticks = 1.0, slip_x1 (m=1.0) recovers the base net
        expectancy; slip_x2 adds one extra tick; slip_x3 adds two.
        """
        fee_per_rt = 1.0
        tick_value = 1.25
        net_exp    = 3.0  # positive edge

        expecs, n_trades, fee_list, tv_list = _make_records(5, net_exp, fee_per_rt, tick_value)
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)

        assert result["stress_data_available"] is True
        gross = net_exp + fee_per_rt  # 4.0

        # slip_x1_5: gross - fee - tv*(1.5-1.0) = 4.0 - 1.0 - 1.25*0.5 = 2.375
        assert result["slip_x1_5_expectancy"] == pytest.approx(gross - fee_per_rt - tick_value * 0.5, abs=1e-6)
        # slip_x2:   gross - fee - tv*(2.0-1.0) = 4.0 - 1.0 - 1.25*1.0 = 1.75
        assert result["slip_x2_expectancy"] == pytest.approx(gross - fee_per_rt - tick_value * 1.0, abs=1e-6)
        # slip_x3:   gross - fee - tv*(3.0-1.0) = 4.0 - 1.0 - 1.25*2.0 = 0.5
        assert result["slip_x3_expectancy"] == pytest.approx(gross - fee_per_rt - tick_value * 2.0, abs=1e-6)

    def test_adder_scenarios_match_formula(self):
        """slip_p5t / slip_1t: gross - fee - tick_value * adder_ticks."""
        fee_per_rt = 1.0
        tick_value = 1.25
        net_exp    = 3.0

        expecs, n_trades, fee_list, tv_list = _make_records(4, net_exp, fee_per_rt, tick_value)
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)

        gross = net_exp + fee_per_rt  # 4.0
        assert result["slip_p5t_expectancy"] == pytest.approx(gross - fee_per_rt - tick_value * 0.5, abs=1e-6)
        assert result["slip_1t_expectancy"] == pytest.approx(gross - fee_per_rt - tick_value * 1.0, abs=1e-6)

    def test_multiple_events_mean_is_arithmetic(self):
        """Varying per-event expectancies → stress values are means."""
        expecs   = [2.0, 4.0]
        n_trades = [3, 7]
        fee_list = [1.0, 1.0]
        tv_list  = [1.25, 1.25]
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["stress_data_available"] is True
        # gross = [3.0, 5.0]; slip_x2 = [3-1-1.25, 5-1-1.25] = [0.75, 2.75]; mean = 1.75
        assert result["slip_x2_expectancy"] == pytest.approx(1.75, abs=1e-6)

    def test_different_tick_values_per_event(self):
        """Per-event tick_value may differ (different products)."""
        expecs   = [2.0, 3.0]
        n_trades = [5, 5]
        fee_list = [1.0, 1.0]
        tv_list  = [1.25, 12.50]  # MES vs ES
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        # gross = [3.0, 4.0]; slip_x2 = [3-1-1.25, 4-1-12.5] = [0.75, -9.5]; mean = -4.375
        assert result["slip_x2_expectancy"] == pytest.approx(-4.375, abs=1e-6)


class TestSlippagePassFail:
    """stress_pass (= slip_x2_pass) flips correctly at the boundary."""

    def test_passes_when_slip_x2_positive(self):
        """gross - fee - tv > 0 → slip_x2_expectancy > 0 → stress_pass True."""
        # net=3.0, fee=1.0, tv=1.25 → slip_x2 = 4.0-1.0-1.25 = 1.75 > 0
        expecs, n_trades, fee_list, tv_list = _make_records(6, 3.0, 1.0, 1.25)
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["slip_x2_pass"] is True
        assert result["stress_pass"] is True

    def test_fails_when_slip_x2_negative(self):
        """slip_x2_expectancy < 0 → stress_pass False."""
        # net=0.5, fee=1.0, tv=12.5 → slip_x2 = 1.5-1.0-12.5 = -12.0 < 0
        expecs, n_trades, fee_list, tv_list = _make_records(6, 0.5, 1.0, 12.5)
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["slip_x2_pass"] is False
        assert result["stress_pass"] is False

    def test_exactly_at_boundary_slip_x2_zero(self):
        """slip_x2_expectancy == 0 → NOT > 0 → stress_pass False."""
        # Want gross - fee - tv = 0 → net = tv - fee + ... gross=fee+tv → net = tv
        # net=1.25, fee=1.0, tv=1.25 → gross=2.25; slip_x2 = 2.25-1.0-1.25 = 0.0
        expecs, n_trades, fee_list, tv_list = _make_records(4, 1.25, 1.0, 1.25)
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["slip_x2_expectancy"] == pytest.approx(0.0, abs=1e-8)
        assert result["slip_x2_pass"] is False
        assert result["stress_pass"] is False

    def test_stress_pass_is_alias_for_slip_x2_pass(self):
        """stress_pass must always equal slip_x2_pass."""
        for net_exp in [-1.0, 0.0, 0.5, 1.5, 5.0]:
            expecs, n_trades, fee_list, tv_list = _make_records(4, net_exp, 1.0, 1.25)
            result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
            if result["stress_data_available"]:
                assert result["stress_pass"] == result["slip_x2_pass"], (
                    f"stress_pass != slip_x2_pass for net_exp={net_exp}"
                )


class TestSlippageOrdering:
    """Higher slippage stress = lower (or equal) expectancy."""

    def test_slip_multipliers_decrease_expectancy(self):
        expecs, n_trades, fee_list, tv_list = _make_records(8, 2.0, 1.0, 1.25)
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["stress_data_available"] is True
        x1_5 = result["slip_x1_5_expectancy"]
        x2   = result["slip_x2_expectancy"]
        x3   = result["slip_x3_expectancy"]
        assert x3 <= x2 - 1e-10, f"slip_x3={x3} should be < slip_x2={x2}"
        assert x2 <= x1_5 - 1e-10, f"slip_x2={x2} should be < slip_x1_5={x1_5}"

    def test_slip_adders_decrease_expectancy(self):
        expecs, n_trades, fee_list, tv_list = _make_records(8, 2.0, 1.0, 1.25)
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        s_p5 = result["slip_p5t_expectancy"]
        s_1t = result["slip_1t_expectancy"]
        assert s_1t <= s_p5 - 1e-10, f"slip_1t={s_1t} should be < slip_p5t={s_p5}"


class TestSlippageGuards:
    """Fail-closed when decomposition fields missing."""

    def test_empty_list_returns_no_events(self):
        result = slippage_stress_for_cell([], [], [], [])
        assert result["stress_data_available"] is False
        assert result["n_events"] == 0
        assert result["slip_x2_expectancy"] is None
        assert result["stress_pass"] is None

    def test_all_zero_fee_returns_data_unavailable(self):
        """Old records with no fee decomposition → stress_data_available=False."""
        expecs   = [1.0, 2.0, 3.0]
        n_trades = [5, 5, 5]
        fee_list = [0.0, 0.0, 0.0]
        tv_list  = [1.25, 1.25, 1.25]
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["stress_data_available"] is False
        assert "decomposition_unavailable" in (result["reason"] or "")
        assert result["slip_x2_pass"] is None
        assert result["stress_pass"] is None
        # base_mean still echoed from expectancies (not None — there are events)
        assert result["base_mean_expectancy"] == pytest.approx(2.0, abs=1e-6)

    def test_n_events_echoed(self):
        expecs, n_trades, fee_list, tv_list = _make_records(7, 1.0, 1.0, 1.25)
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["n_events"] == 7

    def test_reason_none_on_success(self):
        expecs, n_trades, fee_list, tv_list = _make_records(4, 2.0, 1.0, 1.25)
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["stress_data_available"] is True
        assert result["reason"] is None


class TestSlippageDeterminism:
    """No randomness: repeated calls produce identical results."""

    def test_repeated_calls_identical(self):
        expecs, n_trades, fee_list, tv_list = _make_records(10, 2.5, 1.0, 1.25)
        r1 = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        r2 = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert r1 == r2

    def test_no_random_state_dependency(self):
        """Result does not depend on global numpy RNG state."""
        expecs, n_trades, fee_list, tv_list = _make_records(6, 2.0, 1.0, 1.25)
        np.random.seed(42)
        r1 = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        np.random.seed(12345)
        r2 = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert r1 == r2


class TestSlippageOutputShape:
    """Required keys present with correct types."""

    REQUIRED_KEYS = {
        "stress_data_available", "slip_x1_5_expectancy", "slip_x2_expectancy",
        "slip_x2_pass", "slip_x3_expectancy", "slip_p5t_expectancy",
        "slip_1t_expectancy", "stress_pass", "n_events",
        "base_mean_expectancy", "reason",
    }

    def test_success_keys(self):
        expecs, n_trades, fee_list, tv_list = _make_records(4, 2.0, 1.0, 1.25)
        result = slippage_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert set(result.keys()) == self.REQUIRED_KEYS
        assert isinstance(result["stress_data_available"], bool)
        assert isinstance(result["slip_x2_pass"], bool)
        assert isinstance(result["stress_pass"], bool)
        assert isinstance(result["n_events"], int)
        assert result["reason"] is None
        for k in ("slip_x1_5_expectancy", "slip_x2_expectancy", "slip_x3_expectancy",
                  "slip_p5t_expectancy", "slip_1t_expectancy", "base_mean_expectancy"):
            assert isinstance(result[k], float), f"{k} should be float, got {type(result[k])}"

    def test_guard_keys_present(self):
        """Guard (fail-closed) returns same key set with None values."""
        result = slippage_stress_for_cell([1.0, 2.0], [5, 5], [0.0, 0.0], [1.25, 1.25])
        assert set(result.keys()) == self.REQUIRED_KEYS
        assert result["stress_data_available"] is False
        for k in ("slip_x1_5_expectancy", "slip_x2_expectancy", "slip_x3_expectancy",
                  "slip_p5t_expectancy", "slip_1t_expectancy", "slip_x2_pass", "stress_pass"):
            assert result[k] is None

    def test_empty_keys_present(self):
        result = slippage_stress_for_cell([], [], [], [])
        assert set(result.keys()) == self.REQUIRED_KEYS


# ===========================================================================
# latency_stress_for_cell
# ===========================================================================

class TestLatencyHandMath:
    """Latency stress values are analytically derivable; verify against formula."""

    def test_baseline_equals_net_when_baseline_latency_zero(self):
        """With latency_ms_baseline=0, baseline_expectancy == base net mean."""
        fee_per_rt = 1.0
        net_exp    = 3.0
        expecs, n_trades, fee_list, tv_list = _make_records(5, net_exp, fee_per_rt, 12.5)
        result = latency_stress_for_cell(
            expecs, n_trades, fee_list, tv_list,
            latency_ms_baseline=0.0,
            latency_ms_stress=1.0,
            tick_value_usd=12.5,
            ticks_per_ms=0.001,
        )
        assert result["stress_data_available"] is True
        # baseline_cost_per_rt = 0 * 0.001 * 12.5 = 0 → baseline = gross - fee = net
        assert result["baseline_expectancy"] == pytest.approx(net_exp, abs=1e-6)

    def test_latency_cost_per_rt_formula(self):
        """latency_cost_per_rt = (stress - baseline) * ticks_per_ms * tick_value_usd."""
        expecs, n_trades, fee_list, tv_list = _make_records(4, 3.0, 1.0, 12.5)
        result = latency_stress_for_cell(
            expecs, n_trades, fee_list, tv_list,
            latency_ms_baseline=0.0,
            latency_ms_stress=1.0,
            tick_value_usd=12.5,
            ticks_per_ms=0.001,
        )
        # delta = 1.0 ms; cost = 1.0 * 0.001 * 12.5 = 0.0125
        assert result["latency_cost_per_rt"] == pytest.approx(0.0125, abs=1e-8)

    def test_stress_expectancy_subtracts_latency_cost(self):
        """stress_expectancy = gross - fee - latency_cost_per_rt."""
        fee_per_rt = 1.0
        net_exp    = 3.0
        tick_value_usd = 12.5
        ticks_per_ms = 0.001
        expecs, n_trades, fee_list, tv_list = _make_records(4, net_exp, fee_per_rt, 12.5)
        result = latency_stress_for_cell(
            expecs, n_trades, fee_list, tv_list,
            latency_ms_baseline=0.0,
            latency_ms_stress=1.0,
            tick_value_usd=tick_value_usd,
            ticks_per_ms=ticks_per_ms,
        )
        gross = net_exp + fee_per_rt  # 4.0
        cost = 1.0 * ticks_per_ms * tick_value_usd  # 0.0125
        assert result["stress_expectancy"] == pytest.approx(gross - fee_per_rt - cost, abs=1e-6)

    def test_stress_scales_with_latency_ms(self):
        """Doubling latency_ms_stress doubles the latency cost."""
        expecs, n_trades, fee_list, tv_list = _make_records(4, 3.0, 1.0, 12.5)
        r1 = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list, latency_ms_stress=1.0)
        r2 = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list, latency_ms_stress=2.0)
        assert r2["latency_cost_per_rt"] == pytest.approx(2.0 * r1["latency_cost_per_rt"], abs=1e-8)

    def test_nonzero_baseline(self):
        """baseline_expectancy subtracts baseline latency cost; stress adds incremental."""
        fee_per_rt = 1.0
        net_exp = 3.0
        tick_value_usd = 12.5
        ticks_per_ms = 0.001
        expecs, n_trades, fee_list, tv_list = _make_records(4, net_exp, fee_per_rt, 12.5)
        result = latency_stress_for_cell(
            expecs, n_trades, fee_list, tv_list,
            latency_ms_baseline=0.5,
            latency_ms_stress=2.0,
            tick_value_usd=tick_value_usd,
            ticks_per_ms=ticks_per_ms,
        )
        gross = net_exp + fee_per_rt  # 4.0
        baseline_cost = 0.5 * ticks_per_ms * tick_value_usd  # 0.00625
        stress_cost = 2.0 * ticks_per_ms * tick_value_usd   # 0.025
        assert result["baseline_expectancy"] == pytest.approx(gross - fee_per_rt - baseline_cost, abs=1e-6)
        assert result["stress_expectancy"] == pytest.approx(gross - fee_per_rt - stress_cost, abs=1e-6)


class TestLatencyPassFail:
    """stress_pass (= stress_expectancy > 0) flips correctly."""

    def test_passes_when_stress_expectancy_positive(self):
        # net=3.0, fee=1.0 → gross=4.0; stress_cost=0.0125 → 2.9875 > 0
        expecs, n_trades, fee_list, tv_list = _make_records(6, 3.0, 1.0, 12.5)
        result = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list, latency_ms_stress=1.0)
        assert result["stress_pass"] is True

    def test_fails_when_latency_cost_exceeds_edge(self):
        """Large latency → stress_expectancy < 0 → stress_pass False."""
        # net=0.5, fee=1.0 → gross=1.5; with latency_ms_stress=1000 → cost=12.5 → -11.0
        expecs, n_trades, fee_list, tv_list = _make_records(6, 0.5, 1.0, 12.5)
        result = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list, latency_ms_stress=1000.0)
        assert result["stress_expectancy"] < 0.0
        assert result["stress_pass"] is False

    def test_exactly_at_boundary_zero(self):
        """stress_expectancy == 0 → NOT > 0 → stress_pass False."""
        # gross - fee = net; want net == latency_cost
        # net=0.0125, fee=1.0 → gross=1.0125; cost = 1.0*0.001*12.5 = 0.0125 → 0.0
        expecs, n_trades, fee_list, tv_list = _make_records(4, 0.0125, 1.0, 12.5)
        result = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list, latency_ms_stress=1.0)
        assert result["stress_expectancy"] == pytest.approx(0.0, abs=1e-8)
        assert result["stress_pass"] is False

    def test_zero_latency_stress_recovers_base(self):
        """latency_ms_stress == latency_ms_baseline → stress == baseline."""
        expecs, n_trades, fee_list, tv_list = _make_records(4, 2.0, 1.0, 12.5)
        result = latency_stress_for_cell(
            expecs, n_trades, fee_list, tv_list,
            latency_ms_baseline=1.0,
            latency_ms_stress=1.0,
        )
        # delta = 0 → latency_cost_per_rt = 0; stress uses baseline cost (1.0ms)
        assert result["latency_cost_per_rt"] == pytest.approx(0.0, abs=1e-8)
        # baseline uses 1.0ms baseline cost; stress uses 1.0ms baseline cost too
        assert result["stress_expectancy"] == pytest.approx(result["baseline_expectancy"], abs=1e-8)


class TestLatencyGuards:
    """Fail-closed when decomposition fields missing."""

    def test_empty_list_returns_no_events(self):
        result = latency_stress_for_cell([], [], [], [])
        assert result["stress_data_available"] is False
        assert result["n_events"] == 0
        assert result["stress_expectancy"] is None
        assert result["stress_pass"] is None

    def test_all_zero_fee_returns_data_unavailable(self):
        """Old records with no fee decomposition → stress_data_available=False."""
        expecs   = [1.0, 2.0, 3.0]
        n_trades = [5, 5, 5]
        fee_list = [0.0, 0.0, 0.0]
        tv_list  = [12.5, 12.5, 12.5]
        result = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["stress_data_available"] is False
        assert "decomposition_unavailable" in (result["reason"] or "")
        assert result["stress_pass"] is None
        assert result["stress_expectancy"] is None
        assert result["base_mean_expectancy"] == pytest.approx(2.0, abs=1e-6)

    def test_n_events_echoed(self):
        expecs, n_trades, fee_list, tv_list = _make_records(7, 1.0, 1.0, 12.5)
        result = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["n_events"] == 7

    def test_reason_none_on_success(self):
        expecs, n_trades, fee_list, tv_list = _make_records(4, 2.0, 1.0, 12.5)
        result = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["stress_data_available"] is True
        assert result["reason"] is None


class TestLatencyDeterminism:
    """No randomness: repeated calls produce identical results."""

    def test_repeated_calls_identical(self):
        expecs, n_trades, fee_list, tv_list = _make_records(10, 2.5, 1.0, 12.5)
        r1 = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list, latency_ms_stress=2.0)
        r2 = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list, latency_ms_stress=2.0)
        assert r1 == r2

    def test_no_random_state_dependency(self):
        expecs, n_trades, fee_list, tv_list = _make_records(6, 2.0, 1.0, 12.5)
        np.random.seed(42)
        r1 = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        np.random.seed(99999)
        r2 = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert r1 == r2


class TestLatencyOutputShape:
    """Required keys present with correct types."""

    REQUIRED_KEYS = {
        "stress_data_available", "baseline_expectancy", "stress_expectancy",
        "latency_cost_per_rt", "stress_pass", "n_events",
        "base_mean_expectancy", "reason",
    }

    def test_success_keys(self):
        expecs, n_trades, fee_list, tv_list = _make_records(4, 2.0, 1.0, 12.5)
        result = latency_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert set(result.keys()) == self.REQUIRED_KEYS
        assert isinstance(result["stress_data_available"], bool)
        assert isinstance(result["stress_pass"], bool)
        assert isinstance(result["n_events"], int)
        assert result["reason"] is None
        for k in ("baseline_expectancy", "stress_expectancy",
                  "latency_cost_per_rt", "base_mean_expectancy"):
            assert isinstance(result[k], float), f"{k} should be float, got {type(result[k])}"

    def test_guard_keys_present(self):
        result = latency_stress_for_cell([1.0, 2.0], [5, 5], [0.0, 0.0], [12.5, 12.5])
        assert set(result.keys()) == self.REQUIRED_KEYS
        assert result["stress_data_available"] is False
        for k in ("baseline_expectancy", "stress_expectancy",
                  "latency_cost_per_rt", "stress_pass"):
            assert result[k] is None

    def test_empty_keys_present(self):
        result = latency_stress_for_cell([], [], [], [])
        assert set(result.keys()) == self.REQUIRED_KEYS