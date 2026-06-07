"""Bookticker + B2 probe cache invalidation."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import polars as pl

from crypto_lane.src.ingest import bookticker_quality as bq
from crypto_lane.src.ingest.b2_synthetic_probe_cache import (
    load_cached_b2_synthetic_probe,
    save_cached_b2_synthetic_probe,
)


def test_purge_invalidates_b2_synthetic_probe_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(bq, "repo_root_from_lane", lambda: tmp_path)
    monkeypatch.setattr(
        "crypto_lane.src.ingest.b2_synthetic_probe_cache.repo_root_from_lane",
        lambda: tmp_path,
    )
    days = ["2024-04-02"]
    save_cached_b2_synthetic_probe(days, {"available_count": 0, "missing_count": 1})
    path = tmp_path / "2024-04-02.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"best_bid_px": [1.0], "best_ask_px": [1.1], "source": ["coinstats_exchange_price"]}
    ).write_parquet(path)

    monkeypatch.setattr(bq, "bookticker_dest", lambda day, symbol=None: path)
    monkeypatch.setattr(bq, "_parse_date", lambda s: date.fromisoformat(s))
    monkeypatch.setattr(bq, "_date_range", lambda a, b: [date(2024, 4, 2)])

    bq.purge_synthetic_bookticker(start="2024-04-02", end="2024-04-02")
    assert load_cached_b2_synthetic_probe(days) is None


def test_invalidate_bookticker_caches_clears_b2_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "crypto_lane.src.ingest.b2_synthetic_probe_cache.repo_root_from_lane",
        lambda: tmp_path,
    )
    days = ["2024-04-02"]
    save_cached_b2_synthetic_probe(days, {"available_count": 0, "missing_count": 1})
    bq.invalidate_bookticker_caches()
    assert load_cached_b2_synthetic_probe(days) is None
