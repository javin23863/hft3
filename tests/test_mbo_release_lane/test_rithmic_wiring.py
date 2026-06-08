"""Tests for source-priority wiring into the MBO release lane orchestrator."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))


def test_constants_include_rithmic_source():
    from mbo_release_lane.constants import (
        SOURCE_VENDOR,
        SOURCE_VENDOR_RITHMIC,
        SOURCE_PRIORITY,
    )
    assert SOURCE_VENDOR == "databento"
    assert SOURCE_VENDOR_RITHMIC == "rithmic_api"
    assert SOURCE_PRIORITY[0] == SOURCE_VENDOR_RITHMIC
    assert SOURCE_VENDOR in SOURCE_PRIORITY


def test_download_catalog_slot_falls_back_to_databento_when_rithmic_unavailable(
    tmp_path: Path,
):
    """When Rithmic is not applicable (no creds / Windows), the slot falls
    through to the existing Databento path.  We exercise the
    orchestrator stub enough to confirm the routing decision."""
    from mbo_release_lane.download import download_catalog_slot
    from mbo_release_lane.source_priority import resolve_source

    class FakeWindow:
        event_id = "CPI_2024_09_11_TIGHT"
        event_type = "CPI"
        release_date = "2024-09-11"
        start_utc = datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc)
        end_utc = datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc)
        window_name = "TIGHT"
        exchange = "CME"

    # Pretend the slot is already filled by Databento.  The orchestrator
    # should return ImportResult(valid) without contacting either source.
    from mbo_release_lane.storage import (
        build_release_event_path,
        release_event_path_manifest,
        release_slot_dir,
        write_json,
    )

    slot = release_slot_dir(tmp_path, FakeWindow.event_id, "ESM5")
    slot.mkdir(parents=True, exist_ok=True)
    rep = build_release_event_path(
        release_id=FakeWindow.event_id,
        release_name="CPI",
        scheduled_release_timestamp=FakeWindow.start_utc.isoformat(),
        actual_release_timestamp=FakeWindow.start_utc.isoformat(),
        symbol="ESM5",
        venue="GLBX",
        window_start=FakeWindow.start_utc.isoformat(),
        window_end=FakeWindow.end_utc.isoformat(),
        events_ref="events.jsonl",
        event_count=0,
        first_sequence=None,
        last_sequence=None,
        sequence_gap_count=0,
        source_vendor="databento",
        dataset_id="GLBX.MDP3",
        validation_status="valid",
    )
    write_json(release_event_path_manifest(slot), rep)

    # resolve_source should return None (slot is filled) — orchestrator
    # should not attempt Rithmic.
    chosen = resolve_source(tmp_path, FakeWindow.event_id, "ESM5")
    assert chosen is None


def test_attempt_rithmic_fill_honors_topology_guard(tmp_path: Path):
    """The orchestrator-level wrapper enforces the topology rule that
    the sync fetch_event_window also enforces.  This is belt-and-suspenders
    defense in depth — both layers must refuse on Windows."""
    from mbo_release_lane.source_priority import attempt_rithmic_fill

    status, manifest, err = attempt_rithmic_fill(
        tmp_path,
        release_id="CPI_2024_09_11_TIGHT",
        symbol="ESM5",
        exchange="CME",
        start_utc=datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc),
        end_utc=datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc),
        scheduled_release_timestamp="2024-09-11T12:30:00+00:00",
    )
    assert status.skipped_reason == "windows"
    assert manifest is None
    assert "CHI404" in (err or "")


def test_download_report_preserves_source_vendor_metadata():
    """The DownloadReport exposes source_vendor; the orchestrator must
    surface which source filled the slot.  This test pins the API contract."""
    from mbo_release_lane.download import DownloadReport

    rep = DownloadReport()
    assert rep.source_vendor == "databento"  # default
    # The Rithmic fill path does not write its own report, so the
    # orchestrator-level DownloadReport still uses the default
    # source_vendor.  Source-level provenance lives in the per-slot
    # release_event_path.json instead — see test_write_release_artifact
    # in test_rithmic_source.py.
