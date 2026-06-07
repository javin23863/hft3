"""Download UM futures bookTicker from data.binance.vision (monthly archives)."""
from __future__ import annotations

import csv
import io
import time
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import polars as pl

from crypto_lane.src.ingest.bookticker_quality import (
    absent_bookticker_days,
    bookticker_dest,
    classify_bookticker_file,
)
from crypto_lane.src.ingest.gold_pull import _parse_date, _symbol_map
from crypto_lane.src.ingest.paths import ensure_data_dirs

_VISION_MONTHLY_BASE = "https://data.binance.vision/data/futures/um/monthly/bookTicker"
_MIN_REAL_ROWS = 1000


def vision_monthly_url(symbol: str, year: int, month: int) -> str:
    return f"{_VISION_MONTHLY_BASE}/{symbol}/{symbol}-bookTicker-{year}-{month:02d}.zip"


def _months_for_days(days: list[date]) -> list[tuple[int, int, list[date]]]:
    by_month: dict[tuple[int, int], list[date]] = defaultdict(list)
    for day in days:
        by_month[(day.year, day.month)].append(day)
    return [(y, m, sorted(ds)) for (y, m), ds in sorted(by_month.items())]


def _download_bytes(url: str, *, timeout_s: int = 600) -> bytes:
    req = Request(url, headers={"User-Agent": "hft3-crypto-vision/1.0"})
    with urlopen(req, timeout=timeout_s) as resp:
        return resp.read()


def _col_map(headers: list[str]) -> dict[str, str]:
    lower = {h.lower(): h for h in headers}
    mapping: dict[str, str] = {}
    for want, candidates in {
        "update_id": ("update_id", "u"),
        "best_bid_px": ("best_bid_price", "bid_price", "b"),
        "best_bid_qty": ("best_bid_qty", "bid_qty", "B"),
        "best_ask_px": ("best_ask_price", "ask_price", "a"),
        "best_ask_qty": ("best_ask_qty", "ask_qty", "A"),
        "transaction_ts_ms": ("transaction_time", "T"),
        "event_ts_ms": ("event_time", "E"),
    }.items():
        for c in candidates:
            if c in lower:
                mapping[want] = lower[c]
                break
    return mapping


def _row_from_csv(
    headers: list[str],
    cmap: dict[str, str],
    raw: list[str],
    symbol: str,
) -> tuple[date, dict[str, object]] | None:
    if not raw:
        return None
    row = dict(zip(headers, raw))
    if "event_ts_ms" not in cmap and "transaction_ts_ms" not in cmap:
        return None
    event_ms = int(float(row[cmap.get("event_ts_ms", cmap["transaction_ts_ms"])]))
    ts = datetime.fromtimestamp(event_ms / 1000, tz=timezone.utc)
    return ts.date(), {
        "update_id": int(float(row[cmap["update_id"]])) if "update_id" in cmap else 0,
        "best_bid_px": float(row[cmap["best_bid_px"]]),
        "best_bid_qty": float(row[cmap["best_bid_qty"]]),
        "best_ask_px": float(row[cmap["best_ask_px"]]),
        "best_ask_qty": float(row[cmap["best_ask_qty"]]),
        "transaction_ts_ms": int(float(row[cmap["transaction_ts_ms"]]))
        if "transaction_ts_ms" in cmap
        else event_ms,
        "event_ts_ms": event_ms,
        "timestamp": ts,
        "symbol": symbol,
    }


def _stream_monthly_buckets(
    csv_file: BinaryIO,
    symbol: str,
    want_days: set[date],
) -> tuple[dict[date, list[dict[str, object]]], set[date], int]:
    """Stream CSV rows into per-day buckets; return (buckets, days_in_archive, row_count)."""
    text_io = io.TextIOWrapper(csv_file, encoding="utf-8")
    reader = csv.reader(text_io)
    headers = next(reader, None)
    if not headers:
        return {}, set(), 0
    cmap = _col_map(headers)
    buckets: dict[date, list[dict[str, object]]] = defaultdict(list)
    days_in_archive: set[date] = set()
    row_count = 0
    for raw in reader:
        parsed = _row_from_csv(headers, cmap, raw, symbol)
        if parsed is None:
            continue
        day, record = parsed
        days_in_archive.add(day)
        row_count += 1
        if day in want_days:
            buckets[day].append(record)
    return dict(buckets), days_in_archive, row_count


def _write_day(symbol: str, day: date, rows: list[dict[str, object]]) -> tuple[int, str]:
    dest = bookticker_dest(day, symbol)
    if classify_bookticker_file(dest) == "b2_real":
        return 0, "skipped_real"
    if len(rows) < _MIN_REAL_ROWS:
        return 0, "sparse"
    dest.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(dest)
    return len(rows), "binance_vision"


