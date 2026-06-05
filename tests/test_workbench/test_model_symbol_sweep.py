"""Tests for scripts/run_model_symbol_sweep.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from workbench.src.data.event_catalog import EventSpec

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "run_model_symbol_sweep.py"


def test_sweep_requires_explicit_mode():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "Specify --backfill and/or --sweep" in proc.stderr


def test_sweep_dry_run_exit_zero():
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sweep",
            "--dry-run",
            "--symbols",
            "MES.v.0",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_sweep_runnable_count_requires_own_official_npz(tmp_path):
    import scripts.run_model_symbol_sweep as sweep

    event_id = "CPI_2018_01_11_TIGHT"
    es_path = tmp_path / "data" / "npz" / f"ES.v.0_{event_id}_mbo.npz"
    mes_path = tmp_path / "data" / "npz" / f"MES.v.0_{event_id}_mbo.npz"
    es_path.parent.mkdir(parents=True)
    es_path.write_bytes(b"npz")
    mes_path.write_bytes(b"npz")
    events = [
        EventSpec(
            event_id=event_id,
            event_type="CPI",
            release_date="2018-01-11",
            event_context="CPI_TIGHT",
            symbol="MES.v.0",
            npz_path=es_path,
            npz_present=True,
            start_utc=None,
            end_utc=None,
            npz_symbol_used="ES.v.0",
        ),
        EventSpec(
            event_id=event_id,
            event_type="CPI",
            release_date="2018-01-11",
            event_context="CPI_TIGHT",
            symbol="MES.v.0",
            npz_path=mes_path,
            npz_present=True,
            start_utc=None,
            end_utc=None,
            npz_symbol_used="MES.v.0",
        ),
    ]

    with (
        patch("workbench.src.data.event_catalog.load_periods", return_value=[SimpleNamespace(name="Discovery")]),
        patch("workbench.src.data.event_catalog.list_campaign_events", return_value=events),
    ):
        assert sweep._runnable_event_count("HYP_5", "MES.v.0", tmp_path) == 1
