"""Tests for options_risk.src.kill_switch_triggers.

Boundary semantics
------------------
All numeric triggers use strictly-greater: observed > threshold fires.
At exactly the threshold value the trigger does NOT fire.
Tests verify both sides: threshold+epsilon fires, threshold-epsilon does not.
epsilon = 1e-9 throughout.

Config fixture
--------------
The minimal_cfg() fixture returns a dict matching options_kill_switch.yaml
structure with well-defined numeric values that make boundary arithmetic easy.

Test naming convention
----------------------
test_<trigger>_fires   : above threshold
test_<trigger>_no_fire : at or below threshold
test_<trigger>_no_data : None fields return fired=False
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from options_risk.src.kill_switch_triggers import (
    OptionsSnapshot,
    TriggerResult,
    evaluate_all,
    evaluate_assignment_uncertainty,
    evaluate_expiring_position_unmanaged,
    evaluate_gamma_flip,
    evaluate_iv_dislocation,
    evaluate_margin_utilization_breach,
    evaluate_naked_short_without_hedge,
    evaluate_stale_surface,
    evaluate_stress_grid_breach,
    evaluate_vega_spike,
    load_config,
)

_EPS = 1e-9  # epsilon for boundary tests


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def minimal_cfg() -> dict:
    """Minimal valid config dict mirroring options_kill_switch.yaml structure."""
    return {
        "thresholds": {
            "vega_spike_abs_usd_per_vol_pt": 50_000.0,
            "gamma_flip_min_abs_usd": 5_000.0,
            "iv_dislocation_atm_vol_pts": 0.05,
            "iv_dislocation_underlying_move_pct": 0.01,
            "stale_surface_max_age_s": 30.0,
            "stress_grid_breach_usd": 25_000.0,
            "naked_short_max_unhedged_s": 5.0,
            "margin_utilization_hard_limit": 0.90,
            "expiring_position_cutoff_ct": "15:00",
            "assignment_uncertainty_any_mismatch": True,
        },
        "trigger_actions": {
            "vega_spike": [
                "stop_new_orders",
                "cancel_open_orders",
                "halt_options_lane",
                "log_event",
                "create_incident_report",
                "update_observer_state",
            ],
            "gamma_flip": [
                "stop_new_orders",
                "cancel_open_orders",
                "halt_options_lane",
                "log_event",
                "create_incident_report",
                "update_observer_state",
            ],
            "iv_dislocation": [
                "stop_new_orders",
                "cancel_open_orders",
                "halt_options_lane",
                "log_event",
                "create_incident_report",
                "update_observer_state",
            ],
            "stale_surface": [
                "stop_new_orders",
                "log_event",
                "update_observer_state",
            ],
            "stress_grid_breach": [
                "stop_new_orders",
                "cancel_open_orders",
                "halt_options_lane",
                "log_event",
                "create_incident_report",
                "update_observer_state",
            ],
            "naked_short_without_hedge": [
                "stop_new_orders",
                "cancel_open_orders",
                "flatten_positions_if_configured",
                "halt_options_lane",
                "log_event",
                "create_incident_report",
                "update_observer_state",
            ],
            "margin_utilization_breach": [
                "stop_new_orders",
                "cancel_open_orders",
                "flatten_positions_if_configured",
                "halt_options_lane",
                "log_event",
                "create_incident_report",
                "update_observer_state",
            ],
            "expiring_position_unmanaged": [
                "stop_new_orders",
                "cancel_open_orders",
                "halt_options_lane",
                "log_event",
                "create_incident_report",
                "update_observer_state",
            ],
            "assignment_uncertainty": [
                "stop_new_orders",
                "cancel_open_orders",
                "flatten_positions_if_configured",
                "halt_options_lane",
                "log_event",
                "create_incident_report",
                "update_observer_state",
            ],
        },
    }


# ---------------------------------------------------------------------------
# Helper assertions
# ---------------------------------------------------------------------------


def assert_fired(result: TriggerResult, trigger_name: str) -> None:
    assert result.fired, (
        f"Expected trigger '{trigger_name}' to fire; got fired=False. "
        f"message={result.message!r}"
    )
    assert result.trigger_name == trigger_name
    assert len(result.action_list) > 0, "Fired trigger must have non-empty action_list"


def assert_not_fired(result: TriggerResult, trigger_name: str) -> None:
    assert not result.fired, (
        f"Expected trigger '{trigger_name}' NOT to fire; got fired=True. "
        f"message={result.message!r}"
    )
    assert result.trigger_name == trigger_name
    assert result.action_list == [], "Non-fired trigger must have empty action_list"


# ===========================================================================
# Trigger 1: vega_spike
# ===========================================================================


class TestVegaSpike:
    cfg = minimal_cfg()
    threshold = 50_000.0

    def _snap(self, now: float, ago: float) -> OptionsSnapshot:
        return OptionsSnapshot(portfolio_vega_now=now, portfolio_vega_60s_ago=ago)

    def test_fires_above_threshold(self):
        snap = self._snap(self.threshold + _EPS, 0.0)
        result = evaluate_vega_spike(snap, self.cfg)
        assert_fired(result, "vega_spike")

    def test_no_fire_at_threshold(self):
        snap = self._snap(self.threshold, 0.0)
        result = evaluate_vega_spike(snap, self.cfg)
        assert_not_fired(result, "vega_spike")

    def test_no_fire_below_threshold(self):
        snap = self._snap(self.threshold - _EPS, 0.0)
        result = evaluate_vega_spike(snap, self.cfg)
        assert_not_fired(result, "vega_spike")

    def test_no_fire_negative_delta(self):
        # delta of -(threshold-eps) — abs value below threshold
        snap = self._snap(0.0, self.threshold - _EPS)
        result = evaluate_vega_spike(snap, self.cfg)
        assert_not_fired(result, "vega_spike")

    def test_fires_negative_delta_above_threshold(self):
        snap = self._snap(0.0, self.threshold + _EPS)
        result = evaluate_vega_spike(snap, self.cfg)
        assert_fired(result, "vega_spike")

    def test_no_data_vega_now_none(self):
        snap = OptionsSnapshot(portfolio_vega_now=None, portfolio_vega_60s_ago=1000.0)
        result = evaluate_vega_spike(snap, self.cfg)
        assert_not_fired(result, "vega_spike")
        assert result.observed is None

    def test_no_data_vega_ago_none(self):
        snap = OptionsSnapshot(portfolio_vega_now=1000.0, portfolio_vega_60s_ago=None)
        result = evaluate_vega_spike(snap, self.cfg)
        assert_not_fired(result, "vega_spike")

    def test_action_list_content(self):
        snap = self._snap(self.threshold + _EPS, 0.0)
        result = evaluate_vega_spike(snap, self.cfg)
        assert "stop_new_orders" in result.action_list
        assert "halt_options_lane" in result.action_list


# ===========================================================================
# Trigger 2: gamma_flip
# ===========================================================================


class TestGammaFlip:
    cfg = minimal_cfg()
    threshold = 5_000.0

    def test_fires_sign_flip_above_magnitude(self):
        # Was positive, now negative with |now| > threshold
        snap = OptionsSnapshot(
            portfolio_gamma=-(self.threshold + _EPS),
            portfolio_gamma_60s_ago=1000.0,
        )
        result = evaluate_gamma_flip(snap, self.cfg)
        assert_fired(result, "gamma_flip")

    def test_no_fire_same_sign(self):
        # Both negative, no flip
        snap = OptionsSnapshot(
            portfolio_gamma=-(self.threshold + _EPS),
            portfolio_gamma_60s_ago=-1000.0,
        )
        result = evaluate_gamma_flip(snap, self.cfg)
        assert_not_fired(result, "gamma_flip")

    def test_no_fire_flip_but_magnitude_at_threshold(self):
        snap = OptionsSnapshot(
            portfolio_gamma=-self.threshold,
            portfolio_gamma_60s_ago=1000.0,
        )
        result = evaluate_gamma_flip(snap, self.cfg)
        assert_not_fired(result, "gamma_flip")

    def test_no_fire_flip_but_magnitude_below_threshold(self):
        snap = OptionsSnapshot(
            portfolio_gamma=-(self.threshold - _EPS),
            portfolio_gamma_60s_ago=1000.0,
        )
        result = evaluate_gamma_flip(snap, self.cfg)
        assert_not_fired(result, "gamma_flip")

    def test_no_data_returns_not_fired(self):
        snap = OptionsSnapshot(portfolio_gamma=None, portfolio_gamma_60s_ago=1000.0)
        result = evaluate_gamma_flip(snap, self.cfg)
        assert_not_fired(result, "gamma_flip")

    def test_fires_positive_to_negative(self):
        snap = OptionsSnapshot(
            portfolio_gamma=self.threshold + _EPS,
            portfolio_gamma_60s_ago=-100.0,
        )
        result = evaluate_gamma_flip(snap, self.cfg)
        assert_fired(result, "gamma_flip")


# ===========================================================================
# Trigger 3: iv_dislocation
# ===========================================================================


class TestIvDislocation:
    cfg = minimal_cfg()
    iv_thresh = 0.05
    ul_thresh = 0.01

    def _snap(
        self, iv_now: float, iv_ago: float, ul_now: float, ul_ago: float
    ) -> OptionsSnapshot:
        return OptionsSnapshot(
            atm_iv_now=iv_now,
            atm_iv_60s_ago=iv_ago,
            underlying_price_now=ul_now,
            underlying_price_60s_ago=ul_ago,
        )

    def test_fires_large_iv_move_small_ul(self):
        # iv_move = 0.06 > 0.05; ul_move = 0% (<= 1%) -> dislocation
        snap = self._snap(0.26, 0.20, 5000.0, 5000.0)
        result = evaluate_iv_dislocation(snap, self.cfg)
        assert_fired(result, "iv_dislocation")

    def test_fires_iv_just_above_threshold(self):
        snap = self._snap(0.20 + self.iv_thresh + _EPS, 0.20, 5000.0, 5000.0)
        result = evaluate_iv_dislocation(snap, self.cfg)
        assert_fired(result, "iv_dislocation")

    def test_no_fire_iv_at_threshold(self):
        snap = self._snap(0.20 + self.iv_thresh, 0.20, 5000.0, 5000.0)
        result = evaluate_iv_dislocation(snap, self.cfg)
        assert_not_fired(result, "iv_dislocation")

    def test_no_fire_large_iv_large_ul(self):
        # iv_move = 0.06 > 0.05 BUT ul_move = 2% > 1% -> underlying explains
        snap = self._snap(0.26, 0.20, 5100.0, 5000.0)  # ul_move = 2%
        result = evaluate_iv_dislocation(snap, self.cfg)
        assert_not_fired(result, "iv_dislocation")

    def test_no_fire_small_iv_move(self):
        snap = self._snap(0.24, 0.20, 5000.0, 5000.0)  # iv_move = 0.04 < 0.05
        result = evaluate_iv_dislocation(snap, self.cfg)
        assert_not_fired(result, "iv_dislocation")

    def test_no_data_returns_not_fired(self):
        snap = OptionsSnapshot(atm_iv_now=None, atm_iv_60s_ago=0.20)
        result = evaluate_iv_dislocation(snap, self.cfg)
        assert_not_fired(result, "iv_dislocation")


# ===========================================================================
# Trigger 4: stale_surface
# ===========================================================================


class TestStaleSurface:
    cfg = minimal_cfg()
    threshold = 30.0

    def test_fires_rth_age_above_threshold(self):
        snap = OptionsSnapshot(surface_age_s=self.threshold + _EPS, is_rth=True)
        result = evaluate_stale_surface(snap, self.cfg)
        assert_fired(result, "stale_surface")

    def test_no_fire_rth_age_at_threshold(self):
        snap = OptionsSnapshot(surface_age_s=self.threshold, is_rth=True)
        result = evaluate_stale_surface(snap, self.cfg)
        assert_not_fired(result, "stale_surface")

    def test_no_fire_rth_age_below_threshold(self):
        snap = OptionsSnapshot(surface_age_s=self.threshold - _EPS, is_rth=True)
        result = evaluate_stale_surface(snap, self.cfg)
        assert_not_fired(result, "stale_surface")

    def test_no_fire_outside_rth_even_stale(self):
        # Outside RTH stale surface is expected; trigger suppressed
        snap = OptionsSnapshot(surface_age_s=10_000.0, is_rth=False)
        result = evaluate_stale_surface(snap, self.cfg)
        assert_not_fired(result, "stale_surface")

    def test_no_data_returns_not_fired(self):
        snap = OptionsSnapshot(surface_age_s=None, is_rth=True)
        result = evaluate_stale_surface(snap, self.cfg)
        assert_not_fired(result, "stale_surface")
        assert result.observed is None


# ===========================================================================
# Trigger 5: stress_grid_breach
# ===========================================================================


class TestStressGridBreach:
    cfg = minimal_cfg()
    threshold = 25_000.0

    def test_fires_loss_above_threshold(self):
        # worst_cell = -(threshold+eps) → loss = threshold+eps > threshold
        snap = OptionsSnapshot(stress_worst_cell_usd=-(self.threshold + _EPS))
        result = evaluate_stress_grid_breach(snap, self.cfg)
        assert_fired(result, "stress_grid_breach")

    def test_no_fire_loss_at_threshold(self):
        # worst_cell = -threshold → loss == threshold, not strictly greater
        snap = OptionsSnapshot(stress_worst_cell_usd=-self.threshold)
        result = evaluate_stress_grid_breach(snap, self.cfg)
        assert_not_fired(result, "stress_grid_breach")

    def test_no_fire_loss_below_threshold(self):
        snap = OptionsSnapshot(stress_worst_cell_usd=-(self.threshold - _EPS))
        result = evaluate_stress_grid_breach(snap, self.cfg)
        assert_not_fired(result, "stress_grid_breach")

    def test_no_fire_positive_pnl(self):
        snap = OptionsSnapshot(stress_worst_cell_usd=10_000.0)
        result = evaluate_stress_grid_breach(snap, self.cfg)
        assert_not_fired(result, "stress_grid_breach")

    def test_no_data_returns_not_fired(self):
        snap = OptionsSnapshot(stress_worst_cell_usd=None)
        result = evaluate_stress_grid_breach(snap, self.cfg)
        assert_not_fired(result, "stress_grid_breach")

    def test_observed_is_positive_loss(self):
        worst = -(self.threshold + 1000.0)
        snap = OptionsSnapshot(stress_worst_cell_usd=worst)
        result = evaluate_stress_grid_breach(snap, self.cfg)
        assert result.observed == pytest.approx(-worst)


# ===========================================================================
# Trigger 6: naked_short_without_hedge
# ===========================================================================


class TestNakedShortWithoutHedge:
    cfg = minimal_cfg()
    threshold = 5.0

    def test_fires_invariant_violated_duration_above_threshold(self):
        snap = OptionsSnapshot(
            hedge_invariant_ok=False,
            hedge_invariant_fail_s=self.threshold + _EPS,
        )
        result = evaluate_naked_short_without_hedge(snap, self.cfg)
        assert_fired(result, "naked_short_without_hedge")

    def test_no_fire_invariant_violated_duration_at_threshold(self):
        snap = OptionsSnapshot(
            hedge_invariant_ok=False,
            hedge_invariant_fail_s=self.threshold,
        )
        result = evaluate_naked_short_without_hedge(snap, self.cfg)
        assert_not_fired(result, "naked_short_without_hedge")

    def test_no_fire_invariant_ok(self):
        # Invariant satisfied even with large fail_s (shouldn't happen; but robust)
        snap = OptionsSnapshot(
            hedge_invariant_ok=True,
            hedge_invariant_fail_s=self.threshold + 100.0,
        )
        result = evaluate_naked_short_without_hedge(snap, self.cfg)
        assert_not_fired(result, "naked_short_without_hedge")

    def test_no_fire_duration_below_threshold(self):
        snap = OptionsSnapshot(
            hedge_invariant_ok=False,
            hedge_invariant_fail_s=self.threshold - _EPS,
        )
        result = evaluate_naked_short_without_hedge(snap, self.cfg)
        assert_not_fired(result, "naked_short_without_hedge")

    def test_action_list_includes_flatten(self):
        snap = OptionsSnapshot(
            hedge_invariant_ok=False,
            hedge_invariant_fail_s=self.threshold + _EPS,
        )
        result = evaluate_naked_short_without_hedge(snap, self.cfg)
        assert "flatten_positions_if_configured" in result.action_list


# ===========================================================================
# Trigger 7: margin_utilization_breach
# ===========================================================================


class TestMarginUtilizationBreach:
    cfg = minimal_cfg()
    threshold = 0.90

    def test_fires_above_hard_limit(self):
        snap = OptionsSnapshot(margin_utilization=self.threshold + _EPS)
        result = evaluate_margin_utilization_breach(snap, self.cfg)
        assert_fired(result, "margin_utilization_breach")

    def test_no_fire_at_hard_limit(self):
        snap = OptionsSnapshot(margin_utilization=self.threshold)
        result = evaluate_margin_utilization_breach(snap, self.cfg)
        assert_not_fired(result, "margin_utilization_breach")

    def test_no_fire_below_hard_limit(self):
        snap = OptionsSnapshot(margin_utilization=self.threshold - _EPS)
        result = evaluate_margin_utilization_breach(snap, self.cfg)
        assert_not_fired(result, "margin_utilization_breach")

    def test_no_data_returns_not_fired(self):
        snap = OptionsSnapshot(margin_utilization=None)
        result = evaluate_margin_utilization_breach(snap, self.cfg)
        assert_not_fired(result, "margin_utilization_breach")

    def test_action_list_includes_flatten(self):
        snap = OptionsSnapshot(margin_utilization=self.threshold + _EPS)
        result = evaluate_margin_utilization_breach(snap, self.cfg)
        assert "flatten_positions_if_configured" in result.action_list


# ===========================================================================
# Trigger 8: expiring_position_unmanaged
# ===========================================================================


class TestExpiringPositionUnmanaged:
    cfg = minimal_cfg()

    def test_fires_all_conditions_met(self):
        snap = OptionsSnapshot(
            has_expiring_position=True,
            past_expiry_cutoff=True,
            has_management_order=False,
        )
        result = evaluate_expiring_position_unmanaged(snap, self.cfg)
        assert_fired(result, "expiring_position_unmanaged")

    def test_no_fire_has_management_order(self):
        snap = OptionsSnapshot(
            has_expiring_position=True,
            past_expiry_cutoff=True,
            has_management_order=True,
        )
        result = evaluate_expiring_position_unmanaged(snap, self.cfg)
        assert_not_fired(result, "expiring_position_unmanaged")

    def test_no_fire_before_cutoff(self):
        snap = OptionsSnapshot(
            has_expiring_position=True,
            past_expiry_cutoff=False,
            has_management_order=False,
        )
        result = evaluate_expiring_position_unmanaged(snap, self.cfg)
        assert_not_fired(result, "expiring_position_unmanaged")

    def test_no_fire_no_expiring_position(self):
        snap = OptionsSnapshot(
            has_expiring_position=False,
            past_expiry_cutoff=True,
            has_management_order=False,
        )
        result = evaluate_expiring_position_unmanaged(snap, self.cfg)
        assert_not_fired(result, "expiring_position_unmanaged")

    def test_observed_threshold_none(self):
        snap = OptionsSnapshot(
            has_expiring_position=True,
            past_expiry_cutoff=True,
            has_management_order=False,
        )
        result = evaluate_expiring_position_unmanaged(snap, self.cfg)
        # Boolean condition — no numeric observed/threshold
        assert result.observed is None
        assert result.threshold is None


# ===========================================================================
# Trigger 9: assignment_uncertainty (hard halt)
# ===========================================================================


class TestAssignmentUncertainty:
    cfg = minimal_cfg()

    def test_fires_on_mismatch(self):
        snap = OptionsSnapshot(assignment_ledger_ok=False)
        result = evaluate_assignment_uncertainty(snap, self.cfg)
        assert_fired(result, "assignment_uncertainty")

    def test_no_fire_when_reconciled(self):
        snap = OptionsSnapshot(assignment_ledger_ok=True)
        result = evaluate_assignment_uncertainty(snap, self.cfg)
        assert_not_fired(result, "assignment_uncertainty")

    def test_no_fire_when_not_in_window(self):
        snap = OptionsSnapshot(assignment_ledger_ok=None)
        result = evaluate_assignment_uncertainty(snap, self.cfg)
        assert_not_fired(result, "assignment_uncertainty")

    def test_hard_halt_action_set(self):
        """assignment_uncertainty must always include full hard-halt actions."""
        snap = OptionsSnapshot(assignment_ledger_ok=False)
        result = evaluate_assignment_uncertainty(snap, self.cfg)
        required_actions = {
            "stop_new_orders",
            "cancel_open_orders",
            "flatten_positions_if_configured",
            "halt_options_lane",
            "log_event",
            "create_incident_report",
            "update_observer_state",
        }
        assert required_actions.issubset(
            set(result.action_list)
        ), f"Missing hard-halt actions: {required_actions - set(result.action_list)}"

    def test_hard_halt_action_set_matches_config(self):
        """Config's assignment_uncertainty actions must include the hard-halt set."""
        cfg = minimal_cfg()
        actions = set(cfg["trigger_actions"]["assignment_uncertainty"])
        required = {
            "stop_new_orders",
            "cancel_open_orders",
            "flatten_positions_if_configured",
            "halt_options_lane",
        }
        assert required.issubset(actions)


