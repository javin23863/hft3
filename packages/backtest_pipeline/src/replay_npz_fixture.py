"""Build minimal HftBacktest-compatible NPZ for replay parity tests."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from hftbacktest import BacktestAsset, HashMapMarketDepthBacktest
from hftbacktest.types import (
    ADD_ORDER_EVENT,
    BUY_EVENT,
    EXCH_EVENT,
    LOCAL_EVENT,
    SELL_EVENT,
    event_dtype,
)


def build_minimal_mbo_npz(path: Path, *, n_levels: int = 5) -> Path:
    """Write a tiny two-sided book NPZ that HftBacktest can replay."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    ts = 1_000_000_000
    tick = 0.25
    mid = 5000.0

    for i in range(n_levels):
        rows.append(
            (
                ADD_ORDER_EVENT | BUY_EVENT | EXCH_EVENT | LOCAL_EVENT,
                ts,
                ts,
                mid - tick * (i + 1),
                10.0,
                1000 + i,
                0,
                0.0,
            )
        )
        rows.append(
            (
                ADD_ORDER_EVENT | SELL_EVENT | EXCH_EVENT | LOCAL_EVENT,
                ts,
                ts,
                mid + tick * (i + 1),
                10.0,
                2000 + i,
                0,
                0.0,
            )
        )
        ts += 1_000_000

    data = np.array(rows, dtype=event_dtype)
    np.savez_compressed(path, data=data)

    # This fixture uses ADD_ORDER_EVENT (L3/MBO) rows — validate with the L3
    # queue model so the book forms correctly (L2 log_prob_queue_model2 ignores
    # ADD_ORDER_EVENT and produces a permanently-empty book).
    asset = BacktestAsset()
    asset.data(str(path))
    asset.tick_size(tick)
    asset.lot_size(1.0)
    from backtest_pipeline.src.hft_backtest_builder import _apply_constant_latency
    _apply_constant_latency(asset, 1_000_000, 1_000_000)
    asset.no_partial_fill_exchange()
    asset.l3_fifo_queue_model()
    hbt = HashMapMarketDepthBacktest([asset])
    for _ in range(100):
        if hbt.elapse(100_000) == 1:
            break
        d = hbt.depth(0)
        if d.best_bid > 0 and d.best_ask > 0:
            break
    else:
        raise RuntimeError("minimal NPZ did not produce a book")

    return path
