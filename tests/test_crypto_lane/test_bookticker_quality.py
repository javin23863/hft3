"""Bookticker gold quality classification."""
from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl

from crypto_lane.src.ingest.bookticker_quality import classify_bookticker_file


def test_classify_synthetic_by_source_column(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.parquet"
    pl.DataFrame(
        {
            "best_bid_px": [100.0],
            "best_ask_px": [100.1],
            "source": ["coinstats_exchange_price"],
        }
    ).write_parquet(path)
    assert classify_bookticker_file(path) == "synthetic"


def test_classify_real_by_row_count(tmp_path):
    path = tmp_path / "real.parquet"
    n = 1500
    pl.DataFrame(
        {
            "update_id": list(range(n)),
            "best_bid_px": [100.0] * n,
            "best_bid_qty": [1.0] * n,
            "best_ask_px": [100.1] * n,
            "best_ask_qty": [1.0] * n,
            "event_ts_ms": [1_700_000_000_000 + i for i in range(n)],
            "timestamp": [
                datetime.fromtimestamp(1_700_000_000 + i / 1000, tz=timezone.utc)
                for i in range(n)
            ],
            "symbol": ["BTCUSDT"] * n,
        }
    ).write_parquet(path)
    assert classify_bookticker_file(path) == "b2_real"