def pull_bookticker_month(
    symbol: str,
    year: int,
    month: int,
    target_days: list[date],
    *,
    sleep_s: float = 0.2,
) -> dict[str, int | list[str] | dict[str, int] | str | set]:
    want = set(target_days)
    url = vision_monthly_url(symbol, year, month)
    try:
        raw = _download_bytes(url)
    except HTTPError as exc:
        if exc.code == 404:
            return {
                "year": year,
                "month": month,
                "written_days": 0,
                "rows_written": 0,
                "by_status": {"not_found": len(target_days)},
                "filled_dates": [],
            }
        raise
    except URLError:
        return {
            "year": year,
            "month": month,
            "written_days": 0,
            "rows_written": 0,
            "by_status": {"network_error": len(target_days)},
            "filled_dates": [],
        }

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(csv_name) as csv_file:
            buckets, days_in_archive, row_count = _stream_monthly_buckets(
                csv_file, symbol, want
            )

    written: list[str] = []
    rows = 0
    by_status: dict[str, int] = {}
    calendar_days_in_month = len(target_days)
    archive_covers_partial_month = (
        len(days_in_archive) < calendar_days_in_month and row_count > 0
    )

    for day in target_days:
        day_rows = buckets.get(day, [])
        if not day_rows and day not in days_in_archive and row_count > 0:
            status = "upstream_incomplete"
        else:
            try:
                _, status = _write_day(symbol, day, day_rows)
            except Exception:
                status = "error"
        by_status[status] = by_status.get(status, 0) + 1
        if status == "binance_vision":
            written.append(day.isoformat())
            rows += len(day_rows)

    time.sleep(sleep_s)
    out: dict[str, object] = {
        "year": year,
        "month": month,
        "written_days": len(written),
        "rows_written": rows,
        "by_status": by_status,
        "filled_dates": written,
        "archive_row_count": row_count,
        "archive_distinct_days": len(days_in_archive),
    }
    if archive_covers_partial_month:
        out["warning"] = (
            f"Vision monthly archive {year}-{month:02d} spans only "
            f"{len(days_in_archive)} distinct day(s); not a full calendar month"
        )
    return out  # type: ignore[return-value]


def pull_bookticker_day(symbol: str, day: date, *, sleep_s: float = 0.2) -> tuple[int, str]:
    report = pull_bookticker_month(symbol, day.year, day.month, [day], sleep_s=sleep_s)
    by_status = report.get("by_status", {})
    if report.get("written_days", 0) > 0:
        return int(report.get("rows_written", 0)), "binance_vision"
    for status in (
        "skipped_real",
        "sparse",
        "upstream_incomplete",
        "not_found",
        "network_error",
        "error",
    ):
        if by_status.get(status):
            return 0, status
    return 0, "error"


def pull_bookticker_from_vision(
    *,
    start: str,
    end: str,
    sleep_s: float = 0.2,
    max_days: int | None = None,
) -> dict[str, int | list[str] | dict[str, int]]:
    ensure_data_dirs()
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    days = absent_bookticker_days(start=start, end=end)
    if max_days is not None:
        days = days[: max(0, max_days)]

    written: list[str] = []
    rows = 0
    errors = 0
    by_status: dict[str, int] = {}
    monthly_reports: list[dict[str, object]] = []
    error_samples: list[str] = []

    for year, month, month_days in _months_for_days(days):
        try:
            report = pull_bookticker_month(
                symbol, year, month, month_days, sleep_s=sleep_s
            )
            monthly_reports.append(report)
            warning = report.get("warning")
            if warning and len(error_samples) < 5:
                error_samples.append(str(warning))
            for ds in report.get("filled_dates", []):
                written.append(str(ds))
            rows += int(report.get("rows_written", 0))
            for status, count in (report.get("by_status") or {}).items():
                by_status[str(status)] = by_status.get(str(status), 0) + int(count)
        except Exception as exc:
            errors += len(month_days)
            if len(error_samples) < 5:
                error_samples.append(f"{year}-{month:02d}: {exc}")

    if written:
        from crypto_lane.src.ingest.bookticker_quality import invalidate_bookticker_caches

        invalidate_bookticker_caches()
    return {
        "attempted": len(days),
        "written_days": len(written),
        "rows_written": rows,
        "errors": errors,
        "by_status": by_status,
        "filled_dates": written,
        "monthly": monthly_reports,
        "error_samples": error_samples,
    }


def pull_bookticker_from_vision_range(
    start: str,
    end: str,
    **kwargs: object,
) -> dict[str, int | list[str] | dict[str, int]]:
    """Backward-compatible alias."""
    return pull_bookticker_from_vision(start=start, end=end, **kwargs)  # type: ignore[arg-type]
