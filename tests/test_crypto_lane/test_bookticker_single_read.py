"""Manifest build must not double-read parquet files."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import polars as pl

from crypto_lane.src.ingest import bookticker_quality as bq


def test_build_quality_manifest_single_parquet_read(tmp_path, monkeypatch):
    path = tmp_path / "2024-01-01.parquet"
    pl.DataFrame(
        {"best_bid_px": [1.0], "best_ask_px": [1.1], "source": ["coinstats_exchange_price"]}
    ).write_parquet(path)

    monkeypatch.setattr(bq, "bookticker_dest", lambda day, symbol=None: path)
    monkeypatch.setattr(bq, "_parse_date", lambda s: date.fromisoformat(s))
    monkeypatch.setattr(bq, "_date_range", lambda a, b: [date(2024, 1, 1)])

    with patch.object(pl, "read_parquet", wraps=pl.read_parquet) as mock_read:
        manifest = bq.build_quality_manifest(start="2024-01-01", end="2024-01-01")
    assert mock_read.call_count == 1
    assert manifest["2024-01-01"]["class"] == "synthetic"
