"""Read production gold parquet from B2 crypto-alpha-datasets or local cache."""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from crypto_lane.src.config_loader import load_yaml
from crypto_lane.src.ingest.b2_client import B2Client, B2ClientError
from crypto_lane.src.ingest.paths import gold_dir
from crypto_lane.src.types import repo_root_from_lane


class GoldReadError(RuntimeError):
    pass


def _lake_config() -> dict[str, Any]:
    path = repo_root_from_lane() / "packages" / "crypto_lane" / "config" / "lake_sources.yaml"
    return load_yaml(path)


def normalize_gold_granularity(source: str, granularity: str) -> str:
    cfg = _lake_config()
    aliases = (cfg.get("granularity_aliases") or {}).get(source) or {}
    return str(aliases.get(granularity, granularity))


def gold_prefix() -> str:
    cfg = _lake_config()
    return str(cfg.get("gold_prefix") or cfg.get("bronze_prefix") or "quantx/bronze")


def source_asset(source: str) -> str:
    cfg = _lake_config()
    by_source = cfg.get("asset_by_source") or {}
    return str(by_source.get(source, cfg.get("asset", "crypto")))


def gold_key(source: str, symbol: str, day: date, granularity: str) -> str:
    cfg = _lake_config()
    prefix = gold_prefix()
    asset = source_asset(source)
    gran = normalize_gold_granularity(source, granularity)
    ds = day.isoformat()
    fname = f"{source}_{asset}_{symbol}_{ds}_{gran}.parquet"
    return f"{prefix}/source={source}/asset={asset}/symbol={symbol}/date={ds}/{fname}"


def deribit_options_key(symbol: str, day: date, expiration: str | None = None) -> str:
    prefix = gold_prefix()
    asset = source_asset("deribit")
    ds = day.isoformat()
    exp = expiration or ds.replace("-", "")
    sym = f"{symbol}-OPTIONS-{exp}"
    fname = f"deribit_{asset}_{sym}_{ds}_options_chain_1h.parquet"
    return f"{prefix}/source=deribit/asset={asset}/symbol={sym}/date={ds}/{fname}"


def resolve_gold_bucket(_source: str) -> str:
    import os

    cfg = _lake_config()
    env_key = cfg.get("write_bucket_env", "HFT3_CRYPTO_B2_BUCKET")
    default = cfg.get("write_bucket_default", "crypto-alpha-datasets")
    bucket = os.environ.get(env_key, default)
    forbidden = set(cfg.get("forbidden_write_buckets") or [])
    if bucket in forbidden:
        raise GoldReadError(f"bucket {bucket} is forbidden for crypto lane reads")
    return bucket


def _local_cache_path(key: str) -> Path:
    return gold_dir() / key.replace("/", "__")


def read_parquet_key(key: str, *, bucket_name: str | None = None) -> pl.DataFrame:
    import os

    local = _local_cache_path(key)
    if local.is_file():
        return pl.read_parquet(local)
    if os.environ.get("CRYPTO_GOLD_LOCAL_ONLY", "").strip() in ("1", "true", "yes"):
        raise GoldReadError(f"local gold missing (CRYPTO_GOLD_LOCAL_ONLY): {key}")

    bucket = bucket_name or resolve_gold_bucket("binance")
    client = B2Client()
    try:
        raw = client.download_bytes(bucket, key)
    except B2ClientError as exc:
        raise GoldReadError(str(exc)) from exc
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(raw)
    return pl.read_parquet(io.BytesIO(raw))


def read_gold_day(
    source: str,
    symbol: str,
    day: date,
    granularity: str,
    *,
    bucket_name: str | None = None,
) -> pl.DataFrame:
    key = gold_key(source, symbol, day, granularity)
    return read_parquet_key(key, bucket_name=bucket_name)


def read_gold_range(
    source: str,
    symbol: str,
    start: date,
    end: date,
    granularity: str,
    *,
    bucket_name: str | None = None,
) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    cur = start
    while cur <= end:
        try:
            frames.append(read_gold_day(source, symbol, cur, granularity, bucket_name=bucket_name))
        except GoldReadError:
            pass
        cur += timedelta(days=1)
    if not frames:
        raise GoldReadError(
            f"no gold for source={source} symbol={symbol} granularity={granularity} "
            f"window={start}..{end}"
        )
    return pl.concat(frames, how="diagonal_relaxed")


def read_mempool_snapshot_range(
    source: str,
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    granularity: str = "mempool_snapshot_15m",
) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    cur = start.date()
    end_d = end.date()
    while cur <= end_d:
        try:
            frames.append(read_gold_day(source, symbol, cur, granularity))
        except GoldReadError:
            pass
        cur += timedelta(days=1)
    if not frames:
        raise GoldReadError(f"no mempool gold {source}/{symbol} {start}..{end}")
    df = pl.concat(frames, how="diagonal_relaxed")
    if "timestamp" in df.columns and df["timestamp"].dtype == pl.Utf8:
        df = df.with_columns(
            pl.col("timestamp").str.to_datetime(time_zone="UTC", strict=False),
        )
    return df
