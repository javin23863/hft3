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


def _classify_from_df(df: pl.DataFrame) -> str:
    if df.is_empty():
        return "missing"
    if "source" in df.columns:
        sources = df["source"].drop_nulls().unique().to_list()
        if any(str(s) in _SYNTHETIC_SOURCES for s in sources):
            return "synthetic"
    if df.height < _MIN_REAL_ROWS:
        return "sparse"
    return "b2_real"


def inspect_bookticker_file(path: Path) -> tuple[str, dict[str, Any]]:
    """Single parquet read: return (class, {rows, source?})."""
    if not path.is_file():
        return "missing", {"rows": 0}
    try:
        df = pl.read_parquet(path)
    except Exception:
        return "missing", {"rows": 0}
    cls = _classify_from_df(df)
    meta: dict[str, Any] = {"rows": df.height}
    if "source" in df.columns and not df["source"].is_empty():
        meta["source"] = str(df["source"][0])
    return cls, meta


def classify_bookticker_file(path: Path) -> str:
    """Return: missing | synthetic | b2_real | sparse."""
    cls, _ = inspect_bookticker_file(path)
    return cls


def classify_bookticker_day(day: date, symbol: str | None = None) -> str:
    return classify_bookticker_file(bookticker_dest(day, symbol))


def is_production_bookticker_day(day: date, symbol: str | None = None) -> bool:
    return classify_bookticker_day(day, symbol) == "b2_real"


_range_summary_cache: dict[tuple[str, str], dict[str, Any]] = {}


def clear_bookticker_summary_cache() -> None:
    """Clear in-process bookticker range scan cache (call after ingest/purge)."""
    _range_summary_cache.clear()


def invalidate_bookticker_caches() -> None:
    """Clear bookticker summary + B2 synthetic probe disk caches after local gold changes."""
    clear_bookticker_summary_cache()
    from crypto_lane.src.ingest.b2_synthetic_probe_cache import clear_b2_synthetic_probe_cache

    clear_b2_synthetic_probe_cache()


def build_quality_manifest(*, start: str, end: str) -> dict[str, dict[str, Any]]:
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    manifest: dict[str, dict[str, Any]] = {}
    for day in _date_range(start_d, end_d):
        path = bookticker_dest(day, symbol)
        cls, meta = inspect_bookticker_file(path)
        manifest[day.isoformat()] = {"class": cls, **meta}
    return manifest


def summarize_bookticker_range(*, start: str, end: str, use_cache: bool = True) -> dict[str, Any]:
    """Single parquet scan: manifest, class counts, absent/missing/synthetic day lists."""
    key = (start, end)
    if use_cache and key in _range_summary_cache:
        return _range_summary_cache[key]
    ensure_data_dirs()
    manifest = build_quality_manifest(start=start, end=end)
    absent: list[date] = []
    missing: list[date] = []
    synthetic: list[str] = []
    by_class: dict[str, int] = {}
    for iso, entry in manifest.items():
        cls = str(entry.get("class", "missing"))
        by_class[cls] = by_class.get(cls, 0) + 1
        day = date.fromisoformat(iso)
        if cls in ("missing", "sparse"):
            absent.append(day)
        if cls in ("missing", "sparse", "synthetic"):
            missing.append(day)
        if cls == "synthetic":
            synthetic.append(iso)
    summary = {
        "manifest": manifest,
        "by_class": by_class,
        "absent": absent,
        "missing": missing,
        "synthetic": synthetic,
    }
    if use_cache:
        _range_summary_cache[key] = summary
    return summary


def absent_bookticker_days(*, start: str, end: str) -> list[date]:
    """Days with no usable parquet (missing or sparse)."""
    return list(summarize_bookticker_range(start=start, end=end)["absent"])


def missing_bookticker_days(*, start: str, end: str) -> list[date]:
    """Days lacking true L3 (missing, sparse, or degraded synthetic)."""
    return list(summarize_bookticker_range(start=start, end=end)["missing"])


def synthetic_bookticker_days(*, start: str, end: str) -> list[str]:
    return list(summarize_bookticker_range(start=start, end=end)["synthetic"])


def purge_synthetic_bookticker(*, start: str, end: str) -> list[str]:
    """Delete local synthetic/sparse bookticker parquet files in range."""
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    summary = summarize_bookticker_range(start=start, end=end, use_cache=False)
    removed: list[str] = []
    for iso, entry in summary["manifest"].items():
        cls = str(entry.get("class", "missing"))
        if cls in ("synthetic", "sparse"):
            day = date.fromisoformat(iso)
            bookticker_dest(day, symbol).unlink(missing_ok=True)
            removed.append(iso)
    invalidate_bookticker_caches()
    return removed


def quality_manifest_path() -> Path:
    return repo_root_from_lane() / "runtime/data_audits/crypto_gold_quality.json"


def write_quality_manifest(*, start: str, end: str) -> Path:
    manifest = build_quality_manifest(start=start, end=end)
    return write_quality_manifest_dict(manifest)


def write_quality_manifest_dict(manifest: dict[str, dict[str, Any]]) -> Path:
    path = quality_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def write_quality_manifest_from_summary(summary: dict[str, Any]) -> Path:
    return write_quality_manifest_dict(dict(summary.get("manifest") or {}))


def load_quality_manifest() -> dict[str, dict[str, Any]]:
    path = quality_manifest_path()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