# ===========================================================================
# evaluate_all aggregation
# ===========================================================================


class TestEvaluateAll:
    cfg = minimal_cfg()

    def test_no_triggers_fire_on_safe_snapshot(self):
        snap = OptionsSnapshot(
            portfolio_vega_now=0.0,
            portfolio_vega_60s_ago=0.0,
            portfolio_gamma=1000.0,
            portfolio_gamma_60s_ago=900.0,
            atm_iv_now=0.20,
            atm_iv_60s_ago=0.20,
            underlying_price_now=5000.0,
            underlying_price_60s_ago=5000.0,
            surface_age_s=1.0,
            is_rth=True,
            stress_worst_cell_usd=-100.0,
            hedge_invariant_ok=True,
            hedge_invariant_fail_s=0.0,
            margin_utilization=0.50,
            has_expiring_position=False,
            has_management_order=True,
            past_expiry_cutoff=False,
            assignment_ledger_ok=None,
        )
        fired = evaluate_all(snap, self.cfg)
        assert fired == [], f"Expected no triggers; got: {[r.trigger_name for r in fired]}"

    def test_multiple_triggers_can_fire(self):
        # Both vega_spike and stress_grid_breach fire simultaneously
        snap = OptionsSnapshot(
            portfolio_vega_now=100_000.0,  # vega_spike fires
            portfolio_vega_60s_ago=0.0,
            portfolio_gamma=1000.0,
            portfolio_gamma_60s_ago=900.0,
            atm_iv_now=0.20,
            atm_iv_60s_ago=0.20,
            underlying_price_now=5000.0,
            underlying_price_60s_ago=5000.0,
            surface_age_s=1.0,
            is_rth=True,
            stress_worst_cell_usd=-30_000.0,  # stress_grid_breach fires
            hedge_invariant_ok=True,
            hedge_invariant_fail_s=0.0,
            margin_utilization=0.50,
            has_expiring_position=False,
            has_management_order=True,
            past_expiry_cutoff=False,
            assignment_ledger_ok=None,
        )
        fired = evaluate_all(snap, self.cfg)
        names = {r.trigger_name for r in fired}
        assert "vega_spike" in names
        assert "stress_grid_breach" in names

    def test_all_results_have_fired_true(self):
        """evaluate_all only returns fired results."""
        snap = OptionsSnapshot(
            portfolio_vega_now=100_000.0,
            portfolio_vega_60s_ago=0.0,
        )
        fired = evaluate_all(snap, self.cfg)
        for r in fired:
            assert r.fired is True

    def test_evaluate_all_order_stable(self):
        """evaluate_all returns results in consistent (evaluator registration) order."""
        # Two triggers fire: vega_spike (index 0) and stress_grid_breach (index 4)
        snap = OptionsSnapshot(
            portfolio_vega_now=100_000.0,
            portfolio_vega_60s_ago=0.0,
            stress_worst_cell_usd=-30_000.0,
        )
        fired = evaluate_all(snap, self.cfg)
        names = [r.trigger_name for r in fired]
        assert names.index("vega_spike") < names.index("stress_grid_breach")

    def test_assignment_uncertainty_always_hard_halt(self):
        """When assignment_uncertainty fires via evaluate_all it carries hard-halt actions."""
        snap = OptionsSnapshot(assignment_ledger_ok=False)
        fired = evaluate_all(snap, self.cfg)
        au_results = [r for r in fired if r.trigger_name == "assignment_uncertainty"]
        assert len(au_results) == 1
        actions = set(au_results[0].action_list)
        assert "halt_options_lane" in actions
        assert "flatten_positions_if_configured" in actions
        assert "create_incident_report" in actions


