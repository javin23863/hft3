"""Smoke tests for regime filter, event context, and market state pipeline."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from features_engine.src.features.mbo_features import MBOEvent
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline
from features_engine.src.regime.event_context import EventContextEngine
from features_engine.src.regime.regime_filter import RegimeFilter, REGIME_LABELS


def test_regime_posterior_sums_to_one():
    rf = RegimeFilter()
    feats = {
        "spread_stress": 2.0,
        "cancel_to_add_ratio": 2.0,
        "aggressor_volume_imbalance": 0.9,
        "book_slope_change": 0.3,
        "liquidity_vacuum_score": 0.5,
        "near_touch_cancel_pressure": 0.6,
    }
    p = rf.update(feats, "CPI_TIGHT")
    assert abs(sum(p.values()) - 1.0) < 1e-6
    assert set(p.keys()) == set(REGIME_LABELS)


def test_event_context_cpi_tight():
    engine = EventContextEngine()
    # CPI 2024-09-11 08:30 ET -> tight window includes 08:29:30 ET
    ts = datetime(2024, 9, 11, 12, 29, 45, tzinfo=timezone.utc)  # 08:29:45 ET
    label = engine.resolve(ts)
    assert label in ("CPI_TIGHT", "CPI")


def test_pipeline_produces_indexed_vector():
    pipe = MarketStatePipeline(tick_size=0.25)
    events = [
        MBOEvent(1_700_000_000_000, 1, "ADD", "B", 5500.0, 10),
        MBOEvent(1_700_000_000_100, 2, "ADD", "A", 5500.25, 8),
        MBOEvent(1_700_000_000_200, 3, "TRADE", "A", 5500.25, 2),
    ]
    state = None
    for ev in events:
        state = pipe.process_event(ev)
    assert state is not None
    assert state.feature_vector is not None
    assert state.feature_vector.shape == (64,)
    assert state.regime_posterior
    regime_sum = float(np.sum(state.feature_vector[41:50]))
    assert abs(regime_sum - 1.0) < 1e-5
    assert state.feature_vector[26] >= 0  # REALIZED_VOL_STATE
    assert state.f("mid_price", 0.0) > 0
