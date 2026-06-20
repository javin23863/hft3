"""Tests for --require-runnable-npz on generate_vbt_paid_units_jsonl."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_GEN = _REPO / "scripts" / "generate_vbt_paid_units_jsonl.py"


def test_require_runnable_npz_filters_missing(tmp_path, monkeypatch):
    npz_dir = tmp_path / "npz"
    npz_dir.mkdir()
    (npz_dir / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz").write_bytes(b"stub")

    events = tmp_path / "events.csv"
    events.write_text(
        "event_id,event_type,release_date,window_name,symbols\n"
        "CPI_2024_09_11_TIGHT,CPI,2024-09-11,TIGHT,MES.v.0\n"
        "CPI_2025_01_15_TIGHT,CPI,2025-01-15,TIGHT,MES.v.0\n",
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_dir))

    proc = subprocess.run(
        [
            sys.executable,
            str(_GEN),
            "--events-csv",
            str(events),
            "--symbols",
            "MES.v.0",
            "--model-id",
            "SPREAD_BLOWOUT_RECOMPRESSION",
            "--smoke-count",
            "2",
            "--require-runnable-npz",
            "--out",
            str(out),
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1
    assert rows[0]["event_id"] == "CPI_2024_09_11_TIGHT"
