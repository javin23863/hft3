"""Normalize bronze parquet into lane CSV schemas under data/crypto/normalized/."""
from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from crypto_lane.src.config_loader import load_yaml
from crypto_lane.src.ingest.bronze_reader import (
    BronzeReadError,
    deribit_options_key,
    read_bronze_range,
    read_mempool_snapshot_range,
    read_parquet_key,
    _local_cache_path,
)
from crypto_lane.src.align.latency_profile import default_venue_from_backtest, resolve_node_latency, resolve_theta_exch
from crypto_lane.src.ingest.mempool_pull import load_mempool_bronze
from crypto_lane.src.ingest.paths import ensure_data_dirs, normalized_dir
from crypto_lane.src.types import repo_root_from_lane

DEFAULT_BACKTEST_LATENCY = {
    "latency_assumptions": {"ws_rtt_ms": 5.0, "ws_rtt_tracking": True},
    "venues": ["binance_perp"],
}


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _symbol_map() -> dict[str, str]:
    cfg = load_yaml(repo_root_from_lane() / "packages/crypto_lane/config/lake_sources.yaml")
    return cfg.get("symbols", {}).get("BTC", {})


def _ts_ms(col: pl.Expr) -> pl.Expr:
    return col.dt.epoch(time_unit="ms").cast(pl.Int64)


def _assign_validation_periods(df: pl.DataFrame) -> pl.DataFrame:
    """Calendar-free stage labels aligned with fixture smoke (Discovery/Confirmation/Holdout)."""
    n = df.height
    if n == 0:
        return df.with_columns(pl.lit("Discovery").alias("validation_period"))
    p40 = max(1, int(n * 0.4))
    p70 = max(p40 + 1, int(n * 0.7))
    return df.with_row_index("_row").with_columns(
        pl.when(pl.col("_row") < p40)
        .then(pl.lit("Discovery"))
        .when(pl.col("_row") < p70)
        .then(pl.lit("Confirmation"))
        .otherwise(pl.lit("Holdout"))
        .alias("validation_period")
    ).drop("_row")


def _read_klines(source: str, symbol: str, start: date, end: date, granularity: str) -> pl.DataFrame:
    return read_bronze_range(source, symbol, start, end, granularity)


def _mid_from_klines(df: pl.DataFrame, prefix: str) -> pl.DataFrame:
    if df.is_empty():
        return df
    ts_col = "timestamp" if "timestamp" in df.columns else "open_time"
    out = df.sort(ts_col).with_columns([
        _ts_ms(pl.col(ts_col).cast(pl.Datetime(time_zone="UTC"), strict=False)).alias("exchange_timestamp"),
        ((pl.col("high") + pl.col("low")) / 2.0).alias(f"{prefix}_mid"),
    ])
    return out.select(["exchange_timestamp", f"{prefix}_mid"])


