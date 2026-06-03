"""Free daily runner seed resolver tests."""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from equities_lane.src.prediction.runner_seed_resolver import (
    build_pre_event_snapshots,
    detect_runner_events,
    load_seed_tickers,
    resolve_runner_seed_events,
)
from equities_lane.src.models import DailyBar


def _business_days(start: date, n: int) -> list[str]:
    out: list[str] = []
    current = start
    while len(out) < n:
        if current.weekday() < 5:
            out.append(current.isoformat())
        current += timedelta(days=1)
    return out


def _write_seed_config(path: Path, daily_root: Path) -> None:
    path.write_text(
        "repo_root: .\n"
        "paths:\n"
        f"  daily_root: {daily_root.as_posix()}\n"
        "free_data_phase:\n"
        "  event_detection:\n"
        "    max_pre_event_close: 5.0\n"
        "    min_intraday_return_pct: 30.0\n"
        "    min_close_return_pct: 20.0\n"
        "    min_volume_expansion: 3.0\n"
        "    volume_lookback_days: 3\n"
        "    event_cooldown_trading_days: 5\n"
        "positive_seed_tickers:\n"
        "  '2024':\n"
        "    - ABCD\n"
        "    - MISS\n"
        "delisted_seed_tickers:\n"
        "  known_delisted: [ZZZZ, YYYY]\n",
        encoding="utf-8",
    )


def _daily_rows(symbol: str = "ABCD") -> list[dict[str, object]]:
    dates = _business_days(date(2024, 1, 2), 16)
    rows: list[dict[str, object]] = []
    for i, d in enumerate(dates):
        row = {
            "symbol": symbol,
            "date": d,
            "open": 1.0,
            "high": 1.02,
            "low": 0.98,
            "close": 1.0,
            "volume": 1000,
        }
        if i == 11:
            row.update({"open": 1.05, "high": 1.60, "close": 1.35, "volume": 10000})
        if i == 12:
            row.update({"open": 1.30, "high": 1.80, "close": 1.50, "volume": 6000})
        rows.append(row)
    return rows


def _write_daily_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)


def test_real_seed_catalog_has_expected_positive_tickers():
    repo = Path(__file__).resolve().parents[2]
    seeds = load_seed_tickers(repo / "packages" / "equities_lane" / "config" / "historical_runner_benchmark.yaml")
    tickers = [s.ticker for s in seeds]

    assert len(tickers) == 54
    assert len(set(tickers)) == 54
    assert {"TBLT", "TOP", "SERV", "MLGO", "ABTS"}.issubset(tickers)
    assert next(s for s in seeds if s.ticker == "TOP").target_year == 2023
    assert next(s for s in seeds if s.ticker == "ABTS").target_year == 2026


def test_detect_runner_events_from_daily_bars():
    rows = _daily_rows()
    bars = [
        DailyBar(
            symbol=str(r["symbol"]),
            date=str(r["date"]),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r["volume"]),
        )
        for r in rows
    ]
    seed = load_seed_tickers_from_inline("ABCD", "2024")

    events = detect_runner_events(seed, bars, seed_config_for_test())

    assert len(events) == 1
    assert events[0].event_date == rows[11]["date"]
    assert round(events[0].max_intraday_return, 2) == 0.60
    assert round(events[0].max_3day_return, 2) == 0.80


