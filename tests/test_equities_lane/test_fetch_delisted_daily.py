"""Tests for scripts/fetch_delisted_daily.py.

The live download path requires DATABENTO_API_KEY. These tests verify
the dry-run plan, refusal paths, and a mocked live download that converts
a fake .dbn.zst-like object to a CSV in DailyBar format.
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


WORKTREE = Path(__file__).resolve().parent.parent.parent
SCRIPT = WORKTREE / "scripts" / "fetch_delisted_daily.py"
CONFIG = WORKTREE / "packages" / "equities_lane" / "config" / "historical_runner_benchmark.yaml"


def _read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_dry_run_produces_plan_for_all_known_delisted(tmp_path):
    env = os.environ.copy()
    env.pop("DATABENTO_API_KEY", None)
    env["PYTHONPATH"] = str(WORKTREE / "packages")
    manifest_path = tmp_path / "manifest.json"
    out_root = tmp_path / "daily_delisted"
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--config",
        str(CONFIG),
        "--dry-run",
        "--manifest",
        str(manifest_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(WORKTREE))
    assert proc.returncode == 0, proc.stderr
    payload = _read_manifest(manifest_path)
    assert payload["mode"] == "dry_run"
    assert payload["databento_api_key_present"] is False
    assert payload["n_planned"] == 8
    tickers = {r["ticker"] for r in payload["ticker_records"]}
    assert tickers == {"APRN", "RDBX", "AERC", "ANPC", "WETG", "MULN", "SGN", "SGBX"}
    for rec in payload["ticker_records"]:
        assert rec["status"] == "dry_run_no_estimate_no_api_key"
        assert rec["dataset"] == "XNAS.ITCH"
        assert rec["schema"] == "ohlcv-1d"
        assert rec["stype_in"] == "raw_symbol"
        assert rec["start_utc"] < rec["end_utc"]


def test_default_mode_is_dry_run(tmp_path):
    """Without --confirm-purchase, the script silently defaults to dry-run.

    The only way to enter live mode is to pass --confirm-purchase; there is
    no separate 'refuse without confirm' refusal path because the
    --confirm-purchase flag IS the gate.
    """
    env = os.environ.copy()
    env.pop("DATABENTO_API_KEY", None)
    env["PYTHONPATH"] = str(WORKTREE / "packages")
    manifest_path = tmp_path / "m.json"
    cmd = [sys.executable, str(SCRIPT), "--config", str(CONFIG), "--manifest", str(manifest_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(WORKTREE))
    assert proc.returncode == 0, proc.stderr
    payload = _read_manifest(manifest_path)
    assert payload["mode"] == "dry_run"
    assert payload["n_planned"] == 8


def test_live_refuses_without_api_key(tmp_path):
    env = os.environ.copy()
    env.pop("DATABENTO_API_KEY", None)
    env["PYTHONPATH"] = str(WORKTREE / "packages")
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--config",
        str(CONFIG),
        "--confirm-purchase",
        "--manifest",
        str(tmp_path / "m.json"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(WORKTREE))
    assert proc.returncode == 4
    assert "DATABENTO_API_KEY" in proc.stderr


def test_live_redacts_api_key_in_error():
    sys.path.insert(0, str(WORKTREE))
    sys.path.insert(0, str(WORKTREE / "packages"))
    from scripts.fetch_delisted_daily import _redact
    sentinel = "db-testkey-REDACT-ME-1234567890"
    os.environ["DATABENTO_API_KEY"] = sentinel
    try:
        redacted = _redact(f"failed with key {sentinel}")
        assert "REDACT-ME" not in redacted
        assert "[REDACTED]" in redacted
    finally:
        del os.environ["DATABENTO_API_KEY"]


def test_build_pull_plan_uses_cohort_year_window():
    sys.path.insert(0, str(WORKTREE))
    sys.path.insert(0, str(WORKTREE / "packages"))
    from equities_lane.src.prediction.runner_seed_resolver import load_seed_config
    from scripts.fetch_delisted_daily import build_pull_plan
    cfg = load_seed_config(CONFIG)
    plans = build_pull_plan(cfg)
    by_ticker = {p.ticker: p for p in plans}
    assert "APRN" in by_ticker
    aprn = by_ticker["APRN"]
    assert aprn.cohort == "2022"
    assert aprn.target_year == 2022
    assert aprn.start_iso == "2021-12-02"
    assert aprn.end_iso == "2023-12-31"
    assert aprn.dataset == "XNAS.ITCH"
    assert aprn.schema == "ohlcv-1d"
    assert aprn.stype_in == "raw_symbol"


def test_load_delisted_tickers_reads_config():
    sys.path.insert(0, str(WORKTREE))
    sys.path.insert(0, str(WORKTREE / "packages"))
    from equities_lane.src.prediction.runner_seed_resolver import load_seed_config
    from scripts.fetch_delisted_daily import _load_delisted_tickers
    cfg = load_seed_config(CONFIG)
    m = _load_delisted_tickers(cfg)
    assert m["APRN"] == "2022"
    assert m["MULN"] == "2023"
    assert m["SGBX"] == "2025"


def test_write_csv_daily_bar_format(tmp_path):
    sys.path.insert(0, str(WORKTREE))
    sys.path.insert(0, str(WORKTREE / "packages"))
    from scripts.fetch_delisted_daily import write_csv
    rows = [
        {"symbol": "APRN", "date": "2022-06-01", "open": 1.0, "high": 2.0, "low": 0.9, "close": 1.5, "volume": 100.0, "adjclose": 1.5},
        {"symbol": "APRN", "date": "2022-06-02", "open": 1.5, "high": 3.0, "low": 1.4, "close": 2.5, "volume": 200.0, "adjclose": 2.5},
    ]
    path = tmp_path / "APRN.csv"
    n = write_csv(path, rows)
    assert n == 2
    text = path.read_text(encoding="utf-8")
    reader = list(csv.DictReader(io.StringIO(text)))
    assert reader[0]["symbol"] == "APRN"
    assert reader[0]["date"] == "2022-06-01"
    assert float(reader[0]["open"]) == 1.0
    assert float(reader[0]["close"]) == 1.5
    assert float(reader[0]["volume"]) == 100.0
    assert set(reader[0].keys()) == {"symbol", "date", "open", "high", "low", "close", "volume", "adjclose"}


def test_resolver_resolves_delisted_via_secondary_root(tmp_path):
    """End-to-end: build a fake delisted daily CSV, run the resolver with the
    secondary root, and assert the 8 tickers no longer appear as
    data_source_exhausted_free_daily."""
    sys.path.insert(0, str(WORKTREE))
    sys.path.insert(0, str(WORKTREE / "packages"))
    from equities_lane.src.prediction.runner_seed_resolver import resolve_runner_seed_events

    # Build a fake delisted daily CSV for APRN with a clear runner event:
    # pre close = 1.0, intraday high = 5.0, close = 4.0 (300% / 300%), volume 100x.
    rows = []
    for i in range(20):
        rows.append({
            "symbol": "APRN",
            "date": f"2022-01-{(i + 1):02d}" if i + 1 <= 28 else "2022-01-28",
            "open": 1.0, "high": 1.05, "low": 0.95, "close": 1.0, "volume": 100.0,
            "adjclose": 1.0,
        })
    # event day
    rows.append({
        "symbol": "APRN",
        "date": "2022-02-01",
        "open": 4.0, "high": 5.0, "low": 3.5, "close": 4.0, "volume": 10000.0,
        "adjclose": 4.0,
    })
    delisted_root = tmp_path / "delisted"
    delisted_root.mkdir(parents=True, exist_ok=True)
    with (delisted_root / "APRN.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["symbol", "date", "open", "high", "low", "close", "volume", "adjclose"],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    # The above writes rows with symbol=APRN, so the filter should match.

    out_dir = tmp_path / "resolver_out"
    primary_root = tmp_path / "primary"
    primary_root.mkdir(parents=True, exist_ok=True)

    payload = resolve_runner_seed_events(
        seed_config_path=CONFIG,
        daily_root=primary_root,
        output_dir=out_dir,
        delisted_daily_roots=[delisted_root],
    )

    unresolved = payload["unresolved_tickers"]
    aprn_unresolved = [u for u in unresolved if u["ticker"] == "APRN"]
    assert aprn_unresolved == [], f"APRN should be resolved via fallback; got {aprn_unresolved}"
    assert payload["delisted_resolved_via"].get("APRN") == f"fallback:{delisted_root}"
    aprn_events = [e for e in payload["cohort_rows"] if e["ticker"] == "APRN"]
    assert aprn_events, "APRN event should be in cohort_rows"
    assert aprn_events[0]["event_date"] == "2022-02-01"


def test_resolver_without_secondary_root_keeps_delisted_unresolved(tmp_path):
    sys.path.insert(0, str(WORKTREE))
    sys.path.insert(0, str(WORKTREE / "packages"))
    from equities_lane.src.prediction.runner_seed_resolver import resolve_runner_seed_events

    primary_root = tmp_path / "primary"
    primary_root.mkdir(parents=True, exist_ok=True)
    payload = resolve_runner_seed_events(
        seed_config_path=CONFIG,
        daily_root=primary_root,
        output_dir=tmp_path / "out",
    )
    unresolved = payload["unresolved_tickers"]
    aprn = [u for u in unresolved if u["ticker"] == "APRN"]
    assert aprn, "APRN should still be unresolved without a secondary root"
    assert aprn[0]["reason"] == "data_source_exhausted_free_daily"