def build_spot_perp_ticks(start: str, end: str) -> pl.DataFrame:
    sym = _symbol_map()
    start_d = _parse_date(start)
    end_d = _parse_date(end)

    spot = _mid_from_klines(
        _read_klines("binance", sym["binance_spot"], start_d, end_d, "spot_klines_1h"),
        "spot",
    )
    try:
        perp_df = _read_klines("binance", sym["binance_perp"], start_d, end_d, "perp_klines_1h")
    except BronzeReadError:
        perp_df = pl.DataFrame()
    perp = _mid_from_klines(perp_df, "perp")
    perp_is_real = not perp.is_empty()
    if spot.is_empty() and perp.is_empty():
        spot = _mid_from_klines(
            _read_klines("binance", sym["binance_spot"], start_d, end_d, "1h"),
            "spot",
        )
    if perp.is_empty() and not spot.is_empty():
        perp = spot.select(
            pl.col("exchange_timestamp"),
            pl.col("spot_mid").alias("perp_mid"),
        )
        perp_is_real = False

    funding = pl.DataFrame()
    try:
        funding = _read_klines("binance", sym["binance_perp"], start_d, end_d, "perp_funding_rate")
    except BronzeReadError:
        pass
    if not funding.is_empty():
        ts_col = "timestamp" if "timestamp" in funding.columns else "fundingTime"
        rate_col = "fundingRate" if "fundingRate" in funding.columns else ("funding_rate" if "funding_rate" in funding.columns else "last_funding_rate")
        funding = funding.sort(ts_col).with_columns([
            _ts_ms(pl.col(ts_col).cast(pl.Datetime(time_zone="UTC"), strict=False)).alias("exchange_timestamp"),
            pl.col(rate_col).cast(pl.Float64).alias("funding_rate"),
        ]).select(["exchange_timestamp", "funding_rate"])

    out = spot.join(perp, on="exchange_timestamp", how="outer_coalesce").sort("exchange_timestamp")
    if not funding.is_empty():
        out = out.join(funding, on="exchange_timestamp", how="left")
        out = out.with_columns(pl.col("funding_rate").forward_fill())
    else:
        out = out.with_columns(pl.lit(0.0).alias("funding_rate"))

    out = out.with_columns([
        pl.col("spot_mid").forward_fill(),
        pl.col("perp_mid").forward_fill(),
    ]).drop_nulls(subset=["exchange_timestamp"])

    out = out.with_columns([
        pl.col("perp_mid").alias("perp_mid_binance"),
        pl.col("perp_mid").alias("perp_mid_okx"),
        pl.col("funding_rate").alias("funding_rate_binance"),
        pl.col("funding_rate").alias("funding_rate_okx"),
        (pl.col("spot_mid").log().diff()).fill_null(0.0).alias("spot_return"),
        (pl.col("perp_mid").log().diff()).fill_null(0.0).alias("perp_return"),
        pl.lit(0.0).alias("bid_ask_spread"),
        pl.lit(0.0).alias("depth_btc"),
        pl.lit(0.0).alias("order_imbalance"),
        pl.lit(1 if perp_is_real else 0).alias("perp_data_quality_flag"),
    ])
    out = _assign_validation_periods(out.sort("exchange_timestamp"))
    cols = [
        "exchange_timestamp", "validation_period", "spot_mid", "perp_mid",
        "perp_mid_binance", "perp_mid_okx", "funding_rate", "funding_rate_binance",
        "funding_rate_okx", "spot_return", "perp_return", "bid_ask_spread",
        "depth_btc", "order_imbalance", "perp_data_quality_flag",
    ]
    return out.select(cols)


