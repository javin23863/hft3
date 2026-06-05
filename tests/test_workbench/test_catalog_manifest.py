"""Catalog manifest windows must not span calendar years."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


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
    assert payload["periods"]
