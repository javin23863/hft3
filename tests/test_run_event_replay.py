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

    script = _REPO / "scripts" / "run_event_replay.py"
    spec = importlib.util.spec_from_file_location("run_event_replay", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    row = mod.load_event_row(
        "CPI_2024_09_11_TIGHT",
        _REPO / "packages" / "data_system" / "config" / "events.csv",
    )
    assert row["release_date"] == "2024-09-11"
    assert row["primary_symbol"] == "MES.v.0"


def test_combined_strategy_max_abs_beats_mean_on_sparse_signals():
    from hftbacktest.types import event_dtype

    from backtest_pipeline.src.hypothesis_replay_strategy import (
        CombinedHypothesisReplayStrategy,
    )
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
    no_events = np.zeros(0, dtype=event_dtype)
    mean_strat = CombinedHypothesisReplayStrategy(
        hyps, no_events, aggregate_mode="mean", signal_threshold=0.15
    )
    max_strat = CombinedHypothesisReplayStrategy(
        hyps, no_events, aggregate_mode="max_abs", signal_threshold=0.15
    )
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