def build_deribit_surface(start: str, end: str, spot_ticks: pl.DataFrame) -> pl.DataFrame:
    sym = _symbol_map()
    prefix = sym.get("deribit_options_prefix", "BTC")
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    rows: list[dict[str, object]] = []
    cur = start_d
    while cur <= end_d:
        key_path = deribit_options_key(prefix, cur)
        local = _local_cache_path(key_path)
        if not local.is_file():
            try:
                df = read_parquet_key(key_path, source="deribit")
            except BronzeReadError:
                cur += timedelta(days=1)
                continue
        else:
            df = pl.read_parquet(local)
        ts_col = "timestamp" if "timestamp" in df.columns else "quote_timestamp"
        for ts_val in df[ts_col].unique().to_list() if ts_col in df.columns else []:
            if ts_val is None:
                continue
            ts = pl.Series([ts_val]).cast(pl.Datetime(time_zone="UTC"), strict=False)[0]
            ts_ms = int(ts.timestamp() * 1000) if ts is not None else 0
            spot_row = spot_ticks.filter(pl.col("exchange_timestamp") <= ts_ms).tail(1)
            spot_mid = float(spot_row["spot_mid"][0]) if spot_row.height else 0.0
            day_slice = df.filter(pl.col(ts_col) == ts_val) if ts_col in df.columns else df
            strike_col = "strike" if "strike" in day_slice.columns else "strike_price"
            iv_col = "mark_iv" if "mark_iv" in day_slice.columns else "iv"
            if strike_col not in day_slice.columns:
                cur += timedelta(days=1)
                continue
            atm_idx = (day_slice[strike_col].cast(pl.Float64) - spot_mid).abs().arg_min()
            atm = day_slice.row(atm_idx, named=True)
            strike = float(atm.get(strike_col, spot_mid))
            atm_iv = float(atm.get(iv_col, 0.0) or 0.0)
            calls = day_slice.filter(pl.col("option_type") == "call") if "option_type" in day_slice.columns else day_slice
            puts = day_slice.filter(pl.col("option_type") == "put") if "option_type" in day_slice.columns else day_slice
            call_mid = float(calls[iv_col].mean()) if calls.height and iv_col in calls.columns else atm_iv * strike * 0.02
            put_mid = float(puts[iv_col].mean()) if puts.height and iv_col in puts.columns else atm_iv * strike * 0.02
            rows.append({
                "exchange_timestamp": ts_ms,
                "atm_iv": atm_iv,
                "skew_25d": float(puts[iv_col].mean() - calls[iv_col].mean()) if puts.height and calls.height else 0.01,
                "term_structure_slope": 0.007,
                "call_mid": call_mid,
                "put_mid": put_mid,
                "spot_mid": spot_mid,
                "strike": strike,
                "rate": 0.05,
                "yield_q": 0.0,
                "tau_years": 0.08,
                "iv_rv_zscore": 0.0,
                "vol_surface_quality_flag": 1,
            })
        cur += timedelta(days=1)

    if not rows:
        return pl.DataFrame(schema={
            "exchange_timestamp": pl.Int64,
            "atm_iv": pl.Float64,
            "skew_25d": pl.Float64,
            "term_structure_slope": pl.Float64,
            "call_mid": pl.Float64,
            "put_mid": pl.Float64,
            "spot_mid": pl.Float64,
            "strike": pl.Float64,
            "rate": pl.Float64,
            "yield_q": pl.Float64,
            "tau_years": pl.Float64,
            "iv_rv_zscore": pl.Float64,
            "vol_surface_quality_flag": pl.Int64,
        })
    surf = pl.DataFrame(rows).unique(subset=["exchange_timestamp"]).sort("exchange_timestamp")
    if spot_ticks.height:
        joined = surf.join(
            spot_ticks.select(["exchange_timestamp", "spot_return"]).sort("exchange_timestamp"),
            on="exchange_timestamp",
            how="left",
        )
        log_r = joined["spot_return"].fill_null(0.0).to_numpy().tolist()
        zscores = []
        window = 32
        for i in range(joined.height):
            causal = log_r[max(0, i + 1 - window): i + 1]
            rv = math.sqrt(max(0.0, 365.0 * 24.0 * sum(r * r for r in causal) / max(len(causal), 1)))
            iv = float(joined["atm_iv"][i])
            zscores.append((iv - rv) / max(rv, 1e-6))
        surf = joined.with_columns(pl.Series("iv_rv_zscore", zscores)).drop("spot_return")
    return surf


