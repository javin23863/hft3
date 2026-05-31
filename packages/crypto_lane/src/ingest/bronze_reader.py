"""Read bronze parquet objects from B2 or local cache."""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from crypto_lane.src.config_loader import load_yaml
from crypto_lane.src.ingest.b2_client import B2Client, B2ClientError
from crypto_lane.src.ingest.paths import bronze_dir
from crypto_lane.src.types import repo_root_from_lane


class BronzeReadError(RuntimeError):
    pass


def _lake_config() -> dict[str, Any]:
    path = repo_root_from_lane() / "packages" / "crypto_lane" / "config" / "lake_sources.yaml"
    return load_yaml(path)


def normalize_bronze_granularity(source: str, granularity: str) -> str:
    cfg = _lake_config()
    aliases = (cfg.get("granularity_aliases") or {}).get(source) or {}
    return str(aliases.get(granularity, granularity))


def bronze_key(source: str, symbol: str, day: date, granularity: str) -> str:
    cfg = _lake_config()
    prefix = cfg.get("bronze_prefix", "quantx/bronze")
    asset = cfg.get("asset", "crypto")
    gran = normalize_bronze_granularity(source, granularity)
    ds = day.isoformat()
    fname = f"{source}_{asset}_{symbol}_{ds}_{gran}.parquet"
    return f"{prefix}/source={source}/asset={asset}/symbol={symbol}/date={ds}/{fname}"


def deribit_options_key(symbol: str, day: date, expiration: str | None = None) -> str:
    cfg = _lake_config()
    prefix = cfg.get("bronze_prefix", "quantx/bronze")
    asset = cfg.get("asset", "crypto")
    ds = day.isoformat()
    exp = expiration or ds.replace("-", "")
    sym = f"{symbol}-OPTIONS-{exp}"
    fname = f"deribit_{asset}_{sym}_{ds}_options_chain_1h.parquet"
    return f"{prefix}/source=deribit/asset={asset}/symbol={sym}/date={ds}/{fname}"


def resolve_bucket(source: str) -> str:
    import os

    cfg = _lake_config()
    mode = (cfg.get("source_bucket_by_source") or {}).get(source, "write")
    if mode == "source":
        env_key = cfg.get("source_bucket_env", "HFT3_CRYPTO_B2_SOURCE_BUCKET")
        default = cfg.get("source_bucket_default", "quant-x-datasets")
    else:
        env_key = cfg.get("write_bucket_env", "HFT3_CRYPTO_B2_BUCKET")
        default = cfg.get("write_bucket_default", "crypto-alpha-datasets")
    bucket = os.environ.get(env_key, default)
    forbidden = set(_lake_config().get("forbidden_write_buckets") or [])
    if bucket in forbidden:
        raise BronzeReadError(f"bucket {bucket} is forbidden for crypto lane reads")
    return bucket


def _local_cache_path(key: str) -> Path:
    return bronze_dir() / key.replace("/", "__")


def read_parquet_key(key: str, *, bucket_name: str | None = None, source: str = "binance") -> pl.DataFrame:
    local = _local_cache_path(key)
    if local.is_file():
        return pl.read_parquet(local)

    bucket = bucket_name or resolve_bucket(source)
    client = B2Client()
    try:
        raw = client.download_bytes(bucket, key)
    except B2ClientError as exc:
        raise BronzeReadError(str(exc)) from exc
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(raw)
    return pl.read_parquet(io.BytesIO(raw))


def read_bronze_day(
    source: str,
    symbol: str,
    day: date,
    granularity: str,
    *,
    bucket_name: str | None = None,
) -> pl.DataFrame:
    key = bronze_key(source, symbol, day, granularity)
    return read_parquet_key(key, bucket_name=bucket_name, source=source)


def read_bronze_range(
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
            frames.append(read_bronze_day(source, symbol, cur, granularity, bucket_name=bucket_name))
        except BronzeReadError:
            pass
        cur += timedelta(days=1)
    if not frames:
        raise BronzeReadError(
            f"no bronze for source={source} symbol={symbol} granularity={granularity} "
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
            frames.append(read_bronze_day(source, symbol, cur, granularity))
        except BronzeReadError:
            pass
        cur += timedelta(days=1)
    if not frames:
        raise BronzeReadError(f"no mempool bronze {source}/{symbol} {start}..{end}")
    df = pl.concat(frames, how="diagonal_relaxed")
    if "timestamp" in df.columns:
        ts = pl.col("timestamp").str.to_datetime(time_zone="UTC", strict=False)
        df = df.filter(ts >= start.replace(tzinfo=None)).filter(ts <= end.replace(tzinfo=None))
    return df
