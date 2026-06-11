"""Per-hour L2 microstructure aggregates from Binance depthUpdate NDJSON diffs.

Column-name contract feeds normalize.build_spot_perp_ticks seam:
  bid_ask_spread / depth_btc / order_imbalance match normalize.py:141-143 names.
depth_btc unit = sum of top-10 level base-asset quantities both sides.
"""
from __future__ import annotations

import json
from pathlib import Path
from collections import defaultdict

import polars as pl

# Book reconstructed from diffs alone is incomplete until enough updates
# accumulate; intervals before warm-up are discarded.
WARMUP_SECONDS = 60

# Depth/imbalance computed over top-10 price levels per side.
TOP_N_LEVELS = 10

_EMPTY_SCHEMA = {
    "exchange_timestamp": pl.Int64,
    "bid_ask_spread": pl.Float64,
    "depth_btc": pl.Float64,
    "order_imbalance": pl.Float64,
    "l2_sample_count": pl.Int64,
    "l2_crossed_skips": pl.Int64,
}


def _apply_diff(book_side: dict[float, float], levels: list) -> None:
    """qty==0 removes level, else sets."""
    for price_str, qty_str in levels:
        price = float(price_str)
        qty = float(qty_str)
        if qty == 0.0:
            book_side.pop(price, None)
        else:
            book_side[price] = qty


def aggregate_l2_ndjson(
    paths: list[Path],
    symbol: str,
    *,
    bucket_ms: int = 3_600_000,
) -> pl.DataFrame:
    """Aggregate depthUpdate diffs into per-bucket microstructure metrics.

    Parameters
    ----------
    paths:
        NDJSON files, processed sorted by name.
    symbol:
        Filter to this symbol (case-insensitive).
    bucket_ms:
        Bucket width in milliseconds (default 1 hour).

    Returns
    -------
    pl.DataFrame with columns: exchange_timestamp (Int64, bucket start ms),
    bid_ask_spread (Float64, mean), depth_btc (Float64, mean),
    order_imbalance (Float64, mean), l2_sample_count (Int64),
    l2_crossed_skips (Int64).
    """
    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    first_ts: int | None = None
    sym_upper = symbol.upper()

    # bucket_key -> {spread_sum, depth_sum, imbalance_sum, count, crossed}
    buckets: dict[int, dict] = defaultdict(
        lambda: {"spread_sum": 0.0, "depth_sum": 0.0, "imbalance_sum": 0.0, "count": 0, "crossed": 0}
    )

    for path in sorted(paths):
        if not path.exists():
            continue
        if path.stat().st_size < 64:
            continue
        # Reset book and warmup anchor per file: each recorded file is an
        # independent Binance stream session whose update-id sequence starts
        # fresh; levels from a prior session are phantoms in the new one.
        bids = {}
        asks = {}
        first_ts = None
        with path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    msg = json.loads(raw_line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if msg.get("e") != "depthUpdate":
                    continue
                if msg.get("s", "").upper() != sym_upper:
                    continue

                ts_e: int = int(msg["E"])
                if first_ts is None:
                    first_ts = ts_e

                _apply_diff(bids, msg.get("b", []))
                _apply_diff(asks, msg.get("a", []))

                # Only sample after warm-up and with a valid book.
                if ts_e - first_ts < WARMUP_SECONDS * 1000:
                    continue
                if not bids or not asks:
                    continue

                best_bid = max(bids)
                best_ask = min(asks)

                bucket_key = ts_e // bucket_ms * bucket_ms
                b = buckets[bucket_key]

                if best_bid >= best_ask:
                    b["crossed"] += 1
                    continue

                spread = best_ask - best_bid

                top_bid_qty = sum(
                    qty for _, qty in sorted(bids.items(), reverse=True)[:TOP_N_LEVELS]
                )
                top_ask_qty = sum(
                    qty for _, qty in sorted(asks.items())[:TOP_N_LEVELS]
                )
                depth = top_bid_qty + top_ask_qty
                denom = top_bid_qty + top_ask_qty
                imbalance = (top_bid_qty - top_ask_qty) / denom if denom != 0.0 else 0.0

                b["spread_sum"] += spread
                b["depth_sum"] += depth
                b["imbalance_sum"] += imbalance
                b["count"] += 1

    if not buckets:
        return pl.DataFrame(schema=_EMPTY_SCHEMA)

    rows = []
    for bk, b in sorted(buckets.items()):
        n = b["count"]
        if n == 0:
            continue  # bucket had only crossed/warmup messages; exclude from output
        rows.append({
            "exchange_timestamp": bk,
            "bid_ask_spread": b["spread_sum"] / n,
            "depth_btc": b["depth_sum"] / n,
            "order_imbalance": b["imbalance_sum"] / n,
            "l2_sample_count": n,
            "l2_crossed_skips": b["crossed"],
        })

    return pl.DataFrame(rows, schema=_EMPTY_SCHEMA)


def load_l2_aggregate(
    raw_dir: Path,
    symbol: str = "BTCUSDT",
    *,
    bucket_ms: int = 3_600_000,
) -> pl.DataFrame:
    """Glob raw NDJSON files and return per-bucket aggregates.

    Globs both upper- and lower-case symbol variants (filenames on disk use
    both). Never raises on absent dir; returns empty schema frame.
    """
    if not raw_dir.exists():
        return pl.DataFrame(schema=_EMPTY_SCHEMA)

    sym_lower = symbol.lower()
    sym_upper = symbol.upper()
    paths: list[Path] = list(raw_dir.glob(f"binance_l2_*{sym_lower}*.ndjson"))
    if sym_upper != sym_lower:
        paths += list(raw_dir.glob(f"binance_l2_*{sym_upper}*.ndjson"))
    paths = list({p.resolve(): p for p in paths}.values())

    return aggregate_l2_ndjson(paths, symbol, bucket_ms=bucket_ms)
