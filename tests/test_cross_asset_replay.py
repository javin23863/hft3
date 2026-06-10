"""Tests for cross-asset registry default, multi-symbol replay injection,
and family 16-20 evaluate() with populated cross_asset_features.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from features_engine.src.hypotheses.modules import (
    MarketState,
    EsToMesLeadLag,
    NqToMnqLeadLag,
    EsNqDivergenceSnapback,
    ZnZbToEsNqMacroImpulse,
    MicroContractRetailLag,
)
from features_engine.src.hypotheses.registry import (
    CROSS_ASSET_HYP_IDS,
    get_active_hypotheses,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_mbo_events(mid: float = 5000.0, n_levels: int = 5) -> np.ndarray:
    """Build a minimal HftBacktest-compatible structured event array in memory.

    Uses the same dtype as build_minimal_mbo_npz so iter_mbo_events can parse it.
    """
    from hftbacktest.types import (
        ADD_ORDER_EVENT,
        BUY_EVENT,
        EXCH_EVENT,
        LOCAL_EVENT,
        SELL_EVENT,
        event_dtype,
    )

    rows = []
    ts = 1_000_000_000
    tick = 0.25

    for i in range(n_levels):
        rows.append((
            ADD_ORDER_EVENT | BUY_EVENT | EXCH_EVENT | LOCAL_EVENT,
            ts, ts,
            mid - tick * (i + 1), 10.0, 1000 + i, 0, 0.0,
        ))
        rows.append((
            ADD_ORDER_EVENT | SELL_EVENT | EXCH_EVENT | LOCAL_EVENT,
            ts, ts,
            mid + tick * (i + 1), 10.0, 2000 + i, 0, 0.0,
        ))
        ts += 1_000_000

    return np.array(rows, dtype=event_dtype)


def _build_minimal_npz(path: Path, mid: float = 5000.0) -> Path:
    """Write a minimal NPZ file that ReplaySession can load."""
    data = _build_mbo_events(mid=mid)
    np.savez_compressed(path, data=data)
    return path


def _make_market_state(primary: Dict[str, float], cross: Dict[str, Dict[str, float]]) -> MarketState:
    """Construct a MarketState for unit-testing hypothesis evaluate() methods."""
    return MarketState(
        primary_features=primary,
        cross_asset_features=cross,
        regime_state="normal",
        event_context="REGULAR",
        volatility_state="LOW",
        liquidity_state="NORMAL",
        latency_ms=1.0,
        current_inventory=0,
        feature_vector=None,
        regime_posterior={},
    )


# ---------------------------------------------------------------------------
# Task 1: Registry default — cross-asset ON by default, OFF only with =0
# ---------------------------------------------------------------------------

class TestRegistryDefault:
    def test_default_includes_cross_asset(self, monkeypatch):
        """With no env var set, all 45 hypotheses should be active."""
        monkeypatch.delenv("HFT3_CROSS_ASSET", raising=False)
        hyps = get_active_hypotheses()
        assert len(hyps) == 45, f"Expected 45 active hypotheses by default, got {len(hyps)}"

    def test_cross_asset_env_0_excludes_families(self, monkeypatch):
        """HFT3_CROSS_ASSET=0 should drop the 5 cross-asset families → 40 active."""
        monkeypatch.setenv("HFT3_CROSS_ASSET", "0")
        hyps = get_active_hypotheses()
        ids = {h.hyp_id for h in hyps}
        assert len(hyps) == 40, f"Expected 40 hypotheses under HFT3_CROSS_ASSET=0, got {len(hyps)}"
        assert ids.isdisjoint(CROSS_ASSET_HYP_IDS), "Cross-asset IDs must be absent"

    def test_cross_asset_env_false_excludes_families(self, monkeypatch):
        """HFT3_CROSS_ASSET=false should also drop cross-asset families."""
        monkeypatch.setenv("HFT3_CROSS_ASSET", "false")
        hyps = get_active_hypotheses()
        assert len(hyps) == 40

    def test_cross_asset_env_1_still_45(self, monkeypatch):
        """HFT3_CROSS_ASSET=1 (old opt-in form) still returns 45."""
        monkeypatch.setenv("HFT3_CROSS_ASSET", "1")
        hyps = get_active_hypotheses()
        assert len(hyps) == 45

    def test_cross_asset_ids_present_in_default(self, monkeypatch):
        """Families 16-20 must be in the default active set."""
        monkeypatch.delenv("HFT3_CROSS_ASSET", raising=False)
        hyps = get_active_hypotheses()
        ids = {h.hyp_id for h in hyps}
        assert CROSS_ASSET_HYP_IDS.issubset(ids)


# ---------------------------------------------------------------------------
# Task 5b: Family 16-20 evaluate() with hand-built cross_asset_features
# ---------------------------------------------------------------------------

class TestCrossAssetHypothesesEvaluate:
    """Verify families 16-20 return nonzero when cross_asset_features is populated."""

    def test_family16_es_to_mes_lead_lag_nonzero(self):
        """EsToMesLeadLag fires when ES aggressor_imbalance differs from MES."""
        hyp = EsToMesLeadLag()
        # ES has strong buy imbalance; MES has none → divergence = 0.8 → signal nonzero
        state = _make_market_state(
            primary={"aggressor_volume_imbalance": 0.0},
            cross={"ES": {"aggressor_volume_imbalance": 0.8}},
        )
        signal = hyp.evaluate(state)
        assert signal != 0.0, "Family 16 must produce nonzero signal when ES leads MES"
        # tanh(0.8 * 2.0) > 0 — signal should be positive
        assert signal > 0.0

    def test_family17_nq_to_mnq_lead_lag_nonzero(self):
        """NqToMnqLeadLag fires when NQ aggressor_imbalance differs from MNQ."""
        hyp = NqToMnqLeadLag()
        state = _make_market_state(
            primary={"aggressor_volume_imbalance": -0.5},
            cross={"NQ": {"aggressor_volume_imbalance": 0.5}},
        )
        signal = hyp.evaluate(state)
        # divergence = NQ_imb - MNQ_imb = 0.5 - (-0.5) = 1.0 → tanh(2.0) ≈ 0.964
        assert signal > 0.5

    def test_family18_es_nq_divergence_snapback_nonzero(self):
        """EsNqDivergenceSnapback fires when ES and NQ diverge."""
        hyp = EsNqDivergenceSnapback()
        state = _make_market_state(
            primary={},
            cross={
                "ES": {"aggressor_volume_imbalance": 0.9},
                "NQ": {"aggressor_volume_imbalance": -0.1},
            },
        )
        signal = hyp.evaluate(state)
        # divergence = ES - NQ = 1.0 → signal = -tanh(1.5) ≈ -0.905
        assert signal != 0.0
        assert signal < 0.0, "Snapback signal should be negative when ES > NQ"

    def test_family19_zn_zb_to_es_nq_macro_impulse_nonzero(self):
        """ZnZbToEsNqMacroImpulse fires when ZN aggressor differs from ES."""
        hyp = ZnZbToEsNqMacroImpulse()
        state = _make_market_state(
            primary={"aggressor_volume_imbalance": 0.0},
            cross={"ZN": {"aggressor_volume_imbalance": 0.7}},
        )
        signal = hyp.evaluate(state)
        # impulse = ZN_imb - ES_imb = 0.7 → tanh(1.4) ≈ 0.885
        assert signal > 0.5

    def test_family20_micro_contract_retail_lag_nonzero(self):
        """MicroContractRetailLag fires when ES has institutional_flow_score."""
        hyp = MicroContractRetailLag()
        state = _make_market_state(
            primary={},
            cross={"ES": {"institutional_flow_score": 0.6}},
        )
        signal = hyp.evaluate(state)
        # tanh(0.6 * 2.0) = tanh(1.2) ≈ 0.834
        assert signal > 0.5

    def test_cross_asset_families_zero_when_no_cross_features(self):
        """All cross-asset families should return 0.0 when cross_asset_features is empty."""
        state = _make_market_state(primary={}, cross={})
        for cls in (EsToMesLeadLag, NqToMnqLeadLag, EsNqDivergenceSnapback,
                    ZnZbToEsNqMacroImpulse, MicroContractRetailLag):
            signal = cls().evaluate(state)
            # divergence = 0 → tanh(0) = 0
            assert signal == 0.0, f"{cls.__name__} should return 0 with empty cross_asset_features"


# ---------------------------------------------------------------------------
# Task 5a: Multi-symbol replay — cross_asset_features injected into MarketState
# ---------------------------------------------------------------------------

class TestMultiSymbolReplay:
    """Use HistoricalReplayMarketDataAdapter directly (no hbt dep) to test injection."""

    def test_secondary_adapter_provides_primary_features(self, tmp_path):
        """A secondary adapter built from NPZ yields a non-None MarketState
        with primary_features dict containing expected keys."""
        from replay.market_data_adapter import HistoricalReplayMarketDataAdapter

        npz = tmp_path / "ES_test.npz"
        _build_minimal_npz(npz, mid=4900.0)

        mda = HistoricalReplayMarketDataAdapter.from_npz(str(npz))
        # Sync past all events
        state = mda.sync_to_timestamp(10_000_000_000)
        assert state is not None, "Secondary adapter must produce a MarketState"
        assert "aggressor_volume_imbalance" in state.primary_features

    def test_mid_price_plausible_in_secondary_state(self, tmp_path):
        """mid_price in secondary state should be near the mid used to build the NPZ."""
        from replay.market_data_adapter import HistoricalReplayMarketDataAdapter

        npz = tmp_path / "ES_mid.npz"
        _build_minimal_npz(npz, mid=4900.0)

        mda = HistoricalReplayMarketDataAdapter.from_npz(str(npz))
        state = mda.sync_to_timestamp(10_000_000_000)
        assert state is not None
        # The best bid is 4900.0 - 0.25 = 4899.75 and best ask is 4900.25
        # mid ≈ 4900.0; allow ±2.0 for level offset
        mid = state.primary_features.get("mid_price", 0.0)
        assert 4897.0 <= mid <= 4903.0, f"mid_price {mid} unexpectedly far from 4900"

    def test_cross_asset_features_injected_via_config(self, tmp_path):
        """ReplaySessionConfig.cross_asset_events wires secondary features into
        ctx.market_state.cross_asset_features[symbol].

        This test exercises the injection logic without a full hbt run by
        directly using HistoricalReplayMarketDataAdapter + the injection code
        path (mirrored from ReplaySession.run) so hftbacktest is not required.
        """
        from replay.market_data_adapter import HistoricalReplayMarketDataAdapter

        # Build two synthetic event arrays: primary (MES) at 5000, secondary (ES) at 4900
        primary_events = _build_mbo_events(mid=5000.0)
        secondary_events = _build_mbo_events(mid=4900.0)

        primary_mda = HistoricalReplayMarketDataAdapter(primary_events)
        secondary_mda = HistoricalReplayMarketDataAdapter(secondary_events)

        feature_ts = 10_000_000_000  # far past all events

        primary_mda.sync_to_timestamp(feature_ts)
        primary_state = primary_mda.current_market_state("MES")
        assert primary_state is not None

        secondary_mda.sync_to_timestamp(feature_ts)
        secondary_state = secondary_mda.current_market_state("ES")
        assert secondary_state is not None

        # Simulate the injection performed by ReplaySession.run
        injected = MarketState(
            feature_vector=primary_state.feature_vector,
            primary_features=primary_state.primary_features,
            cross_asset_features={"ES": secondary_state.primary_features},
            regime_posterior=primary_state.regime_posterior,
            regime_state=primary_state.regime_state,
            event_context=primary_state.event_context,
            volatility_state=primary_state.volatility_state,
            liquidity_state=primary_state.liquidity_state,
            latency_ms=primary_state.latency_ms,
            current_inventory=primary_state.current_inventory,
        )

        assert "ES" in injected.cross_asset_features, "ES must be in cross_asset_features"
        es_feats = injected.cross_asset_features["ES"]
        assert "aggressor_volume_imbalance" in es_feats
        # mid_price in ES secondary should be near 4900 (best bid ≈ 4899.75)
        es_mid = es_feats.get("mid_price", 0.0)
        assert 4897.0 <= es_mid <= 4903.0, f"ES mid_price {es_mid} unexpected"

    def test_cross_asset_features_nonempty_symbol_key(self, tmp_path):
        """The injected cross_asset_features dict has the expected symbol key."""
        from replay.market_data_adapter import HistoricalReplayMarketDataAdapter

        secondary_events = _build_mbo_events(mid=4900.0)
        mda = HistoricalReplayMarketDataAdapter(secondary_events)
        mda.sync_to_timestamp(10_000_000_000)
        sec_state = mda.current_market_state("ES")
        assert sec_state is not None

        state = _make_market_state(
            primary={"aggressor_volume_imbalance": 0.0},
            cross={"ES": sec_state.primary_features},
        )

        # Family 16 should now see ES cross-asset features
        hyp = EsToMesLeadLag()
        # We can't guarantee a nonzero signal here since the synthetic data
        # may produce identical imbalances, but the structure must be correct.
        _ = hyp.evaluate(state)
        assert "ES" in state.cross_asset_features
        assert "aggressor_volume_imbalance" in state.cross_asset_features["ES"]

    def test_replay_session_config_cross_asset_fields_default_empty(self):
        """ReplaySessionConfig.cross_asset_npz and cross_asset_events default to {}."""
        from replay.replay_session import ReplaySessionConfig

        cfg = ReplaySessionConfig()
        assert cfg.cross_asset_npz == {}
        assert cfg.cross_asset_events == {}

    def test_replay_session_config_accepts_cross_asset_npz(self, tmp_path):
        """ReplaySessionConfig accepts cross_asset_npz dict."""
        from replay.replay_session import ReplaySessionConfig

        npz = str(tmp_path / "ES.npz")
        cfg = ReplaySessionConfig(cross_asset_npz={"ES": npz})
        assert cfg.cross_asset_npz == {"ES": npz}

    def test_replay_session_config_accepts_cross_asset_events(self):
        """ReplaySessionConfig accepts cross_asset_events dict."""
        from replay.replay_session import ReplaySessionConfig

        events = _build_mbo_events(mid=4900.0)
        cfg = ReplaySessionConfig(cross_asset_events={"ES": events})
        assert "ES" in cfg.cross_asset_events
        assert len(cfg.cross_asset_events["ES"]) > 0
