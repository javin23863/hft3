"""Pull production gold parquet from B2 into data/crypto/gold cache."""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from urllib.request import Request, urlopen

import polars as pl

from crypto_lane.src.config_loader import load_yaml
from crypto_lane.src.config.env_loader import ensure_crypto_env, require_env
from crypto_lane.src.ingest.b2_client import B2Client, B2ClientError
from crypto_lane.src.ingest.gold_reader import (
    deribit_options_key,
    gold_key,
    resolve_gold_bucket,
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


def _http_json(url: str) -> object:
    req = Request(url, headers={"User-Agent": "hft3-crypto-gold/1.0"})
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _klines_df(rows: list[list]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "open_time": [int(r[0]) for r in rows],
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [float(r[5]) for r in rows],
            "timestamp": [
                datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc) for r in rows
            ],
        }
    )


def supplement_perp_from_binance(*, start: str, end: str) -> dict[str, int]:
    """Fill missing perp_klines_1h gold from Binance fapi (public market data)."""
    ensure_data_dirs()
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    missing = [
        day
        for day in _date_range(start_d, end_d)
        if not _local_cache_path(gold_key("binance", symbol, day, "perp_klines_1h")).is_file()
    ]
    if not missing:
        return {"written": 0, "skipped": (end_d - start_d).days + 1}

    start_ms = int(datetime.combine(start_d, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.combine(end_d, datetime.max.time(), tzinfo=timezone.utc).timestamp() * 1000)
    all_rows: list[list] = []
    cur = start_ms
    while cur < end_ms:
        url = (
            "https://fapi.binance.com/fapi/v1/klines?"
            f"symbol={symbol}&interval=1h&startTime={cur}&endTime={end_ms}&limit=1500"
        )
        chunk = _http_json(url)
        if not chunk:
            break
        all_rows.extend(chunk)
        cur = int(chunk[-1][0]) + 3_600_000
        time.sleep(0.15)

    df = _klines_df(all_rows).unique(subset=["open_time"]).sort("open_time")
    written = 0
    for day in missing:
        day_df = df.filter(pl.col("timestamp").dt.date() == day)
        if day_df.is_empty():
            continue
        dest = _local_cache_path(gold_key("binance", symbol, day, "perp_klines_1h"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        day_df.write_parquet(dest)
        written += 1
    return {"written": written, "skipped": (end_d - start_d).days + 1 - len(missing)}


def supplement_dvol_from_deribit(*, start: str, end: str) -> dict[str, int]:
    """Fill missing options-chain gold from Deribit public DVOL index (real market data)."""
    ensure_data_dirs()
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    symbol = "BTC-DVOL"
    missing = [
        day
        for day in _date_range(start_d, end_d)
        if not _local_cache_path(gold_key("deribit", symbol, day, "dvol_1h")).is_file()
    ]
    if not missing:
        return {"written": 0, "skipped": (end_d - start_d).days + 1}

    written = 0
    for day in missing:
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        day_end = datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc)
        start_ms = int(day_start.timestamp() * 1000)
        end_ms = int(day_end.timestamp() * 1000)
        url = (
            "https://www.deribit.com/api/v2/public/get_volatility_index_data?"
            f"currency=BTC&start_timestamp={start_ms}&end_timestamp={end_ms}&resolution=3600"
        )
        payload = _http_json(url)
        chunk = (payload or {}).get("result", {}).get("data") if isinstance(payload, dict) else None
        if not chunk:
            time.sleep(0.15)
            continue
        df = pl.DataFrame(
            {
                "timestamp_ms": [int(r[0]) for r in chunk],
                "open": [float(r[1]) for r in chunk],
                "high": [float(r[2]) for r in chunk],
                "low": [float(r[3]) for r in chunk],
                "close": [float(r[4]) for r in chunk],
                "timestamp": [
                    datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc) for r in chunk
                ],
            }
        ).unique(subset=["timestamp_ms"]).sort("timestamp_ms")
        dest = _local_cache_path(gold_key("deribit", symbol, day, "dvol_1h"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(dest)
        written += 1
        time.sleep(0.15)
    return {"written": written, "skipped": (end_d - start_d).days + 1 - len(missing)}


def pull_bookticker_from_b2(
    *,
    start: str,
    end: str,
    max_days: int | None = None,
) -> dict[str, int]:
    """Download futures_um_bookticker_tick from B2 for days not already real locally."""
    from crypto_lane.src.ingest.bookticker_quality import (
        absent_bookticker_days,
        classify_bookticker_day,
    )

    ensure_crypto_env()
    require_env("HFT3_CRYPTO_B2_KEY_ID", "HFT3_CRYPTO_B2_APP_KEY", hint=".env.example")
    ensure_data_dirs()
    sym = _symbol_map()
    perp = sym.get("binance_perp", "BTCUSDT")
    client = B2Client()
    bucket = resolve_gold_bucket("binance")
    days = absent_bookticker_days(start=start, end=end)
    if max_days is not None:
        days = days[: max(0, max_days)]

    counts: dict[str, object] = {
        "downloaded": 0,
        "skipped": 0,
        "errors": 0,
        "still_missing": 0,
        "bucket": bucket,
        "error_samples": [],
    }
    error_samples: list[dict[str, str]] = []
    for day in days:
        if classify_bookticker_day(day, perp) == "b2_real":
            counts["skipped"] = int(counts["skipped"]) + 1
            continue
        key = gold_key("binance", perp, day, "futures_um_bookticker_tick")
        dest = _local_cache_path(key)
        try:
            client.download_to_path(bucket, key, dest)
            if classify_bookticker_day(day, perp) == "b2_real":
                counts["downloaded"] = int(counts["downloaded"]) + 1
            else:
                dest.unlink(missing_ok=True)
                counts["errors"] = int(counts["errors"]) + 1
                if len(error_samples) < 5:
                    error_samples.append(
                        {
                            "day": day.isoformat(),
                            "error": "downloaded file failed quality check (sparse/synthetic)",
                        }
                    )
        except B2ClientError as exc:
            counts["errors"] = int(counts["errors"]) + 1
            if len(error_samples) < 5:
                error_samples.append({"day": day.isoformat(), "error": str(exc)})

    counts["error_samples"] = error_samples
    from crypto_lane.src.ingest.bookticker_quality import absent_bookticker_days as _absent

    counts["still_missing"] = len(_absent(start=start, end=end))
    if int(counts["downloaded"]) > 0:
        from crypto_lane.src.ingest.bookticker_quality import invalidate_bookticker_caches

        invalidate_bookticker_caches()
    return counts  # type: ignore[return-value]


def pull_gold(
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
    bucket = resolve_gold_bucket("binance")
    counts: dict[str, int] = {"downloaded": 0, "skipped": 0, "errors": 0}

    if "binance" in wanted:
        spot = sym.get("binance_spot", "BTCUSDT")
        perp = sym.get("binance_perp", "BTCUSDT")
        for day in _date_range(start_d, end_d):
            for gran in ("spot_klines_1h", "perp_funding_rate"):
                key = gold_key("binance", spot, day, gran)
                dest = _local_cache_path(key)
                if dest.is_file():
                    counts["skipped"] += 1
                    continue
                try:
                    client.download_to_path(bucket, key, dest)
                    counts["downloaded"] += 1
                except B2ClientError:
                    counts["errors"] += 1
            perp_key = gold_key("binance", perp, day, "perp_klines_1h")
            dest = _local_cache_path(perp_key)
            if not dest.is_file():
                try:
                    client.download_to_path(bucket, perp_key, dest)
                    counts["downloaded"] += 1
                except B2ClientError:
                    counts["errors"] += 1
            else:
                counts["skipped"] += 1
            bt_key = gold_key("binance", perp, day, "futures_um_bookticker_tick")
            bt_dest = _local_cache_path(bt_key)
            if not bt_dest.is_file():
                try:
                    client.download_to_path(bucket, bt_key, bt_dest)
                    counts["downloaded"] += 1
                except B2ClientError:
                    counts["errors"] += 1
            else:
                counts["skipped"] += 1

    if "deribit" in wanted:
        prefix = sym.get("deribit_options_prefix", "BTC")
        for day in _date_range(start_d, end_d):
            key = deribit_options_key(prefix, day)
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
        mempool_bucket = resolve_gold_bucket("bitcoind")
        for day in _date_range(start_d, end_d):
            key = gold_key("bitcoind", btc_sym, day, "mempool_snapshot_15m")
            dest = _local_cache_path(key)
            if dest.is_file():
                counts["skipped"] += 1
                continue
            try:
                client.download_to_path(mempool_bucket, key, dest)
                counts["downloaded"] += 1
            except B2ClientError:
                counts["errors"] += 1

    return counts
