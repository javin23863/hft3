"""Tests for hot-memory manager (Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.src.data.hot_memory_manager import HotMemoryManager
from workbench.src.data.instrument_registry import load_instrument_registry

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def manager() -> HotMemoryManager:
    return HotMemoryManager.from_repo(REPO)


def test_hot_symbols_resident_at_init(manager: HotMemoryManager):
    registry = load_instrument_registry(REPO)
    expected = {
        s for s, r in registry.items() if r.hot_memory_tier in {"HOT_EXECUTABLE", "HOT_SENSOR"}
    }
    assert expected.issubset(manager.resident_symbols())
    assert "ES" in manager.resident_symbols()
    assert "VIX" in manager.resident_symbols()


def test_promote_demote_audit(manager: HotMemoryManager):
    record = manager.promote(
        "RB",
        reason_code="ENERGY_SHOCK",
        event_ts="2024-09-11T12:30:00Z",
        triggering_feature="crack_spread_z",
        expected_hot_duration_sec=3600,
    )
    assert record.symbol == "RB"
    assert "RB" in manager.promoted_resident
    manager.demote("RB", reason_code="COOLDOWN", event_ts="2024-09-11T13:30:00Z")
    assert "RB" not in manager.promoted_resident
    assert len(manager.promotion_audit) >= 2


def test_load_pressure_preserves_core(manager: HotMemoryManager):
    manager.promote(
        "RB",
        reason_code="TEST",
        event_ts="2024-01-01T00:00:00Z",
        triggering_feature="x",
        expected_hot_duration_sec=60,
    )
    manager.promote(
        "HO",
        reason_code="TEST",
        event_ts="2024-01-01T00:00:00Z",
        triggering_feature="x",
        expected_hot_duration_sec=60,
    )
    demoted = manager.apply_load_pressure()
    assert "RB" in demoted or "HO" in demoted
    for core in ("ES", "NQ", "ZT", "ZN", "SR3"):
        assert core in manager.resident_symbols()


def test_missing_vix_no_crash(manager: HotMemoryManager):
    manager.update_feed_status("VIX", "MISSING", None)
    manager.update_feed_status("VVIX", "MISSING", None)
    snap = manager.snapshot_telemetry()
    assert "VIX" in snap["missing_sensor_warnings"]
    assert "ES" in snap["resident"]


def test_cannot_demote_core_without_force(manager: HotMemoryManager):
    with pytest.raises(ValueError, match="core protected"):
        manager.demote("ES", reason_code="TEST", event_ts="2024-01-01T00:00:00Z")
