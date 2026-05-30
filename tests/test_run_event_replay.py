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
        _REPO / "data_system" / "config" / "events.csv",
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


def test_run_event_accurate_mbo_smoke():
    npz = _REPO / "data" / "npz" / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    if not npz.is_file():
        pytest.skip("CPI NPZ not present locally")

    import importlib.util

    script = _REPO / "scripts" / "run_event_replay.py"
    spec = importlib.util.spec_from_file_location("run_event_replay", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    from features_engine.src.features.npz_feed import load_npz_events

    raw = load_npz_events(str(npz))
    result = mod.run_event_accurate_mbo(raw, latency_ms=4.094)
    assert result["engine"] == "event_accurate_mbo"
    assert result["hypothesis_count"] >= 30
    assert result["total_trades_all_hypotheses"] > 0
    assert result["hyp_5_spread_blowout"]["num_trades"] > 0
