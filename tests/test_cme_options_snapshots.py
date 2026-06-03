"""Tests for CME options snapshot planning/fetch gates (no API key required)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKTREE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_cme_options_snapshot_plan import (  # noqa: E402
    EXECUTABLE_POLICY,
    MISSING_MAPPING_POLICY,
    build_plan,
    load_symbol_map,
    write_outputs,
)
from fetch_cme_options_snapshots import (  # noqa: E402
    _filter_executable,
    _paths_for_row,
    build_manifest,
)


def _write_events(path: Path) -> None:
    path.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes\n"
        "CPI_2024_09_11_TIGHT,CPI,2024-09-11,08:30:00,America/New_York,TIGHT,-30,300,"
        "MES.v.0,50,BLS,https://bls.gov,2018-01-01,test\n",
        encoding="utf-8",
    )


def _write_plan(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _plan_row(**extras: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_id": "CPI_2024_09_11_TIGHT",
        "event_type": "CPI",
        "release_date": "2024-09-11",
        "future_symbol": "MES.v.0",
        "options_symbol": "REAL_CME_OPTION_SYMBOL",
        "options_symbol_label": "near_atm_call",
        "offset_sec": -60,
        "snapshot_timestamp_utc": "2024-09-11T12:29:00+00:00",
        "options_window_start_utc": "2024-09-11T12:28:00+00:00",
        "options_window_end_utc": "2024-09-11T12:29:00+00:00",
        "quote_window_seconds": 60,
        "dataset": "GLBX.MDP3",
        "schema": "mbp-1",
        "stype_in": "raw_symbol",
        "download_now": True,
        "download_policy": EXECUTABLE_POLICY,
    }
    row.update(extras)
    return row


def test_symbol_map_accepts_direct_and_symbols_wrappers(tmp_path):
    direct = tmp_path / "direct.json"
    direct.write_text(json.dumps({"MES.v.0": [{"options_symbol": "OPT1", "label": "c"}]}), encoding="utf-8")
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"symbols": {"MES": "OPT2"}}), encoding="utf-8")

    assert load_symbol_map(direct)["MES.v.0"][0]["options_symbol"] == "OPT1"
    assert load_symbol_map(wrapped)["MES"][0]["options_symbol"] == "OPT2"


def test_build_plan_aligns_options_window_without_lookahead(tmp_path):
    events = tmp_path / "events.csv"
    _write_events(events)
    symbol_map = {
        "MES.v.0": [
            {
                "options_symbol": "REAL_CME_OPTION_SYMBOL",
                "label": "near_atm_call",
                "dataset": "GLBX.MDP3",
                "schema": "mbp-1",
                "stype_in": "raw_symbol",
            }
        ]
    }

    plan = build_plan(
        events,
        symbol_map,
        event_ids=["CPI_2024_09_11_TIGHT"],
        symbols=["MES.v.0"],
        offsets_sec=[-60, 0],
        quote_window_seconds=60,
    )

    assert len(plan) == 2
    first = plan[0]
    assert first["download_policy"] == EXECUTABLE_POLICY
    assert first["options_window_end_utc"] == first["snapshot_timestamp_utc"]
    assert first["options_window_start_utc"] == "2024-09-11T12:28:00+00:00"
    assert first["options_window_end_utc"] == "2024-09-11T12:29:00+00:00"
    assert first["download_now"] is True


def test_build_plan_marks_missing_symbol_mapping(tmp_path):
    events = tmp_path / "events.csv"
    _write_events(events)

    plan = build_plan(
        events,
        {},
        event_ids=["CPI_2024_09_11_TIGHT"],
        symbols=["MES.v.0"],
        offsets_sec=[0],
    )

    assert len(plan) == 1
    assert plan[0]["download_now"] is False
    assert plan[0]["download_policy"] == MISSING_MAPPING_POLICY
    assert plan[0]["options_symbol"] == ""


def test_write_outputs_records_gap_counts(tmp_path):
    output = tmp_path / "cme_options_snapshot_plan.json"
    manifest = write_outputs([_plan_row(), _plan_row(download_now=False, download_policy=MISSING_MAPPING_POLICY, options_symbol="")], output, symbol_map_path=None)
    assert output.exists()
    assert Path(manifest["manifest_path"]).exists()
    assert manifest["n_executable_rows"] == 1
    assert manifest["n_missing_mapping_rows"] == 1


def test_fetch_filter_keeps_only_configured_options_rows():
    rows = [
        _plan_row(),
        _plan_row(download_now=False, download_policy=MISSING_MAPPING_POLICY, options_symbol=""),
    ]
    assert [_row["options_symbol"] for _row in _filter_executable(rows)] == ["REAL_CME_OPTION_SYMBOL"]


def test_fetch_dry_run_without_key_does_not_estimate(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    plan = tmp_path / "plan.json"
    _write_plan(plan, [_plan_row(), _plan_row(options_symbol="SECOND_REAL_SYMBOL", options_symbol_label="put")])

    manifest = build_manifest(
        plan,
        tmp_path / "out",
        dry_run=True,
        confirm_purchase=False,
        max_total_cost_usd=None,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )

    assert manifest["status"] == "completed"
    assert [r["status"] for r in manifest["records"]] == [
        "dry_run_no_estimate_no_api_key",
        "dry_run_no_estimate_no_api_key",
    ]


def test_fetch_dry_run_cost_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    plan = tmp_path / "plan.json"
    _write_plan(plan, [_plan_row(), _plan_row(options_symbol="SECOND_REAL_SYMBOL", options_symbol_label="put")])
    monkeypatch.setattr("fetch_cme_options_snapshots._estimate_row_cost", lambda row: 0.75)

    manifest = build_manifest(
        plan,
        tmp_path / "out",
        dry_run=True,
        confirm_purchase=False,
        max_total_cost_usd=1.0,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )

    assert [r["status"] for r in manifest["records"]] == ["dry_run_estimate", "skipped_total_cost_cap"]
    assert manifest["estimated_total_cost_usd"] == 0.75


def test_fetch_existing_raw_skips_before_costing(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    row = _plan_row()
    plan = tmp_path / "plan.json"
    _write_plan(plan, [row])
    raw, _ = _paths_for_row(tmp_path / "out", row)
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"dbn")

    def _boom(_row):
        raise AssertionError("estimate should not run for already-downloaded snapshots")

    monkeypatch.setattr("fetch_cme_options_snapshots._estimate_row_cost", _boom)
    manifest = build_manifest(
        plan,
        tmp_path / "out",
        dry_run=False,
        confirm_purchase=True,
        max_total_cost_usd=None,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )

    assert manifest["records"][0]["status"] == "skipped_already_downloaded"
    assert manifest["estimated_total_cost_usd"] == 0.0


def test_fetch_terminal_symbology_failure_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    plan = tmp_path / "plan.json"
    _write_plan(plan, [_plan_row()])
    calls = {"n": 0}

    def _fail(_row):
        calls["n"] += 1
        raise RuntimeError("422 symbology_invalid_request None of the symbols could be resolved")

    monkeypatch.setattr("fetch_cme_options_snapshots._estimate_row_cost", _fail)
    first = build_manifest(
        plan,
        tmp_path / "out",
        dry_run=True,
        confirm_purchase=False,
        max_total_cost_usd=None,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )
    assert first["records"][0]["status"] == "estimate_failed"
    assert Path(first["records"][0]["failure_path"]).exists()

    second = build_manifest(
        plan,
        tmp_path / "out",
        dry_run=True,
        confirm_purchase=False,
        max_total_cost_usd=None,
        max_requests=None,
        override_operating_cap=False,
        override_hard_limit=False,
    )
    assert second["records"][0]["status"] == "skipped_terminal_failure"
    assert calls["n"] == 1


def test_fetch_cli_refuses_live_without_key(tmp_path):
    plan = tmp_path / "plan.json"
    _write_plan(plan, [_plan_row()])
    env = os.environ.copy()
    env.pop("DATABENTO_API_KEY", None)
    env["PYTHONPATH"] = str(WORKTREE / "packages")
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "fetch_cme_options_snapshots.py"),
            "--plan",
            str(plan),
            "--output-dir",
            str(tmp_path / "out"),
            "--confirm-purchase",
        ],
        cwd=str(WORKTREE),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 4
    assert "DATABENTO_API_KEY" in proc.stderr
