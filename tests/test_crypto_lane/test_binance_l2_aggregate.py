"""Tests for binance_l2_aggregate: per-hour L2 microstructure from diff NDJSON."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crypto_lane.src.ingest.binance_l2_aggregate import (
    aggregate_l2_ndjson,
    load_l2_aggregate,
    WARMUP_SECONDS,
    _EMPTY_SCHEMA,
)
from crypto_lane.src.types import repo_root_from_lane


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_ndjson(path: Path, messages: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(m) for m in messages) + "\n",
        encoding="utf-8",
    )


def _depth_msg(
    ts_ms: int,
    bids: list[tuple[str, str]],
    asks: list[tuple[str, str]],
    symbol: str = "BTCUSDT",
) -> dict:
    return {
        "e": "depthUpdate",
        "E": ts_ms,
        "s": symbol,
        "U": 1,
        "u": 2,
        "b": [[p, q] for p, q in bids],
        "a": [[p, q] for p, q in asks],
        "_local_ts_utc": "2026-06-01T00:00:00Z",
    }


# Base epoch for all tests: 2026-06-01 00:00:00 UTC in ms
_BASE = 1_748_736_000_000
_WARMUP_MS = WARMUP_SECONDS * 1000


# ---------------------------------------------------------------------------
# 1. test_basic_aggregate
# ---------------------------------------------------------------------------

def test_basic_aggregate(tmp_path):
    """Post-warmup messages in one hour bucket → exact spread/depth/imbalance."""
    t0 = _BASE
    # Two post-warmup messages, same bucket.
    # msg A: bid=50000, ask=50001 → spread=1, bid_qty=1.0, ask_qty=2.0
    msg_a = _depth_msg(t0, [("50000.00", "1.0")], [("50001.00", "2.0")])
    # msg B (after warmup): remove 50001 ask, add 50002 ask → spread=2, bid_qty=1.0, ask_qty=3.0
    t1 = t0 + _WARMUP_MS + 1000
    msg_b = _depth_msg(t1, [("50000.00", "1.0")], [("50001.00", "0.0"), ("50002.00", "3.0")])

    p = tmp_path / "feed.ndjson"
    _write_ndjson(p, [msg_a, msg_b])

    df = aggregate_l2_ndjson([p], "BTCUSDT")
    # msg_a is within warmup (t0 - t0 = 0 < WARMUP_MS); only msg_b sampled
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["l2_sample_count"] == 1
    assert abs(row["bid_ask_spread"] - 2.0) < 1e-9
    assert abs(row["depth_btc"] - 4.0) < 1e-9  # 1.0 + 3.0
    expected_imbalance = (1.0 - 3.0) / (1.0 + 3.0)
    assert abs(row["order_imbalance"] - expected_imbalance) < 1e-9
    assert row["l2_crossed_skips"] == 0


# ---------------------------------------------------------------------------
# 2. test_warmup_discarded
# ---------------------------------------------------------------------------

def test_warmup_discarded(tmp_path):
    """Messages within first 60 s yield no samples; later messages do."""
    t0 = _BASE
    # Three messages within warmup window
    msgs_in_warmup = [
        _depth_msg(t0 + i * 1000, [("50000.00", "1.0")], [("50001.00", "1.0")])
        for i in range(3)
    ]
    # One message after warmup
    t_post = t0 + _WARMUP_MS + 5000
    msg_post = _depth_msg(t_post, [("50000.00", "1.0")], [("50002.00", "1.0")])

    p = tmp_path / "feed.ndjson"
    _write_ndjson(p, msgs_in_warmup + [msg_post])

    df = aggregate_l2_ndjson([p], "BTCUSDT")
    assert df.height == 1
    assert df["l2_sample_count"][0] == 1


# ---------------------------------------------------------------------------
# 3. test_crossed_book_skipped
# ---------------------------------------------------------------------------

def test_crossed_book_skipped(tmp_path):
    """best_bid >= best_ask → not sampled, l2_crossed_skips increments."""
    t0 = _BASE
    # Seed the book crossed (bid 50001 >= ask 50000) within warmup so not sampled yet
    seed = _depth_msg(t0, [("50001.00", "1.0")], [("50000.00", "1.0")])
    # Post-warmup crossed: bid=50001 >= ask=50000
    t_post = t0 + _WARMUP_MS + 1000
    crossed = _depth_msg(t_post, [("50001.00", "1.0")], [("50000.00", "1.0")])
    # Post-warmup valid: remove crossing levels, leave 49999 bid / 50001 ask
    t_valid = t_post + 1000
    valid = _depth_msg(t_valid, [("50001.00", "0"), ("49999.00", "1.0")], [("50000.00", "0"), ("50001.00", "1.0")])

    p = tmp_path / "feed.ndjson"
    _write_ndjson(p, [seed, crossed, valid])

    df = aggregate_l2_ndjson([p], "BTCUSDT")
    assert df.height == 1
    assert df["l2_sample_count"][0] == 1
    assert df["l2_crossed_skips"][0] == 1


# ---------------------------------------------------------------------------
# 4. test_qty_zero_removes_level
# ---------------------------------------------------------------------------

def test_qty_zero_removes_level(tmp_path):
    """qty==0 in diff removes the price level from the book."""
    t0 = _BASE
    # Seed: two bid levels
    seed = _depth_msg(t0, [("50000.00", "2.0"), ("49999.00", "1.0")], [("50001.00", "1.0")])
    # Remove the top bid level
    t1 = t0 + 500
    remove = _depth_msg(t1, [("50000.00", "0.0")], [])
    # Post-warmup: only 49999 remains as best bid
    t_post = t0 + _WARMUP_MS + 1000
    check = _depth_msg(t_post, [], [])

    p = tmp_path / "feed.ndjson"
    _write_ndjson(p, [seed, remove, check])

    df = aggregate_l2_ndjson([p], "BTCUSDT")
    assert df.height == 1
    row = df.row(0, named=True)
    # best_bid should now be 49999, best_ask 50001
    assert abs(row["bid_ask_spread"] - 2.0) < 1e-9


# ---------------------------------------------------------------------------
# 5. test_empty_and_garbage_files
# ---------------------------------------------------------------------------

def test_empty_and_garbage_files(tmp_path):
    """Empty file + garbage lines tolerated; returns empty schema frame."""
    empty = tmp_path / "empty.ndjson"
    empty.write_bytes(b"")

    garbage = tmp_path / "garbage.ndjson"
    garbage.write_text("not json\n{bad\n\x00\n", encoding="utf-8")

    df = aggregate_l2_ndjson([empty, garbage], "BTCUSDT")
    assert df.height == 0
    for col, dtype in _EMPTY_SCHEMA.items():
        assert col in df.columns
        assert df[col].dtype == dtype


# ---------------------------------------------------------------------------
# 6. test_multiple_buckets
# ---------------------------------------------------------------------------

def test_multiple_buckets(tmp_path):
    """Messages spanning two hour buckets → two rows, correct bucket keys."""
    bucket_ms = 3_600_000
    t0 = _BASE  # start of bucket 0
    t1 = _BASE + bucket_ms  # start of bucket 1

    # Seed book in bucket 0 (within warmup, no sample)
    seed = _depth_msg(t0, [("50000.00", "1.0")], [("50001.00", "1.0")])
    # Post-warmup message in bucket 0
    t_a = t0 + _WARMUP_MS + 1000
    msg_a = _depth_msg(t_a, [("50000.00", "1.0")], [("50001.00", "1.0")])
    # Message in bucket 1 (still post-warmup from t0); remove 50001 ask explicitly
    t_b = t1 + 500
    msg_b = _depth_msg(t_b, [("50000.00", "1.0")], [("50001.00", "0.0"), ("50002.00", "1.0")])

    p = tmp_path / "feed.ndjson"
    _write_ndjson(p, [seed, msg_a, msg_b])

    df = aggregate_l2_ndjson([p], "BTCUSDT")
    assert df.height == 2
    keys = df["exchange_timestamp"].to_list()
    assert t0 in keys
    assert t1 in keys
    row0 = df.filter(pl.col("exchange_timestamp") == t0).row(0, named=True)
    row1 = df.filter(pl.col("exchange_timestamp") == t1).row(0, named=True)
    assert abs(row0["bid_ask_spread"] - 1.0) < 1e-9
    assert abs(row1["bid_ask_spread"] - 2.0) < 1e-9


# polars needed for the filter above
import polars as pl  # noqa: E402


# ---------------------------------------------------------------------------
# 8. test_book_resets_per_file
# ---------------------------------------------------------------------------

def test_book_resets_per_file(tmp_path):
    """Book and warmup anchor reset at each file boundary.

    file1 seeds a deep ask level (50010); file2 is a fresh session that never
    touches that level.  Depth in file2's bucket must NOT include the phantom
    50010 level from file1.
    """
    # file1: seed a level at 50010 ask (within warmup, no sample)
    t0 = _BASE
    seed = _depth_msg(t0, [("50000.00", "1.0")], [("50001.00", "1.0"), ("50010.00", "5.0")])
    p1 = tmp_path / "a_feed1.ndjson"
    _write_ndjson(p1, [seed])

    # file2: independent session — warmup from its own t0, then one sample
    t2_start = _BASE + 10_000  # distinct epoch
    seed2 = _depth_msg(t2_start, [("50000.00", "1.0")], [("50001.00", "1.0")])
    t2_post = t2_start + _WARMUP_MS + 1000
    sample2 = _depth_msg(t2_post, [("50000.00", "2.0")], [("50001.00", "2.0")])
    p2 = tmp_path / "b_feed2.ndjson"
    _write_ndjson(p2, [seed2, sample2])

    df = aggregate_l2_ndjson([p1, p2], "BTCUSDT")
    # Only file2 produces a sample row (file1 has no post-warmup messages)
    assert df.height == 1
    row = df.row(0, named=True)
    # depth = top-10 both sides; file2 book has only one level each side: 2.0+2.0=4.0
    assert abs(row["depth_btc"] - 4.0) < 1e-9


# ---------------------------------------------------------------------------
# 9. test_zero_count_bucket_excluded
# ---------------------------------------------------------------------------

def test_zero_count_bucket_excluded(tmp_path):
    """Bucket containing only crossed-book messages emits no row."""
    t0 = _BASE
    # Seed an inverted book within warmup
    seed = _depth_msg(t0, [("50001.00", "1.0")], [("50000.00", "1.0")])
    # Post-warmup: still crossed (bid=50001 >= ask=50000) — only crossed skip counted
    t_crossed = t0 + _WARMUP_MS + 1000
    crossed = _depth_msg(t_crossed, [("50001.00", "1.0")], [("50000.00", "1.0")])

    p = tmp_path / "feed.ndjson"
    _write_ndjson(p, [seed, crossed])

    df = aggregate_l2_ndjson([p], "BTCUSDT")
    assert df.height == 0


# ---------------------------------------------------------------------------
# 7. test_real_capture_smoke
# ---------------------------------------------------------------------------

_REAL_FILE = (
    repo_root_from_lane()
    / "data"
    / "crypto"
    / "binance_l2_raw"
    / "binance_l2_btcusdt_20260601_074847.ndjson"
)


@pytest.mark.skipif(
    not _REAL_FILE.exists(),
    reason="Real capture file absent: data/crypto/binance_l2_raw/binance_l2_btcusdt_20260601_074847.ndjson",
)
def test_real_capture_smoke():
    raw_dir = _REAL_FILE.parent
    df = load_l2_aggregate(raw_dir, symbol="BTCUSDT")
    assert df.height >= 1, "Expected at least one row from real capture"
    assert float(df["bid_ask_spread"].max()) > 0.0
    assert float(df["depth_btc"].max()) > 0.0
    assert int(df["l2_sample_count"].sum()) > 0
