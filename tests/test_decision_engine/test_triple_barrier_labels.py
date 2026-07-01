from __future__ import annotations

import numpy as np
import pytest

from decision_engine.python.src.targets import (
    build_meta_labels,
    build_triple_barrier_labels,
)

TICK = 0.25
MS = 1_000_000


def _ts(n: int, step_ms: int = 100) -> np.ndarray:
    return (np.arange(n, dtype=np.int64) * step_ms * MS) + 1_000_000_000


def test_profit_barrier_hit_long() -> None:
    # Mid rises 4 ticks by index 2; pt at 2 ticks fires there first.
    mid = np.array([100.0, 100.25, 101.0, 101.5])
    frame = build_triple_barrier_labels(
        mid, _ts(4), pt_ticks=2.0, sl_ticks=2.0, max_holding_ms=10_000, tick_size=TICK
    )
    assert frame.loc[0, "y_tb_outcome"] == 1.0
    assert frame.loc[0, "y_tb_exit_idx"] == 2
    assert frame.loc[0, "y_tb_return_ticks"] == 4.0


def test_stop_barrier_hit_short_side() -> None:
    # Short side: rising price is adverse; sl at 2 ticks fires at index 2.
    mid = np.array([100.0, 100.25, 100.75, 100.0])
    sides = np.array([-1.0, -1.0, -1.0, -1.0])
    frame = build_triple_barrier_labels(
        mid, _ts(4), pt_ticks=2.0, sl_ticks=2.0, max_holding_ms=10_000, tick_size=TICK, sides=sides
    )
    assert frame.loc[0, "y_tb_outcome"] == -1.0
    assert frame.loc[0, "y_tb_return_ticks"] == -3.0


def test_vertical_barrier_timeout_exits_at_horizon_row() -> None:
    # Flat path never touches barriers; 300ms max holding exits at the first
    # observation at/after the horizon (the vertical-barrier row itself).
    mid = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
    frame = build_triple_barrier_labels(
        mid, _ts(5), pt_ticks=2.0, sl_ticks=2.0, max_holding_ms=300, tick_size=TICK
    )
    assert frame.loc[0, "y_tb_outcome"] == 0.0
    assert frame.loc[0, "y_tb_exit_idx"] == 3
    assert frame.loc[0, "y_tb_holding_ms"] == 300.0


def test_barrier_crossed_exactly_at_horizon_row_is_a_hit() -> None:
    # Profit barrier first crossed AT the vertical-barrier timestamp counts
    # as a profit hit, not a timeout at the previous tick.
    mid = np.array([100.0, 100.0, 100.0, 101.0, 100.0])
    frame = build_triple_barrier_labels(
        mid, _ts(5), pt_ticks=2.0, sl_ticks=2.0, max_holding_ms=300, tick_size=TICK
    )
    assert frame.loc[0, "y_tb_outcome"] == 1.0
    assert frame.loc[0, "y_tb_exit_idx"] == 3
    assert frame.loc[0, "y_tb_return_ticks"] == 4.0


def test_censored_horizon_without_barrier_hit_is_nan() -> None:
    # Horizon extends beyond recorded data and no barrier hit: the timeout
    # outcome is unknowable — NaN, never an invented early exit.
    mid = np.array([100.0, 100.1, 100.1])
    frame = build_triple_barrier_labels(
        mid, _ts(3), pt_ticks=2.0, sl_ticks=2.0, max_holding_ms=10_000, tick_size=TICK
    )
    assert np.isnan(frame.loc[0, "y_tb_outcome"])
    assert frame.loc[0, "y_tb_exit_idx"] == -1


def test_tail_rows_have_nan_outcome() -> None:
    mid = np.array([100.0, 100.5])
    frame = build_triple_barrier_labels(
        mid, _ts(2), pt_ticks=1.0, sl_ticks=1.0, max_holding_ms=10_000, tick_size=TICK
    )
    assert np.isnan(frame.loc[1, "y_tb_outcome"])
    assert frame.loc[1, "y_tb_exit_idx"] == -1


