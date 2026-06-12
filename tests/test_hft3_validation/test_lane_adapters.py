"""Tests for per-lane adapters and LaneConfig Protocol."""
from __future__ import annotations

from pathlib import Path

import pytest

from hft3.validation.lanes import (
    Backtester,
    Lane,
    LaneConfig,
    validate_lane_config,
)
from hft3.validation.lanes.adapters.cme_adapter import (
    CMEBacktester,
    CMEConfig,
    load_cme_config,
)
from hft3.validation.lanes.registration import (
    OptionsLaneBacktester,
    OptionsLaneConfig,
    load_options_lane_config,
)


# --- CME adapter ---


def test_cme_config_defaults():
    cfg = CMEConfig()
    assert cfg.lane == Lane.CME_FUTURES
    assert "ES" in cfg.symbols
    assert "MES" in cfg.symbols
    assert cfg.tick_size == 0.25
    assert cfg.lot_size == 1.0
    assert cfg.latency_bands_ms == [0.5, 1.0, 2.0, 5.0, 10.0]
    assert cfg.capability_profile.is_hft is True
    assert cfg.capability_profile.dma is True


def test_cme_config_satisfies_protocol():
    cfg = load_cme_config()
    errors = validate_lane_config(cfg)
    assert errors == []
    assert isinstance(cfg, LaneConfig)


def test_cme_config_loads_from_events_csv():
    csv_path = Path("packages/data_system/config/events.csv")
    if not csv_path.is_file():
        pytest.skip("events.csv not present in this checkout")
    cfg = load_cme_config(csv_path)
    assert cfg.windows.start_offset_seconds == -60.0
    assert cfg.windows.end_offset_seconds == 10.0


def test_cme_backtester_satisfies_protocol():
    bt = CMEBacktester(load_cme_config())
    assert isinstance(bt, Backtester)
    errors = bt.validate_config()
    assert errors == []
    result = bt.run(target="CPI_2024_09_11_TIGHT")
    assert result.lane == Lane.CME_FUTURES
    assert result.degraded is False


# --- Options/parity lane (registered under Lane.EQUITIES, the historical name) ---


def test_options_lane_config_defaults():
    cfg = OptionsLaneConfig()
    assert cfg.lane == Lane.EQUITIES
    assert "OPTIONS" in cfg.symbols
    assert "PARITY" in cfg.symbols
    assert cfg.latency_bands_ms == [5.0, 10.0, 50.0, 100.0, 250.0]
    assert cfg.capability_profile.speed_advantage is True
    assert cfg.capability_profile.is_hft is False
    assert cfg.capability_profile.dma is False


def test_options_lane_config_event_types():
    cfg = OptionsLaneConfig()
    assert "options_parity" in cfg.event_types
    assert "parity" in cfg.event_types


def test_options_lane_config_latency_floor():
    cfg = OptionsLaneConfig()
    assert cfg.latency_floor_ms == 5.0


def test_options_lane_config_to_dict_includes_latency_floor():
    cfg = OptionsLaneConfig()
    d = cfg.to_dict()
    assert "latency_floor_ms" in d
    assert d["latency_floor_ms"] == 5.0


def test_options_lane_config_satisfies_protocol():
    cfg = OptionsLaneConfig()
    errors = validate_lane_config(cfg)
    assert errors == []


def test_options_lane_backtester_satisfies_protocol():
    bt = OptionsLaneBacktester(load_options_lane_config())
    assert isinstance(bt, Backtester)
    assert bt.validate_config() == []
    result = bt.run(target="options_parity_fixture")
    assert result.lane == Lane.EQUITIES
    assert result.degraded is False


# --- Protocol validation ---


def test_validate_lane_config_flags_missing_fields():
    class Incomplete:
        lane = Lane.CME_FUTURES
        symbols = []

    errors = validate_lane_config(Incomplete())
    assert any("windows" in e for e in errors)
    assert any("horizons" in e for e in errors)
    assert any("latency_bands_ms" in e for e in errors)
    assert any("tick_size" in e for e in errors)


def test_all_adapters_round_trip_to_dict():
    for cfg in (
        load_cme_config(),
        load_options_lane_config(),
    ):
        d = cfg.to_dict()
        assert d["lane"] == cfg.lane.value
        assert isinstance(d["symbols"], list)
        assert isinstance(d["latency_bands_ms"], list)
        assert isinstance(d["tick_size"], (int, float))
        assert isinstance(d["capability_profile"], dict)
