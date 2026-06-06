"""CME futures lane adapter.

Wraps the existing CME backtester (ReplaySession, signal_backtester) and
the events.csv calendar to satisfy the Backtester Protocol. The legacy
certification_runner.py and CME-specific code remain untouched.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apps.workbench.src.artifacts.paths import artifact_root

from ..backtester_protocol import validate_lane_config
from ..lane import (
    CME_TRUE_HFT_DMA_PROFILE,
    GenericBacktestResult,
    HorizonConfig,
    Lane,
    LaneCapabilityProfile,
    LaneConfig,
    WindowConfig,
)

CME_SYMBOLS = ["ES", "MES", "NQ", "MNQ", "ZN", "ZB", "YM", "CL", "GC", "SI", "HG", "RTY"]
CME_LATENCY_BANDS_MS = [0.5, 1.0, 2.0, 5.0, 10.0]
CME_QUEUE_MODELS = ["LogProbQueueModel2", "SquareProbQueueModel"]
CME_EVENT_TYPES = ["macro"]


class CMEConfigError(ValueError):
    """Raised when events.csv cannot produce a valid CME config."""


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
    capability_profile: LaneCapabilityProfile = CME_TRUE_HFT_DMA_PROFILE

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
            "capability_profile": self.capability_profile.to_dict(),
        }


def load_cme_config(events_csv: Path | None = None) -> CMEConfig:
    """Load CME lane config. Optionally inspect events.csv to populate windows."""
    cfg = CMEConfig()
    if events_csv is None:
        events_csv = Path(cfg.events_csv_path)
    if not events_csv.is_file():
        raise CMEConfigError(f"CME events.csv not found: {events_csv}")
    try:
        with events_csv.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise CMEConfigError(f"Failed to read CME events.csv: {events_csv}") from exc
    if not rows:
        raise CMEConfigError(f"CME events.csv is empty: {events_csv}")
    first = rows[0]
    for column in ("start_offset_seconds", "end_offset_seconds"):
        value = first.get(column)
        if value is None:
            raise CMEConfigError(f"CME events.csv missing required column: {column}")
        if value.strip() == "":
            raise CMEConfigError(f"CME events.csv has empty value for: {column}")
    try:
        start = float(first["start_offset_seconds"])
        end = float(first["end_offset_seconds"])
    except ValueError as exc:
        raise CMEConfigError("CME events.csv has invalid numeric offsets") from exc
    cfg.windows = WindowConfig(start_offset_seconds=start, end_offset_seconds=end)
    return cfg


class CMEBacktester:
    """Backtester Protocol implementation for CME.

    run() reads execution evidence emitted by scripts/run_event_replay.py.
    Missing or invalid replay artifacts degrade the result rather than
    synthesizing clean zero metrics.
    """

    def __init__(self, config: CMEConfig) -> None:
        self.config = config

    def run(self, target: str | None = None) -> GenericBacktestResult:
        if not target:
            return self._degraded_missing_artifact(target)

        artifact = self._find_replay_artifact(target)
        if artifact is None:
            return self._degraded_missing_artifact(target)

        extra: dict[str, Any] = {
            "target": target,
            "execution_evidence_status": "FOUND",
            "execution_evidence_artifact": str(artifact),
            "execution_evidence_source": f"{artifact_root()} replay result.json",
        }
        try:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return self._degraded_invalid_artifact(target, artifact, f"Malformed replay artifact: {exc}")
        if not isinstance(payload, dict):
            return self._degraded_invalid_artifact(target, artifact, "Malformed replay artifact: root is not an object")

        engine = (payload.get("engines") or {}).get("replay_execution_adapter")
        if not isinstance(engine, dict):
            return self._degraded_invalid_artifact(
                target,
                artifact,
                "Replay artifact missing engines.replay_execution_adapter",
            )
        if engine.get("skipped"):
            return self._degraded_invalid_artifact(target, artifact, "Replay execution adapter was skipped")
        if engine.get("error"):
            return self._degraded_invalid_artifact(
                target,
                artifact,
                f"Replay execution adapter error: {engine.get('error')}",
            )

        metrics = engine.get("result")
        if not isinstance(metrics, dict):
            return self._degraded_invalid_artifact(
                target,
                artifact,
                "Replay artifact missing engines.replay_execution_adapter.result",
            )
        if metrics.get("skipped"):
            return self._degraded_invalid_artifact(target, artifact, "Replay execution result was skipped")
        if metrics.get("error"):
            return self._degraded_invalid_artifact(
                target,
                artifact,
                f"Replay execution result error: {metrics.get('error')}",
            )
        missing_metrics = [name for name in ("balance", "num_trades", "trading_volume") if name not in metrics]
        if missing_metrics:
            return self._degraded_invalid_artifact(
                target,
                artifact,
                f"Replay execution result missing required metrics: {', '.join(missing_metrics)}",
            )

        max_drawdown = 0.0
        if "max_drawdown" in metrics:
            try:
                max_drawdown = float(metrics["max_drawdown"])
            except (TypeError, ValueError):
                return self._degraded_invalid_artifact(
                    target,
                    artifact,
                    "Replay execution result has invalid max_drawdown",
                )
            extra["max_drawdown_evidence_status"] = "FOUND"
        else:
            extra["max_drawdown_evidence_status"] = "MISSING"
        try:
            net_pnl = float(metrics["balance"])
            num_trades = int(metrics["num_trades"])
            turnover = float(metrics["trading_volume"])
        except (TypeError, ValueError):
            return self._degraded_invalid_artifact(
                target,
                artifact,
                "Replay execution result has invalid numeric metrics",
            )

        return GenericBacktestResult(
            lane=self.config.lane,
            net_pnl=net_pnl,
            num_trades=num_trades,
            max_drawdown=max_drawdown,
            turnover=turnover,
            degraded=False,
            failure_notes=[],
            extra=extra,
        )

    def validate_config(self) -> list[str]:
        return validate_lane_config(self.config)

    def _find_replay_artifact(self, target: str) -> Path | None:
        artifacts = sorted(artifact_root().glob(f"{target}_replay*/result.json"))
        return artifacts[-1] if artifacts else None

    def _degraded_missing_artifact(self, target: str | None) -> GenericBacktestResult:
        root = artifact_root()
        hint = str(root / f"{target}_replay*" / "result.json") if target else str(root / "{target}_replay*" / "result.json")
        notes = ["CME replay execution evidence is missing", f"Expected artifact: {hint}"]
        extra = {
            "execution_evidence_status": "MISSING_REPLAY_ARTIFACT",
            "execution_evidence_required_artifact": hint,
        }
        if target:
            extra["target"] = target
        return GenericBacktestResult(
            lane=self.config.lane,
            degraded=True,
            failure_notes=notes,
            extra=extra,
        )

    def _degraded_invalid_artifact(self, target: str, artifact: Path, note: str) -> GenericBacktestResult:
        return GenericBacktestResult(
            lane=self.config.lane,
            degraded=True,
            failure_notes=[note],
            extra={
                "target": target,
                "execution_evidence_status": "INVALID_REPLAY_ARTIFACT",
                "execution_evidence_artifact": str(artifact),
                "execution_evidence_source": f"{artifact_root()} replay result.json",
            },
        )
