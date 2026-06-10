"""Tests for per-lane adapters and LaneConfig Protocol."""
from __future__ import annotations

import json
import textwrap
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


# --- Crypto adapter ---


def test_crypto_config_defaults():
    cfg = CryptoConfig()
    assert cfg.lane == Lane.CRYPTO
    assert cfg.symbols == []
    assert cfg.instrument_coverage == "candidate_config"
    assert cfg.environment_validated is False
    assert cfg.environment_source_ref == ""
    assert cfg.capability_profile.is_hft is True
    assert cfg.capability_profile.node_direct is True
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
        "- DOGEUSDT\n"
        "- WIF/USDT\n"
        "venues:\n"
        "- binance_spot\n"
        "- deribit\n"
        "data_environment: btc_computer_validated_crypto_data_environment\n"
        "instrument_coverage: validated_crypto_data_environment\n"
        "environment_validated: true\n"
        "environment_source_ref: config://candidate/self-attested.yaml\n"
        "training_window: 90d\n"
        "test_window: 30d\n"
        "embargo: 24h\n"
        "fixture_label_horizon_ms: 1000\n"
    )
    cfg = load_crypto_config(yaml_path)
    assert cfg.candidate_id == "crypto_h_test"
    assert cfg.hypothesis_id == "CRYPTO_H_TEST"
    assert "DOGEUSDT" in cfg.symbols
    assert "WIF/USDT" in cfg.symbols
    assert cfg.data_environment == ""
    assert cfg.instrument_coverage == "candidate_config"
    assert cfg.environment_validated is False
    assert cfg.environment_source_ref == ""
    assert cfg.windows.training_window_days == 90
    assert cfg.windows.test_window_days == 30
    assert cfg.windows.embargo_seconds == 86400.0
    assert cfg.windows.label_horizon_ms == 1000


def test_crypto_config_handles_missing_yaml():
    cfg = load_crypto_config(Path("/nonexistent/path.yaml"))
    assert cfg.candidate_id == ""
    assert cfg.symbols == []
    assert cfg.environment_validated is False


def test_crypto_backtester_satisfies_protocol():
    bt = CryptoBacktester(CryptoConfig())
    assert isinstance(bt, Backtester)
    assert bt.validate_config() == []
    result = bt.run(target="crypto_h1")
    assert result.lane == Lane.CRYPTO
    assert result.degraded is False


# --- Equities adapter (merged: US stocks + options via IBKR) ---


def test_equities_config_defaults():
    cfg = EquitiesConfig()
    assert cfg.lane == Lane.EQUITIES
    assert cfg.horizons.horizons == [1, 2, 5]
    assert cfg.horizons.lookback_days == 60
    assert cfg.latency_bands_ms == [5.0, 10.0, 50.0, 100.0, 250.0]
    assert cfg.capability_profile.speed_advantage is True
    assert cfg.capability_profile.is_hft is False
    assert cfg.capability_profile.dma is False


def test_equities_config_merged_symbols():
    cfg = EquitiesConfig()
    assert "RUNNER" in cfg.symbols
    assert "LOW_FLOAT" in cfg.symbols
    assert "OPTIONS" in cfg.symbols
    assert "PARITY" in cfg.symbols


def test_equities_config_merged_event_types():
    cfg = EquitiesConfig()
    assert "equities_low_float" in cfg.event_types
    assert "equities_runner_event" in cfg.event_types
    assert "options_parity" in cfg.event_types


def test_equities_config_latency_floor():
    cfg = EquitiesConfig()
    assert cfg.latency_floor_ms == 5.0


def test_equities_config_to_dict_includes_latency_floor():
    cfg = EquitiesConfig()
    d = cfg.to_dict()
    assert "latency_floor_ms" in d
    assert d["latency_floor_ms"] == 5.0


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
    # structural merged-lane symbols must always be present even with a custom yaml session
    assert "OPTIONS" in cfg.symbols
    assert "PARITY" in cfg.symbols
    assert cfg.train_days == 90
    assert cfg.test_days == 30
    assert cfg.latency_bands_ms == [10.0]
    assert cfg.windows.training_window_days == 90
    assert cfg.windows.test_window_days == 30
    # latency_floor_ms must survive yaml override
    assert cfg.latency_floor_ms == 5.0


