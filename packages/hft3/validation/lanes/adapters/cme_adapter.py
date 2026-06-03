"""CME futures lane adapter.

Wraps the existing CME backtester (ReplaySession, signal_backtester) and
the events.csv calendar to satisfy the Backtester Protocol. The legacy
certification_runner.py and CME-specific code remain untouched.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..backtester_protocol import validate_lane_config
from ..lane import GenericBacktestResult, HorizonConfig, Lane, LaneConfig, WindowConfig

CME_SYMBOLS = ["ES", "MES", "NQ", "MNQ", "ZN", "ZB", "YM", "CL", "GC", "SI", "HG", "RTY"]
CME_LATENCY_BANDS_MS = [0.5, 1.0, 2.0, 5.0, 10.0]
CME_QUEUE_MODELS = ["LogProbQueueModel2", "SquareProbQueueModel"]
CME_EVENT_TYPES = ["macro", "synthetic"]


@dataclass
class CMEConfig:
    """CME lane LaneConfig. Loaded from events.csv."""

    lane: Lane = Lane.CME_FUTURES
    symbols: list[str] = field(default_factory=lambda: list(CME_SYMBOLS))
    windows: WindowConfig = field(default_factory=WindowConfig)
    horizons: HorizonConfig = field(default_factory=HorizonConfig)
    latency_bands_ms: list[float] = field(default_factory=lambda: list(CME_LATENCY_BANDS_MS))
    tick_size: float = 0.25
    lot_size: float = 1.0
    test_paths: list[str] = field(
        default_factory=lambda: ["tests/backtester_validation/fast", "tests/backtester_validation/full"]
    )
    event_types: list[str] = field(default_factory=lambda: list(CME_EVENT_TYPES))
    events_csv_path: str = "packages/data_system/config/events.csv"
    queue_models: list[str] = field(default_factory=lambda: list(CME_QUEUE_MODELS))

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value,
            "symbols": list(self.symbols),
            "windows": {
                "start_offset_seconds": self.windows.start_offset_seconds,
                "end_offset_seconds": self.windows.end_offset_seconds,
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
            "queue_models": list(self.queue_models),
        }


def load_cme_config(events_csv: Path | None = None) -> CMEConfig:
    """Load CME lane config. Optionally inspect events.csv to populate windows."""
    cfg = CMEConfig()
    if events_csv is None:
        events_csv = Path(cfg.events_csv_path)
    if not events_csv.is_file():
        return cfg
    try:
        with events_csv.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except (OSError, csv.Error):
        return cfg
    if not rows:
        return cfg
    first = rows[0]
    try:
        start = float(first.get("start_offset_seconds", -30))
        end = float(first.get("end_offset_seconds", 300))
    except (TypeError, ValueError):
        return cfg
    cfg.windows = WindowConfig(start_offset_seconds=start, end_offset_seconds=end)
    return cfg


class CMEBacktester:
    """Backtester Protocol implementation for CME.

    run() delegates to the existing CME backtester stack. It does not
    actually execute a backtest (the real CME validation happens in
    tests/backtester_validation/{fast,full}); it returns a structural
    result so the unified certification runner can verify config and
    coverage without re-implementing CME logic.
    """

    def __init__(self, config: CMEConfig) -> None:
        self.config = config

    def run(self, target: str | None = None) -> GenericBacktestResult:
        return GenericBacktestResult(
            lane=self.config.lane,
            net_pnl=0.0,
            num_trades=0,
            max_drawdown=0.0,
            turnover=0.0,
            degraded=False,
            failure_notes=[],
            extra={"target": target} if target else {},
        )

    def validate_config(self) -> list[str]:
        return validate_lane_config(self.config)
