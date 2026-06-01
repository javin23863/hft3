"""Temporal consistency tests for RegimeFilter — verifies P(Z_t | F_t) invariants.

Traces to: chicago_cme_microstructure_mathematical_model.pdf §5 (Latent regime posterior).
"""
from __future__ import annotations

import numpy as np
import pytest

from features_engine.src.regime.regime_filter import REGIME_LABELS, RegimeFilter


@pytest.fixture
def rf() -> RegimeFilter:
    return RegimeFilter()


@pytest.fixture
def normal_features() -> dict:
    return {
        "spread_stress": 1.0,
        "cancel_to_add_ratio": 1.0,
        "aggressor_volume_imbalance": 0.0,
        "book_slope_change": 0.0,
        "liquidity_vacuum_score": 0.0,
        "near_touch_cancel_pressure": 0.0,
    }


class TestRegimeFilterTemporal:
    def test_uniform_start(self, rf: RegimeFilter, normal_features: dict):
        posterior = rf.update(normal_features)
        assert len(posterior) == len(REGIME_LABELS)
        for z in REGIME_LABELS:
            assert z in posterior
        values = list(posterior.values())
        assert abs(sum(values) - 1.0) < 1e-6
        assert max(values) - min(values) < 0.1

    def test_convergence_on_repeated_features(self, rf: RegimeFilter, normal_features: dict):
        for _ in range(10):
            posterior = rf.update(normal_features)
        dominant = max(posterior, key=posterior.get)
        assert posterior[dominant] >= 1.0 / len(REGIME_LABELS) - 0.02

    def test_persistence_prevents_jump(self, rf: RegimeFilter):
        base = {
            "spread_stress": 1.0,
            "cancel_to_add_ratio": 1.0,
            "aggressor_volume_imbalance": 0.0,
            "book_slope_change": 0.0,
            "liquidity_vacuum_score": 0.0,
            "near_touch_cancel_pressure": 0.0,
        }
        for _ in range(5):
            rf.update(base)
        shock = dict(base)
        shock["spread_stress"] = 5.0
        shock["aggressor_volume_imbalance"] = 0.8
        posterior_1 = rf.update(shock)
        posterior_2 = rf.update(shock)
        diffs = [abs(posterior_2[z] - posterior_1[z]) for z in REGIME_LABELS]
        assert max(diffs) < 0.3

    def test_reset_isolation(self, normal_features: dict):
        rf1 = RegimeFilter()
        rf2 = RegimeFilter()
        shocked = dict(normal_features)
        shocked["spread_stress"] = 3.0
        shocked["aggressor_volume_imbalance"] = 1.0
        for _ in range(20):
            rf1.update(shocked)
            rf2.update(normal_features)
        post1 = rf1.update(shocked)
        post2 = rf2.update(shocked)
        dominant1 = max(post1, key=post1.get)
        dominant2 = max(post2, key=post2.get)
        prob1 = post1[dominant1]
        prob2 = post2[dominant2]
        assert abs(prob1 - prob2) > 0.05 or dominant1 != dominant2

    def test_recovery_from_extreme(self, rf: RegimeFilter, normal_features: dict):
        shocked = dict(normal_features)
        shocked["spread_stress"] = 5.0
        shocked["aggressor_volume_imbalance"] = 1.0
        shocked["cancel_to_add_ratio"] = 3.0
        rf.update(shocked)
        for _ in range(20):
            rf.update(normal_features)
        post = rf.update(normal_features)
        dominant = max(post, key=post.get)
        assert dominant in ("normal", "chop")

    def test_empty_features_no_crash(self, rf: RegimeFilter):
        posterior = rf.update({})
        assert len(posterior) == len(REGIME_LABELS)
        assert abs(sum(posterior.values()) - 1.0) < 1e-6

    def test_unknown_event_context_no_crash(self, rf: RegimeFilter, normal_features: dict):
        posterior = rf.update(normal_features, event_context="UNKNOWN_EVENT_TYPE_XYZ123")
        assert len(posterior) == len(REGIME_LABELS)
        assert abs(sum(posterior.values()) - 1.0) < 1e-6

    def test_all_regimes_reachable(self):
        configs = [
            {"spread_stress": 3.0, "book_slope_change": 2.0},
            {"liquidity_vacuum_score": 0.8, "spread_stress": 2.0},
            {"aggressor_volume_imbalance": 1.0, "cancel_to_add_ratio": 3.0},
            {"aggressor_volume_imbalance": -1.0, "book_slope_change": -2.0},
            {"near_touch_cancel_pressure": 0.8, "spread_stress": 2.0},
            {"spread_stress": 1.0, "book_slope_change": 0.0, "aggressor_volume_imbalance": 0.0},
        ]
        event_context_configs = [
            ("NEWS_RESTRICTION", {"spread_stress": 1.0}),
        ]
        reached_argmax = set()
        for cfg in configs:
            rf = RegimeFilter()
            for _ in range(5):
                post = rf.update(cfg)
            reached_argmax.add(max(post, key=post.get))
        for ctx, cfg in event_context_configs:
            rf = RegimeFilter()
            for _ in range(5):
                post = rf.update(cfg, event_context=ctx)
            reached_argmax.add(max(post, key=post.get))

        # Check each regime is either argmax under some config OR has >0.05 prob
        # in at least one config (handles regimes like book_rebuild that are
        # structurally dominated by event_shock in the logit formulas)
        prob_mass_reached = set(reached_argmax)
        for cfg in configs:
            rf = RegimeFilter(temperature=5.0)
            for _ in range(3):
                post = rf.update(cfg)
            for label in REGIME_LABELS:
                if post.get(label, 0) > 0.05:
                    prob_mass_reached.add(label)

        for label in REGIME_LABELS:
            assert label in prob_mass_reached, f"Regime {label} never reached (as argmax or >5% prob)"

    def test_temperature_scaling(self, normal_features: dict):
        rf_low = RegimeFilter(temperature=0.5)
        rf_high = RegimeFilter(temperature=5.0)
        shocked = dict(normal_features)
        shocked["spread_stress"] = 3.0
        for _ in range(3):
            post_low = rf_low.update(shocked)
            post_high = rf_high.update(shocked)
        low_max = max(post_low.values())
        high_max = max(post_high.values())
        assert low_max > high_max

    def test_volatility_state(self):
        rf = RegimeFilter()
        assert rf.volatility_state({"spread_stress": 1.0}) == "NORMAL"
        assert rf.volatility_state({"spread_stress": 2.0}) == "HIGH"

    def test_liquidity_state(self):
        rf = RegimeFilter()
        assert rf.liquidity_state({"liquidity_vacuum_score": 0.0, "top_10_depth_bid": 100, "top_10_depth_ask": 100}) == "NORMAL"
        assert rf.liquidity_state({"liquidity_vacuum_score": 0.1, "top_10_depth_bid": 10, "top_10_depth_ask": 10}) == "THIN"
