"""Auction imbalance fed into replay at event time (filtration-safe)."""

from __future__ import annotations

from pathlib import Path

import pytest

from features_engine.src.imbalance.ablation import all_ablation_modes
from features_engine.src.imbalance.apply import imbalance_signal_boost, wrap_hypothesis_for_ablation
from features_engine.src.imbalance.auction_events import load_auction_events
from features_engine.src.hypotheses.modules import BaseHypothesis, MarketState
from replay.auction_replay_feed import AuctionReplayFeed
from replay.market_data_adapter import HistoricalReplayMarketDataAdapter

REPO = Path(__file__).resolve().parents[2]
NPZ = REPO / "tests" / "fixtures" / "replay_minimal_mbo.npz"


class _ZeroHypothesis(BaseHypothesis):
    def __init__(self) -> None:
        super().__init__(0, "zero")

    def evaluate(self, state: MarketState) -> float:
        return 0.0


def test_auction_feed_merges_into_market_state():
    events = load_auction_events(REPO, "CPI_2024_09_11_TIGHT", "MES")
    assert events, "fixture auction NDJSON required"
    import numpy as np

    from features_engine.src.features.npz_feed import load_npz_events

    if not NPZ.is_file():
        pytest.skip("replay_minimal_mbo.npz missing")
    raw = load_npz_events(str(NPZ))
    adapter = HistoricalReplayMarketDataAdapter(
        raw,
        auction_events=events,
        event_window_id="CPI_2024_09_11_TIGHT",
    )
    state = adapter.sync_to_timestamp(1_000_000_000)
    assert state is not None
    auc = (state.imbalance_snapshot or {}).get("auction")
    assert auc is not None
    assert auc.get("auction_pressure_score") is not None


def test_auction_only_boost_differs_from_baseline():
    events = load_auction_events(REPO, "CPI_2024_09_11_TIGHT", "MES")
    assert events
    if not NPZ.is_file():
        pytest.skip("replay_minimal_mbo.npz missing")
    import numpy as np
    from features_engine.src.features.npz_feed import load_npz_events

    raw = load_npz_events(str(NPZ))
    baseline_mode = next(m for m in all_ablation_modes() if m.mode_id == "baseline")
    auction_mode = next(m for m in all_ablation_modes() if m.mode_id == "auction_only")

    def _boost(mode):
        adapter = HistoricalReplayMarketDataAdapter(
            raw,
            imbalance_ablation_mode=mode,
            auction_events=events,
            event_window_id="CPI_2024_09_11_TIGHT",
        )
        state = adapter.sync_to_timestamp(1_000_000_000)
        assert state is not None
        return imbalance_signal_boost(state, mode)

    assert _boost(auction_mode) != _boost(baseline_mode)


@pytest.mark.skipif(not NPZ.is_file(), reason="replay_minimal_mbo.npz missing")
def test_replay_collects_auction_in_samples():
    pytest.importorskip("hftbacktest")
    from backtest_pipeline.src.replay_matrix import run_hypothesis_replay

    events = load_auction_events(REPO, "CPI_2024_09_11_TIGHT", "MES")
    auction_mode = next(m for m in all_ablation_modes() if m.mode_id == "auction_only")
    meta: dict = {}
    run_hypothesis_replay(
        wrap_hypothesis_for_ablation(_ZeroHypothesis(), auction_mode),
        str(NPZ),
        imbalance_ablation_mode_id="auction_only",
        auction_events=events,
        event_window_id="CPI_2024_09_11_TIGHT",
        meta_out=meta,
    )
    samples = meta.get("imbalance_samples") or []
    assert any(s.get("auction") for s in samples), "expected auction snapshots in replay collector"
