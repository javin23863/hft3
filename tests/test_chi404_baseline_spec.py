"""CHI404 baseline JSON is present and structurally valid."""

from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SPEC = _REPO / "docs" / "ai" / "chi404_system_spec.json"
_SNAPSHOT = _REPO / "runtime" / "chi404" / "baseline" / "2026-05-31T030000Z_baseline.json"


def test_chi404_system_spec_exists():
    assert _SPEC.is_file(), "missing docs/ai/chi404_system_spec.json"


def test_chi404_system_spec_required_fields():
    data = json.loads(_SPEC.read_text(encoding="utf-8"))
    for key in (
        "schema_version",
        "host_id",
        "captured_at",
        "hardware",
        "kernel_runtime",
        "cpu_layout",
        "network",
        "validation",
        "software_layers",
    ):
        assert key in data, f"missing {key}"
    assert data["host_id"] == "CHI404"
    assert data["schema_version"] == "1.0"
    mem = data["hardware"]["memory"]
    assert mem["dimm_count"] == len(mem["dimms"])
    assert mem["total_installed_gib"] == sum(d["size_gib"] for d in mem["dimms"])


def test_committed_baseline_snapshot_matches_spec():
    snap = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    spec = json.loads(_SPEC.read_text(encoding="utf-8"))
    assert snap == spec


def test_hardware_baseline_capture_script_exists():
    path = _REPO / "infrastructure" / "chi404" / "00_hardware_baseline_capture.sh"
    assert path.is_file()


def test_baseline_gap_fix_script_exists():
    path = _REPO / "infrastructure" / "chi404" / "01_fix_baseline_gaps.sh"
    assert path.is_file()


def test_network_spec_rings_post_fix():
    spec = json.loads(_SPEC.read_text(encoding="utf-8"))
    rings = spec["network"]["ring_buffers"]
    assert rings["rx_current"] >= 511
    assert rings["tx_current"] >= 511
    off = spec["network"]["offloads_observed"]
    assert off.get("generic_receive_offload") == "off"
    assert off.get("tcp_segmentation_offload") == "off"
    assert off.get("large_receive_offload") == "off"
