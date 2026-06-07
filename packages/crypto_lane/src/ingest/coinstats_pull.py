"""Degraded bookticker gap fill via CoinStats or perp klines (not production-grade L3)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import polars as pl

from crypto_lane.src.config.env_loader import ensure_crypto_env
from crypto_lane.src.ingest.bookticker_quality import absent_bookticker_days
from crypto_lane.src.ingest.gold_pull import _parse_date, _symbol_map
from crypto_lane.src.ingest.gold_reader import GoldReadError, gold_key, read_gold_day
from crypto_lane.src.ingest.gold_reader import _local_cache_path
from crypto_lane.src.ingest.paths import ensure_data_dirs

_BASE_URL = "https://openapiv1.coinstats.app"
_EXCHANGE = "BinanceFutures"
_TICK = 0.1
_DEFAULT_QTY = 1.0
_QUOTA_ERRORS = frozenset({402, 406, 429})


def _coinstats_headers() -> dict[str, str]:
    import os

    ensure_crypto_env()
    api_key = os.environ.get("COINSTATS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("COINSTATS_API_KEY missing; set in .env")
    return {
        "X-API-KEY": api_key,
        "User-Agent": "hft3-crypto-coinstats/1.0",
        "Accept": "application/json",
    }


def _http_json(path: str, params: dict[str, str | int | float]) -> object:
    query = urllib.parse.urlencode(params)
    url = f"{_BASE_URL}{path}?{query}"
    req = urllib.request.Request(url, headers=_coinstats_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _hourly_timestamps(day: date) -> Iterable[datetime]:
    for hour in range(24):
        yield datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(
            hours=hour
        )


def _synthetic_bookticker_row(
    *,
    symbol: str,
    ts: datetime,
    mid: float,
    update_id: int,
    source: str,
    spread: float | None = None,
) -> dict[str, object]:
    half = max(_TICK / 2.0, (spread or _TICK) / 2.0)
    bid = round(mid - half, 1)
    ask = round(mid + half, 1)
    ts_ms = int(ts.timestamp() * 1000)
    return {
        "update_id": update_id,
        "best_bid_px": bid,
        "best_bid_qty": _DEFAULT_QTY,
        "best_ask_px": ask,
        "best_ask_qty": _DEFAULT_QTY,
        "transaction_ts_ms": ts_ms,
        "event_ts_ms": ts_ms,
        "timestamp": ts,
        "symbol": symbol,
        "source": source,
    }


def _fetch_exchange_price(ts: datetime) -> float:
    payload = _http_json(
        "/coins/price/exchange",
        {
            "exchange": _EXCHANGE,
            "from": "BTC",
            "to": "USDT",
            "timestamp": int(ts.timestamp()),
        },
    )
    if not isinstance(payload, dict) or "price" not in payload:
        raise RuntimeError(f"unexpected CoinStats response: {payload!r}")
    return float(payload["price"])


def _rows_from_perp_klines(symbol: str, day: date) -> list[dict[str, object]]:
    klines = read_gold_day("binance", symbol, day, "perp_klines_1h")
    if klines.is_empty():
        raise GoldReadError(f"perp_klines_1h missing for {day}")

    ts_col = "timestamp" if "timestamp" in klines.columns else "open_time"
    if ts_col == "open_time":
        klines = klines.with_columns(
            pl.from_epoch(pl.col("open_time"), time_unit="ms").alias("timestamp"),
        )

    rows: list[dict[str, object]] = []
    update_id = int(day.strftime("%Y%m%d")) * 1000
    for row in klines.sort("timestamp").iter_rows(named=True):
        ts = row["timestamp"]
        if not isinstance(ts, datetime):
            ts = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
        high = float(row.get("high") or row.get("close") or 0.0)
        low = float(row.get("low") or row.get("close") or 0.0)
        close = float(row.get("close") or (high + low) / 2.0)
        spread = max(_TICK, high - low)
        rows.append(
            _synthetic_bookticker_row(
                symbol=symbol,
                ts=ts,
                mid=close,
                update_id=update_id,
                source="perp_klines_1h_fallback",
                spread=spread,
            )
        )
        update_id += 1
    return rows


def _write_day_degraded(
    symbol: str,
    day: date,
    *,
    sleep_s: float,
    prefer_klines: bool,
) -> tuple[int, str]:
    if prefer_klines:
        rows = _rows_from_perp_klines(symbol, day)
        source = "perp_klines_1h_fallback"
    else:
        rows = []
        update_id = int(day.strftime("%Y%m%d")) * 1000
        quota_hit = False
        for ts in _hourly_timestamps(day):
            if quota_hit:
                break
            try:
                mid = _fetch_exchange_price(ts)
            except urllib.error.HTTPError as exc:
                if exc.code in _QUOTA_ERRORS:
                    quota_hit = True
                    break
                raise
            rows.append(
                _synthetic_bookticker_row(
                    symbol=symbol,
                    ts=ts,
                    mid=mid,
                    update_id=update_id,
                    source="coinstats_exchange_price",
                )
            )
            update_id += 1
            time.sleep(sleep_s)

        if quota_hit or not rows:
            rows = _rows_from_perp_klines(symbol, day)
            source = "perp_klines_1h_fallback"
        else:
            source = "coinstats_exchange_price"

    dest = _local_cache_path(
        gold_key("binance", symbol, day, "futures_um_bookticker_tick")
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(dest)
    return len(rows), source


def fill_bookticker_gaps_degraded(
    *,
    start: str,
    end: str,
    sleep_s: float = 0.25,
    max_days: int | None = None,
    prefer_klines: bool = True,
) -> dict[str, int | list[str] | dict[str, int]]:
    """Fill missing bookticker days with degraded synthetic data."""
    ensure_crypto_env()
    ensure_data_dirs()
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    missing = absent_bookticker_days(start=start, end=end)
    missing_before = len(missing)
    if max_days is not None:
        missing = missing[: max(0, max_days)]

    written_days: list[str] = []
    rows_written = 0
    errors = 0
    by_source: dict[str, int] = {}
    for day in missing:
        try:
            n, source = _write_day_degraded(
                symbol, day, sleep_s=sleep_s, prefer_klines=prefer_klines
            )
            written_days.append(day.isoformat())
            rows_written += n
            by_source[source] = by_source.get(source, 0) + 1
        except Exception:
            errors += 1

    return {
        "missing_before": missing_before,
        "missing_after": len(absent_bookticker_days(start=start, end=end)),
        "attempted": len(missing),
        "written_days": len(written_days),
        "rows_written": rows_written,
        "errors": errors,
        "by_source": by_source,
        "filled_dates": written_days,
    }
