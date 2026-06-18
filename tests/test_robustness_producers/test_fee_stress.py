"""Tests for fee_stress_for_cell (R6).

Covers:
  - analytic recompute matches hand math on synthetic per-event records
  - stress_pass (fee_x2_pass) flips correctly: True when gross >> fees, False when thin
  - stress_data_available=False when fee_per_rt all-zero (old records)
  - guard: n=0 returns None fields
  - all five stress scenarios produce correct ordering
  - tick-value slippage penalty scales correctly
  - stress_pass is alias for fee_x2_pass
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

from research_pipeline.src.robustness_producers import fee_stress_for_cell


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


# ---------------------------------------------------------------------------
# 1. Hand-math verification
# ---------------------------------------------------------------------------

class TestHandMath:
    """All stress values are analytically derivable; verify against direct formula."""

    def test_base_case_recovery(self):
        """gross = net + fee_per_rt; at x1 multiplier, net == base."""
        fee_per_rt = 1.52    # MES non_member: 2 × (0.25 + 0.25 + 0.02) = 1.04; use 1.52 for clarity
        tick_value = 1.25    # MES
        net_exp    = 3.0     # positive edge

        expecs, n_trades, fee_list, tv_list = _make_records(5, net_exp, fee_per_rt, tick_value)
        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)

        assert result["stress_data_available"] is True
        assert result["base_mean_expectancy"] == pytest.approx(net_exp, abs=1e-6)

        # gross = net + fee = 3.0 + 1.52 = 4.52
        gross = net_exp + fee_per_rt
        # fee_x1_5 = gross - fee * 1.5 = 4.52 - 2.28 = 2.24
        expected_x1_5 = gross - fee_per_rt * 1.5
        assert result["fee_x1_5_expectancy"] == pytest.approx(expected_x1_5, abs=1e-6)

        # fee_x2 = gross - fee * 2 = 4.52 - 3.04 = 1.48
        expected_x2 = gross - fee_per_rt * 2.0
        assert result["fee_x2_expectancy"] == pytest.approx(expected_x2, abs=1e-6)

        # fee_x3 = gross - fee * 3 = 4.52 - 4.56 = -0.04
        expected_x3 = gross - fee_per_rt * 3.0
        assert result["fee_x3_expectancy"] == pytest.approx(expected_x3, abs=1e-6)

        # slip_p5t = gross - fee - tick_value * 0.5 = 4.52 - 1.52 - 0.625 = 2.375
        expected_slip_p5t = gross - fee_per_rt - tick_value * 0.5
        assert result["slip_p5t_expectancy"] == pytest.approx(expected_slip_p5t, abs=1e-6)

        # slip_1t = gross - fee - tick_value * 1.0 = 4.52 - 1.52 - 1.25 = 1.75
        expected_slip_1t = gross - fee_per_rt - tick_value * 1.0
        assert result["slip_1t_expectancy"] == pytest.approx(expected_slip_1t, abs=1e-6)

    def test_multiple_events_mean_is_arithmetic(self):
        """With varying per-event expectancies, stress values are means."""
        fee_per_rt = 1.0
        tick_value = 1.25
        # Two events with different net expectancies
        expecs   = [2.0, 4.0]
        n_trades = [3, 7]
        fee_list = [fee_per_rt, fee_per_rt]
        tv_list  = [tick_value, tick_value]

        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["stress_data_available"] is True

        # gross = [3.0, 5.0]; fee_x2 = [3.0 - 2.0, 5.0 - 2.0] = [1.0, 3.0]; mean = 2.0
        assert result["fee_x2_expectancy"] == pytest.approx(2.0, abs=1e-6)

    def test_different_fee_rates_per_event(self):
        """Per-event fee_per_rt may differ (different products / tiers)."""
        expecs   = [2.0, 3.0]
        n_trades = [5, 5]
        fee_list = [1.0, 2.0]   # first event cheaper
        tv_list  = [1.25, 1.25]

        # gross = [3.0, 5.0]; fee_x2 = [3.0 - 2.0, 5.0 - 4.0] = [1.0, 1.0]; mean = 1.0
        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["fee_x2_expectancy"] == pytest.approx(1.0, abs=1e-6)

    def test_slip_p5t_uses_tick_value(self):
        """slip_p5t penalty = tick_value * 0.5 per round trip."""
        expecs   = [5.0]
        n_trades = [10]
        fee_list = [1.0]
        tv_list  = [12.50]  # ES tick value

        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        # gross = 6.0; slip_p5t = 6.0 - 1.0 - 12.5*0.5 = 6.0 - 1.0 - 6.25 = -1.25
        assert result["slip_p5t_expectancy"] == pytest.approx(-1.25, abs=1e-6)
        # slip_1t = 6.0 - 1.0 - 12.5*1.0 = -7.5
        assert result["slip_1t_expectancy"] == pytest.approx(-7.5, abs=1e-6)


# ---------------------------------------------------------------------------
# 2. stress_pass (fee_x2_pass) flip semantics
# ---------------------------------------------------------------------------

class TestStressPassFlip:
    """stress_pass flips between True and False at the boundary."""

    def test_passes_when_gross_exceeds_2x_fees(self):
        """gross > 2 × fee → fee_x2_expectancy > 0 → stress_pass True."""
        fee_per_rt = 1.0
        net_exp    = 1.5   # gross = 2.5 > 2 * 1.0 = 2.0 → net_at_x2 = 0.5 > 0
        expecs, n_trades, fee_list, tv_list = _make_records(6, net_exp, fee_per_rt, 1.25)
        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["fee_x2_pass"]  is True
        assert result["stress_pass"]  is True

    def test_fails_when_gross_below_2x_fees(self):
        """gross < 2 × fee → fee_x2_expectancy ≤ 0 → stress_pass False."""
        fee_per_rt = 1.0
        net_exp    = 0.5   # gross = 1.5 < 2 * 1.0 → net_at_x2 = 1.5 - 2.0 = -0.5 < 0
        expecs, n_trades, fee_list, tv_list = _make_records(6, net_exp, fee_per_rt, 1.25)
        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["fee_x2_pass"]  is False
        assert result["stress_pass"]  is False

    def test_exactly_at_boundary_fee_x2_zero(self):
        """gross == 2 × fee → fee_x2_expectancy == 0 → NOT > 0 → stress_pass False."""
        fee_per_rt = 1.0
        net_exp    = 1.0   # gross = 2.0 = 2 * fee → net_at_x2 = 0 (not > 0)
        expecs, n_trades, fee_list, tv_list = _make_records(4, net_exp, fee_per_rt, 1.25)
        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["fee_x2_expectancy"] == pytest.approx(0.0, abs=1e-8)
        assert result["fee_x2_pass"] is False

    def test_stress_pass_is_alias_for_fee_x2_pass(self):
        """stress_pass must always equal fee_x2_pass."""
        for net_exp in [-1.0, 0.0, 0.5, 1.5, 5.0]:
            expecs, n_trades, fee_list, tv_list = _make_records(4, net_exp, 1.0, 1.25)
            result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
            if result["stress_data_available"]:
                assert result["stress_pass"] == result["fee_x2_pass"], (
                    f"stress_pass != fee_x2_pass for net_exp={net_exp}"
                )

    def test_flip_with_mixed_event_expectancies(self):
        """When events are mixed, mean determines the flag."""
        fee_per_rt = 1.0
        # gross = [0.5, 3.0]; fee_x2 = [-1.5, 1.0]; mean = -0.25 → fail
        expecs   = [-0.5, 2.0]
        n_trades = [3, 3]
        fee_list = [1.0, 1.0]
        tv_list  = [1.25, 1.25]
        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["fee_x2_expectancy"] == pytest.approx(-0.25, abs=1e-6)
        assert result["fee_x2_pass"] is False


# ---------------------------------------------------------------------------
# 3. Ordering: fee_x3 ≤ fee_x2 ≤ fee_x1_5 ≤ base ≤ slip_p5t ≤ slip_1t
# ---------------------------------------------------------------------------

class TestStressOrdering:
    """Higher stress = lower (or equal) expectancy."""

    def test_fee_multipliers_decrease_expectancy(self):
        expecs, n_trades, fee_list, tv_list = _make_records(8, 2.0, 1.0, 1.25)
        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["stress_data_available"] is True

        base  = result["base_mean_expectancy"]
        x1_5  = result["fee_x1_5_expectancy"]
        x2    = result["fee_x2_expectancy"]
        x3    = result["fee_x3_expectancy"]
        s_p5  = result["slip_p5t_expectancy"]
        s_1t  = result["slip_1t_expectancy"]

        # Fee multipliers lower expectancy monotonically
        assert x3 <= x2 - 1e-10, f"fee_x3={x3} should be < fee_x2={x2}"
        assert x2 <= x1_5 - 1e-10
        assert x1_5 <= base - 1e-10  # x1_5 costs more than base x1

        # Slippage at base fees but added tick cost
        assert s_p5 <= base, f"slip_p5t={s_p5} should be <= base={base}"
        assert s_1t  <= s_p5


# ---------------------------------------------------------------------------
# 4. Guard conditions
# ---------------------------------------------------------------------------

class TestGuards:
    def test_empty_list_returns_no_events(self):
        result = fee_stress_for_cell([], [], [], [])
        assert result["stress_data_available"] is False
        assert result["n_events"] == 0
        assert result["fee_x2_expectancy"] is None
        assert result["stress_pass"] is None

    def test_all_zero_fee_returns_data_unavailable(self):
        """Old records with no fee decomposition → stress_data_available=False."""
        expecs   = [1.0, 2.0, 3.0]
        n_trades = [5, 5, 5]
        fee_list = [0.0, 0.0, 0.0]   # old records lack this field
        tv_list  = [1.25, 1.25, 1.25]
        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["stress_data_available"] is False
        assert "fee_decomposition_unavailable" in (result["reason"] or "")
        assert result["fee_x2_pass"] is None
        assert result["stress_pass"] is None

    def test_n_events_echoed(self):
        expecs, n_trades, fee_list, tv_list = _make_records(7, 1.0, 1.0, 1.25)
        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["n_events"] == 7

    def test_reason_none_on_success(self):
        expecs, n_trades, fee_list, tv_list = _make_records(4, 2.0, 1.0, 1.25)
        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["stress_data_available"] is True
        assert result["reason"] is None

    def test_single_fee_broadcast_fails_closed(self):
        """One fee value for many events must not silently broadcast via NumPy."""
        expecs = [1.0, 2.0, 3.0]
        n_trades = [5, 5, 5]
        fee_list = [1.0]  # length 1 vs 3 expectancies
        tv_list = [1.25, 1.25, 1.25]
        result = fee_stress_for_cell(expecs, n_trades, fee_list, tv_list)
        assert result["stress_data_available"] is False
        assert "decomposition_length_mismatch" in (result["reason"] or "")
        assert "per_event_fee_per_rt" in (result["reason"] or "")
        assert result["fee_x2_pass"] is None
        assert result["stress_pass"] is None
