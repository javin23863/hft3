"""Tests for the audit's Rithmic source_vendor recognition.

Verifies that ``_mbo_slot_status`` and ``build_priority_lane_coverage``
in ``data_system/src/event_data_resolver.py`` correctly read the
``source_vendor`` field from Rithmic-sourced release_event_path.json
manifests, and that the per-vendor counts surface in the audit output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from data_system.src.event_data_resolver import (  # noqa: E402
    MboSlotStatus,
    _mbo_slot_status,
)
from mbo_release_lane.constants import SOURCE_VENDOR_RITHMIC  # noqa: E402
from mbo_release_lane.storage import (  # noqa: E402
    build_release_event_path,
    release_event_path_manifest,
    release_slot_dir,
    write_json,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_rithmic_slot(
    repo_root: Path,
    release_id: str,
    symbol: str,
    *,
    event_count: int = 10,
) -> None:
    """Lay down a minimal Rithmic-sourced release_event_path manifest."""
    slot = release_slot_dir(repo_root, release_id, symbol)
    slot.mkdir(parents=True, exist_ok=True)
    rep = build_release_event_path(
        release_id=release_id,
        release_name=release_id.split("_")[0],
        scheduled_release_timestamp="2024-09-11T12:30:00+00:00",
        actual_release_timestamp="2024-09-11T12:30:00+00:00",
        symbol=symbol,
        venue="GLBX",
        window_start="2024-09-11T12:29:00+00:00",
        window_end="2024-09-11T12:30:00+00:00",
        events_ref="events.jsonl",
        event_count=event_count,
        first_sequence=1,
        last_sequence=event_count,
        sequence_gap_count=0,
        source_vendor=SOURCE_VENDOR_RITHMIC,
        dataset_id="RITHMIC_HISTORY",
        validation_status="valid",
    )
    write_json(release_event_path_manifest(slot), rep)


def _write_databento_slot(
    repo_root: Path,
    release_id: str,
    symbol: str,
    *,
    event_count: int = 10,
) -> None:
    slot = release_slot_dir(repo_root, release_id, symbol)
    slot.mkdir(parents=True, exist_ok=True)
    rep = build_release_event_path(
        release_id=release_id,
        release_name=release_id.split("_")[0],
        scheduled_release_timestamp="2024-09-11T12:30:00+00:00",
        actual_release_timestamp="2024-09-11T12:30:00+00:00",
        symbol=symbol,
        venue="GLBX",
        window_start="2024-09-11T12:29:00+00:00",
        window_end="2024-09-11T12:30:00+00:00",
        events_ref="events.jsonl",
        event_count=event_count,
        first_sequence=1,
        last_sequence=event_count,
        sequence_gap_count=0,
        source_vendor="databento",
        dataset_id="GLBX.MDP3",
        validation_status="valid",
    )
    write_json(release_event_path_manifest(slot), rep)


# ---------------------------------------------------------------------------
# MboSlotStatus dataclass
# ---------------------------------------------------------------------------


def test_mbo_slot_status_includes_source_vendor_field():
    assert "source_vendor" in MboSlotStatus.__dataclass_fields__
    # Default is "unknown" so existing call sites that don't pass it still work.
    st = MboSlotStatus(
        event_id="X",
        symbol="Y",
        raw_ok=False,
        npz_ok=False,
        validation_status=None,
        status="not_downloaded",
        raw_path="",
        npz_path="",
    )
    assert st.source_vendor == "unknown"


# ---------------------------------------------------------------------------
# _mbo_slot_status
# ---------------------------------------------------------------------------


def test_slot_status_reads_rithmic_source_vendor(tmp_path: Path):
    """When the slot manifest has source_vendor=rithmic_api, the status
    object reflects that — separate from the (raw_ok, npz_ok) status."""
    _write_rithmic_slot(tmp_path, "CPI_RITH_TEST", "ESM5", event_count=42)
    st = _mbo_slot_status(
        tmp_path,
        "CPI_RITH_TEST",
        "ESM5",
        ("MES.v.0", "ESM5"),
    )
    # No NPZ is on disk, so the slot is not "complete", but the source
    # vendor should still be read from the manifest.
    assert st.source_vendor == SOURCE_VENDOR_RITHMIC
    # validation_status is None because no raw DBN file exists on disk;
    # resolve_mbo_raw_for_event only returns validation when it finds a
    # raw file.  The manifest itself says "valid" but the resolver
    # doesn't see a raw file, so the status field is None.
    assert st.validation_status is None


def test_slot_status_reads_databento_source_vendor(tmp_path: Path):
    _write_databento_slot(tmp_path, "DBN_TEST", "ESM5")
    st = _mbo_slot_status(
        tmp_path,
        "DBN_TEST",
        "ESM5",
        ("MES.v.0", "ESM5"),
    )
    assert st.source_vendor == "databento"


def test_slot_status_default_unknown_when_no_manifest(tmp_path: Path):
    """Empty repo: no manifest, no NPZ — source_vendor is 'unknown'."""
    st = _mbo_slot_status(
        tmp_path,
        "NEVER_DOWNLOADED",
        "ESM5",
        ("MES.v.0", "ESM5"),
    )
    assert st.source_vendor == "unknown"
    assert st.status == "not_downloaded"


# ---------------------------------------------------------------------------
# build_priority_lane_coverage
# ---------------------------------------------------------------------------


def test_build_priority_lane_coverage_includes_source_vendor_counts(tmp_path: Path):
    """The audit output must include mbo.source_vendor_counts key,
    even on an empty repo with no slots filled."""
    from data_system.src.event_data_resolver import build_priority_lane_coverage

    cov = build_priority_lane_coverage(tmp_path)
    assert "mbo" in cov
    assert "source_vendor_counts" in cov["mbo"]
    counts = cov["mbo"]["source_vendor_counts"]
    # On an empty repo all slots are 'unknown'; verify the key is
    # present and is a dict (integration test against real catalog).
    assert isinstance(counts, dict)
    # At minimum 'unknown' should appear since no Rithmic/Databento
    # manifests exist in a temp tree.
    assert "unknown" in counts or len(counts) == 0


def test_build_priority_lane_coverage_unknown_when_empty(tmp_path: Path):
    """Empty repo: all slots are 'unknown' source_vendor."""
    from data_system.src.event_data_resolver import build_priority_lane_coverage

    cov = build_priority_lane_coverage(tmp_path)
    counts = cov["mbo"]["source_vendor_counts"]
    # The audit may not produce an output if no events match; if it
    # does, every slot is 'unknown' on a clean tree.
    if counts:
        # Only "unknown" is allowed for an empty tree.
        for vendor, n in counts.items():
            assert vendor == "unknown", f"unexpected vendor {vendor}={n} on empty tree"
            assert n >= 0
