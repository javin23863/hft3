"""Catalog manifest windows must not span calendar years."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]


def _fake_event(event_id: str = "E1", *, npz_present: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        event_id=event_id,
        event_type="CPI",
        event_context="inflation",
        release_date="2024-01-11",
        symbol="MES.v.0",
        row_status="SOURCED",
        runnable_eligible=True,
        model_eligible=True,
        symbol_eligible=True,
        npz_present=npz_present,
        npz_symbol_used="MES.v.0",
        npz_path=REPO / "data" / "npz" / f"{event_id}.npz",
        start_utc="2024-01-11T13:29:45Z",
        end_utc="2024-01-11T13:30:15Z",
        source="unit",
        source_url="",
        source_file="unit.csv",
    )


def _summary() -> dict:
    return {"visible_events": 1, "missing_count": 1, "row_status_counts": {"SOURCED": 1}}


def test_manifest_event_windows_single_year(tmp_path):
    subprocess.run(
        [
            sys.executable,
            str(REPO / "apps" / "workbench" / "scripts" / "backfill_catalog.py"),
            "--model",
            "HYP_5",
            "--symbol",
            "MES.v.0",
            "--out-dir",
            str(tmp_path),
        ],
        cwd=str(REPO),
        check=True,
        capture_output=True,
    )
    manifest = json.loads((tmp_path / "workbench_catalog_manifest.json").read_text(encoding="utf-8"))
    for ev in manifest["events"]:
        assert "release_year" in ev
        if ev.get("start_utc") and ev.get("end_utc"):
            assert str(ev["start_utc"])[:4] == str(ev["end_utc"])[:4]


def test_workbench_download_cli_dry_run_json_uses_bootstrap_env():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), str(REPO / "packages"), str(REPO / "apps")]
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "workbench",
            "download",
            "--model",
            "HYP_5",
            "--symbol",
            "MES.v.0",
            "--dry-run",
            "--json",
        ],
        cwd=str(REPO),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "dry_run"
    assert payload["model_id"] == "HYP_5"
    assert payload["symbol"] == "MES.v.0"
    assert payload["status"] == "PASS"
    assert payload["blocking"] == []
    assert payload["error"] == ""
    assert payload["periods"]


def test_workbench_download_dispatcher_forwards_apps_backfill_args(monkeypatch):
    from apps.workbench.scripts import backfill_catalog
    from workbench.__main__ import main as workbench_main

    captured = {}

    def fake_backfill_main(argv):
        captured["argv"] = argv
        return 1

    monkeypatch.setattr(backfill_catalog, "main", fake_backfill_main)

    result = workbench_main(
        [
            "download",
            "--model",
            "HYP_5",
            "--symbol",
            "MES.v.0",
            "--max-cost-usd",
            "7.5",
            "--scope",
            "sourced_runnable",
            "--json",
        ]
    )

    assert result == 1
    assert captured["argv"] == [
        "--model",
        "HYP_5",
        "--symbol",
        "MES.v.0",
        "--max-cost-usd",
        "7.5",
        "--scope",
        "sourced_runnable",
        "--download-missing",
        "--json",
    ]


def test_backfill_dry_run_response_contract(monkeypatch):
    from workbench.scripts import backfill_catalog

    monkeypatch.setattr(backfill_catalog, "events_for_scope", lambda *_args, **_kwargs: [("Discovery", _fake_event())])
    monkeypatch.setattr(backfill_catalog, "summarize_event_specs", lambda _rows: _summary())

    result = backfill_catalog.run_backfill(
        SimpleNamespace(scope="full_universe", model="HYP_5", symbol="MES.v.0", dry_run=True)
    )

    assert result["status"] == "PASS"
    assert result["blocking"] == []
    assert result["error"] == ""
    assert result["mode"] == "dry_run"


def test_backfill_cost_cap_blocks_without_download(monkeypatch, tmp_path):
    from workbench.scripts import backfill_catalog

    event = _fake_event()
    monkeypatch.setattr(backfill_catalog, "events_for_scope", lambda *_args, **_kwargs: [("Discovery", event)])
    monkeypatch.setattr(backfill_catalog, "summarize_event_specs", lambda _rows: _summary())
    monkeypatch.setattr(backfill_catalog, "missing_for_campaign", lambda *_args, **_kwargs: [event])
    monkeypatch.setattr(backfill_catalog, "estimate_download_cost_usd", lambda _missing: 25.0)
    monkeypatch.setattr(
        backfill_catalog,
        "download_events",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("download should not run")),
    )

    args = SimpleNamespace(
        scope="full_universe",
        model="HYP_5",
        symbol="MES.v.0",
        dry_run=False,
        download_missing=True,
        max_cost_usd=1.0,
        out_dir=tmp_path,
    )
    result = backfill_catalog.run_backfill(args)

    assert result["status"] == "BLOCKING"
    assert result["error"]
    assert result["blocking"] == [
        {
            "gate": "download_cost_cap",
            "status": "BLOCKING",
            "reason": "Estimated cost $25.00 exceeds max $1.00",
        }
    ]
    assert backfill_catalog.main(
        [
            "--model",
            "HYP_5",
            "--symbol",
            "MES.v.0",
            "--download-missing",
            "--max-cost-usd",
            "1.0",
            "--out-dir",
            str(tmp_path),
            "--json",
        ]
    ) == 1


def test_backfill_partial_download_blocks(monkeypatch, tmp_path):
    from workbench.scripts import backfill_catalog

    events = [_fake_event("E1"), _fake_event("E2")]
    monkeypatch.setattr(backfill_catalog, "events_for_scope", lambda *_args, **_kwargs: [("Discovery", events[0])])
    monkeypatch.setattr(backfill_catalog, "summarize_event_specs", lambda _rows: _summary())
    monkeypatch.setattr(backfill_catalog, "missing_for_campaign", lambda *_args, **_kwargs: events)
    monkeypatch.setattr(backfill_catalog, "estimate_download_cost_usd", lambda _missing: 0.0)
    monkeypatch.setattr(backfill_catalog, "_options_discover_manifest", lambda: {})
    monkeypatch.setattr(backfill_catalog, "download_events", lambda *_args, **_kwargs: ["E1"])

    result = backfill_catalog.run_backfill(
        SimpleNamespace(
            scope="full_universe",
            model="HYP_5",
            symbol="MES.v.0",
            dry_run=False,
            download_missing=True,
            max_cost_usd=30.0,
            out_dir=tmp_path,
        )
    )

    assert result["status"] == "BLOCKING"
    assert result["blocking"][0]["gate"] == "download_missing_events"
    assert result["download_requested_for"] == ["E1"]


def test_backfill_download_exception_blocks_json(monkeypatch, tmp_path, capsys):
    from workbench.scripts import backfill_catalog

    event = _fake_event()
    reason = "DATABENTO_API_KEY must be set"
    monkeypatch.setattr(backfill_catalog, "events_for_scope", lambda *_args, **_kwargs: [("Discovery", event)])
    monkeypatch.setattr(backfill_catalog, "summarize_event_specs", lambda _rows: _summary())
    monkeypatch.setattr(backfill_catalog, "missing_for_campaign", lambda *_args, **_kwargs: [event])
    monkeypatch.setattr(backfill_catalog, "estimate_download_cost_usd", lambda _missing: 0.0)
    monkeypatch.setattr(backfill_catalog, "_options_discover_manifest", lambda: {})
    monkeypatch.setattr(
        backfill_catalog,
        "download_events",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError(reason)),
    )

    result = backfill_catalog.run_backfill(
        SimpleNamespace(
            scope="full_universe",
            model="HYP_5",
            symbol="MES.v.0",
            dry_run=False,
            download_missing=True,
            max_cost_usd=30.0,
            out_dir=tmp_path,
        )
    )

    assert result["status"] == "BLOCKING"
    assert result["error"] == reason
    assert result["blocking"] == [{"gate": "download_events", "status": "BLOCKING", "reason": reason}]

    assert backfill_catalog.main(
        [
            "--model",
            "HYP_5",
            "--symbol",
            "MES.v.0",
            "--download-missing",
            "--max-cost-usd",
            "30.0",
            "--out-dir",
            str(tmp_path),
            "--json",
        ]
    ) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "BLOCKING"
    assert payload["error"] == reason
    assert payload["blocking"] == [{"gate": "download_events", "status": "BLOCKING", "reason": reason}]
