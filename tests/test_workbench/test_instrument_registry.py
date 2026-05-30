"""Tests for hot-memory instrument registry (Phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.src.data.instrument_registry import (
    assert_not_executable,
    is_tradable,
    load_instrument_registry,
)

REPO = Path(__file__).resolve().parents[2]


def test_loads_full_hot_executable_set():
    records = load_instrument_registry(REPO)
    hot_exec = [s for s, r in records.items() if r.hot_memory_tier == "HOT_EXECUTABLE"]
    assert len(hot_exec) >= 17
    for core in ("ES", "NQ", "ZT", "ZN", "SR3"):
        assert core in records
        assert records[core].hot_memory_tier == "HOT_EXECUTABLE"


def test_vix_vvix_not_tradable():
    records = load_instrument_registry(REPO)
    for sym in ("VIX", "VVIX"):
        rec = records[sym]
        assert rec.instrument_type == "index_sensor"
        assert rec.tradable is False
        assert is_tradable(sym, records) is False


def test_assert_not_executable_raises_for_vix():
    records = load_instrument_registry(REPO)
    with pytest.raises(ValueError, match="not executable"):
        assert_not_executable("VIX", records)


def test_vx_legs_are_cfe_futures():
    records = load_instrument_registry(REPO)
    for sym in ("VX1", "VX2"):
        rec = records[sym]
        assert rec.instrument_type == "futures"
        assert rec.venue == "CFE"
        assert rec.hot_memory_tier == "HOT_SENSOR"


def test_invalid_tier_rejected(tmp_path: Path):
    cfg = tmp_path / "workbench" / "config"
    cfg.mkdir(parents=True)
    (cfg / "hot_memory_universe.yaml").write_text(
        """
core_protected_symbols: [ES]
defaults:
  instrument_type: futures
  tradable: true
  order_book_available: true
  trade_print_available: true
  index_sensor_available: false
  point_in_time_safe: true
  data_delay_status: MISSING
  live_feed_status: MISSING
  historical_feed_status: MISSING
instruments:
  - canonical_internal_symbol: ES
    research_symbol: ES.v.0
    data_vendor_symbol: ES.c.0
    exchange: CME
    venue: GLBX
    hot_memory_tier: INVALID_TIER
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid hot_memory_tier"):
        load_instrument_registry(tmp_path)