def test_equities_backtester_satisfies_protocol():
    bt = EquitiesBacktester(load_equities_config())
    assert isinstance(bt, Backtester)
    assert bt.validate_config() == []
    result = bt.run(target="fixture_low_float_v1")
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
        CryptoConfig(),
        load_equities_config(),
    ):
        d = cfg.to_dict()
        assert d["lane"] == cfg.lane.value
        assert isinstance(d["symbols"], list)
        assert isinstance(d["latency_bands_ms"], list)
        assert isinstance(d["tick_size"], (int, float))
        assert isinstance(d["capability_profile"], dict)


# --- Equities adapter: latency floor clamp (Finding 1) ---


def test_equities_latency_band_clamped_to_floor(tmp_path: Path) -> None:
    """yaml latency_ms 1.0 → cfg.latency_bands_ms == [5.0] (clamped to floor)."""
    yaml_path = tmp_path / "universe.yaml"
    yaml_path.write_text(
        "execution:\n"
        "  latency_ms: 1.0\n",
        encoding="utf-8",
    )
    cfg = load_equities_config(yaml_path)
    assert cfg.latency_bands_ms == [5.0], (
        f"Expected [5.0] after clamp, got {cfg.latency_bands_ms}"
    )


def test_equities_latency_band_above_floor_unchanged(tmp_path: Path) -> None:
    """yaml latency_ms 10.0 → cfg.latency_bands_ms == [10.0] (above floor, unaffected)."""
    yaml_path = tmp_path / "universe.yaml"
    yaml_path.write_text(
        "execution:\n"
        "  latency_ms: 10.0\n",
        encoding="utf-8",
    )
    cfg = load_equities_config(yaml_path)
    assert cfg.latency_bands_ms == [10.0]


# --- Equities adapter: endpoint status floor tightening (Finding 2b) ---


def test_equities_floor_tightened_from_endpoint_status(tmp_path: Path) -> None:
    """When endpoint status has measured_rtt_ms > 5, floor is raised to that value."""
    yaml_path = tmp_path / "universe.yaml"
    yaml_path.write_text("execution:\n  latency_ms: 10.0\n", encoding="utf-8")

    status_path = tmp_path / "ibkr_endpoint_status.json"
    status_path.write_text(
        json.dumps({"measured_rtt_ms": 12.5, "ready": True}),
        encoding="utf-8",
    )

    cfg = load_equities_config(yaml_path, endpoint_status_path=status_path)
    assert cfg.latency_floor_ms == 12.5


def test_equities_floor_not_lowered_below_default(tmp_path: Path) -> None:
    """measured_rtt_ms of 2.0 (below hard minimum) must not lower the floor."""
    yaml_path = tmp_path / "universe.yaml"
    yaml_path.write_text("execution:\n  latency_ms: 10.0\n", encoding="utf-8")

    status_path = tmp_path / "ibkr_endpoint_status.json"
    status_path.write_text(
        json.dumps({"measured_rtt_ms": 2.0}),
        encoding="utf-8",
    )

    cfg = load_equities_config(yaml_path, endpoint_status_path=status_path)
    assert cfg.latency_floor_ms == 5.0


def test_equities_floor_unchanged_when_status_file_missing(tmp_path: Path) -> None:
    """Missing endpoint status file → floor stays at default 5.0."""
    yaml_path = tmp_path / "universe.yaml"
    yaml_path.write_text("execution:\n  latency_ms: 10.0\n", encoding="utf-8")

    cfg = load_equities_config(
        yaml_path,
        endpoint_status_path=tmp_path / "nonexistent_status.json",
    )
    assert cfg.latency_floor_ms == 5.0


def test_equities_floor_unchanged_when_status_json_corrupt(tmp_path: Path) -> None:
    """Corrupt JSON in endpoint status file → floor stays at default 5.0."""
    yaml_path = tmp_path / "universe.yaml"
    yaml_path.write_text("execution:\n  latency_ms: 10.0\n", encoding="utf-8")

    status_path = tmp_path / "ibkr_endpoint_status.json"
    status_path.write_text("{not valid json", encoding="utf-8")

    cfg = load_equities_config(yaml_path, endpoint_status_path=status_path)
    assert cfg.latency_floor_ms == 5.0


def test_equities_floor_unchanged_when_measured_rtt_ms_absent(tmp_path: Path) -> None:
    """Status file without measured_rtt_ms field → floor stays at default 5.0."""
    yaml_path = tmp_path / "universe.yaml"
    yaml_path.write_text("execution:\n  latency_ms: 10.0\n", encoding="utf-8")

    status_path = tmp_path / "ibkr_endpoint_status.json"
    status_path.write_text(json.dumps({"ready": True}), encoding="utf-8")

    cfg = load_equities_config(yaml_path, endpoint_status_path=status_path)
    assert cfg.latency_floor_ms == 5.0
