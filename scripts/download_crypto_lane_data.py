#!/usr/bin/env python3
"""DEPRECATED — use production gold ingest instead.

  python -m crypto_lane.pipeline pull-gold --start 2024-01-01 --end 2024-12-31
  python -m crypto_lane.pipeline normalize --start 2024-01-01 --end 2024-12-31

This script previously pulled quant-x-datasets bronze, fabricated Deribit IV from
index OHLC, and synthesized mempool from spot returns. Those paths are forbidden.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

import polars as pl

from crypto_lane.src.config.env_loader import ensure_crypto_env
from crypto_lane.src.ingest.bronze_reader import _local_cache_path, bronze_key
from crypto_lane.src.ingest.paths import ensure_data_dirs


def _http_json(url: str, *, timeout: int = 60) -> object:
    req = Request(url, headers={"User-Agent": "hft3-crypto-download/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _deribit_rpc(method: str, params: dict | None = None) -> object:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    req = Request(
        "https://www.deribit.com/api/v2",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "hft3-crypto-download/1.0"},
        method="POST",
    )
    with urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode())
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload.get("result")


def _s3_client():
    import os

    ensure_crypto_env()
    import boto3

    key_id = os.environ.get("HFT3_CRYPTO_B2_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("HFT3_CRYPTO_B2_APP_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")
    endpoint = os.environ.get("B2_ENDPOINT_URL") or os.environ.get("HFT3_CRYPTO_B2_ENDPOINT")
    if not key_id or not secret:
        raise RuntimeError(
            "Set HFT3_CRYPTO_B2_KEY_ID/APP_KEY or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY (Backblaze B2)."
        )
    if endpoint and endpoint.startswith("https://s3."):
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
        )
    raise RuntimeError("B2 S3 endpoint required (B2_ENDPOINT_URL=https://s3....backblazeb2.com)")


def _write_bronze_parquet(key: str, df: pl.DataFrame) -> Path:
    dest = _local_cache_path(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(dest)
    return dest


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


def _split_klines_by_day(df: pl.DataFrame) -> dict[date, pl.DataFrame]:
    out: dict[date, pl.DataFrame] = {}
    for d in df["timestamp"].unique().to_list():
        if d is None:
            continue
        day = d.date() if hasattr(d, "date") else d
        out[day] = df.filter(pl.col("timestamp").dt.date() == day)
    return out


def pull_spot_from_b2(s3, bucket: str, year: int) -> int:
    symbol = "BTCUSDT"
    written = 0
    for month in range(1, 13):
        key = (
            f"quantx/bronze/source=binance/dataset=spot_klines_1h/symbol={symbol}/"
            f"year={year}/{symbol}-1h-{year}-{month:02d}.zip"
        )
        buf = io.BytesIO()
        try:
            s3.download_fileobj(bucket, key, buf)
        except Exception as exc:
            print(f"spot skip {key}: {exc}", file=sys.stderr)
            continue
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            csv_name = zf.namelist()[0]
            text = zf.read(csv_name).decode()
        rows = list(csv.reader(io.StringIO(text)))
        df = _klines_df(rows)
        for day, day_df in _split_klines_by_day(df).items():
            bkey = bronze_key("binance", symbol, day, "spot_klines_1h")
            _write_bronze_parquet(bkey, day_df)
            written += 1
        print(f"spot {year}-{month:02d} -> {len(_split_klines_by_day(df))} days")
    return written


def pull_funding_from_b2(s3, bucket: str, year: int) -> int:
    symbol = "BTCUSDT"
    written = 0
    for month in range(1, 13):
        key = (
            f"quantx/bronze/source=binance/dataset=perp_funding_rate/symbol=BTC/"
            f"year={year}/{month:02d}.json"
        )
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            records = json.loads(obj["Body"].read())
        except Exception as exc:
            print(f"funding skip {key}: {exc}", file=sys.stderr)
            continue
        df = pl.DataFrame(records).with_columns(
            pl.from_epoch(pl.col("fundingTime"), time_unit="ms").alias("timestamp"),
            pl.col("fundingRate").cast(pl.Float64),
        )
        by_day: dict[date, pl.DataFrame] = {}
        for row in df.iter_rows(named=True):
            ts = row["timestamp"]
            d = ts.date() if isinstance(ts, datetime) else ts
            by_day.setdefault(d, []).append(row)
        for d, rows in by_day.items():
            day_df = pl.DataFrame(rows)
            bkey = bronze_key("binance", symbol, d, "perp_funding_rate")
            _write_bronze_parquet(bkey, day_df)
            written += 1
        print(f"funding {year}-{month:02d} -> {len(by_day)} days")
    return written


def pull_perp_from_binance(start: date, end: date) -> int:
    symbol = "BTCUSDT"
    start_ms = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc).timestamp() * 1000)
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
    for day, day_df in _split_klines_by_day(df).items():
        if day < start or day > end:
            continue
        bkey = bronze_key("binance", symbol, day, "perp_klines_1h")
        _write_bronze_parquet(bkey, day_df)
        written += 1
    print(f"perp binance API -> {written} days")
    return written


def pull_deribit_index_b2(s3, bucket: str, start: date, end: date) -> int:
    """Pull Deribit BTC-PERPETUAL 1h index from B2; write options_chain_1h bronze (IV proxy from OHLC)."""
    from crypto_lane.src.ingest.bronze_reader import deribit_options_key

    prefix = "BTC"
    written = 0
    cur = start
    while cur <= end:
        key = (
            f"quantx/bronze/source=deribit_index/asset=crypto/symbol=BTC-PERPETUAL/"
            f"date={cur.isoformat()}/deribit_index_crypto_BTC-PERPETUAL_{cur.isoformat()}_1h.parquet"
        )
        buf = io.BytesIO()
        try:
            s3.download_fileobj(bucket, key, buf)
        except Exception:
            cur += timedelta(days=1)
            continue
        buf.seek(0)
        idx = pl.read_parquet(buf)
        if idx.is_empty():
            cur += timedelta(days=1)
            continue
        ts_col = "timestamp"
        close = idx["close"].cast(pl.Float64)
        ret = close.log().diff().fill_null(0.0)
        rv = (ret.rolling_std(window_size=min(24, idx.height)).fill_null(0.0) * (365.0 * 24.0) ** 0.5).clip(0.05, 2.0)
        strike = float(close.median())
        chain_rows: list[dict[str, object]] = []
        for i in range(idx.height):
            ts = idx[ts_col][i]
            if isinstance(ts, (int, float)):
                ts_dt = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
            else:
                ts_dt = ts
            iv = float(rv[i])
            chain_rows.append(
                {
                    "timestamp": ts_dt,
                    "quote_timestamp": ts_dt,
                    "strike": strike,
                    "strike_price": strike,
                    "mark_iv": iv,
                    "iv": iv,
                    "option_type": "call",
                }
            )
            chain_rows.append(
                {
                    "timestamp": ts_dt,
                    "quote_timestamp": ts_dt,
                    "strike": strike * 0.75,
                    "strike_price": strike * 0.75,
                    "mark_iv": iv * 1.05,
                    "iv": iv * 1.05,
                    "option_type": "put",
                }
            )
        bkey = deribit_options_key(prefix, cur)
        _write_bronze_parquet(bkey, pl.DataFrame(chain_rows))
        written += 1
        cur += timedelta(days=1)
    print(f"deribit index B2 ({bucket}) -> {written} day files")
    return written


def pull_mempool_synthetic_from_spot(start: date, end: date) -> int:
    """Mempool bronze from spot hourly vol (blockchain.info chart has no 2024 history)."""
    norm = _REPO / "data" / "crypto" / "normalized" / "spot_perp_ticks.csv"
    if not norm.is_file():
        print("mempool synthetic skip: spot_perp_ticks.csv missing", file=sys.stderr)
        return 0
    ticks = pl.read_csv(norm)
    ticks = ticks.with_columns(
        pl.from_epoch(pl.col("exchange_timestamp"), time_unit="ms").alias("ts"),
    )
    sym = "BTC"
    written = 0
    cur = start
    while cur <= end:
        day_df = ticks.filter(pl.col("ts").dt.date() == cur)
        if day_df.is_empty():
            cur += timedelta(days=1)
            continue
        rets = day_df["perp_return"].abs().to_list()
        mean_r = sum(rets) / max(len(rets), 1)
        rows: list[dict[str, object]] = []
        for row in day_df.iter_rows(named=True):
            ts_ms = int(row["exchange_timestamp"])
            shock = min(1.0, abs(float(row.get("perp_return") or 0.0)) / max(mean_r, 1e-6))
            usage = int(80_000_000 + shock * 180_000_000)
            max_bytes = 300_000_000
            stress = usage / max_bytes
            rows.append(
                {
                    "timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
                    "node_observation_time": ts_ms - 100,
                    "bytes": usage,
                    "usage_bytes": usage,
                    "mempool_bytes": usage,
                    "mempool_max_bytes": max_bytes,
                    "size_txs": max(500, int(usage / 400)),
                    "mempool_tx_count": max(500, int(usage / 400)),
                    "mempool_min_fee": 0.00001 + stress * 0.0003,
                    "min_fee_sat": 12.0 + stress * 200.0,
                    "blockspace_stress_score": stress,
                    "btc_blockspace_stress_score": stress,
                }
            )
        bkey = bronze_key("bitcoind", sym, cur, "mempool_snapshot_15m")
        _write_bronze_parquet(bkey, pl.DataFrame(rows))
        written += 1
        cur += timedelta(days=1)
    print(f"mempool synthetic (from spot) -> {written} day files")
    return written


def pull_mempool_blockchain_chart(start: date, end: date) -> int:
    """Backfill mempool bronze from blockchain.info charts (15m-ish samples)."""
    days = (end - start).days + 400
    url = f"https://api.blockchain.info/charts/mempool-size?timespan={days}days&format=json&rollingAverage=8hours"
    try:
        payload = _http_json(url)
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"mempool chart skip: {exc}", file=sys.stderr)
        return 0
    values = payload.get("values") or []
    sym = "BTC"
    by_day: dict[date, list[dict[str, object]]] = {}
    for pt in values:
        ts_ms = int(pt.get("x", 0))
        usage = float(pt.get("y", 0))
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
        if d < start or d > end:
            continue
        max_bytes = 300_000_000
        stress = min(1.0, usage / max_bytes)
        by_day.setdefault(d, []).append(
            {
                "timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
                "node_observation_time": ts_ms - 100,
                "bytes": int(usage),
                "usage_bytes": int(usage),
                "mempool_bytes": int(usage),
                "mempool_max_bytes": max_bytes,
                "size_txs": max(1, int(usage / 500)),
                "mempool_tx_count": max(1, int(usage / 500)),
                "mempool_min_fee": 0.00001 + stress * 0.0002,
                "min_fee_sat": 10.0 + stress * 150.0,
                "blockspace_stress_score": stress,
                "btc_blockspace_stress_score": stress,
            }
        )
    written = 0
    for d, rows in by_day.items():
        bkey = bronze_key("bitcoind", sym, d, "mempool_snapshot_15m")
        _write_bronze_parquet(bkey, pl.DataFrame(rows))
        written += 1
    print(f"mempool blockchain.info -> {written} day files")
    return written


def main() -> int:
    p = argparse.ArgumentParser(description="Deprecated — delegates to crypto_lane pull-gold + normalize")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="2024-12-31")
    p.add_argument("--sources", default="binance,deribit,mempool")
    p.add_argument("--skip-pull", action="store_true")
    p.add_argument("--skip-normalize", action="store_true")
    args = p.parse_args()

    from crypto_lane.src.config.env_loader import ensure_crypto_env
    from crypto_lane.src.ingest.gold_pull import pull_gold, supplement_perp_from_binance
    from crypto_lane.src.ingest.normalize import normalize_all

    ensure_crypto_env()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    summary: dict[str, object] = {}
    if not args.skip_pull:
        summary["gold"] = pull_gold(start=args.start, end=args.end, sources=sources)
        if "binance" in {s.strip().lower() for s in sources}:
            summary["gold"]["perp_binance_api"] = supplement_perp_from_binance(
                start=args.start, end=args.end
            )
    if not args.skip_normalize:
        paths = normalize_all(start=args.start, end=args.end)
        summary["normalized"] = {k: str(v) for k, v in paths.items()}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
