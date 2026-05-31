"""Point-in-time availability boundary for BTC node ↔ exchange joins."""
from __future__ import annotations

from typing import Any

import polars as pl

from crypto_lane.src.types import PitConfig


class StructuralDataLeakageError(RuntimeError):
    """Raised when node data would be available after exchange decision time."""


def apply_pit_alignment(
    df: pl.DataFrame,
    *,
    max_staleness_ms: int = 15000,
    strict: bool = False,
) -> pl.DataFrame:
    """
    Enforce T_avail <= T_exch_true and staleness window.

    Expects columns (ms or ns — auto-detected by name suffix):
    node_observation_time, node_clock_drift_ms, network_latency_ms,
    processing_latency_ms, exchange_timestamp, exchange_clock_drift_ms
    """
    out = df.with_columns([
        (
            pl.col("node_observation_time")
            + pl.col("node_clock_drift_ms")
            + pl.col("network_latency_ms")
            + pl.col("processing_latency_ms")
        ).alias("T_avail"),
        (pl.col("exchange_timestamp") + pl.col("exchange_clock_drift_ms")).alias("T_exch_true"),
    ]).with_columns([
        (pl.col("T_avail") <= pl.col("T_exch_true")).alias("is_pit_safe"),
        (pl.col("T_exch_true") - pl.col("T_avail")).alias("staleness_delta_ms"),
    ]).with_columns([
        pl.when(
            (pl.col("is_pit_safe") == True) & (pl.col("staleness_delta_ms") <= max_staleness_ms)
        )
        .then(pl.lit(1))
        .otherwise(pl.lit(0))
        .alias("btc_node_data_available_flag"),
    ])

    if strict:
        leaks = out.filter(pl.col("is_pit_safe") == False)
        if leaks.height > 0:
            raise StructuralDataLeakageError(
                f"{leaks.height} rows violate T_avail <= T_exch_true"
            )
    return out


def backward_join_node_to_exchange(
    exchange_df: pl.DataFrame,
    node_df: pl.DataFrame,
    *,
    exch_ts_col: str = "exchange_timestamp",
    node_ts_col: str = "node_observation_time",
    config: PitConfig | None = None,
) -> pl.DataFrame:
    """As-of backward join: latest node row with node_ts <= exch_ts."""
    cfg = config or PitConfig()
    node_sorted = node_df.sort(node_ts_col).with_columns(
        pl.col(node_ts_col).cast(pl.Int64)
    )
    exch_sorted = exchange_df.sort(exch_ts_col).with_columns(
        pl.col(exch_ts_col).cast(pl.Int64)
    )
    joined = exch_sorted.join_asof(
        node_sorted,
        left_on=exch_ts_col,
        right_on=node_ts_col,
        strategy="backward",
    )
    for col, default in (
        ("node_clock_drift_ms", 0.0),
        ("network_latency_ms", cfg.network_latency_ms),
        ("processing_latency_ms", cfg.processing_latency_ms),
        ("exchange_clock_drift_ms", 0.0),
    ):
        if col not in joined.columns:
            joined = joined.with_columns(pl.lit(default).alias(col))
    return apply_pit_alignment(
        joined,
        max_staleness_ms=cfg.max_staleness_ms,
        strict=cfg.strict,
    )


def pit_config_from_dict(d: dict[str, Any] | None) -> PitConfig:
    if not d:
        return PitConfig()
    return PitConfig(
        max_staleness_ms=int(d.get("max_staleness_ms", 15000)),
        strict=bool(d.get("strict", False)),
        network_latency_ms=float(d.get("network_latency_ms", 0.0)),
        processing_latency_ms=float(d.get("processing_latency_ms", 0.0)),
    )
