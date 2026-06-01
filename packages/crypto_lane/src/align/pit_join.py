"""Point-in-time availability boundary for BTC node ↔ exchange joins."""
from __future__ import annotations

from typing import Any

import polars as pl

from crypto_lane.src.types import PitConfig


class StructuralDataLeakageError(RuntimeError):
    """Raised when node data would be available after exchange decision time."""


def _ensure_latency_columns(df: pl.DataFrame, cfg: PitConfig) -> pl.DataFrame:
    out = df
    for col, default in (
        ("node_clock_drift_ms", 0.0),
        ("network_latency_ms", cfg.network_latency_ms),
        ("processing_latency_ms", cfg.processing_latency_ms),
        ("exchange_clock_drift_ms", cfg.exchange_clock_drift_ms),
    ):
        if col not in out.columns:
            out = out.with_columns(pl.lit(default).alias(col))
    return out


def compute_t_avail(df: pl.DataFrame, *, cfg: PitConfig | None = None) -> pl.DataFrame:
    """T_avail = T_node_obs - θ_node + δ_net + δ_proc (NTP: θ = node - local; subtract to convert node→local)."""
    cfg = cfg or PitConfig()
    out = _ensure_latency_columns(df, cfg)
    return out.with_columns(
        (
            pl.col("node_observation_time").cast(pl.Float64)
            - pl.col("node_clock_drift_ms")
            + pl.col("network_latency_ms")
            + pl.col("processing_latency_ms")
        ).alias("T_avail")
    )


def compute_t_exch_true(df: pl.DataFrame, *, exch_ts_col: str, cfg: PitConfig | None = None) -> pl.DataFrame:
    """T_exch_true = T_exch - θ_exch (NTP: θ = exchange - local; subtract to convert exchange→local)."""
    cfg = cfg or PitConfig()
    out = _ensure_latency_columns(df, cfg)
    return out.with_columns(
        (pl.col(exch_ts_col).cast(pl.Float64) - pl.col("exchange_clock_drift_ms")).alias("T_exch_true")
    )


def apply_pit_alignment(
    df: pl.DataFrame,
    *,
    max_staleness_ms: int = 15000,
    strict: bool = False,
    exch_ts_col: str = "exchange_timestamp",
) -> pl.DataFrame:
    """
    Enforce T_avail <= T_exch_true and staleness window.

    Expects columns (ms):
    node_observation_time, node_clock_drift_ms, network_latency_ms,
    processing_latency_ms, exchange_timestamp, exchange_clock_drift_ms
    """
    cfg = PitConfig(max_staleness_ms=max_staleness_ms, strict=strict)
    out = compute_t_avail(df, cfg=cfg)
    out = compute_t_exch_true(out, exch_ts_col=exch_ts_col, cfg=cfg)

    out = out.with_columns([
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
        leaks = out.filter((pl.col("is_pit_safe") == False) | pl.col("is_pit_safe").is_null())
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
    """As-of backward join on availability time: latest node row with T_avail <= T_exch_true."""
    cfg = config or PitConfig()
    node_prepared = _ensure_latency_columns(node_df.sort(node_ts_col), cfg)
    node_prepared = compute_t_avail(node_prepared, cfg=cfg)

    exch_prepared = exchange_df.sort(exch_ts_col).with_columns(
        pl.col(exch_ts_col).cast(pl.Float64)
    )
    if "exchange_clock_drift_ms" not in exch_prepared.columns:
        exch_prepared = exch_prepared.with_columns(
            pl.lit(cfg.exchange_clock_drift_ms).alias("exchange_clock_drift_ms")
        )
    exch_prepared = compute_t_exch_true(exch_prepared, exch_ts_col=exch_ts_col, cfg=cfg)

    joined = exch_prepared.join_asof(
        node_prepared,
        left_on="T_exch_true",
        right_on="T_avail",
        strategy="backward",
    )

    if node_ts_col != "node_observation_time" and node_ts_col in joined.columns:
        joined = joined.rename({node_ts_col: "node_observation_time"})

    out = apply_pit_alignment(
        joined,
        max_staleness_ms=cfg.max_staleness_ms,
        strict=cfg.strict,
        exch_ts_col=exch_ts_col,
    )
    if cfg.strict and out.filter(pl.col("node_observation_time").is_null()).height > 0:
        raise StructuralDataLeakageError("strict join: no node snapshot with T_avail <= T_exch_true")
    return out


def pit_config_from_dict(d: dict[str, Any] | None, *, backtest: dict[str, Any] | None = None) -> PitConfig:
    merged = dict(d or {})
    bt = backtest or {}
    latency = bt.get("latency_assumptions") or merged.get("latency_assumptions") or {}
    defaults = merged.get("defaults") or {}

    from crypto_lane.src.align.latency_profile import default_venue_from_backtest, resolve_theta_exch

    venue = default_venue_from_backtest(bt)
    profile = resolve_theta_exch(venue, bt)

    return PitConfig(
        max_staleness_ms=int(
            merged.get("max_staleness_ms", bt.get("max_feature_staleness_ms", 15000))
        ),
        strict=bool(merged.get("strict", bt.get("btc_node_feature_availability_mode") == "pit_strict")),
        network_latency_ms=float(
            merged.get("network_latency_ms", defaults.get("network_latency_ms", latency.get("network_latency_ms", 5.0)))
        ),
        processing_latency_ms=float(
            merged.get(
                "processing_latency_ms",
                defaults.get("processing_latency_ms", latency.get("processing_latency_ms", 2.0)),
            )
        ),
        exchange_clock_drift_ms=float(
            merged.get("exchange_clock_drift_ms", profile.theta_exch_ms)
        ),
    )
