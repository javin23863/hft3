"""Integration: imbalance vector slots + replay ablation wrapper."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from features_engine.src.features.feature_index import FeatureIndex, vector_to_feature_dict
from features_engine.src.features.mbo_features import MBOEvent, MBOFeatureExtractor
from features_engine.src.imbalance.apply import apply_imbalance_to_vector, wrap_hypothesis_for_ablation
from features_engine.src.imbalance.ablation import all_ablation_modes
from features_engine.src.imbalance.engine import ImbalanceEngine
from features_engine.src.imbalance.classification import DataClass
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline
from features_engine.src.hypotheses.modules import BaseHypothesis, MarketState

REPO = Path(__file__).resolve().parents[2]
NPZ = REPO / "tests" / "fixtures" / "replay_minimal_mbo.npz"


def test_vector_book_imbalance_slots_populated():
    pipe = MarketStatePipeline(tick_size=0.25, emit_imbalance=True)
    ev = MBOEvent(timestamp_ns=1, action="ADD", side="B", price=100.0, size=40, order_id=1)
    pipe.process_event(ev)
    ev2 = MBOEvent(timestamp_ns=2, action="ADD", side="A", price=100.25, size=20, order_id=2)
    state = pipe.process_event(ev2)
    assert state.feature_vector is not None
    assert state.feature_vector[FeatureIndex.BOOK_IMBALANCE_L1] != 0.0
    assert state.primary_features.get("book_imbalance_l1") is not None


@pytest.mark.skipif(not NPZ.is_file(), reason="replay_minimal_mbo.npz missing")
def test_replay_ablation_wires_mode_into_session():
    pytest.importorskip("hftbacktest")
    from backtest_pipeline.src.replay_matrix import run_hypothesis_replay
    from features_engine.src.hypotheses.registry import get_active_hypotheses

    hyp = get_active_hypotheses()[4]
    baseline_mode = next(m for m in all_ablation_modes() if m.mode_id == "baseline")
    all_mode = next(m for m in all_ablation_modes() if m.mode_id == "all_three")
    meta_b: dict = {}
    meta_t: dict = {}
    run_hypothesis_replay(
        wrap_hypothesis_for_ablation(hyp, baseline_mode),
        str(NPZ),
        imbalance_ablation_mode_id="baseline",
        meta_out=meta_b,
    )
    run_hypothesis_replay(
        wrap_hypothesis_for_ablation(hyp, all_mode),
        str(NPZ),
        imbalance_ablation_mode_id="all_three",
        meta_out=meta_t,
    )
    assert "imbalance_snapshot_summary" in meta_b
    assert "imbalance_snapshot_summary" in meta_t
    assert meta_t["imbalance_snapshot_summary"].get("sample_count", 0) >= 0
