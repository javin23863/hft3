"""Tests for the L2/L3 Databento fetcher (no real API key required)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[2]
SCRIPTS = WORKTREE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from fetch_l2_l3_databento import (  # noqa: E402
    _filter_executable,
    _group_by_ticker,
    _parse_et,
    _ticker_window_bounds,
    build_manifest,
)


def _write_plan(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _row(ticker: str, snapshot_name: str, start: str, end: str, **extras) -> dict:
    base = {
        "ticker": ticker,
        "snapshot_name": snapshot_name,
        "window_date": start[:10],
        "window_start_et": start,
        "window_end_et": end,
        "purpose": "test",
        "schema": "mbo",
        "dataset_hint": "XNAS.ITCH",
    }
    base.update(extras)
    return base


def test_filter_executable_keeps_only_lifted_rows():
    rows = [
        _row("A", "T-1 close", "2024-05-28T09:30:00", "2024-05-28T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
        _row("A", "T0 open", "2024-05-29T09:25:00", "2024-05-29T09:35:00", download_now=False, download_policy="plan_only_until_free_daily_benchmark_passes"),
        _row("B", "T-1 close", "2024-06-01T09:30:00", "2024-06-01T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
    ]
    out = _filter_executable(rows)
    assert len(out) == 2
    assert {r["ticker"] for r in out} == {"A", "B"}


def test_group_by_ticker_groups_correctly():
    rows = [
        _row("A", "T-1 close", "2024-05-28T09:30:00", "2024-05-28T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
        _row("A", "T0 open", "2024-05-29T09:25:00", "2024-05-29T09:35:00", download_now=True, download_policy="free_daily_benchmark_passed"),
        _row("B", "T-1 close", "2024-06-01T09:30:00", "2024-06-01T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
    ]
    grouped = _group_by_ticker(_filter_executable(rows))
    assert set(grouped.keys()) == {"A", "B"}
    assert len(grouped["A"]) == 2
    assert len(grouped["B"]) == 1


def test_ticker_window_bounds_picks_min_start_max_end():
    rows = [
        _row("A", "T-1 close", "2024-05-28T09:30:00", "2024-05-28T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
        _row("A", "T0 open", "2024-05-29T09:25:00", "2024-05-29T09:35:00", download_now=True, download_policy="free_daily_benchmark_passed"),
        _row("A", "T-2 trading days", "2024-05-24T04:00:00", "2024-05-24T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
    ]
    start, end, n = _ticker_window_bounds(rows)
    assert n == 3
    assert str(start.date()) == "2024-05-24"
    assert str(end.date()) == "2024-05-29"
    assert start.utcoffset().total_seconds() == 0
    assert end.utcoffset().total_seconds() == 0


def test_parse_et_handles_offsets():
    parsed = _parse_et("2024-05-29T09:35:00")
    assert parsed.utcoffset().total_seconds() == 0
    assert parsed.hour == 13
    assert parsed.minute == 35


def test_dry_run_works_without_api_key(tmp_path, monkeypatch):
    plan_path = tmp_path / "plan.json"
    rows = [
        _row("A", "T-1 close", "2024-05-28T09:30:00", "2024-05-28T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
        _row("A", "T0 open", "2024-05-29T09:25:00", "2024-05-29T09:35:00", download_now=True, download_policy="free_daily_benchmark_passed"),
        _row("A", "T-2 trading days", "2024-05-24T04:00:00", "2024-05-24T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
    ]
    _write_plan(plan_path, rows)
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)

    out_dir = tmp_path / "l2_l3"
    manifest = build_manifest(
        plan_path,
        out_dir,
        dry_run=True,
        confirm_purchase=False,
        dataset="XNAS.ITCH",
        schema="mbo",
        stype_in="raw_symbol",
        override_operating_cap=False,
        override_hard_limit=False,
        max_total_cost_usd=None,
        max_tickers=None,
    )

    assert manifest["mode"] == "dry_run"
    assert manifest["status"] == "completed"
    assert manifest["databento_api_key_present"] is False
    assert manifest["n_executable_rows"] == 3
    assert manifest["n_executable_tickers"] == 1
    assert manifest["n_plan_only_rows"] == 0
    rec = manifest["ticker_records"][0]
    assert rec["ticker"] == "A"
    assert rec["n_windows"] == 3
    assert rec["status"] == "dry_run_no_estimate_no_api_key"
    assert (out_dir / "l2_l3_fetch_manifest.json").exists()


def test_confirmed_download_refuses_without_confirm_purchase(tmp_path):
    plan_path = tmp_path / "plan.json"
    rows = [
        _row("A", "T-1 close", "2024-05-28T09:30:00", "2024-05-28T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
    ]
    _write_plan(plan_path, rows)
    out_dir = tmp_path / "l2_l3"
    manifest = build_manifest(
        plan_path,
        out_dir,
        dry_run=False,
        confirm_purchase=False,
        dataset="XNAS.ITCH",
        schema="mbo",
        stype_in="raw_symbol",
        override_operating_cap=False,
        override_hard_limit=False,
        max_total_cost_usd=None,
        max_tickers=None,
    )
    assert manifest["mode"] == "confirmed_download"
    assert manifest["status"] == "refused"
    assert "--confirm-purchase" in manifest["refusal_reason"]
    assert "confirmed downloads" in manifest["refusal_reason"]
    assert "ticker_records" not in manifest


def test_confirmed_download_refuses_without_api_key(tmp_path, monkeypatch):
    plan_path = tmp_path / "plan.json"
    rows = [
        _row("A", "T-1 close", "2024-05-28T09:30:00", "2024-05-28T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
    ]
    _write_plan(plan_path, rows)
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    out_dir = tmp_path / "l2_l3"
    manifest = build_manifest(
        plan_path,
        out_dir,
        dry_run=False,
        confirm_purchase=True,
        dataset="XNAS.ITCH",
        schema="mbo",
        stype_in="raw_symbol",
        override_operating_cap=False,
        override_hard_limit=False,
        max_total_cost_usd=None,
        max_tickers=None,
    )
    assert manifest["mode"] == "confirmed_download"
    assert manifest["status"] == "refused"
    assert "DATABENTO_API_KEY" in manifest["refusal_reason"]
    assert "confirmed downloads" in manifest["refusal_reason"]


def test_dry_run_estimate_failure_is_recorded(tmp_path, monkeypatch):
    plan_path = tmp_path / "plan.json"
    rows = [
        _row("A", "T-1 close", "2024-05-28T09:30:00", "2024-05-28T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
    ]
    _write_plan(plan_path, rows)
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)

    from fetch_l2_l3_databento import _estimate_ticker_cost  # noqa: E402

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated estimate failure")

    monkeypatch.setattr("fetch_l2_l3_databento._estimate_available", lambda: True)
    monkeypatch.setattr("fetch_l2_l3_databento._estimate_ticker_cost", _boom)

    out_dir = tmp_path / "l2_l3"
    manifest = build_manifest(
        plan_path,
        out_dir,
        dry_run=True,
        confirm_purchase=False,
        dataset="XNAS.ITCH",
        schema="mbo",
        stype_in="raw_symbol",
        override_operating_cap=False,
        override_hard_limit=False,
        max_total_cost_usd=None,
        max_tickers=None,
    )
    assert manifest["status"] == "completed"
    rec = manifest["ticker_records"][0]
    assert rec["status"] == "estimate_failed"
    assert "simulated estimate failure" in rec["estimate_error"]


def test_dry_run_cost_cap_blocks_after_first_ticker(tmp_path, monkeypatch):
    plan_path = tmp_path / "plan.json"
    rows = [
        _row("A", "T-1 close", "2024-05-28T09:30:00", "2024-05-28T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
        _row("B", "T-1 close", "2024-06-01T09:30:00", "2024-06-01T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
        _row("C", "T-1 close", "2024-07-01T09:30:00", "2024-07-01T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
    ]
    _write_plan(plan_path, rows)
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)

    from fetch_l2_l3_databento import _estimate_ticker_cost  # noqa: E402

    monkeypatch.setattr("fetch_l2_l3_databento._estimate_available", lambda: True)
    monkeypatch.setattr("fetch_l2_l3_databento._estimate_ticker_cost", lambda *a, **kw: 1.0)

    out_dir = tmp_path / "l2_l3"
    manifest = build_manifest(
        plan_path,
        out_dir,
        dry_run=True,
        confirm_purchase=False,
        dataset="XNAS.ITCH",
        schema="mbo",
        stype_in="raw_symbol",
        override_operating_cap=False,
        override_hard_limit=False,
        max_total_cost_usd=1.5,
        max_tickers=None,
    )
    statuses = [r["status"] for r in manifest["ticker_records"]]
    assert statuses == ["dry_run_estimate", "skipped_total_cost_cap", "skipped_total_cost_cap"]
    assert manifest["estimated_total_cost_usd"] == 1.0


def test_dry_run_max_tickers_cap(tmp_path, monkeypatch):
    plan_path = tmp_path / "plan.json"
    rows = [
        _row("A", "T-1 close", "2024-05-28T09:30:00", "2024-05-28T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
        _row("B", "T-1 close", "2024-06-01T09:30:00", "2024-06-01T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
        _row("C", "T-1 close", "2024-07-01T09:30:00", "2024-07-01T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
    ]
    _write_plan(plan_path, rows)
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)

    from fetch_l2_l3_databento import _estimate_ticker_cost  # noqa: E402

    monkeypatch.setattr("fetch_l2_l3_databento._estimate_available", lambda: True)
    monkeypatch.setattr("fetch_l2_l3_databento._estimate_ticker_cost", lambda *a, **kw: 0.01)

    out_dir = tmp_path / "l2_l3"
    manifest = build_manifest(
        plan_path,
        out_dir,
        dry_run=True,
        confirm_purchase=False,
        dataset="XNAS.ITCH",
        schema="mbo",
        stype_in="raw_symbol",
        override_operating_cap=False,
        override_hard_limit=False,
        max_total_cost_usd=None,
        max_tickers=1,
    )
    statuses = [r["status"] for r in manifest["ticker_records"]]
    assert statuses == ["dry_run_estimate", "skipped_max_tickers", "skipped_max_tickers"]


def test_skip_existing_skips_tickers_with_existing_dbn_zst(tmp_path, monkeypatch):
    """If out_path already exists with size > 0, the download function returns
    skipped_already_downloaded without calling the Databento client."""
    from fetch_l2_l3_databento import _download_ticker_windows  # noqa: E402

    rows = [
        _row("AAA", "T-1 close", "2024-05-28T09:30:00", "2024-05-28T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
    ]
    out_dir = tmp_path / "l2_l3"
    ticker_dir = out_dir / "AAA"
    ticker_dir.mkdir(parents=True, exist_ok=True)
    existing = ticker_dir / "2024-05-28_2024-05-28_mbo.dbn.zst"
    existing.write_bytes(b"\x28\xb5\x2f\xfd" + b"x" * 4096)  # zstd magic + payload

    called = {"n": 0}

    def _would_call(*a, **kw):
        called["n"] += 1
        raise AssertionError("Databento client should not be called when --skip-existing is set and file exists")

    monkeypatch.setattr("data_system.src.databento_client.DatabentoResearchClient.download_event_window", _would_call)
    monkeypatch.setattr("data_system.src.databento_client.DatabentoResearchClient", _would_call)

    result = _download_ticker_windows(
        "AAA", rows, out_dir, "XNAS.ITCH", "mbo", "raw_symbol",
        override_operating_cap=False, override_hard_limit=False,
    )
    assert result["status"] == "skipped_already_downloaded"
    assert result["size_bytes"] == existing.stat().st_size
    assert result["output_path"] == str(existing)
    assert called["n"] == 0


def test_main_cli_dry_run_exits_zero(tmp_path):
    plan_path = tmp_path / "plan.json"
    rows = [
        _row("A", "T-1 close", "2024-05-28T09:30:00", "2024-05-28T16:00:00", download_now=True, download_policy="free_daily_benchmark_passed"),
    ]
    _write_plan(plan_path, rows)
    out_dir = tmp_path / "l2_l3"
    rc = os.system(
        f'python "{SCRIPTS / "fetch_l2_l3_databento.py"}" '
        f'--plan "{plan_path}" --output-dir "{out_dir}" --dry-run'
    )
    assert rc == 0
    assert (out_dir / "l2_l3_fetch_manifest.json").exists()
