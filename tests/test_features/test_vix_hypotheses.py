"""Tests for VIX hypothesis modules (HYP 46-50)."""
import math
import os

import numpy as np
import pytest

from features_engine.src.hypotheses.modules import MarketState
from features_engine.src.hypotheses.vix_modules import (
    VixSpikeEventFade,
    VixQuotePullLiquidityVacuum,
    VixImpliedRealizedGap,
    VixDepthImbalanceDirection,
    VixLevelConditionedContinuation,
)
from features_engine.src.hypotheses.registry import get_active_hypotheses
from features_engine.src.model_registry import get_slug_for_hyp_id

_VIX_HYPS = [
    VixSpikeEventFade(),
    VixQuotePullLiquidityVacuum(),
    VixImpliedRealizedGap(),
    VixDepthImbalanceDirection(),
    VixLevelConditionedContinuation(),
]

_FEATURE_DIM = 64


def _minimal_state(cross_asset=None, event_context="NORMAL", regime_posterior=None,
                   primary_features=None, feature_vector=None):
    return MarketState(
        primary_features=primary_features or {},
        cross_asset_features=cross_asset or {},
        regime_state="flat",
        event_context=event_context,
        volatility_state="LOW",
        liquidity_state="NORMAL",
        latency_ms=0.1,
        current_inventory=0,
        feature_vector=feature_vector if feature_vector is not None else np.zeros(_FEATURE_DIM),
        regime_posterior=regime_posterior or {},
    )


def _vix_dict(**kwargs):
    base = {
        "vix_opt_bipower_var": 0.05,
        "vix_opt_tsrv": 0.03,
        "vix_quote_arrival_accel": -0.5,
        "vix_opt_spread_stress": 2.5,
        "vix_opt_depth_imbalance": 0.6,
        "vix_atm_strike": 22.0,
    }
    base.update(kwargs)
    return base


class TestAbstainWithoutVix:
    def test_abstain_without_vix(self):
        fv = np.zeros(_FEATURE_DIM)
        state = _minimal_state(cross_asset={}, feature_vector=fv)
        for hyp in _VIX_HYPS:
            result = hyp.evaluate(state)
            assert result == 0.0, (
                f"{hyp.__class__.__name__} returned {result} without VIX; expected 0.0"
            )


class TestBoundedSignals:
    def _firing_state(self, hyp):
        fv = np.zeros(_FEATURE_DIM)
        fv[0] = 0.9   # aggressor_volume_imbalance (slot 0)
        fv[13] = 1.0  # book_slope (slot 13)
        fv[16] = 3.0  # spread_stress (slot 16)
        fv[17] = 0.8  # liquidity_vacuum_score (slot 17)
        fv[26] = 0.02  # realized_vol_state (slot 26)

        vix = _vix_dict(
            vix_opt_bipower_var=10.0,
            vix_opt_tsrv=0.001,
            vix_quote_arrival_accel=-2.0,
            vix_opt_spread_stress=5.0,
            vix_opt_depth_imbalance=1.0,
            vix_atm_strike=30.0,
        )
        return _minimal_state(
            cross_asset={"VIX": vix},
            event_context="CPI_TIGHT",
            regime_posterior={"trend_continuation": 1.0, "event_shock": 1.0},
            feature_vector=fv,
        )

    def test_bounded_signals(self):
        for hyp in _VIX_HYPS:
            state = self._firing_state(hyp)
            sig = hyp.evaluate(state)
            assert -1.0 <= sig <= 1.0, (
                f"{hyp.__class__.__name__} produced out-of-bounds signal {sig}"
            )

    def test_bounded_signals_extreme_negative_imbalance(self):
        fv = np.zeros(_FEATURE_DIM)
        fv[0] = -0.95
        fv[13] = -1.0
        fv[16] = 5.0
        fv[17] = 1.0
        vix = _vix_dict(
            vix_opt_bipower_var=50.0,
            vix_opt_tsrv=0.0001,
            vix_opt_depth_imbalance=-1.0,
            vix_atm_strike=35.0,
        )
        state = _minimal_state(
            cross_asset={"VIX": vix},
            event_context="FOMC_TIGHT",
            regime_posterior={"trend_continuation": 1.0, "event_shock": 1.0},
            feature_vector=fv,
        )
        for hyp in _VIX_HYPS:
            sig = hyp.evaluate(state)
            assert -1.0 <= sig <= 1.0, (
                f"{hyp.__class__.__name__} extreme-neg: out-of-bounds {sig}"
            )


