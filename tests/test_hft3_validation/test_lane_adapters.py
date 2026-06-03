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
from hft3.validation.lanes.adapters.crypto_adapter import (
    CryptoBacktester,
    CryptoConfig,
    load_crypto_config,
)
from hft3.validation.lanes.adapters.equities_adapter import (
    EquitiesBacktester,
    EquitiesConfig,
    load_equities_config,
)
from hft3.validation.lanes.adapters.options_adapter import (
    OptionsBacktester,
    OptionsConfig,
    load_options_config,
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
    assert cfg.windows.start_offset_seconds == -30
    assert cfg.windows.end_offset_seconds == 300


def test_cme_backtester_satisfies_protocol():
    bt = CMEBacktester(load_cme_config())
    assert isinstance(bt, Backtester)
    errors = bt.validate_config()
    assert errors == []
    result = bt.run(target="CPI_2024_09_11_TIGHT")
    assert result.lane == Lane.CME_FUTURES
    assert result.degraded is False


# --- Crypto adapter ---


def test_crypto_config_defaults():
    cfg = CryptoConfig()
    assert cfg.lane == Lane.CRYPTO
    assert "BTCUSDT" in cfg.symbols
    assert cfg.tick_size == 0.1
    assert cfg.latency_bands_ms == [5.0, 50.0, 200.0]


def test_crypto_config_satisfies_protocol():
    cfg = CryptoConfig()
    errors = validate_lane_config(cfg)
    assert errors == []


def test_crypto_config_loads_from_yaml(tmp_path):
    yaml_path = tmp_path / "h_test.yaml"
    yaml_path.write_text(
        "config_id: h_test\n"
        "hypothesis_id: CRYPTO_H_TEST\n"
        "candidate_id: crypto_h_test\n"
        "universe:\n"
        "- BTC\n"
        "- ETH\n"
        "venues:\n"
        "- binance_spot\n"
        "- deribit\n"
        "training_window: 90d\n"
        "test_window: 30d\n"
        "embargo: 24h\n"
        "fixture_label_horizon_ms: 1000\n"
    )
    cfg = load_crypto_config(yaml_path)
    assert cfg.candidate_id == "crypto_h_test"
    assert cfg.hypothesis_id == "CRYPTO_H_TEST"
    assert "BTCUSDT" in cfg.symbols
    assert "ETHUSDT" in cfg.symbols
    assert cfg.windows.training_window_days == 90
    assert cfg.windows.test_window_days == 30
    assert cfg.windows.embargo_seconds == 86400.0
    assert cfg.windows.label_horizon_ms == 1000


def test_crypto_config_handles_missing_yaml():
    cfg = load_crypto_config(Path("/nonexistent/path.yaml"))
    assert cfg.candidate_id == ""
    assert cfg.symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BTC/USD", "ETH/USD"]


def test_crypto_backtester_satisfies_protocol():
    bt = CryptoBacktester(CryptoConfig())
    assert isinstance(bt, Backtester)
    assert bt.validate_config() == []
    result = bt.run(target="crypto_h1")
    assert result.lane == Lane.CRYPTO
    assert result.degraded is False


# --- Equities adapter ---


def test_equities_config_defaults():
    cfg = EquitiesConfig()
    assert cfg.lane == Lane.EQUITIES
    assert cfg.horizons.horizons == [1, 2, 5]
    assert cfg.horizons.lookback_days == 60
    assert cfg.latency_bands_ms == [5.0, 50.0]


def test_equities_config_satisfies_protocol():
    cfg = EquitiesConfig()
    errors = validate_lane_config(cfg)
    assert errors == []


def test_equities_config_loads_from_yaml(tmp_path):
    yaml_path = tmp_path / "universe.yaml"
    yaml_path.write_text(
        "sessions:\n"
        "  - id: test_session\n"
        "    symbol: TESTCO\n"
        "    date: '2024-01-15'\n"
        "walk_forward:\n"
        "  train_days: 90\n"
        "  val_days: 30\n"
        "  test_days: 30\n"
        "  step_days: 30\n"
        "execution:\n"
        "  latency_ms: 10.0\n"
    )
    cfg = load_equities_config(yaml_path)
    assert "TESTCO" in cfg.symbols
    assert cfg.train_days == 90
    assert cfg.test_days == 30
    assert cfg.latency_bands_ms == [10.0]
    assert cfg.windows.training_window_days == 90
    assert cfg.windows.test_window_days == 30


def test_equities_backtester_satisfies_protocol():
    bt = EquitiesBacktester(load_equities_config())
    assert isinstance(bt, Backtester)
    assert bt.validate_config() == []
    result = bt.run(target="fixture_low_float_v1")
    assert result.lane == Lane.EQUITIES
    assert result.degraded is False


# --- Options adapter ---


def test_options_config_defaults():
    cfg = OptionsConfig()
    assert cfg.lane == Lane.OPTIONS
    assert cfg.latency_ms == 1.0
    assert cfg.lot_size == 100.0


def test_options_config_satisfies_protocol():
    cfg = load_options_config()
    errors = validate_lane_config(cfg)
    assert errors == []


def test_options_backtester_satisfies_protocol():
    bt = OptionsBacktester(load_options_config())
    assert isinstance(bt, Backtester)
    assert bt.validate_config() == []
    result = bt.run(target="multi_leg_test")
    assert result.lane == Lane.OPTIONS


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
        CryptoConfig(),
        load_equities_config(),
        load_options_config(),
    ):
        d = cfg.to_dict()
        assert d["lane"] == cfg.lane.value
        assert isinstance(d["symbols"], list)
        assert isinstance(d["latency_bands_ms"], list)
        assert isinstance(d["tick_size"], (int, float))
