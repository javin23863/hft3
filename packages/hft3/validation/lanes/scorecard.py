"""Lane-aware scorecard.

Replaces the CME-hardcoded scorecard in certification_runner.py with a
per-lane coverage structure. Legacy fields (covered_symbols,
covered_event_types) are preserved as the CME lane's coverage for
backward compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .lane import Lane
from .lane_registry import LaneRegistry
from .registration import register_all_lanes


@dataclass
class LaneCoverage:
    """Coverage metadata for one lane."""

    symbols: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)
    latency_bands_ms: list[float] = field(default_factory=list)
    test_paths: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "symbols": list(self.symbols),
            "event_types": list(self.event_types),
            "latency_bands_ms": list(self.latency_bands_ms),
            "test_paths": list(self.test_paths),
        }
        d.update(self.extra)
        return d


@dataclass
class LaneScorecard:
    """Unified lane-aware scorecard."""

    covered_lanes: list[str] = field(default_factory=list)
    lane_coverage: dict[str, dict[str, Any]] = field(default_factory=dict)
    timestamp_utc: str = ""
    git_sha: str = ""
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "timestamp_utc": self.timestamp_utc,
            "git_sha": self.git_sha,
            "covered_lanes": list(self.covered_lanes),
            "lane_coverage": {k: dict(v) for k, v in self.lane_coverage.items()},
        }


def build_lane_scorecard(
    git_sha: str = "",
    timestamp_utc: str = "",
    *,
    auto_register: bool = True,
) -> LaneScorecard:
    """Build a lane-aware scorecard by querying each registered lane's adapter.

    If auto_register is True, registers all four lane adapters before building.
    """
    if auto_register:
        register_all_lanes()
    reg = LaneRegistry.instance()
    card = LaneScorecard(git_sha=git_sha, timestamp_utc=timestamp_utc)
    for lane_reg in reg.all_registrations():
        lane = lane_reg.lane
        try:
            cfg = lane_reg.config_loader()
        except Exception:
            cfg = None
        if cfg is None:
            coverage = LaneCoverage(test_paths=list(lane_reg.test_paths))
        else:
            coverage = LaneCoverage(
                symbols=list(getattr(cfg, "symbols", [])),
                event_types=list(getattr(cfg, "event_types", [])),
                latency_bands_ms=list(getattr(cfg, "latency_bands_ms", [])),
                test_paths=list(getattr(cfg, "test_paths", list(lane_reg.test_paths))),
                extra=getattr(cfg, "to_dict", lambda: {})(),
            )
        # Drop nested config keys from the extra so the scorecard stays clean
        for key in (
            "lane",
            "symbols",
            "windows",
            "horizons",
            "latency_bands_ms",
            "tick_size",
            "lot_size",
            "test_paths",
            "event_types",
        ):
            coverage.extra.pop(key, None)
        card.covered_lanes.append(lane.value)
        card.lane_coverage[lane.value] = coverage.to_dict()
    return card


def legacy_cme_scorecard_fields(card: LaneScorecard) -> dict[str, Any]:
    """Extract the legacy CME-only scorecard fields for backward compatibility.

    The old certification_runner.py wrote flat fields like covered_symbols,
    covered_event_types, covered_latency_bands, covered_queue_models. This
    function returns those fields extracted from the CME lane's coverage.
    """
    cme = card.lane_coverage.get(Lane.CME_FUTURES.value, {})
    return {
        "covered_modules": [
            "backtest_pipeline",
            "execution",
            "replay",
            "features_engine",
            "workbench",
        ],
        "covered_symbols": cme.get("symbols", []),
        "covered_event_types": cme.get("event_types", []),
        "covered_latency_bands": cme.get("latency_bands_ms", []),
        "covered_queue_models": cme.get("queue_models", []),
        "covered_execution_modes": ["REPLAY"],
    }
