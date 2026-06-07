"""Tests for event-driven replay and zero-trades fix."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]


def test_resolve_event_npz_cpi():
    from backtest.adapters.rithmic_replay_loader import resolve_event_npz

    npz = resolve_event_npz("CPI_2024_09_11_TIGHT", _REPO)
    assert npz.name == "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    if not npz.is_file():
        pytest.skip("CPI NPZ not present locally (data/npz gitignored); run download_micro_probe.py")


def test_resolve_event_npz_missing_raises():
    from backtest.adapters.rithmic_replay_loader import resolve_event_npz

    with pytest.raises(ValueError, match="not in events.csv"):
        resolve_event_npz("NO_SUCH_EVENT", _REPO)


def test_load_event_row_from_run_event_replay():
    import importlib.util
    from hft3_bootstrap import data_system_root

    script = _REPO / "scripts" / "run_event_replay.py"
    spec = importlib.util.spec_from_file_location("run_event_replay", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.DEFAULT_EVENTS_CSV == data_system_root(_REPO) / "config" / "events.csv"

    row = mod.load_event_row(
        "CPI_2024_09_11_TIGHT",
        mod.DEFAULT_EVENTS_CSV,
    )
    assert row["release_date"] == "2024-09-11"
    assert row["primary_symbol"] == "MES.v.0"


def test_combined_strategy_max_abs_beats_mean_on_sparse_signals():
    from backtest_pipeline.src.hft_strategy import CombinedHypothesisStrategy
    from features_engine.src.hypotheses.modules import BaseHypothesis, MarketState

    class AlwaysZero(BaseHypothesis):
        def __init__(self):
            super().__init__(99, "zero")

        def evaluate(self, state: MarketState) -> float:
            return 0.0

    class StrongShort(BaseHypothesis):
        def __init__(self):
            super().__init__(5, "strong")

        def evaluate(self, state: MarketState) -> float:
            return -0.5

    hyps = [AlwaysZero(), AlwaysZero(), AlwaysZero(), StrongShort()]
    mean_strat = CombinedHypothesisStrategy(hyps, aggregate_mode="mean", signal_threshold=0.15)
    max_strat = CombinedHypothesisStrategy(hyps, aggregate_mode="max_abs", signal_threshold=0.15)
    state = MarketState(
        primary_features={},
        cross_asset_features={},
        regime_state="NORMAL",
        event_context="NORMAL",
        volatility_state="NORMAL",
        liquidity_state="NORMAL",
        latency_ms=1.0,
        current_inventory=0,
    )
    assert abs(mean_strat._combined_signal(state)) < 0.15
    assert max_strat._combined_signal(state) == -0.5


def test_run_per_hypothesis_replay_smoke(tmp_path):
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz
    from backtest_pipeline.src.replay_matrix import run_hypothesis_replay
    from features_engine.src.hypotheses.modules import BaseHypothesis, MarketState

    class _AlwaysLong(BaseHypothesis):
        def __init__(self):
            super().__init__(1, "always_long")

        def evaluate(self, state: MarketState) -> float:
            return 0.5

    npz = tmp_path / "smoke.npz"
    build_minimal_mbo_npz(npz)
    res = run_hypothesis_replay(_AlwaysLong(), str(npz), latency_ms=1.0, max_steps=300)
    assert res.num_trades >= 0
    if res.num_trades:
        assert len(res.fills) == res.num_trades
        assert all(fill.timestamp_ns > 0 for fill in res.fills)
        assert all(fill.exec_price > 0 for fill in res.fills)


def test_run_hypothesis_replay_fails_when_trades_lack_lifecycle_fills(monkeypatch, tmp_path):
    from backtest_pipeline.src import replay_matrix
    from backtest_pipeline.src.replay_matrix import run_hypothesis_replay
    from features_engine.src.hypotheses.modules import BaseHypothesis, MarketState

    class _AlwaysLong(BaseHypothesis):
        def __init__(self):
            super().__init__(1, "always_long")

        def evaluate(self, state: MarketState) -> float:
            return 0.5

    class _BrokenReplaySession:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            return {
                "balance": 1.0,
                "num_trades": 1,
                "fill_events": [],
                "order_lifecycle_summary": {"filled_count": 0},
            }

    monkeypatch.setattr(replay_matrix, "ReplaySession", _BrokenReplaySession)
    npz = tmp_path / "dummy.npz"
    np.savez_compressed(npz, data=np.array([], dtype=np.int64))

    with pytest.raises(RuntimeError, match="reported trades but emitted no lifecycle fill events"):
        run_hypothesis_replay(_AlwaysLong(), str(npz))


def test_run_hypothesis_replay_caps_surplus_lifecycle_fills(monkeypatch, tmp_path):
    from backtest_pipeline.src import replay_matrix
    from backtest_pipeline.src.replay_matrix import run_hypothesis_replay
    from features_engine.src.hypotheses.modules import BaseHypothesis, MarketState

    class _AlwaysLong(BaseHypothesis):
        def __init__(self):
            super().__init__(1, "always_long")

        def evaluate(self, state: MarketState) -> float:
            return 0.5

    class _ReplaySessionWithAuditOverage:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            return {
                "balance": 2.0,
                "num_trades": 1,
                "fill_events": [
                    {
                        "event_type": "ORDER_FILLED",
                        "timestamp_ns": 100,
                        "side": "BUY",
                        "price": 5000.0,
                        "quantity": 1,
                        "filled_quantity": 1,
                    },
                    {
                        "event_type": "ORDER_FILLED",
                        "timestamp_ns": 200,
                        "side": "BUY",
                        "price": 5001.0,
                        "quantity": 1,
                        "filled_quantity": 1,
                    },
                ],
            }

    monkeypatch.setattr(replay_matrix, "ReplaySession", _ReplaySessionWithAuditOverage)
    npz = tmp_path / "dummy.npz"
    np.savez_compressed(npz, data=np.array([], dtype=np.int64))

    result = run_hypothesis_replay(_AlwaysLong(), str(npz))

    assert result.num_trades == 1
    assert len(result.fills) == 1
    assert result.expectancy == 2.0
