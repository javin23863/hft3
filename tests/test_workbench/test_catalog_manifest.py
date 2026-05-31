"""Catalog manifest windows must not span calendar years."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_manifest_event_windows_single_year():
    subprocess.run(
        [
            sys.executable,
            str(REPO / "apps" / "workbench" / "scripts" / "backfill_catalog.py"),
            "--model",
            "HYP_5",
            "--symbol",
            "MES.v.0",
        ],
        cwd=str(REPO),
        check=True,
        capture_output=True,
    )
    manifest = json.loads(
        (REPO / "artifacts" / "research_cards" / "workbench_catalog_manifest.json").read_text(encoding="utf-8")
    )
    for ev in manifest["events"]:
        assert "release_year" in ev
        if ev.get("start_utc") and ev.get("end_utc"):
            assert str(ev["start_utc"])[:4] == str(ev["end_utc"])[:4]