def test_stop_wins_tie_against_profit() -> None:
    # Conservative tie-break: if the same observation crosses both barriers
    # (gap through), the stop is recorded.
    mid = np.array([100.0, 90.0, 110.0])
    frame = build_triple_barrier_labels(
        mid, _ts(3), pt_ticks=2.0, sl_ticks=2.0, max_holding_ms=10_000, tick_size=TICK
    )
    assert frame.loc[0, "y_tb_outcome"] == -1.0


def test_invalid_params_fail_loud() -> None:
    mid = np.array([100.0, 100.0])
    with pytest.raises(ValueError):
        build_triple_barrier_labels(mid, _ts(2), pt_ticks=0.0, sl_ticks=1.0, max_holding_ms=100)
    with pytest.raises(ValueError):
        build_triple_barrier_labels(
            mid, _ts(2), pt_ticks=1.0, sl_ticks=1.0, max_holding_ms=100, sides=np.array([2.0, 1.0])
        )
    with pytest.raises(ValueError, match="monotonic"):
        build_triple_barrier_labels(
            mid, np.array([2_000_000_000, 1_000_000_000]), pt_ticks=1.0, sl_ticks=1.0, max_holding_ms=100
        )


def test_meta_labels_gate_primary_signal() -> None:
    signal = np.array([1.0, -0.5, 0.0, 1.0])
    outcome = np.array([1.0, -1.0, 1.0, np.nan])
    meta = build_meta_labels(signal, outcome)
    assert meta[0] == 1.0  # long signal, profit barrier -> take
    assert meta[1] == 0.0  # short signal, stop barrier -> skip
    assert np.isnan(meta[2])  # no signal fired
    assert np.isnan(meta[3])  # unknown barrier outcome


def _ensure_hftbacktest_stub() -> None:
    """Minimal guarded stub: train.py's import chain binds hftbacktest.types
    constants at import time; no-op when the real package is importable."""
    import importlib.util
    import sys
    from types import ModuleType

    if "hftbacktest" in sys.modules or importlib.util.find_spec("hftbacktest") is not None:
        return
    fake_types = ModuleType("hftbacktest.types")
    for name, value in {
        "ADD_ORDER_EVENT": 10,
        "CANCEL_ORDER_EVENT": 11,
        "MODIFY_ORDER_EVENT": 12,
        "FILL_EVENT": 13,
        "TRADE_EVENT": 2,
        "BUY_EVENT": 1 << 11,
        "SELL_EVENT": 1 << 12,
        "DEPTH_EVENT": 1,
        "EXCH_EVENT": 1 << 8,
        "LOCAL_EVENT": 1 << 9,
    }.items():
        setattr(fake_types, name, value)
    fake_pkg = ModuleType("hftbacktest")
    fake_pkg.__path__ = []  # type: ignore[attr-defined]
    fake_pkg.types = fake_types  # type: ignore[attr-defined]
    sys.modules["hftbacktest"] = fake_pkg
    sys.modules["hftbacktest.types"] = fake_types


def test_gbm_trainer_requires_lightgbm_or_trains() -> None:
    _ensure_hftbacktest_stub()

    from decision_engine.python.src.train import _model_predictions, _train_lightgbm

    rng = np.random.default_rng(7)
    X = rng.normal(size=(400, 8))
    y = X[:, 0] * 2.0 + rng.normal(scale=0.1, size=400)
    data = {"X": X, "y_return": y}
    try:
        import lightgbm  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="ml"):
            _train_lightgbm(data)
        return
    model = _train_lightgbm(data)
    assert model["kind"] == "lightgbm"
    pred = _model_predictions(model, X)
    corr = np.corrcoef(pred, y)[0, 1]
    assert corr > 0.8