# ===========================================================================
# Config loading
# ===========================================================================


class TestLoadConfig:
    def test_load_valid_dict(self):
        cfg = minimal_cfg()
        loaded = load_config(cfg)
        assert "thresholds" in loaded
        assert "trigger_actions" in loaded

    def test_load_json_file(self, tmp_path):
        cfg = minimal_cfg()
        path = str(tmp_path / "options_kill_switch.json")
        with open(path, "w") as fh:
            json.dump(cfg, fh)
        loaded = load_config(path)
        assert loaded["thresholds"]["vega_spike_abs_usd_per_vol_pt"] == 50_000.0

    def test_missing_thresholds_key_raises(self):
        cfg = {"trigger_actions": {}}
        with pytest.raises(ValueError, match="thresholds"):
            load_config(cfg)

    def test_missing_trigger_actions_key_raises(self):
        cfg = {"thresholds": {}}
        with pytest.raises(ValueError, match="trigger_actions"):
            load_config(cfg)

    def test_thresholds_not_dict_raises(self):
        cfg = {"thresholds": "bad", "trigger_actions": {}}
        with pytest.raises(ValueError):
            load_config(cfg)

    def test_trigger_actions_not_dict_raises(self):
        cfg = {"thresholds": {}, "trigger_actions": "bad"}
        with pytest.raises(ValueError):
            load_config(cfg)

    def test_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/to/options_kill_switch.json")

    def test_malformed_json_raises(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as fh:
            fh.write("{not valid json}")
        with pytest.raises(Exception):  # json.JSONDecodeError
            load_config(path)


# ===========================================================================
# Fail-closed: missing threshold key
# ===========================================================================


class TestFailClosed:
    def test_missing_threshold_key_raises_value_error(self):
        """If a threshold key is absent from config, trigger raises ValueError (fail-closed)."""
        cfg = minimal_cfg()
        del cfg["thresholds"]["vega_spike_abs_usd_per_vol_pt"]
        snap = OptionsSnapshot(portfolio_vega_now=100_000.0, portfolio_vega_60s_ago=0.0)
        with pytest.raises(ValueError, match="vega_spike_abs_usd_per_vol_pt"):
            evaluate_vega_spike(snap, cfg)

    def test_evaluate_all_propagates_config_error(self):
        """evaluate_all does NOT swallow config errors (fail-closed)."""
        cfg = minimal_cfg()
        del cfg["thresholds"]["vega_spike_abs_usd_per_vol_pt"]
        snap = OptionsSnapshot(portfolio_vega_now=100_000.0, portfolio_vega_60s_ago=0.0)
        with pytest.raises(ValueError):
            evaluate_all(snap, cfg)
