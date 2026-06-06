"""Catalog manifest windows must not span calendar years."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_backfill_catalog_module():
    spec = importlib.util.spec_from_file_location(
        "test_backfill_catalog",
        REPO / "apps" / "workbench" / "scripts" / "backfill_catalog.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_manifest_event_windows_single_year(tmp_path, monkeypatch):
    module = _load_backfill_catalog_module()

    def fail_cost_estimate(_missing):
        raise AssertionError("manifest-only backfill must not estimate Databento cost")

    monkeypatch.setattr(module, "estimate_download_cost_usd", fail_cost_estimate)
    monkeypatch.setattr(module, "_options_discover_manifest", lambda: {"skipped": "test"})
    args = module._build_parser().parse_args(
        [
            "--model",
            "HYP_5",
            "--symbol",
            "MES.v.0",
            "--out-dir",
            str(tmp_path),
        ]
    )
    result = module.run_backfill(args)
    assert result["mode"] == "manifest"
    assert result["estimated_cost_usd"] == 0.0
    assert result["cost_estimate_status"] == "not_requested_manifest_only"

    manifest = json.loads((tmp_path / "workbench_catalog_manifest.json").read_text(encoding="utf-8"))
    assert manifest["estimated_cost_usd"] == 0.0
    assert manifest["cost_estimate_status"] == "not_requested_manifest_only"
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
    assert payload["periods"]
