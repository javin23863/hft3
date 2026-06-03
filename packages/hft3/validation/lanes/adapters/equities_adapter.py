"""Equities lane adapter.

Wraps the equities lane's universe.yaml config and LowFloatBacktester to
satisfy the Backtester Protocol. Does not modify any teammate-owned code
in packages/equities_lane/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..backtester_protocol import validate_lane_config
from ..lane import (
    EQUITIES_SPEED_ADVANTAGE_PROFILE,
    GenericBacktestResult,
    HorizonConfig,
    Lane,
    LaneCapabilityProfile,
    LaneConfig,
    WindowConfig,
)

EQUITIES_SYMBOLS = ["RUNNER", "LOW_FLOAT"]
EQUITIES_LATENCY_BANDS_MS = [5.0, 50.0]
EQUITIES_EVENT_TYPES = ["equities_low_float", "equities_runner_event"]


@dataclass
class EquitiesConfig:
    """Equities lane LaneConfig. Loaded from universe.yaml."""

    lane: Lane = Lane.EQUITIES
    symbols: list[str] = field(default_factory=lambda: list(EQUITIES_SYMBOLS))
    windows: WindowConfig = field(default_factory=WindowConfig)
    horizons: HorizonConfig = field(
        default_factory=lambda: HorizonConfig(horizons=[1, 2, 5], lookback_days=60, feature_window_days=20)
    )
    latency_bands_ms: list[float] = field(default_factory=lambda: list(EQUITIES_LATENCY_BANDS_MS))
    tick_size: float = 0.01
    lot_size: float = 1.0
    test_paths: list[str] = field(default_factory=lambda: ["tests/test_equities_lane"])
    event_types: list[str] = field(default_factory=lambda: list(EQUITIES_EVENT_TYPES))
    train_days: int = 60
    val_days: int = 20
    test_days: int = 20
    step_days: int = 20
    universe_yaml_path: str = "packages/equities_lane/config/universe.yaml"
    capability_profile: LaneCapabilityProfile = EQUITIES_SPEED_ADVANTAGE_PROFILE

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value,
            "symbols": list(self.symbols),
            "windows": {
                "training_window_days": self.windows.training_window_days,
                "test_window_days": self.windows.test_window_days,
            },
            "horizons": {
                "horizons": list(self.horizons.horizons),
                "lookback_days": self.horizons.lookback_days,
                "feature_window_days": self.horizons.feature_window_days,
            },
            "latency_bands_ms": list(self.latency_bands_ms),
            "tick_size": self.tick_size,
            "lot_size": self.lot_size,
            "test_paths": list(self.test_paths),
            "event_types": list(self.event_types),
            "walk_forward": {
                "train_days": self.train_days,
                "val_days": self.val_days,
                "test_days": self.test_days,
                "step_days": self.step_days,
            },
            "capability_profile": self.capability_profile.to_dict(),
        }


def load_equities_config(universe_yaml: Path | None = None) -> EquitiesConfig:
    """Load equities lane config from universe.yaml."""
    cfg = EquitiesConfig()
    if universe_yaml is None:
        universe_yaml = Path(cfg.universe_yaml_path)
    if not universe_yaml.is_file():
        return cfg
    try:
        data = yaml.safe_load(universe_yaml.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    sessions = data.get("sessions", [])
    if isinstance(sessions, list) and sessions:
        symbols = []
        for s in sessions:
            if isinstance(s, dict) and "symbol" in s:
                symbols.append(str(s["symbol"]))
        if symbols:
            cfg.symbols = symbols
    wf = data.get("walk_forward", {})
    if isinstance(wf, dict):
        cfg.train_days = int(wf.get("train_days", cfg.train_days))
        cfg.val_days = int(wf.get("val_days", cfg.val_days))
        cfg.test_days = int(wf.get("test_days", cfg.test_days))
        cfg.step_days = int(wf.get("step_days", cfg.step_days))
    exec_cfg = data.get("execution", {})
    if isinstance(exec_cfg, dict):
        lat = exec_cfg.get("latency_ms")
        if isinstance(lat, (int, float)):
            cfg.latency_bands_ms = [float(lat)]
    cfg.windows = WindowConfig(
        training_window_days=cfg.train_days,
        test_window_days=cfg.test_days,
    )
    return cfg


class EquitiesBacktester:
    """Backtester Protocol implementation for equities.

    run() does not execute a real backtest (the real equities validation
    happens in tests/test_equities_lane/); it returns a structural result.
    """

    def __init__(self, config: EquitiesConfig) -> None:
        self.config = config

    def run(self, target: str | None = None) -> GenericBacktestResult:
        return GenericBacktestResult(
            lane=self.config.lane,
            degraded=False,
            failure_notes=[],
            extra={"session": target, "walk_forward": self.config.to_dict().get("walk_forward", {})},
        )

    def validate_config(self) -> list[str]:
        return validate_lane_config(self.config)
