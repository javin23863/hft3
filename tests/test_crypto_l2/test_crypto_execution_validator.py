"""Tests for crypto execution validator — verifies replay + metrics computation.

Replay tests require real NPZ data in the routing-expected directories.
Without real data, replay tests are skipped (honest about no synthetic data).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "packages"))

from crypto_lane.src.validation.crypto_execution_validator import (
    CryptoExecutionResult,
    CryptoReplayStrategy,
    compute_crypto_metrics,
    run_crypto_replay,
)
from backtest_pipeline.src.crypto_hft_builder import build_binance_hftbacktest, build_kraken_hftbacktest


def _real_binance_npz() -> bool:
    return any(
        (_REPO / "data" / "replay" / "hftbacktest" / "crypto" / "binance" / sym).glob("*.npz")
        for sym in ("btcusdt", "ethusdt", "solusdt")
    )


def _real_kraken_npz() -> bool:
    return any(
        (_REPO / "data" / "replay" / "hftbacktest" / "crypto" / "kraken" / sym).glob("*.npz")
        for sym in ("BTC_USD", "ETH_USD", "SOL_USD")
    )


def test_crypto_replay_strategy_returns_intent():
    """CryptoReplayStrategy produces OrderIntent when signal exceeds threshold."""
    from replay.replay_session import ReplayStepContext
    from replay.replay_clock import ReplayClock
    from unittest.mock import MagicMock

    strategy = CryptoReplayStrategy(
        signal_threshold=0.1,
        base_quantity=1.0,
        model_id="CRYPTO_H5",
        is_perp=True,
    )
    getter = lambda: 0.5
    strategy.set_signal_getter(getter)

    clock = ReplayClock()
    ctx = ReplayStepContext(
        run_id="test",
        clock=clock,
        market_state=None,
        best_bid=50000.0,
        best_ask=50001.0,
        position=0.0,
        order_events=[],
        execution=MagicMock(),
        symbol="BTCUSDT",
    )
    actions = strategy.on_step(ctx)
    assert len(actions) == 1
    assert actions[0].side == "BUY"
    assert actions[0].price == 50000.0
    assert actions[0].symbol == "BTCUSDT"


def test_crypto_replay_strategy_skips_below_threshold():
    """CryptoReplayStrategy returns empty list when signal is below threshold."""
    from replay.replay_session import ReplayStepContext
    from replay.replay_clock import ReplayClock
    from unittest.mock import MagicMock

    strategy = CryptoReplayStrategy(signal_threshold=0.1)
    strategy.set_signal_getter(lambda: 0.05)
    ctx = ReplayStepContext(
        run_id="test", clock=ReplayClock(), market_state=None,
        best_bid=50000.0, best_ask=50001.0, position=0.0,
        order_events=[], execution=MagicMock(), symbol="BTCUSDT",
    )
    assert strategy.on_step(ctx) == []


@pytest.mark.skipif(not _real_binance_npz(), reason="No real Binance L2 NPZ data. Run: python -m crypto_lane.pipeline convert-l2 <ndjson> --routing-symbol btcusdt")
def test_run_crypto_replay_with_binance_l2():
    """run_crypto_replay executes against real Binance L2 NPZ data."""
    os.environ["EXECUTION_MODE"] = "REPLAY"
    npz_dir = _REPO / "data" / "replay" / "hftbacktest" / "crypto" / "binance" / "btcusdt"
    npz_files = sorted(npz_dir.glob("*.npz"))
    if not npz_files:
        pytest.skip("No NPZ files found")
    npz_file = str(npz_files[0])
    symbol = "BTCUSDT"

    hbt = build_binance_hftbacktest(npz_file, symbol=symbol, latency_ms=50.0)
    strategy = CryptoReplayStrategy(
        signal_threshold=0.1,
        base_quantity=1.0,
        model_id="CRYPTO_H5",
        is_perp=True,
    )
    strategy.set_signal_getter(lambda: 0.5)

    result = run_crypto_replay(
        hbt=hbt,
        strategy=strategy,
        npz_path=npz_file,
        symbol=symbol,
        tick_size=0.1,
        latency_ms=50.0,
        queue_model="SquareProbQueueModel",
        max_steps=2000,
    )
    assert "error" not in result or not result.get("error")


@pytest.mark.skipif(not _real_kraken_npz(), reason="No real Kraken L3 NPZ data. Run: python -m crypto_lane.pipeline convert-l3 <ndjson> --routing-symbol BTC/USD")
def test_run_crypto_replay_with_kraken_l3():
    """run_crypto_replay executes against real Kraken L3 NPZ data."""
    os.environ["EXECUTION_MODE"] = "REPLAY"
    npz_dir = _REPO / "data" / "replay" / "hftbacktest" / "crypto" / "kraken" / "BTC_USD"
    npz_files = sorted(npz_dir.glob("*.npz"))
    if not npz_files:
        pytest.skip("No NPZ files found")
    npz_file = str(npz_files[0])
    symbol = "BTC/USD"

    hbt = build_kraken_hftbacktest(npz_file, symbol=symbol, latency_ms=50.0)
    strategy = CryptoReplayStrategy(
        signal_threshold=0.1,
        base_quantity=1.0,
        model_id="CRYPTO_H5",
        is_perp=False,
    )
    strategy.set_signal_getter(lambda: -0.5)

    result = run_crypto_replay(
        hbt=hbt,
        strategy=strategy,
        npz_path=npz_file,
        symbol=symbol,
        tick_size=0.1,
        latency_ms=50.0,
        queue_model="LogProbQueueModel2",
        max_steps=2000,
    )
    assert "error" not in result or not result.get("error")


def test_compute_crypto_metrics_returns_defaults_for_empty_run():
    """compute_crypto_metrics returns default/zero metrics for an empty run."""
    result = compute_crypto_metrics(
        {"balance": 0.0, "fee": 0.0, "num_trades": 0, "order_intent_count": 0, "fill_events": []},
        "L2_PROXY_ONLY",
    )
    assert isinstance(result, CryptoExecutionResult)
    assert result.net_pnl == 0.0
    assert result.num_trades == 0
    assert result.fill_rate == 0.0
    assert result.execution_classification == "L2_PROXY_ONLY"
    assert result.error == ""


def test_compute_crypto_metrics_with_simulated_fills():
    """compute_crypto_metrics computes realistic metrics from fill events."""
    fill_events = [
        {"event_type": "ORDER_FILLED", "side": "BUY", "filled_quantity": 1.0, "avg_fill_price": 50000.0},
        {"event_type": "ORDER_FILLED", "side": "SELL", "filled_quantity": 1.0, "avg_fill_price": 50010.0},
        {"event_type": "ORDER_FILLED", "side": "BUY", "filled_quantity": 1.0, "avg_fill_price": 50005.0},
        {"event_type": "ORDER_FILLED", "side": "SELL", "filled_quantity": 1.0, "avg_fill_price": 50020.0},
    ]
    result = compute_crypto_metrics(
        {"balance": 25.0, "fee": 0.0, "num_trades": 4, "order_intent_count": 4, "fill_events": fill_events},
        "L3_VALIDATED",
    )
    assert result.net_pnl == 25.0
    assert result.num_trades == 4
    assert result.fill_rate == 1.0
    assert result.execution_classification == "L3_VALIDATED"
    assert result.gross_pnl > 0
