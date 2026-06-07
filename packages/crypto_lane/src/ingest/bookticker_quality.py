"""Classify and audit BTC futures_um_bookticker_tick gold files."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from crypto_lane.src.ingest.gold_pull import _date_range, _parse_date, _symbol_map
from crypto_lane.src.ingest.gold_reader import _local_cache_path, gold_key
from crypto_lane.src.ingest.paths import ensure_data_dirs
from crypto_lane.src.types import repo_root_from_lane

_MIN_REAL_ROWS = 1000
_GRANULARITY = "futures_um_bookticker_tick"
_SYNTHETIC_SOURCES = frozenset(
    {"coinstats_exchange_price", "perp_klines_1h_fallback", "synthetic"}
)


def bookticker_dest(day: date, symbol: str | None = None) -> Path:
    sym = symbol or _symbol_map().get("binance_perp", "BTCUSDT")
    return _local_cache_path(gold_key("binance", sym, day, _GRANULARITY))


def classify_bookticker_file(path: Path) -> str:
    """Return: missing | synthetic | b2_real | sparse."""
    if not path.is_file():
        return "missing"
    try:
        df = pl.read_parquet(path)
    except Exception:
        return "missing"
    if df.is_empty():
        return "missing"
    if "source" in df.columns:
        sources = df["source"].drop_nulls().unique().to_list()
        if any(str(s) in _SYNTHETIC_SOURCES for s in sources):
            return "synthetic"
    if df.height < _MIN_REAL_ROWS:
        return "sparse"
    return "b2_real"


def classify_bookticker_day(day: date, symbol: str | None = None) -> str:
    return classify_bookticker_file(bookticker_dest(day, symbol))


def is_production_bookticker_day(day: date, symbol: str | None = None) -> bool:
    return classify_bookticker_day(day, symbol) == "b2_real"


def absent_bookticker_days(*, start: str, end: str) -> list[date]:
    """Days with no usable parquet (missing or sparse)."""
    ensure_data_dirs()
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    out: list[date] = []
    for day in _date_range(start_d, end_d):
        if classify_bookticker_day(day, symbol) in ("missing", "sparse"):
            out.append(day)
    return out


def missing_bookticker_days(*, start: str, end: str) -> list[date]:
    """Days lacking true L3 (missing, sparse, or degraded synthetic)."""
    ensure_data_dirs()
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    missing: list[date] = []
    for day in _date_range(start_d, end_d):
        cls = classify_bookticker_day(day, symbol)
        if cls in ("missing", "sparse", "synthetic"):
            missing.append(day)
    return missing


def synthetic_bookticker_days(*, start: str, end: str) -> list[str]:
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    out: list[str] = []
    for day in _date_range(start_d, end_d):
        if classify_bookticker_day(day, symbol) == "synthetic":
            out.append(day.isoformat())
    return out


def purge_synthetic_bookticker(*, start: str, end: str) -> list[str]:
    """Delete local synthetic/sparse bookticker parquet files in range."""
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    removed: list[str] = []
    for day in _date_range(start_d, end_d):
        path = bookticker_dest(day, symbol)
        cls = classify_bookticker_file(path)
        if cls in ("synthetic", "sparse"):
            path.unlink(missing_ok=True)
            removed.append(day.isoformat())
    return removed


def build_quality_manifest(*, start: str, end: str) -> dict[str, dict[str, Any]]:
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    manifest: dict[str, dict[str, Any]] = {}
    for day in _date_range(start_d, end_d):
        path = bookticker_dest(day, symbol)
        cls = classify_bookticker_file(path)
        entry: dict[str, Any] = {"class": cls, "rows": 0}
        if path.is_file():
            try:
                df = pl.read_parquet(path)
                entry["rows"] = df.height
                if "source" in df.columns and not df["source"].is_empty():
                    entry["source"] = str(df["source"][0])
            except Exception:
                entry["class"] = "missing"
        manifest[day.isoformat()] = entry
    return manifest


def quality_manifest_path() -> Path:
    return repo_root_from_lane() / "runtime/data_audits/crypto_gold_quality.json"


def write_quality_manifest(*, start: str, end: str) -> Path:
    manifest = build_quality_manifest(start=start, end=end)
    path = quality_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def load_quality_manifest() -> dict[str, dict[str, Any]]:
    path = quality_manifest_path()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