def test_build_pre_event_snapshots_marks_t0_as_not_daily_scoreable():
    rows = _daily_rows()
    bars = [
        DailyBar(str(r["symbol"]), str(r["date"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"]))
        for r in rows
    ]
    event = detect_runner_events(load_seed_tickers_from_inline("ABCD", "2024"), bars, seed_config_for_test())[0]

    snapshots = build_pre_event_snapshots(event, bars)
    by_name = {row["snapshot_name"]: row for row in snapshots}

    assert by_name["T-10 trading days"]["snapshot_date"] == rows[1]["date"]
    assert by_name["T-10 trading days"]["scoreable_with_free_daily"] is True
    assert by_name["T0 premarket"]["scoreable_with_free_daily"] is False
    assert by_name["T0 premarket"]["free_daily_cutoff_date"] == rows[10]["date"]
    assert "Do not use same-day daily OHLCV" in by_name["T0 open"]["leakage_guard"]


def test_resolve_runner_seed_events_writes_free_daily_plan(tmp_path):
    daily_root = tmp_path / "daily"
    seed_config = tmp_path / "seeds.yaml"
    output = tmp_path / "out"
    _write_seed_config(seed_config, daily_root)
    _write_daily_csv(daily_root / "ABCD.csv", _daily_rows())

    result = resolve_runner_seed_events(seed_config, daily_root=daily_root, output_dir=output)

    assert result["no_paid_market_data_downloads"] is True
    assert result["n_seed_tickers"] == 2
    assert result["n_resolved_events"] == 1
    assert result["n_unresolved_tickers"] == 1
    assert result["cohort_rows"][0]["ticker"] == "ABCD"
    assert result["unresolved_tickers"][0]["ticker"] == "MISS"
    assert result["unresolved_tickers"][0]["reason"] == "missing_daily_bars"
    assert sorted(result["delisted_seed_tickers"]) == ["YYYY", "ZZZZ"]
    assert result["l2_l3_minimal_pull_plan"][0]["download_now"] is False
    assert result["l2_l3_minimal_pull_plan"][0]["download_policy"] == "plan_only_until_free_daily_benchmark_passes"
    assert (output / "runner_seed_resolution_manifest.json").exists()
    assert (output / "runner_cohorts.json").exists()
    assert (output / "snapshot_plan.json").exists()
    assert (output / "l2_l3_minimal_pull_plan.json").exists()


def load_seed_tickers_from_inline(ticker: str, cohort: str):
    from equities_lane.src.prediction.runner_seed_resolver import SeedTicker

    return SeedTicker(ticker=ticker, cohort=cohort, target_year=int(cohort))


def seed_config_for_test():
    from equities_lane.src.prediction.runner_seed_resolver import DetectionConfig

    return DetectionConfig(volume_lookback_days=3)


def test_free_daily_benchmark_fails_when_not_enough_events(tmp_path):
    daily_root = tmp_path / "daily"
    seed_config = tmp_path / "seeds.yaml"
    output = tmp_path / "out"
    _write_seed_config(seed_config, daily_root)
    _write_daily_csv(daily_root / "ABCD.csv", _daily_rows())

    result = resolve_runner_seed_events(seed_config, daily_root=daily_root, output_dir=output)

    benchmark = result["free_daily_benchmark"]
    assert benchmark["passed"] is False
    assert benchmark["lift_l2_l3_download"] is False
    assert benchmark["metrics"]["n_resolved_events"] == 1
    assert any("n_resolved_events" in f for f in benchmark["failures"])
    assert result["l2_l3_minimal_pull_plan"][0]["download_now"] is False
    assert result["l2_l3_minimal_pull_plan"][0]["download_policy"] == "plan_only_until_free_daily_benchmark_passes"
    assert (output / "free_daily_benchmark.json").exists()


def test_free_daily_benchmark_lifts_l2_l3_when_passed():
    from equities_lane.src.prediction.runner_seed_resolver import (
        BenchmarkConfig,
        RunnerEvent,
        SeedTicker,
        evaluate_free_daily_benchmark,
    )

    seeds = [
        SeedTicker("A", "2024", 2024),
        SeedTicker("B", "2024", 2024),
        SeedTicker("C", "2025", 2025),
        SeedTicker("D", "2025", 2025),
        SeedTicker("E", "2025", 2025),
    ]
    events = [
        RunnerEvent("A", "2024", "2024-05-01", 1, 1.0, 0.5, 0.5, 0.6, 4.0, 2.0),
        RunnerEvent("B", "2024", "2024-06-01", 1, 1.0, 0.4, 0.4, 0.5, 3.0, 1.5),
        RunnerEvent("C", "2025", "2025-05-01", 1, 1.0, 0.3, 0.3, 0.4, 3.5, 1.4),
        RunnerEvent("D", "2025", "2025-06-01", 1, 1.0, 0.6, 0.5, 0.7, 5.0, 3.0),
        RunnerEvent("E", "2025", "2025-07-01", 1, 1.0, 0.5, 0.4, 0.6, 4.5, 2.5),
    ]

    benchmark = evaluate_free_daily_benchmark(events, seeds, BenchmarkConfig())

    assert benchmark.passed is True
    assert benchmark.lift_l2_l3_download is True
    assert benchmark.metrics["n_active_cohorts"] == 2
    assert benchmark.metrics["resolved_ticker_ratio"] == 1.0
    assert benchmark.failures == []


def test_free_daily_benchmark_blocks_concentration():
    from equities_lane.src.prediction.runner_seed_resolver import (
        BenchmarkConfig,
        RunnerEvent,
        SeedTicker,
        evaluate_free_daily_benchmark,
    )

    seeds = [SeedTicker(f"T{i}", "2024", 2024) for i in range(4)]
    events = [
        RunnerEvent("T0", "2024", "2024-05-01", 1, 1.0, 0.5, 0.5, 0.6, 4.0, 2.0),
        RunnerEvent("T0", "2024", "2024-06-01", 1, 1.0, 0.4, 0.4, 0.5, 3.0, 1.5),
        RunnerEvent("T0", "2024", "2024-07-01", 1, 1.0, 0.6, 0.5, 0.7, 5.0, 3.0),
        RunnerEvent("T0", "2024", "2024-08-01", 1, 1.0, 0.5, 0.4, 0.6, 4.5, 2.5),
        RunnerEvent("T1", "2024", "2024-05-15", 1, 1.0, 0.3, 0.3, 0.4, 3.5, 1.4),
    ]

    benchmark = evaluate_free_daily_benchmark(events, seeds, BenchmarkConfig())

    assert benchmark.passed is False
    assert benchmark.lift_l2_l3_download is False
    assert any("largest_ticker" in f for f in benchmark.failures)


def test_resolve_runner_seed_events_marks_delisted_as_data_source_exhausted(tmp_path):
    daily_root = tmp_path / "daily"
    seed_config = tmp_path / "seeds.yaml"
    output = tmp_path / "out"
    seed_config.write_text(
        "repo_root: .\n"
        "paths:\n"
        f"  daily_root: {daily_root.as_posix()}\n"
        "free_data_phase:\n"
        "  event_detection:\n"
        "    volume_lookback_days: 3\n"
        "    event_cooldown_trading_days: 5\n"
        "positive_seed_tickers:\n"
        "  '2024':\n"
        "    - ABCD\n"
        "    - DELIST\n"
        "delisted_seed_tickers:\n"
        "  known_delisted: [DELIST]\n",
        encoding="utf-8",
    )
    _write_daily_csv(daily_root / "ABCD.csv", _daily_rows())

    result = resolve_runner_seed_events(seed_config, daily_root=daily_root, output_dir=output)

    delisted_record = next(r for r in result["unresolved_tickers"] if r["ticker"] == "DELIST")
    assert delisted_record["reason"] == "data_source_exhausted_free_daily"
    assert "delisted" in delisted_record["note"].lower()
    assert result["delisted_seed_tickers"] == ["DELIST"]
    assert (output / "runner_seed_resolution_manifest.json").exists()
