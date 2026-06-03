"""Resolve historical runner seed events from free daily OHLCV.

This module never downloads paid market data. It reads local/free daily bars,
detects visible runner-event dates, and emits the minimal snapshot/window plan
needed before any L2/L3 spend is considered.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from equities_lane.src.ingest.daily_bars_io import load_daily_bars, load_daily_parquet
from equities_lane.src.models import DailyBar


DAILY_SNAPSHOT_NAMES = (
    "T-10 trading days",
    "T-5 trading days",
    "T-3 trading days",
    "T-2 trading days",
    "T-1 close",
    "T-1 after-hours",
    "T0 premarket",
    "T0 open",
)

SNAPSHOT_OFFSETS = {
    "T-10 trading days": -10,
    "T-5 trading days": -5,
    "T-3 trading days": -3,
    "T-2 trading days": -2,
    "T-1 close": -1,
    "T-1 after-hours": -1,
    "T0 premarket": 0,
    "T0 open": 0,
}

SNAPSHOT_TIMES_ET = {
    "T-10 trading days": "16:00:00",
    "T-5 trading days": "16:00:00",
    "T-3 trading days": "16:00:00",
    "T-2 trading days": "16:00:00",
    "T-1 close": "16:00:00",
    "T-1 after-hours": "20:00:00",
    "T0 premarket": "09:00:00",
    "T0 open": "09:30:00",
}

L2_L3_WINDOW_TEMPLATES = {
    "T-10 trading days": ("04:00:00", "16:00:00", "full snapshot day"),
    "T-5 trading days": ("04:00:00", "16:00:00", "full snapshot day"),
    "T-3 trading days": ("04:00:00", "16:00:00", "full snapshot day"),
    "T-2 trading days": ("04:00:00", "16:00:00", "full snapshot day"),
    "T-1 close": ("09:30:00", "16:00:00", "regular session to close"),
    "T-1 after-hours": ("16:00:00", "20:00:00", "after-hours only"),
    "T0 premarket": ("04:00:00", "09:30:00", "premarket only"),
    "T0 open": ("09:25:00", "09:35:00", "opening auction and first 5 minutes"),
}


@dataclass(frozen=True)
class SeedTicker:
    ticker: str
    cohort: str
    target_year: int | None


@dataclass(frozen=True)
class DetectionConfig:
    max_pre_event_close: float = 5.0
    min_intraday_return_pct: float = 30.0
    min_close_return_pct: float = 20.0
    min_volume_expansion: float = 3.0
    volume_lookback_days: int = 20
    event_cooldown_trading_days: int = 5


@dataclass(frozen=True)
class BenchmarkConfig:
    lift_l2_l3_download_when_passed: bool = True
    min_resolved_events: int = 5
    min_resolved_ticker_ratio: float = 0.10
    min_active_cohorts: int = 2
    min_median_event_strength: float = 1.0
    max_share_of_events_in_a_single_ticker: float = 0.75


@dataclass(frozen=True)
class BenchmarkResult:
    passed: bool
    metrics: dict[str, float]
    thresholds: dict[str, float]
    failures: list[str]
    lift_l2_l3_download: bool


@dataclass(frozen=True)
class RunnerEvent:
    ticker: str
    seed_cohort: str
    event_date: str
    event_index: int
    prior_close: float
    max_intraday_return: float
    close_return: float
    max_3day_return: float
    volume_expansion: float
    event_strength_score: float

    @property
    def runner_label_id(self) -> str:
        return f"{self.ticker}-{self.event_date}"


def load_seed_config(path: str | Path) -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise ValueError(f"seed config not found: {cfg_path}")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"seed config must be a mapping: {cfg_path}")
    return data


def load_seed_tickers(path: str | Path) -> list[SeedTicker]:
    data = load_seed_config(path)
    raw = data.get("positive_seed_tickers") or {}
    if not isinstance(raw, dict) or not raw:
        raise ValueError("seed config has no positive_seed_tickers mapping")

    seeds: list[SeedTicker] = []
    seen: set[str] = set()
    for cohort, tickers in raw.items():
        if not isinstance(tickers, list):
            raise ValueError(f"seed cohort {cohort} must be a list")
        for ticker in tickers:
            sym = str(ticker).strip().upper()
            if not sym:
                raise ValueError(f"empty ticker in seed cohort {cohort}")
            if sym in seen:
                raise ValueError(f"duplicate seed ticker: {sym}")
            seen.add(sym)
            seeds.append(SeedTicker(sym, str(cohort), _target_year(str(cohort))))
    return seeds


def resolve_runner_seed_events(
    seed_config_path: str | Path,
    daily_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    delisted_daily_roots: list[str | Path] | None = None,
) -> dict[str, Any]:
    cfg = load_seed_config(seed_config_path)
    seeds = load_seed_tickers(seed_config_path)
    detection = _detection_config(cfg)
    benchmark_cfg = _benchmark_config(cfg)
    delisted = _delisted_tickers(cfg)
    root = Path(daily_root) if daily_root is not None else _cfg_path(cfg, Path(seed_config_path), "daily_root", "data/equities/daily")
    delisted_roots = [Path(p) for p in (delisted_daily_roots or [])]

    events: list[RunnerEvent] = []
    unresolved: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    l2_l3_plan: list[dict[str, Any]] = []
    delisted_resolved_via: dict[str, str] = {}

    for seed in seeds:
        if seed.ticker in delisted:
            bars, source_label = _load_with_fallback(root, delisted_roots, seed.ticker)
            if not bars:
                unresolved.append({
                    "ticker": seed.ticker,
                    "seed_cohort": seed.cohort,
                    "reason": "data_source_exhausted_free_daily",
                    "expected_paths": _expected_daily_paths(root, seed.ticker) + _flatten_expected_paths(delisted_roots, seed.ticker),
                    "note": "Documented as delisted; free daily sources do not retain history. See delisted_seed_tickers in seed config. Pass delisted_daily_roots to retry with a paid pull.",
                })
                continue
            delisted_resolved_via[seed.ticker] = source_label
            symbol_events = detect_runner_events(seed, bars, detection)
            if not symbol_events:
                unresolved.append({
                    "ticker": seed.ticker,
                    "seed_cohort": seed.cohort,
                    "reason": "no_runner_event_detected_from_daily_bars",
                    "n_daily_bars": len(bars),
                    "target_year": seed.target_year,
                    "delisted_resolved_via": source_label,
                })
                continue
            for event in symbol_events:
                events.append(event)
                event_snapshots = build_pre_event_snapshots(event, bars)
                snapshots.extend(event_snapshots)
                l2_l3_plan.extend(build_l2_l3_pull_plan(event, event_snapshots))
            continue

        bars = _load_symbol_daily_bars(root, seed.ticker)
        if not bars:
            unresolved.append({
                "ticker": seed.ticker,
                "seed_cohort": seed.cohort,
                "reason": "missing_daily_bars",
                "expected_paths": _expected_daily_paths(root, seed.ticker),
            })
            continue

        symbol_events = detect_runner_events(seed, bars, detection)
        if not symbol_events:
            unresolved.append({
                "ticker": seed.ticker,
                "seed_cohort": seed.cohort,
                "reason": "no_runner_event_detected_from_daily_bars",
                "n_daily_bars": len(bars),
                "target_year": seed.target_year,
            })
            continue

        for event in symbol_events:
            events.append(event)
            event_snapshots = build_pre_event_snapshots(event, bars)
            snapshots.extend(event_snapshots)
            l2_l3_plan.extend(build_l2_l3_pull_plan(event, event_snapshots))

    benchmark = evaluate_free_daily_benchmark(events, seeds, benchmark_cfg)
    l2_l3_plan = _apply_benchmark_to_l2_l3_plan(l2_l3_plan, benchmark)

    cohort_rows = [_cohort_row(event) for event in events]
    payload = {
        "mode": "free_daily_ohlcv_event_resolution",
        "seed_config_path": str(seed_config_path),
        "daily_root": str(root),
        "delisted_daily_roots": [str(p) for p in delisted_roots],
        "no_paid_market_data_downloads": True,
        "n_seed_tickers": len(seeds),
        "n_resolved_events": len(events),
        "n_unresolved_tickers": len(unresolved),
        "delisted_seed_tickers": sorted(delisted),
        "delisted_resolved_via": delisted_resolved_via,
        "detection_config": detection.__dict__,
        "benchmark_config": benchmark_cfg.__dict__,
        "free_daily_benchmark": {
            "passed": benchmark.passed,
            "metrics": benchmark.metrics,
            "thresholds": benchmark.thresholds,
            "failures": benchmark.failures,
            "lift_l2_l3_download": benchmark.lift_l2_l3_download,
        },
        "seed_tickers": [seed.__dict__ for seed in seeds],
        "cohort_rows": cohort_rows,
        "snapshot_rows": snapshots,
        "l2_l3_minimal_pull_plan": l2_l3_plan,
        "unresolved_tickers": unresolved,
    }

    if output_dir is not None:
        _write_outputs(Path(output_dir), payload)
    return payload


def detect_runner_events(
    seed: SeedTicker,
    bars: list[DailyBar],
    config: DetectionConfig,
) -> list[RunnerEvent]:
    sorted_bars = _sorted_bars(bars)
    candidates: list[RunnerEvent] = []
    for idx in range(1, len(sorted_bars)):
        bar = sorted_bars[idx]
        event_year = _parse_date(bar.date).year
        if seed.target_year is not None and event_year != seed.target_year:
            continue

        prev = sorted_bars[idx - 1]
        if prev.close <= 0 or prev.close > config.max_pre_event_close:
            continue
        prior_volumes = [b.volume for b in sorted_bars[max(0, idx - config.volume_lookback_days):idx] if b.volume > 0]
        if not prior_volumes:
            continue

        vol_expansion = bar.volume / max(median(prior_volumes), 1.0)
        intraday_return = (bar.high - prev.close) / prev.close
        close_return = (bar.close - prev.close) / prev.close
        max_3day_return = _max_forward_high_return(sorted_bars, idx, prev.close, horizon=3)

        if vol_expansion < config.min_volume_expansion:
            continue
        if intraday_return < config.min_intraday_return_pct / 100.0 and close_return < config.min_close_return_pct / 100.0:
            continue

        strength = max(intraday_return, close_return, max_3day_return) * vol_expansion
        candidates.append(RunnerEvent(
            ticker=seed.ticker,
            seed_cohort=seed.cohort,
            event_date=bar.date,
            event_index=idx,
            prior_close=prev.close,
            max_intraday_return=intraday_return,
            close_return=close_return,
            max_3day_return=max_3day_return,
            volume_expansion=vol_expansion,
            event_strength_score=strength,
        ))

    return _dedupe_event_cluster(candidates, config.event_cooldown_trading_days)


def build_pre_event_snapshots(event: RunnerEvent, bars: list[DailyBar]) -> list[dict[str, Any]]:
    sorted_bars = _sorted_bars(bars)
    idx = _find_date_index(sorted_bars, event.event_date)
    if idx is None:
        return []
    previous_date = sorted_bars[idx - 1].date if idx > 0 else None
    rows: list[dict[str, Any]] = []
    for name in DAILY_SNAPSHOT_NAMES:
        offset = SNAPSHOT_OFFSETS[name]
        snapshot_idx = idx + offset
        if snapshot_idx < 0 or snapshot_idx >= len(sorted_bars):
            rows.append({
                "ticker": event.ticker,
                "runner_label_id": event.runner_label_id,
                "event_date": event.event_date,
                "snapshot_name": name,
                "status": "missing_daily_history",
                "required_offset_trading_days": offset,
            })
            continue

        snapshot_date = sorted_bars[snapshot_idx].date
        intraday_required = name in {"T0 premarket", "T0 open"}
        rows.append({
            "ticker": event.ticker,
            "runner_label_id": event.runner_label_id,
            "event_date": event.event_date,
            "snapshot_name": name,
            "snapshot_date": snapshot_date,
            "snapshot_timestamp_et": f"{snapshot_date}T{SNAPSHOT_TIMES_ET[name]}",
            "free_daily_cutoff_date": previous_date if intraday_required else snapshot_date,
            "scoreable_with_free_daily": not intraday_required,
            "status": "planned_not_scoreable_with_daily_only" if intraday_required else "ready_from_free_daily",
            "paid_l2_l3_download_required": False,
            "leakage_guard": "Do not use same-day daily OHLCV before it is available." if intraday_required else "Daily close is available at snapshot timestamp.",
        })
    return rows


def build_l2_l3_pull_plan(event: RunnerEvent, snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snap in snapshots:
        if snap.get("status") == "missing_daily_history":
            continue
        name = snap["snapshot_name"]
        start_time, end_time, purpose = L2_L3_WINDOW_TEMPLATES[name]
        rows.append({
            "ticker": event.ticker,
            "runner_label_id": event.runner_label_id,
            "event_date": event.event_date,
            "snapshot_name": name,
            "window_date": snap["snapshot_date"],
            "window_start_et": f"{snap['snapshot_date']}T{start_time}",
            "window_end_et": f"{snap['snapshot_date']}T{end_time}",
            "purpose": purpose,
            "schema": "mbo",
            "dataset_hint": "XNAS.ITCH_or_listing_venue",
            "download_now": False,
            "download_policy": "plan_only_until_free_daily_benchmark_passes",
        })
    return rows


def _cohort_row(event: RunnerEvent) -> dict[str, Any]:
    return {
        "ticker": event.ticker,
        "event_date": event.event_date,
        "event_start_timestamp": f"{event.event_date}T09:30:00",
        "pre_event_reference_timestamp": f"{_previous_calendar_day(event.event_date)}T20:00:00",
        "runner_label_id": event.runner_label_id,
        "event_strength": round(event.event_strength_score, 6),
        "max_intraday_return": round(event.max_intraday_return, 6),
        "max_3day_return": round(event.max_3day_return, 6),
        "volume_expansion": round(event.volume_expansion, 6),
        "float_state": "unknown_free_daily",
        "session_type": "regular_or_extended_unknown_from_daily",
        "primary_catalyst_type_if_known": "unknown_free_daily",
        "halt_flag": "unknown_free_daily",
        "dilution_after_event_flag": "unknown_free_daily",
        "delisting_status": "unknown_free_daily",
    }


def _write_outputs(output_dir: Path, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "runner_seed_resolution_manifest.json", payload)
    _write_json(output_dir / "runner_cohorts.json", payload["cohort_rows"])
    _write_json(output_dir / "snapshot_plan.json", payload["snapshot_rows"])
    _write_json(output_dir / "l2_l3_minimal_pull_plan.json", payload["l2_l3_minimal_pull_plan"])
    _write_json(output_dir / "unresolved_tickers.json", payload["unresolved_tickers"])
    _write_json(output_dir / "free_daily_benchmark.json", payload["free_daily_benchmark"])


def evaluate_free_daily_benchmark(
    events: list[RunnerEvent],
    seeds: list[SeedTicker],
    config: BenchmarkConfig,
) -> BenchmarkResult:
    metrics: dict[str, float] = {
        "n_resolved_events": float(len(events)),
        "n_resolved_tickers": float(len({e.ticker for e in events})),
        "n_seed_tickers": float(len(seeds)),
        "resolved_ticker_ratio": float(len({e.ticker for e in events})) / float(len(seeds)) if seeds else 0.0,
        "n_active_cohorts": float(len({e.seed_cohort for e in events})),
        "median_event_strength": _median([e.event_strength_score for e in events]) if events else 0.0,
        "share_of_events_in_largest_ticker": _largest_ticker_share(events) if events else 0.0,
    }

    failures: list[str] = []
    if metrics["n_resolved_events"] < config.min_resolved_events:
        failures.append(
            f"n_resolved_events={int(metrics['n_resolved_events'])} < min_resolved_events={config.min_resolved_events}"
        )
    if metrics["resolved_ticker_ratio"] < config.min_resolved_ticker_ratio:
        failures.append(
            f"resolved_ticker_ratio={metrics['resolved_ticker_ratio']:.4f} < min_resolved_ticker_ratio={config.min_resolved_ticker_ratio:.4f}"
        )
    if metrics["n_active_cohorts"] < config.min_active_cohorts:
        failures.append(
            f"n_active_cohorts={int(metrics['n_active_cohorts'])} < min_active_cohorts={config.min_active_cohorts}"
        )
    if events and metrics["median_event_strength"] < config.min_median_event_strength:
        failures.append(
            f"median_event_strength={metrics['median_event_strength']:.4f} < min_median_event_strength={config.min_median_event_strength:.4f}"
        )
    if events and metrics["share_of_events_in_largest_ticker"] > config.max_share_of_events_in_a_single_ticker:
        failures.append(
            f"share_of_events_in_largest_ticker={metrics['share_of_events_in_largest_ticker']:.4f} > max_share_of_events_in_a_single_ticker={config.max_share_of_events_in_a_single_ticker:.4f}"
        )

    passed = not failures
    lift = config.lift_l2_l3_download_when_passed and passed
    thresholds = {
        "min_resolved_events": float(config.min_resolved_events),
        "min_resolved_ticker_ratio": float(config.min_resolved_ticker_ratio),
        "min_active_cohorts": float(config.min_active_cohorts),
        "min_median_event_strength": float(config.min_median_event_strength),
        "max_share_of_events_in_a_single_ticker": float(config.max_share_of_events_in_a_single_ticker),
    }
    return BenchmarkResult(
        passed=passed,
        metrics=metrics,
        thresholds=thresholds,
        failures=failures,
        lift_l2_l3_download=lift,
    )


def _apply_benchmark_to_l2_l3_plan(
    plan: list[dict[str, Any]],
    benchmark: BenchmarkResult,
) -> list[dict[str, Any]]:
    if not benchmark.lift_l2_l3_download:
        return plan
    out: list[dict[str, Any]] = []
    for row in plan:
        updated = dict(row)
        updated["download_now"] = True
        updated["download_policy"] = "free_daily_benchmark_passed"
        out.append(updated)
    return out


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(median(values))


def _largest_ticker_share(events: list[RunnerEvent]) -> float:
    if not events:
        return 0.0
    counts: dict[str, int] = {}
    for event in events:
        counts[event.ticker] = counts.get(event.ticker, 0) + 1
    return max(counts.values()) / float(len(events))


def _load_symbol_daily_bars(root: Path, ticker: str) -> list[DailyBar]:
    ticker = ticker.upper()
    if root.is_file():
        return [b for b in load_daily_bars(root) if b.symbol.upper() == ticker]
    parquet = root / f"{ticker}.parquet"
    if parquet.exists():
        return load_daily_parquet(parquet, ticker)
    csv_path = root / f"{ticker}.csv"
    if csv_path.exists():
        return [b for b in load_daily_bars(csv_path) if b.symbol.upper() == ticker]
    return []


def _load_with_fallback(primary: Path, fallbacks: list[Path], ticker: str) -> tuple[list[DailyBar], str]:
    """Try primary root, then each fallback root in order. Returns (bars, source_label)."""
    bars = _load_symbol_daily_bars(primary, ticker)
    if bars:
        return bars, f"primary:{primary}"
    for fb in fallbacks:
        bars = _load_symbol_daily_bars(fb, ticker)
        if bars:
            return bars, f"fallback:{fb}"
    return [], "none"


def _flatten_expected_paths(roots: list[Path], ticker: str) -> list[str]:
    out: list[str] = []
    for r in roots:
        out.extend(_expected_daily_paths(r, ticker))
    return out


def _expected_daily_paths(root: Path, ticker: str) -> list[str]:
    if root.is_file():
        return [str(root)]
    return [str(root / f"{ticker}.csv"), str(root / f"{ticker}.parquet")]


def _detection_config(data: dict[str, Any]) -> DetectionConfig:
    raw = ((data.get("free_data_phase") or {}).get("event_detection") or {})
    return DetectionConfig(
        max_pre_event_close=float(raw.get("max_pre_event_close", 5.0)),
        min_intraday_return_pct=float(raw.get("min_intraday_return_pct", 30.0)),
        min_close_return_pct=float(raw.get("min_close_return_pct", 20.0)),
        min_volume_expansion=float(raw.get("min_volume_expansion", 3.0)),
        volume_lookback_days=int(raw.get("volume_lookback_days", 20)),
        event_cooldown_trading_days=int(raw.get("event_cooldown_trading_days", 5)),
    )


def _benchmark_config(data: dict[str, Any]) -> BenchmarkConfig:
    raw = ((data.get("free_data_phase") or {}).get("free_daily_benchmark") or {})
    rules = raw.get("rules") or {}
    return BenchmarkConfig(
        lift_l2_l3_download_when_passed=bool(raw.get("lift_l2_l3_download_when_passed", True)),
        min_resolved_events=int(rules.get("min_resolved_events", 5)),
        min_resolved_ticker_ratio=float(rules.get("min_resolved_ticker_ratio", 0.10)),
        min_active_cohorts=int(rules.get("min_active_cohorts", 2)),
        min_median_event_strength=float(rules.get("min_median_event_strength", 1.0)),
        max_share_of_events_in_a_single_ticker=float(rules.get("max_share_of_events_in_a_single_ticker", 0.75)),
    )


def _delisted_tickers(data: dict[str, Any]) -> set[str]:
    block = data.get("delisted_seed_tickers") or {}
    raw = block.get("known_delisted") or []
    return {str(t).strip().upper() for t in raw if str(t).strip()}


def _cfg_path(data: dict[str, Any], cfg_path: Path, key: str, default: str) -> Path:
    repo = Path(data.get("repo_root", "."))
    if not repo.is_absolute():
        repo = (cfg_path.parent.parent.parent.parent / repo).resolve()
    paths = data.get("paths") or {}
    return repo / str(paths.get(key, default))


def _target_year(cohort: str) -> int | None:
    text = str(cohort)
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _sorted_bars(bars: list[DailyBar]) -> list[DailyBar]:
    return sorted(bars, key=lambda b: b.date)


def _find_date_index(bars: list[DailyBar], value: str) -> int | None:
    for i, bar in enumerate(bars):
        if bar.date == value:
            return i
    return None


def _max_forward_high_return(bars: list[DailyBar], idx: int, prior_close: float, horizon: int) -> float:
    if prior_close <= 0:
        return 0.0
    window = bars[idx:idx + horizon]
    if not window:
        return 0.0
    return (max(b.high for b in window) - prior_close) / prior_close


def _dedupe_event_cluster(events: list[RunnerEvent], cooldown: int) -> list[RunnerEvent]:
    kept: list[RunnerEvent] = []
    last_idx: int | None = None
    for event in events:
        if last_idx is not None and event.event_index - last_idx <= cooldown:
            continue
        kept.append(event)
        last_idx = event.event_index
    return kept


def _previous_calendar_day(value: str) -> str:
    return (_parse_date(value) - timedelta(days=1)).isoformat()


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(str(value)[:10]).date()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
