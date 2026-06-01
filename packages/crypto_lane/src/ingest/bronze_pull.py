"""Pull bronze parquet from B2 into data/crypto/bronze cache."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Iterable

from crypto_lane.src.config_loader import load_yaml
from crypto_lane.src.config.env_loader import ensure_crypto_env, require_env
from crypto_lane.src.ingest.b2_client import B2Client, B2ClientError
from crypto_lane.src.ingest.bronze_reader import (
    BronzeReadError,
    bronze_key,
    deribit_options_key,
    read_bronze_day,
    resolve_bucket,
    _local_cache_path,
)
from crypto_lane.src.ingest.paths import ensure_data_dirs
from crypto_lane.src.types import repo_root_from_lane


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _date_range(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _symbol_map() -> dict[str, str]:
    cfg = load_yaml(repo_root_from_lane() / "packages/crypto_lane/config/lake_sources.yaml")
    return cfg.get("symbols", {}).get("BTC", {})


def pull_bronze(
    *,
    start: str,
    end: str,
    sources: list[str] | None = None,
) -> dict[str, int]:
    ensure_crypto_env()
    require_env("HFT3_CRYPTO_B2_KEY_ID", "HFT3_CRYPTO_B2_APP_KEY", hint=".env.example")
    ensure_data_dirs()
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    wanted = {s.strip().lower() for s in (sources or ["binance", "deribit", "mempool"])}
    sym = _symbol_map()
    client = B2Client()
    counts: dict[str, int] = {"downloaded": 0, "skipped": 0, "errors": 0}

    if "binance" in wanted:
        spot = sym.get("binance_spot", "BTCUSDT")
        perp = sym.get("binance_perp", "BTCUSDT")
        for day in _date_range(start_d, end_d):
            for gran in ("spot_klines_1h", "perp_klines_1h", "perp_funding_rate"):
                symbol = spot if "spot" in gran else perp
                key = bronze_key("binance", symbol, day, gran)
                bucket = resolve_bucket("binance")
                dest = _local_cache_path(key)
                if dest.is_file():
                    counts["skipped"] += 1
                    continue
                try:
                    client.download_to_path(bucket, key, dest)
                    counts["downloaded"] += 1
                except B2ClientError:
                    counts["errors"] += 1

    if "deribit" in wanted:
        prefix = sym.get("deribit_options_prefix", "BTC")
        for day in _date_range(start_d, end_d):
            key = deribit_options_key(prefix, day)
            bucket = resolve_bucket("deribit")
            dest = _local_cache_path(key)
            if dest.is_file():
                counts["skipped"] += 1
                continue
            try:
                client.download_to_path(bucket, key, dest)
                counts["downloaded"] += 1
            except B2ClientError:
                counts["errors"] += 1

    if "mempool" in wanted:
        btc_sym = sym.get("bitcoind", "BTC")
        for day in _date_range(start_d, end_d):
            key = bronze_key("bitcoind", btc_sym, day, "mempool_snapshot_15m")
            cache_path = _local_cache_path(key)
            if cache_path.is_file():
                counts["skipped"] += 1
                continue
            succeeded = False
            for attempt in range(3):
                try:
                    # Empty frame is treated as success (matches original semantics:
                    # an empty read means the day exists in bronze but had no
                    # mempool snapshots — not a transient error).
                    read_bronze_day("bitcoind", btc_sym, day, "mempool_snapshot_15m")
                    counts["downloaded"] += 1
                    succeeded = True
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(2 ** attempt)
            if not succeeded:
                counts["errors"] += 1

    return counts
