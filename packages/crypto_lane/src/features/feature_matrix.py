"""Assemble feature matrix, labels, and provenance."""
from __future__ import annotations

import polars as pl

from crypto_lane.src.align.latency_profile import default_venue_from_backtest, resolve_theta_exch
from crypto_lane.src.align.pit_join import backward_join_node_to_exchange, pit_config_from_dict
from crypto_lane.src.features.crypto.basis_features import attach_provenance, build_basis_features
from crypto_lane.src.features.crypto.deribit_vol_features import build_deribit_vol_features
from crypto_lane.src.features.crypto.funding_features import build_exchange_microstructure, build_funding_features
from crypto_lane.src.features.onchain.btc_blockspace_event_features import build_blockspace_event_features
from crypto_lane.src.features.onchain.btc_node_mempool_features import build_mempool_features_from_join
from crypto_lane.src.labels.event_study_labels import attach_event_study_labels
from crypto_lane.src.labels.forward_labels import attach_forward_labels
from crypto_lane.src.config.data_paths import data_provenance_source, resolve_lane_data_dir
from crypto_lane.src.types import FeatureProvenance


def build_labeled_frame(
    *,
    include_btc_node: bool = True,
    horizon_ms: int = 1000,
    backtest_config: dict | None = None,
    hypothesis_id: str | None = None,
    horizons: list[str] | None = None,
    required_features: list[str] | None = None,
) -> pl.DataFrame:
    bt = backtest_config or {}
    fd = resolve_lane_data_dir(bt)
    ticks = pl.read_csv(fd / "spot_perp_ticks.csv")
    basis = build_basis_features(ticks)
    funding = build_funding_features(ticks)
    micro = build_exchange_microstructure(ticks)

    surface = pl.read_csv(fd / "deribit_surface.csv")
    log_r = ticks["spot_return"].to_numpy().tolist()
    vol = build_deribit_vol_features(surface, log_r, align_timestamps=ticks["exchange_timestamp"])

    out = basis.join(funding, on="exchange_timestamp", how="left").join(
        micro, on="exchange_timestamp", how="left"
    ).join(vol, on="exchange_timestamp", how="left")

    if "perp_data_quality_flag" in ticks.columns:
        out = out.join(
            ticks.select(["exchange_timestamp", "perp_data_quality_flag"]),
            on="exchange_timestamp",
            how="left",
        )

    if "validation_period" in ticks.columns:
        out = out.join(
            ticks.select(["exchange_timestamp", "validation_period"]),
            on="exchange_timestamp",
            how="left",
        )

    pit_cfg = pit_config_from_dict(
        {
            "max_staleness_ms": bt.get("max_feature_staleness_ms", 15000),
            "strict": bt.get("btc_node_feature_availability_mode") == "pit_strict",
        },
        backtest=bt,
    )

    if include_btc_node:
        mempool = pl.read_csv(fd / "mempool_snapshots.csv")
        venue = default_venue_from_backtest(bt)
        theta_exch = resolve_theta_exch(venue, bt).theta_exch_ms
        exch = out.select("exchange_timestamp").unique().with_columns([
            pl.col("exchange_timestamp").cast(pl.Int64),
            pl.lit(theta_exch).alias("exchange_clock_drift_ms"),
        ])
        drop_cols = {"exchange_timestamp"}
        node_raw = mempool.drop([c for c in drop_cols if c in mempool.columns])
        mp_raw = node_raw.with_columns(pl.col("node_observation_time").cast(pl.Int64))
        mp_join = backward_join_node_to_exchange(
            exch,
            mp_raw,
            exch_ts_col="exchange_timestamp",
            node_ts_col="node_observation_time",
            config=pit_cfg,
        ).with_columns(pl.col("exchange_timestamp").cast(pl.Int64))
        mp_feat = build_mempool_features_from_join(mp_join)
        ev = build_blockspace_event_features(mp_feat.sort("exchange_timestamp"))
        node_cols = [
            "exchange_timestamp",
            "btc_mempool_usage_bytes",
            "btc_fee_spike_zscore",
            "btc_node_data_available_flag",
            "jump_intensity_lambda",
            "btc_blockspace_stress_score",
            "is_pit_safe",
            "staleness_delta_ms",
            "T_avail",
            "T_exch_true",
        ]
        out = out.join(
            mp_feat.select([c for c in node_cols if c in mp_feat.columns]),
            on="exchange_timestamp",
            how="left",
        )
        out = out.join(ev, on="exchange_timestamp", how="left")

    if "btc_node_data_available_flag" in out.columns:
        avail = pl.col("btc_node_data_available_flag") == 1
        for col in ("btc_mempool_usage_bytes", "btc_fee_spike_zscore", "jump_intensity_lambda"):
            if col in out.columns:
                out = out.with_columns(pl.when(avail).then(pl.col(col)).otherwise(None).alias(col))

    if "perp_data_quality_flag" in out.columns:
        perp_ok = pl.col("perp_data_quality_flag") == 1
        for col in ("spot_perp_basis", "basis_pct", "basis_zscore", "annualized_basis_yield",
                     "funding_adjusted_basis", "cross_venue_basis_dispersion",
                     "ou_basis_compression_signal", "basis_momentum", "basis_volatility"):
            if col in out.columns:
                out = out.with_columns(pl.when(perp_ok).then(pl.col(col)).otherwise(None).alias(col))

    if "is_pit_safe" in out.columns:
        pit_col = out["is_pit_safe"]
        is_pit_safe_all = (
            pit_col.null_count() == 0 and bool((pit_col == True).all())
        )
    else:
        is_pit_safe_all = True
    if "staleness_delta_ms" in out.columns and out.height:
        avg_staleness = float(out["staleness_delta_ms"].max())
    else:
        avg_staleness = 0.0
    node_flag = int(out["btc_node_data_available_flag"].min()) if "btc_node_data_available_flag" in out.columns else (1 if include_btc_node else 0)
    if "T_avail" in out.columns and "T_exch_true" in out.columns and out.height:
        worst = out.sort("staleness_delta_ms", descending=True).head(1)
        t_avail_ns = int(worst["T_avail"][0] * 1_000_000)
        t_exch_ns = int(worst["T_exch_true"][0] * 1_000_000)
    else:
        t_avail_ns = 0
        t_exch_ns = 0

    prov = FeatureProvenance(
        source=data_provenance_source(bt),
        t_avail_ns=t_avail_ns,
        t_exch_true_ns=t_exch_ns,
        staleness_delta_ms=avg_staleness,
        is_pit_safe=is_pit_safe_all,
        btc_node_data_available_flag=node_flag,
    )
    out = attach_provenance(out, prov)

    cost = bt.get("cost_assumptions") or {}
    if hypothesis_id == "CRYPTO_H7":
        out = attach_event_study_labels(out, horizons=horizons, horizon_ms=horizon_ms)
    else:
        out = attach_forward_labels(out, horizon_ms=horizon_ms, cost_assumptions=cost)

    out = out.filter(pl.col("exchange_timestamp").is_not_null())
    if required_features:
        present = [c for c in required_features if c in out.columns]
        if present:
            out = out.drop_nulls(subset=present)
    return out
