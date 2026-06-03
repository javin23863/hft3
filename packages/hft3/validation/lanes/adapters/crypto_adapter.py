"""Crypto lane adapter.

Wraps the crypto lane's per-candidate YAML configs and
crypto_execution_validator to satisfy the Backtester Protocol. Does not
modify any teammate-owned code in packages/crypto_lane/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..backtester_protocol import validate_lane_config
from ..lane import (
    CRYPTO_NODE_DIRECT_HFT_PROFILE,
    GenericBacktestResult,
    HorizonConfig,
    Lane,
    LaneCapabilityProfile,
    LaneConfig,
    WindowConfig,
)

CRYPTO_LATENCY_BANDS_MS = [5.0, 50.0, 200.0]
CRYPTO_VENUES = ["binance_spot", "binance_perp", "deribit", "kraken"]
CRYPTO_EVENT_TYPES = ["crypto_l2", "crypto_l3", "crypto_shock_event"]
CRYPTO_CONFIG_INSTRUMENT_COVERAGE = "candidate_config"


def _parse_duration_to_ms(s: str | int | float | None) -> int | None:
    """Parse '24h', '30m', '1000ms', or a bare number to milliseconds."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    text = str(s).strip().lower()
    if not text:
        return None
    if text.endswith("ms"):
        try:
            return int(text[:-2])
        except ValueError:
            return None
    if text.endswith("s"):
        try:
            return int(float(text[:-1]) * 1000)
        except ValueError:
            return None
    if text.endswith("m"):
        try:
            return int(float(text[:-1]) * 60 * 1000)
        except ValueError:
            return None
    if text.endswith("h"):
        try:
            return int(float(text[:-1]) * 3600 * 1000)
        except ValueError:
            return None
    if text.endswith("d"):
        try:
            return int(float(text[:-1]) * 86400 * 1000)
        except ValueError:
            return None
    try:
        return int(text)
    except ValueError:
        return None


@dataclass
class CryptoConfig:
    """Crypto lane LaneConfig. Loaded from per-candidate YAML."""

    lane: Lane = Lane.CRYPTO
    symbols: list[str] = field(default_factory=list)
    windows: WindowConfig = field(default_factory=WindowConfig)
    horizons: HorizonConfig = field(default_factory=HorizonConfig)
    latency_bands_ms: list[float] = field(default_factory=lambda: list(CRYPTO_LATENCY_BANDS_MS))
    tick_size: float = 0.1
    lot_size: float = 1.0
    test_paths: list[str] = field(default_factory=lambda: ["tests/test_crypto_lane"])
    event_types: list[str] = field(default_factory=lambda: list(CRYPTO_EVENT_TYPES))
    venues: list[str] = field(default_factory=lambda: list(CRYPTO_VENUES))
    candidate_id: str = ""
    hypothesis_id: str = ""
    data_environment: str = ""
    instrument_coverage: str = CRYPTO_CONFIG_INSTRUMENT_COVERAGE
    environment_validated: bool = False
    environment_source_ref: str = ""
    capability_profile: LaneCapabilityProfile = CRYPTO_NODE_DIRECT_HFT_PROFILE

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value,
            "symbols": list(self.symbols),
            "windows": {
                "start_offset_seconds": self.windows.start_offset_seconds,
                "end_offset_seconds": self.windows.end_offset_seconds,
                "training_window_days": self.windows.training_window_days,
                "test_window_days": self.windows.test_window_days,
                "embargo_seconds": self.windows.embargo_seconds,
                "label_horizon_ms": self.windows.label_horizon_ms,
            },
            "horizons": {
                "horizons": list(self.horizons.horizons),
                "lookback_days": self.horizons.lookback_days,
            },
            "latency_bands_ms": list(self.latency_bands_ms),
            "tick_size": self.tick_size,
            "lot_size": self.lot_size,
            "test_paths": list(self.test_paths),
            "event_types": list(self.event_types),
            "venues": list(self.venues),
            "candidate_id": self.candidate_id,
            "hypothesis_id": self.hypothesis_id,
            "data_environment": self.data_environment,
            "instrument_coverage": self.instrument_coverage,
            "environment_validated": self.environment_validated,
            "environment_source_ref": self.environment_source_ref,
            "capability_profile": self.capability_profile.to_dict(),
        }


def load_crypto_config(backtest_yaml: Path | None = None) -> CryptoConfig:
    """Load crypto lane config from a per-candidate YAML."""
    cfg = CryptoConfig()
    if backtest_yaml is None or not backtest_yaml.is_file():
        return cfg
    try:
        data = yaml.safe_load(backtest_yaml.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    universe = data.get("universe", [])
    if isinstance(universe, list):
        cfg.symbols = [s for s in universe if isinstance(s, str)]
    venues = data.get("venues", [])
    if isinstance(venues, list):
        cfg.venues = [v for v in venues if isinstance(v, str)]
    train_ms = _parse_duration_to_ms(data.get("training_window"))
    test_ms = _parse_duration_to_ms(data.get("test_window"))
    embargo_ms = _parse_duration_to_ms(data.get("embargo"))
    label_ms = data.get("fixture_label_horizon_ms")
    cfg.windows = WindowConfig(
        training_window_days=train_ms // 86_400_000 if train_ms else None,
        test_window_days=test_ms // 86_400_000 if test_ms else None,
        embargo_seconds=embargo_ms / 1000.0 if embargo_ms else None,
        label_horizon_ms=label_ms if isinstance(label_ms, int) else None,
    )
    cfg.candidate_id = str(data.get("candidate_id", ""))
    cfg.hypothesis_id = str(data.get("hypothesis_id", ""))
    return cfg


class CryptoBacktester:
    """Backtester Protocol implementation for crypto.

    run() does not execute a real backtest (the real crypto validation
    happens in tests/test_crypto_lane/); it returns a structural result
    so the unified runner can verify config and coverage.
    """

    def __init__(self, config: CryptoConfig) -> None:
        self.config = config

    def run(self, target: str | None = None) -> GenericBacktestResult:
        return GenericBacktestResult(
            lane=self.config.lane,
            degraded=False,
            failure_notes=[],
            extra={
                "candidate_id": self.config.candidate_id,
                "hypothesis_id": self.config.hypothesis_id,
                "target": target or self.config.candidate_id,
            },
        )

    def validate_config(self) -> list[str]:
        return validate_lane_config(self.config)
