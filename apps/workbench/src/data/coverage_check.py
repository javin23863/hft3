"""Simple valid-trading-day coverage check before robustness runs."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from features_engine.src.model_registry import resolve_model_id
from workbench.src.data.event_catalog import EventSpec, list_campaign_events, load_model_binding, load_periods
from workbench.src.registry.unified_registry import get_model_config

MIN_VALID_TRADING_DAYS = 250
TARGET_VALID_TRADING_DAYS = 750
OPTIONS_MIN_VALID_DAYS = 150
OPTIONS_TARGET_VALID_DAYS = 500

_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(20\d{2})[-_](\d{2})[-_](\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"),
)
_FUTURES_MONTH_CODES = set("FGHJKMNQUVXZ")


@dataclass(frozen=True)
class SymbolCoverage:
    symbol: str
    valid_trading_days: int
    available_start_date: str
    available_end_date: str
    coverage_status: str
    runnable_npz_days: int = 0
    raw_download_days: int = 0
    missing_conversion_days: int = 0


@dataclass(frozen=True)
class CoverageSummary:
    model_name: str
    data_type: str
    required_symbols: list[str]
    available_start_date: str
    available_end_date: str
    valid_trading_days: int
    minimum_required_days: int
    target_days: int
    coverage_status: str
    missing_date_ranges: list[str]
    action_taken: str
    per_symbol: list[SymbolCoverage] = field(default_factory=list)
    overlap_required: bool = False
    overlap_valid_trading_days: int = 0
    option_valid_trading_days: int | None = None
    option_minimum_required_days: int | None = None
    option_target_days: int | None = None
    runnable_npz_days: int = 0
    raw_download_days: int = 0
    missing_conversion_days: int = 0
    official_coverage_status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _base_symbol(symbol: str) -> str:
    return str(symbol).split(".")[0].upper()


def _macro_micro_pair(symbol: str) -> list[str]:
    base = _base_symbol(symbol)
    pairs = {
        "ES": ("ES", "MES"),
        "MES": ("ES", "MES"),
        "NQ": ("NQ", "MNQ"),
        "MNQ": ("NQ", "MNQ"),
        "YM": ("YM", "MYM"),
        "MYM": ("YM", "MYM"),
        "RTY": ("RTY", "M2K"),
        "M2K": ("RTY", "M2K"),
    }
    return list(pairs.get(base, (base,)))


def required_symbols_for_model(model_id: str, symbol: str, binding: dict[str, Any] | None = None) -> list[str]:
    """Infer the instruments that must each have enough data for this model."""
    slug = resolve_model_id(model_id)
    binding = binding or {}
    required_datasets = {str(v) for v in (binding.get("required_datasets") or [])}
    if slug == "ES_MES_LEAD_LAG" or slug == "GHOST_ROUTE":
        return ["ES", "MES"]
    if slug == "NQ_MNQ_LEAD_LAG":
        return ["NQ", "MNQ"]
    if "macro_micro_pair_calibration" in required_datasets or slug in {
        "MICRO_CONTRACT_RETAIL_LAG",
        "MAX_CONTRACT_CROWDING_IN_MICROS",
    }:
        return _macro_micro_pair(symbol)
    if slug == "ES_NQ_DIVERGENCE_SNAPBACK":
        return ["ES", "NQ"]
    if slug == "ZN_ZB_ES_NQ_MACRO_IMPULSE":
        return ["ZN", "ZB", "ES", "NQ"]
    return [_base_symbol(symbol)]


def data_type_for_model(model_id: str, binding: dict[str, Any] | None = None) -> str:
    slug = resolve_model_id(model_id)
    binding = binding or {}
    datasets = {str(v) for v in (binding.get("required_datasets") or [])}
    if "options_chain" in datasets:
        return "0DTE options and underlying intraday"
    if {"fixing_mbo", "options_ohlcv", "options_definitions", "options_statistics"} & datasets:
        return "CME options expiry-day datasets"
    if "cme_mdp3_mbo" in datasets or "mbo_npz" in datasets:
        return "CME MBO Level 3"
    if "l2_order_book" in datasets:
        return "Level 2 order book"
    if "tick_prints" in datasets:
        return "tick/trade prints"
    if "intraday_bars" in datasets:
        return "intraday bars"
    if slug in {"BOOK_PRESSURE", "CROSS_ASSET_LEAD_LAG", "VPIN_TOXICITY"}:
        return "intraday/event replay"
    return "intraday/event replay"


def _coverage_status(valid_days: int, minimum: int = MIN_VALID_TRADING_DAYS, target: int = TARGET_VALID_TRADING_DAYS) -> str:
    if valid_days < minimum:
        return "BELOW_MINIMUM"
    if valid_days < target:
        return "MINIMUM_ONLY"
    if valid_days == target:
        return "TARGET_MET"
    return "ABOVE_TARGET"


def _action_for_status(status: str) -> str:
    if status == "BELOW_MINIMUM":
        return "fill missing data before robustness"
    if status == "MINIMUM_ONLY":
        return "proceed to robustness (minimum coverage only)"
    return "proceed to robustness"


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_date(value: date | None) -> str:
    return value.isoformat() if value else ""


def _date_from_path(path: Path) -> date | None:
    text = str(path)
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
    return None


def _path_matches_symbol(path: Path, symbol: str) -> bool:
    base = _base_symbol(symbol)
    text = re.sub(r"[^A-Z0-9]+", " ", str(path).upper())
    tokens = text.split()
    if base in tokens:
        return True
    for token in tokens:
        if len(token) == len(base) + 2 and token.startswith(base):
            month = token[len(base)]
            year = token[-1]
            if month in _FUTURES_MONTH_CODES and year.isdigit():
                return True
    return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _npz_manifest_rows(repo_root: Path, npz_root: Path) -> dict[Path, dict[str, Any]]:
    manifest_path = npz_root / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = raw.get("files", raw) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return {}
    out: dict[Path, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_path = row.get("npz_path") or row.get("path")
        if not raw_path:
            continue
        p = Path(str(raw_path))
        candidates = [p] if p.is_absolute() else [repo_root / p, npz_root / p, npz_root / p.name]
        for candidate in candidates:
            try:
                out[candidate.resolve()] = row
            except OSError:
                out[candidate] = row
    return out


def _runnable_npz_artifact_ok(path: Path, manifest_rows: dict[Path, dict[str, Any]]) -> bool:
    try:
        row = manifest_rows.get(path.resolve())
    except OSError:
        row = manifest_rows.get(path)
    if not row:
        return False
    try:
        expected_count = int(row.get("event_count"))
    except (TypeError, ValueError):
        return False
    if expected_count <= 0:
        return False
    expected_sha = str(row.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        return False
    try:
        if _file_sha256(path) != expected_sha:
            return False
        import numpy as np  # noqa: PLC0415

        with np.load(path, allow_pickle=False) as npz:
            if "data" not in npz:
                return False
            return len(npz["data"]) == expected_count
    except Exception:  # noqa: BLE001
        return False


def _runnable_npz_dates_for_symbol(repo_root: Path, symbol: str) -> set[date]:
    """Official CME robustness coverage: converted MBO NPZ files only."""
    npz_root = repo_root / "data" / "npz"
    if not npz_root.is_dir():
        return set()
    manifest_rows = _npz_manifest_rows(repo_root, npz_root)
    dates: set[date] = set()
    for path in npz_root.glob("*_mbo.npz"):
        if not path.is_file():
            continue
        if not _path_matches_symbol(path, symbol):
            continue
        if not _runnable_npz_artifact_ok(path, manifest_rows):
            continue
        found = _date_from_path(path)
        if found is not None:
            dates.add(found)
    return dates


def _raw_backlog_dates_for_symbol(repo_root: Path, symbol: str) -> set[date]:
    """Downloaded/backlog dates that are not official runnable robustness coverage."""
    data_root = repo_root / "data"
    if not data_root.is_dir():
        return set()
    roots = (
        data_root,
        data_root / "raw",
        data_root / "replay" / "mbp10",
    )
    dates: set[date] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.dbn.zst"):
            if not path.is_file() or not _path_matches_symbol(path, symbol):
                continue
            found = _date_from_path(path)
            if found is not None:
                dates.add(found)
    return dates


def _catalog_dates_for_symbol(repo_root: Path, model_id: str, symbol: str) -> tuple[set[date], list[EventSpec]]:
    present: set[date] = set()
    missing: list[EventSpec] = []
    for period in load_periods(repo_root):
        for event in list_campaign_events(model_id, period, symbol, repo_root):
            event_date = _parse_date(event.release_date)
            if _event_has_own_official_npz(repo_root, event, symbol) and event_date is not None:
                present.add(event_date)
            elif not _event_has_own_official_npz(repo_root, event, symbol):
                missing.append(event)
    return present, missing


def _event_has_own_official_npz(repo_root: Path, event: EventSpec, requested_symbol: str) -> bool:
    npz_root = (repo_root / "data" / "npz").resolve()
    try:
        parent = event.npz_path.resolve().parent
    except OSError:
        parent = event.npz_path.parent
    return (
        bool(event.npz_present)
        and event.npz_symbol_used == requested_symbol
        and event.npz_path.name.endswith("_mbo.npz")
        and parent == npz_root
    )


def _option_roots(repo_root: Path) -> list[Path]:
    from data_system.src.npz_resolver import lake_root

    option_roots = [repo_root / "data" / "options"]
    lake_options = lake_root(repo_root) / "options"
    if lake_options not in option_roots:
        option_roots.append(lake_options)
    return option_roots


_OPTION_DATASET_SCHEMAS = {
    "fixing_mbo": "mbo",
    "options_ohlcv": "ohlcv",
    "options_definitions": "definition",
    "options_statistics": "statistics",
    "options_chain": "mbo",
}


def _option_expected_schema(dataset: str, path: Path) -> str | None:
    if dataset == "fixing_mbo" and "_trades_" in path.name:
        return "trades"
    return _OPTION_DATASET_SCHEMAS.get(dataset)


def _option_artifact_valid(repo_root: Path, path: Path, expected_schema: str | None) -> bool:
    if path.name.endswith(".doctor.json") or path.suffix.lower() == ".json":
        return False
    if not path.name.endswith((".dbn", ".dbn.zst")):
        return False
    try:
        scripts_dir = repo_root / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        import data_doctor as dd  # noqa: PLC0415

        ok, _ = dd._valid_options_artifact(path, expected_schema=expected_schema)
        return bool(ok)
    except Exception:  # noqa: BLE001
        return False


def _option_dataset_dates(repo_root: Path, dataset: str) -> set[date]:
    subdirs = {
        "fixing_mbo": ("fixing_mbo",),
        "options_ohlcv": ("ohlcv",),
        "options_definitions": ("definitions",),
        "options_statistics": ("statistics",),
        "options_chain": ("chain",),
    }.get(dataset, ())
    dates: set[date] = set()
    for root in _option_roots(repo_root):
        if not root.is_dir():
            continue
        roots = [root / sub for sub in subdirs] if subdirs else []
        for ds_root in roots:
            if not ds_root.is_dir():
                continue
            for path in ds_root.rglob("*"):
                if not path.is_file():
                    continue
                if not _option_artifact_valid(repo_root, path, _option_expected_schema(dataset, path)):
                    continue
                found = _date_from_path(path)
                if found is not None:
                    dates.add(found)
    return dates


def _option_dates(repo_root: Path) -> set[date]:
    return _option_dataset_dates(repo_root, "fixing_mbo")


def _compress_dates(dates: Iterable[date]) -> list[str]:
    ordered = sorted(set(dates))
    if not ordered:
        return []
    ranges: list[str] = []
    start = ordered[0]
    prev = ordered[0]
    for current in ordered[1:]:
        if current == prev + timedelta(days=1):
            prev = current
            continue
        ranges.append(start.isoformat() if start == prev else f"{start.isoformat()}..{prev.isoformat()}")
        start = current
        prev = current
    ranges.append(start.isoformat() if start == prev else f"{start.isoformat()}..{prev.isoformat()}")
    return ranges


def _weekday_gaps(dates: Sequence[date]) -> list[date]:
    if len(dates) < 2:
        return []
    available = set(dates)
    cursor = min(available)
    end = max(available)
    missing: list[date] = []
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in available:
            missing.append(cursor)
        cursor += timedelta(days=1)
    return missing


def build_coverage_summary_from_dates(
    *,
    model_name: str,
    data_type: str,
    required_symbols: Sequence[str],
    symbol_dates: dict[str, set[date]],
    missing_dates: Iterable[date] = (),
    minimum_required_days: int = MIN_VALID_TRADING_DAYS,
    target_days: int = TARGET_VALID_TRADING_DAYS,
    option_dates: set[date] | None = None,
    raw_backlog_dates: dict[str, set[date]] | None = None,
) -> CoverageSummary:
    normalized_symbols = [_base_symbol(symbol) for symbol in required_symbols]
    raw_backlog_dates = raw_backlog_dates or {}
    per_symbol: list[SymbolCoverage] = []
    for symbol in normalized_symbols:
        dates = sorted(symbol_dates.get(symbol, set()))
        raw_dates = raw_backlog_dates.get(symbol, set())
        missing_conversion = raw_dates - set(dates)
        per_symbol.append(
            SymbolCoverage(
                symbol=symbol,
                valid_trading_days=len(dates),
                available_start_date=_format_date(min(dates) if dates else None),
                available_end_date=_format_date(max(dates) if dates else None),
                coverage_status=_coverage_status(len(dates), minimum_required_days, target_days),
                runnable_npz_days=len(dates),
                raw_download_days=len(raw_dates),
                missing_conversion_days=len(missing_conversion),
            )
        )

    date_sets = [symbol_dates.get(symbol, set()) for symbol in normalized_symbols]
    overlap_dates = set.intersection(*date_sets) if date_sets and all(date_sets) else set()
    overlap_required = len(normalized_symbols) > 1
    valid_dates = sorted(overlap_dates if overlap_required else (date_sets[0] if date_sets else set()))
    status = _coverage_status(len(valid_dates), minimum_required_days, target_days)

    option_valid_days: int | None = None
    option_min: int | None = None
    option_target: int | None = None
    if option_dates is not None:
        option_valid_days = len(option_dates)
        option_min = OPTIONS_MIN_VALID_DAYS
        option_target = OPTIONS_TARGET_VALID_DAYS
        option_status = _coverage_status(option_valid_days, option_min, option_target)
        if status != "BELOW_MINIMUM" and option_status == "BELOW_MINIMUM":
            status = "BELOW_MINIMUM"
        elif status in {"TARGET_MET", "ABOVE_TARGET"} and option_status == "MINIMUM_ONLY":
            status = "MINIMUM_ONLY"

    symbol_failures = [s for s in per_symbol if s.coverage_status == "BELOW_MINIMUM"]
    if symbol_failures:
        status = "BELOW_MINIMUM"

    available_start = min(valid_dates).isoformat() if valid_dates else ""
    available_end = max(valid_dates).isoformat() if valid_dates else ""
    gaps = set(missing_dates)
    if valid_dates:
        gaps.update(_weekday_gaps(valid_dates))
    missing_ranges = _compress_dates(gaps)
    raw_sets = [raw_backlog_dates.get(symbol, set()) for symbol in normalized_symbols]
    raw_overlap = set.intersection(*raw_sets) if len(raw_sets) > 1 and all(raw_sets) else set()
    raw_dates_for_summary = raw_overlap if overlap_required else (raw_sets[0] if raw_sets else set())
    missing_conversion_dates = raw_dates_for_summary - set(valid_dates)
    return CoverageSummary(
        model_name=resolve_model_id(model_name),
        data_type=data_type,
        required_symbols=normalized_symbols,
        available_start_date=available_start,
        available_end_date=available_end,
        valid_trading_days=len(valid_dates),
        minimum_required_days=minimum_required_days,
        target_days=target_days,
        coverage_status=status,
        missing_date_ranges=missing_ranges or ["none"],
        action_taken=_action_for_status(status),
        per_symbol=per_symbol,
        overlap_required=overlap_required,
        overlap_valid_trading_days=len(overlap_dates),
        option_valid_trading_days=option_valid_days,
        option_minimum_required_days=option_min,
        option_target_days=option_target,
        runnable_npz_days=len(valid_dates),
        raw_download_days=len(raw_dates_for_summary),
        missing_conversion_days=len(missing_conversion_dates),
        official_coverage_status=status,
    )


def compute_model_coverage(repo_root: Path, model_id: str, symbol: str) -> CoverageSummary:
    slug = resolve_model_id(model_id)
    binding = load_model_binding(repo_root, slug)
    cfg = get_model_config(slug)
    binding = {
        **binding,
        "required_datasets": sorted(
            {str(v) for v in (binding.get("required_datasets") or [])}
            | {str(v) for v in (cfg.required_datasets or [])}
        ),
    }
    data_type = data_type_for_model(slug, binding)
    required_symbols = required_symbols_for_model(slug, symbol, binding)
    symbol_dates: dict[str, set[date]] = {}
    raw_backlog_dates: dict[str, set[date]] = {}
    missing_dates: set[date] = set()
    for required_symbol in required_symbols:
        base = _base_symbol(required_symbol)
        file_dates = _runnable_npz_dates_for_symbol(repo_root, base)
        catalog_dates, missing_events = _catalog_dates_for_symbol(repo_root, slug, f"{base}.v.0")
        symbol_dates[base] = file_dates | catalog_dates
        raw_backlog_dates[base] = _raw_backlog_dates_for_symbol(repo_root, base)
        for event in missing_events:
            event_date = _parse_date(event.release_date)
            if event_date is not None:
                missing_dates.add(event_date)
    _options_dataset_triggers = {"options_chain", "fixing_mbo", "options_ohlcv", "options_definitions", "options_statistics"}
    required_option_datasets = _options_dataset_triggers & {str(v) for v in (binding.get("required_datasets") or [])}
    option_dates = None
    if required_option_datasets:
        date_sets = [_option_dataset_dates(repo_root, ds) for ds in sorted(required_option_datasets)]
        option_dates = set.intersection(*date_sets) if date_sets and all(date_sets) else set()
    return build_coverage_summary_from_dates(
        model_name=slug,
        data_type=data_type,
        required_symbols=required_symbols,
        symbol_dates=symbol_dates,
        missing_dates=missing_dates,
        option_dates=option_dates,
        raw_backlog_dates=raw_backlog_dates,
    )


def missing_event_specs_for_model(repo_root: Path, model_id: str, symbol: str) -> list[EventSpec]:
    slug = resolve_model_id(model_id)
    binding = load_model_binding(repo_root, slug)
    required_symbols = required_symbols_for_model(slug, symbol, binding)
    missing: list[EventSpec] = []
    seen: set[tuple[str, str]] = set()
    for required_symbol in required_symbols:
        base = _base_symbol(required_symbol)
        for period in load_periods(repo_root):
            for event in list_campaign_events(slug, period, f"{base}.v.0", repo_root):
                key = (event.event_id, event.symbol)
                if not event.npz_present and key not in seen:
                    missing.append(event)
                    seen.add(key)
    return missing


def write_coverage_summary(path: Path, summary: CoverageSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")


def format_coverage_summary(summary: CoverageSummary) -> str:
    data = summary.to_dict()
    keys = (
        "model_name",
        "data_type",
        "required_symbols",
        "available_start_date",
        "available_end_date",
        "valid_trading_days",
        "minimum_required_days",
        "target_days",
        "coverage_status",
        "missing_date_ranges",
        "action_taken",
    )
    lines = ["Backtest coverage before robustness:"]
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        lines.append(f"{key}: {value}")
    return "\n".join(lines)