class TestNanVixFieldsAbstainOrDrop:
    def _base_firing_state(self):
        fv = np.zeros(_FEATURE_DIM)
        fv[0] = 0.8
        fv[13] = 0.5
        fv[16] = 3.0
        fv[17] = 0.7
        vix = _vix_dict(vix_atm_strike=25.0)
        return _minimal_state(
            cross_asset={"VIX": vix},
            event_context="CPI_TIGHT",
            regime_posterior={"trend_continuation": 0.8, "event_shock": 0.8},
            feature_vector=fv,
        )

    def _state_with_nan_field(self, field_name):
        state = self._base_firing_state()
        state.cross_asset_features["VIX"][field_name] = math.nan
        return state

    def test_nan_bipower_var_no_propagation(self):
        for hyp in (VixSpikeEventFade(), VixImpliedRealizedGap()):
            state = self._state_with_nan_field("vix_opt_bipower_var")
            sig = hyp.evaluate(state)
            assert math.isfinite(sig), f"{hyp.__class__.__name__} propagated NaN"
            assert sig == 0.0, f"{hyp.__class__.__name__} should abstain on NaN bipower"

    def test_nan_tsrv_no_propagation(self):
        hyp = VixSpikeEventFade()
        state = self._state_with_nan_field("vix_opt_tsrv")
        sig = hyp.evaluate(state)
        assert math.isfinite(sig)
        assert sig == 0.0

    def test_nan_arrival_accel_no_propagation(self):
        hyp = VixQuotePullLiquidityVacuum()
        state = self._state_with_nan_field("vix_quote_arrival_accel")
        sig = hyp.evaluate(state)
        assert math.isfinite(sig)
        assert sig == 0.0

    def test_nan_spread_stress_no_propagation(self):
        hyp = VixQuotePullLiquidityVacuum()
        state = self._state_with_nan_field("vix_opt_spread_stress")
        sig = hyp.evaluate(state)
        assert math.isfinite(sig)
        assert sig == 0.0

    def test_nan_depth_imbalance_no_propagation(self):
        hyp = VixDepthImbalanceDirection()
        state = self._state_with_nan_field("vix_opt_depth_imbalance")
        sig = hyp.evaluate(state)
        assert math.isfinite(sig)
        assert sig == 0.0

    def test_nan_atm_strike_no_propagation(self):
        hyp = VixLevelConditionedContinuation()
        state = self._state_with_nan_field("vix_atm_strike")
        sig = hyp.evaluate(state)
        assert math.isfinite(sig)
        assert sig == 0.0


class TestGating:
    def test_hyp46_returns_zero_under_normal_context(self):
        fv = np.zeros(_FEATURE_DIM)
        fv[0] = 0.9
        vix = _vix_dict(vix_opt_bipower_var=5.0, vix_opt_tsrv=0.01)
        state = _minimal_state(
            cross_asset={"VIX": vix},
            event_context="NORMAL",
            feature_vector=fv,
        )
        assert VixSpikeEventFade().evaluate(state) == 0.0

    def test_hyp46_can_fire_under_macro_tight(self):
        fv = np.zeros(_FEATURE_DIM)
        fv[0] = 0.9
        vix = _vix_dict(vix_opt_bipower_var=10.0, vix_opt_tsrv=0.001)
        state = _minimal_state(
            cross_asset={"VIX": vix},
            event_context="CPI_TIGHT",
            feature_vector=fv,
        )
        sig = VixSpikeEventFade().evaluate(state)
        assert sig != 0.0, "VixSpikeEventFade should fire under CPI_TIGHT with jump_ratio > 1.5"

    def test_hyp50_returns_zero_when_atm_strike_below_20(self):
        fv = np.zeros(_FEATURE_DIM)
        fv[0] = 0.8
        vix = _vix_dict(vix_atm_strike=15.0)
        state = _minimal_state(
            cross_asset={"VIX": vix},
            event_context="CPI_TIGHT",
            regime_posterior={"event_shock": 0.9},
            feature_vector=fv,
        )
        assert VixLevelConditionedContinuation().evaluate(state) == 0.0

    def test_hyp50_returns_zero_when_event_shock_zero(self):
        fv = np.zeros(_FEATURE_DIM)
        fv[0] = 0.8
        vix = _vix_dict(vix_atm_strike=25.0)
        state = _minimal_state(
            cross_asset={"VIX": vix},
            event_context="CPI_TIGHT",
            regime_posterior={"event_shock": 0.0},
            feature_vector=fv,
        )
        assert VixLevelConditionedContinuation().evaluate(state) == 0.0

    def test_hyp50_fires_when_conditions_met(self):
        fv = np.zeros(_FEATURE_DIM)
        fv[0] = 0.7
        vix = _vix_dict(vix_atm_strike=22.0)
        state = _minimal_state(
            cross_asset={"VIX": vix},
            event_context="NORMAL",
            regime_posterior={"event_shock": 0.8},
            feature_vector=fv,
        )
        sig = VixLevelConditionedContinuation().evaluate(state)
        assert sig != 0.0, "VixLevelConditionedContinuation should fire when atm>=20 and event_shock>0"


class TestRegistryContainsVixHyps:
    def test_get_active_hypotheses_includes_46_to_50(self):
        hyps = get_active_hypotheses()
        ids = {h.hyp_id for h in hyps}
        for expected_id in (46, 47, 48, 49, 50):
            assert expected_id in ids, f"hyp_id {expected_id} missing from get_active_hypotheses()"

    def test_hft3_cross_asset_zero_excludes_vix_hyps(self, monkeypatch):
        monkeypatch.setenv("HFT3_CROSS_ASSET", "0")
        hyps = get_active_hypotheses()
        ids = {h.hyp_id for h in hyps}
        for excluded_id in (46, 47, 48, 49, 50):
            assert excluded_id not in ids, (
                f"hyp_id {excluded_id} should be excluded when HFT3_CROSS_ASSET=0"
            )

    def test_get_slug_for_hyp_id_46(self):
        slug = get_slug_for_hyp_id(46)
        assert slug == "VIX_SPIKE_EVENT_FADE"

    def test_get_slug_for_hyp_id_47(self):
        assert get_slug_for_hyp_id(47) == "VIX_QUOTE_PULL_LIQUIDITY_VACUUM"

    def test_get_slug_for_hyp_id_48(self):
        assert get_slug_for_hyp_id(48) == "VIX_IMPLIED_REALIZED_GAP"

    def test_get_slug_for_hyp_id_49(self):
        assert get_slug_for_hyp_id(49) == "VIX_DEPTH_IMBALANCE_DIRECTION"

    def test_get_slug_for_hyp_id_50(self):
        assert get_slug_for_hyp_id(50) == "VIX_LEVEL_CONDITIONED_CONTINUATION"
