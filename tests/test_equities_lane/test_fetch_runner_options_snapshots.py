"""Tests for runner options snapshot fetcher (no real API key required)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]
SCRIPTS = WORKTREE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from fetch_runner_options_snapshots import (  # noqa: E402
    _filter_executable,
    _paths_for_row,
    build_manifest,
)


def _row(ticker: str = "ABCD", *, download_now: bool = True) -> dict[str, object]:
    return {
        "ticker": ticker,
        "runner_label_id": f"{ticker}-2024-01-18",
        "event_date": "2024-01-18",
        "snapshot_name": "T-1 close",
        "equity_snapshot_timestamp_et": "2024-01-17T16:00:00",
        "options_reference_timestamp_et": "2024-01-17T16:00:00",
        "options_window_start_et": "2024-01-17T15:59:00",
        "options_window_end_et": "2024-01-17T16:00:00",
        "options_market_status": "options_regular_session",
        "underlying_parent_symbol": f"{ticker}.OPT",
        "dataset": "OPRA.PILLAR",
        "schema": "cbbo-1m",
        "stype_in": "parent",
        "quote_window_seconds": 60,
        "download_now": download_now,
        "download_policy": "free_daily_benchmark_passed" if download_now else "plan_only_until_free_daily_benchmark_passes",
        "purpose": "options feature snapshot aligned to equity runner snapshot",
        "leakage_guard": "test",
    }


def _write_plan(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_filter_executable_only_keeps_lifted_rows():
    rows = [_row("AAA", download_now=True), _row("BBB", download_now=False)]
    assert [r["ticker"] for r in _filter_executable(rows)] == ["AAA"]


def test_paths_for_row_are_equities_feature_paths(tmp_path):
    raw, norm = _paths_for_row(tmp_path, _row("ABCD"))
    assert raw.as_posix().endswith("raw/ABCD/ABCD-2024-01-18/2024-01-17_155900_160000_t-1_close_cbbo-1m.dbn.zst")
    assert norm.as_posix().endswith("normalized/ABCD/ABCD-2024-01-18/2024-01-17_155900_160000_t-1_close_cbbo-1m.ndjson")


def test_dry_run_without_key_does_not_estimate(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    plan = tmp_path / "options_snapshot_plan.json"
    _write_plan(plan, [_row("AAA"), _row("BBB")])

    manifest = build_manifest(
        plan,
        tmp_path / "options",
        dry_run=True,
        confirm_purchase=False,
        max_total_cost_usd=None,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )

    assert manifest["status"] == "completed"
    assert manifest["n_executable_rows"] == 2
    assert [r["status"] for r in manifest["ticker_records"]] == [
        "dry_run_no_estimate_no_api_key",
        "dry_run_no_estimate_no_api_key",
    ]


def test_dry_run_cost_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    plan = tmp_path / "options_snapshot_plan.json"
    _write_plan(plan, [_row("AAA"), _row("BBB"), _row("CCC")])
    monkeypatch.setattr("fetch_runner_options_snapshots._estimate_row_cost", lambda row: 0.75)

    manifest = build_manifest(
        plan,
        tmp_path / "options",
        dry_run=True,
        confirm_purchase=False,
        max_total_cost_usd=1.0,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )

    assert [r["status"] for r in manifest["ticker_records"]] == [
        "dry_run_estimate",
        "skipped_total_cost_cap",
        "skipped_total_cost_cap",
    ]
    assert manifest["estimated_total_cost_usd"] == 0.75


def test_existing_normalized_file_skips_before_costing(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    plan = tmp_path / "options_snapshot_plan.json"
    row = _row("AAA")
    _write_plan(plan, [row])
    _, norm = _paths_for_row(tmp_path / "options", row)
    norm.parent.mkdir(parents=True, exist_ok=True)
    norm.write_text('{"ok": true}\n', encoding="utf-8")

    def _boom(_row):
        raise AssertionError("estimate should not run for already-normalized snapshots")

    monkeypatch.setattr("fetch_runner_options_snapshots._estimate_row_cost", _boom)
    manifest = build_manifest(
        plan,
        tmp_path / "options",
        dry_run=False,
        confirm_purchase=True,
        max_total_cost_usd=None,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )

    assert manifest["mode"] == "confirmed_download"
    assert manifest["status"] == "confirmed_download_completed"
    assert manifest["ticker_records"][0]["status"] == "skipped_already_normalized"
    assert manifest["estimated_total_cost_usd"] == 0.0


def test_existing_empty_normalized_file_is_terminal_no_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    plan = tmp_path / "options_snapshot_plan.json"
    row = _row("AAA")
    _write_plan(plan, [row])
    _, norm = _paths_for_row(tmp_path / "options", row)
    norm.parent.mkdir(parents=True, exist_ok=True)
    norm.write_text("", encoding="utf-8")

    def _boom(_row):
        raise AssertionError("estimate should not run for already-normalized no-data snapshots")

    monkeypatch.setattr("fetch_runner_options_snapshots._estimate_row_cost", _boom)
    manifest = build_manifest(
        plan,
        tmp_path / "options",
        dry_run=False,
        confirm_purchase=True,
        max_total_cost_usd=None,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )

    assert manifest["ticker_records"][0]["status"] == "skipped_already_empty_normalized"
    assert manifest["ticker_records"][0]["normalized_size_bytes"] == 0
    assert manifest["estimated_total_cost_usd"] == 0.0


def test_existing_raw_normalizes_before_costing(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    plan = tmp_path / "options_snapshot_plan.json"
    row = _row("AAA")
    _write_plan(plan, [row])
    raw, norm = _paths_for_row(tmp_path / "options", row)
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"dbn")

    def _boom(_row):
        raise AssertionError("estimate should not run for already-downloaded raw snapshots")

    def _normalize(raw_path, output_path, *, session_id, underlying):
        assert raw_path == raw
        assert session_id == "AAA-2024-01-18"
        assert underlying == "AAA"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"ok": true}\n', encoding="utf-8")
        return 1

    monkeypatch.setattr("fetch_runner_options_snapshots._estimate_row_cost", _boom)
    monkeypatch.setattr("fetch_runner_options_snapshots.normalize_options_dbn", _normalize)
    manifest = build_manifest(
        plan,
        tmp_path / "options",
        dry_run=False,
        confirm_purchase=True,
        max_total_cost_usd=None,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )

    assert manifest["ticker_records"][0]["status"] == "normalized_existing_raw"
    assert manifest["ticker_records"][0]["resolved_symbol_count"] == 1
    assert norm.exists()
    assert manifest["estimated_total_cost_usd"] == 0.0


def test_terminal_symbology_failure_is_cached_and_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    plan = tmp_path / "options_snapshot_plan.json"
    _write_plan(plan, [_row("RDBX")])
    calls = {"n": 0}

    def _fail(_row):
        calls["n"] += 1
        raise RuntimeError("422 symbology_invalid_request None of the symbols could be resolved")

    monkeypatch.setattr("fetch_runner_options_snapshots._estimate_row_cost", _fail)

    first = build_manifest(
        plan,
        tmp_path / "options",
        dry_run=True,
        confirm_purchase=False,
        max_total_cost_usd=None,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )
    assert first["ticker_records"][0]["status"] == "estimate_failed"
    failure_path = Path(first["ticker_records"][0]["failure_path"])
    assert failure_path.exists()

    second = build_manifest(
        plan,
        tmp_path / "options",
        dry_run=True,
        confirm_purchase=False,
        max_total_cost_usd=None,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )
    assert second["ticker_records"][0]["status"] == "skipped_terminal_failure"
    assert calls["n"] == 1


def test_cli_refuses_confirmed_download_without_key(tmp_path):
    plan = tmp_path / "options_snapshot_plan.json"
    _write_plan(plan, [_row("AAA")])
    env = os.environ.copy()
    env.pop("DATABENTO_API_KEY", None)
    env["PYTHONPATH"] = str(WORKTREE / "packages")
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "fetch_runner_options_snapshots.py"),
            "--plan",
            str(plan),
            "--output-dir",
            str(tmp_path / "options"),
            "--confirm-purchase",
        ],
        cwd=str(WORKTREE),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 4
    assert "DATABENTO_API_KEY" in proc.stderr
    assert "confirmed downloads" in proc.stderr