def build_mempool_snapshots(start: str, end: str, spot_ticks: pl.DataFrame) -> pl.DataFrame:
    sym = _symbol_map()
    start_dt = datetime.combine(_parse_date(start), datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(_parse_date(end), datetime.max.time(), tzinfo=UTC)
    frames: list[pl.DataFrame] = []

    try:
        bronze_mp = read_mempool_snapshot_range("bitcoind", sym["bitcoind"], start_dt, end_dt)
        if not bronze_mp.is_empty():
            frames.append(bronze_mp)
    except BronzeReadError:
        pass

    local_mp = load_mempool_bronze(start_dt, end_dt)
    if not local_mp.is_empty():
        frames.append(local_mp)

    if not frames:
        return pl.DataFrame(schema={
            "node_observation_time": pl.Int64,
            "mempool_bytes": pl.Int64,
            "mempool_max_bytes": pl.Int64,
            "mempool_tx_count": pl.Int64,
            "min_fee_sat": pl.Float64,
            "btc_blockspace_stress_score": pl.Float64,
            "node_clock_drift_ms": pl.Float64,
            "network_latency_ms": pl.Float64,
            "processing_latency_ms": pl.Float64,
            "exchange_clock_drift_ms": pl.Float64,
            "estimated_latency_ms": pl.Float64,
        })

    raw = pl.concat(frames, how="diagonal_relaxed")
    node_lat = resolve_node_latency()
    venue_profile = resolve_theta_exch(default_venue_from_backtest(DEFAULT_BACKTEST_LATENCY), DEFAULT_BACKTEST_LATENCY)
    rows: list[dict[str, object]] = []

    for row in raw.iter_rows(named=True):
        node_ms = int(row.get("node_observation_time") or 0)
        if not node_ms and "timestamp" in row:
            ts = row["timestamp"]
            if isinstance(ts, str):
                node_ms = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
        if not node_ms and "timestamp_iso" in row:
            node_ms = int(datetime.fromisoformat(str(row["timestamp_iso"]).replace("Z", "+00:00")).timestamp() * 1000)
        usage = float(row.get("usage_bytes") or row.get("mempool_bytes") or row.get("bytes") or 0)
        max_bytes = int(row.get("mempool_max_bytes") or 300_000_000)
        min_fee = float(row.get("min_fee_sat") or 0.0)
        if not min_fee and row.get("mempool_min_fee"):
            min_fee = float(row["mempool_min_fee"]) * 1e8 / 1000.0
        stress = float(row.get("btc_blockspace_stress_score") or row.get("blockspace_stress_score") or usage / max(max_bytes, 1))
        rows.append({
            "node_observation_time": node_ms,
            "mempool_bytes": int(usage),
            "mempool_max_bytes": max_bytes,
            "mempool_tx_count": int(row.get("mempool_tx_count") or row.get("size_txs") or 0),
            "min_fee_sat": min_fee,
            "btc_blockspace_stress_score": min(1.0, stress),
            "node_clock_drift_ms": float(row.get("node_clock_drift_ms") or node_lat.theta_node_ms),
            "network_latency_ms": float(row.get("network_latency_ms") or node_lat.network_latency_ms),
            "processing_latency_ms": float(row.get("processing_latency_ms") or node_lat.processing_latency_ms),
            "exchange_clock_drift_ms": float(row.get("exchange_clock_drift_ms") or venue_profile.theta_exch_ms),
            "estimated_latency_ms": float(
                row.get("estimated_latency_ms")
                or (node_lat.network_latency_ms + node_lat.processing_latency_ms)
            ),
        })

    return pl.DataFrame(rows).unique(subset=["node_observation_time"]).sort("node_observation_time")


def normalize_all(*, start: str, end: str) -> dict[str, Path]:
    ensure_data_dirs()
    out_dir = normalized_dir()
    spot = build_spot_perp_ticks(start, end)
    if spot.is_empty():
        raise RuntimeError(
            f"No spot/perp bronze for {start}..{end}. Run: python -m crypto_lane.pipeline pull-bronze --start {start} --end {end}"
        )
    deribit = build_deribit_surface(start, end, spot)
    mempool = build_mempool_snapshots(start, end, spot)

    paths = {
        "spot_perp_ticks": out_dir / "spot_perp_ticks.csv",
        "deribit_surface": out_dir / "deribit_surface.csv",
        "mempool_snapshots": out_dir / "mempool_snapshots.csv",
    }
    for path in paths.values():
        if path.is_file():
            path.unlink()
    spot.write_csv(paths["spot_perp_ticks"])
    (deribit if not deribit.is_empty() else _empty_deribit()).write_csv(paths["deribit_surface"])
    (mempool if not mempool.is_empty() else _empty_mempool()).write_csv(paths["mempool_snapshots"])
    return paths


def _empty_deribit() -> pl.DataFrame:
    return pl.DataFrame(schema={
        "exchange_timestamp": pl.Int64,
        "atm_iv": pl.Float64,
        "skew_25d": pl.Float64,
        "term_structure_slope": pl.Float64,
        "call_mid": pl.Float64,
        "put_mid": pl.Float64,
        "spot_mid": pl.Float64,
        "strike": pl.Float64,
        "rate": pl.Float64,
        "yield_q": pl.Float64,
        "tau_years": pl.Float64,
        "iv_rv_zscore": pl.Float64,
        "vol_surface_quality_flag": pl.Int64,
    })


def _empty_mempool() -> pl.DataFrame:
    return pl.DataFrame(schema={
        "node_observation_time": pl.Int64,
        "mempool_bytes": pl.Int64,
        "mempool_max_bytes": pl.Int64,
        "mempool_tx_count": pl.Int64,
        "min_fee_sat": pl.Float64,
        "btc_blockspace_stress_score": pl.Float64,
        "node_clock_drift_ms": pl.Float64,
        "network_latency_ms": pl.Float64,
        "processing_latency_ms": pl.Float64,
        "exchange_clock_drift_ms": pl.Float64,
        "estimated_latency_ms": pl.Float64,
    })
