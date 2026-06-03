"""Backtester Protocol and config validation helpers.

Adapters wrap their lane's native backtester so it satisfies this Protocol.
The certification runner calls run() uniformly across all lanes.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .lane import BacktestResult, LaneConfig


@runtime_checkable
class Backtester(Protocol):
    """Structural type for any backtester, regardless of lane.

    Adapters implement this by delegating to the lane's native backtester.
    The certification runner calls run() uniformly across all lanes.
    """

    config: LaneConfig

    def run(self, target: str | None = None) -> BacktestResult:
        """Run a backtest. target is lane-specific (event_id, session_id, etc.)."""
        ...

    def validate_config(self) -> list[str]:
        """Return a list of validation errors; empty if config is valid."""
        ...


def validate_lane_config(cfg: Any) -> list[str]:
    """Structural check that an object satisfies the LaneConfig Protocol.

    Returns a list of error strings; empty if valid.
    """
    errors: list[str] = []
    if not hasattr(cfg, "lane"):
        errors.append("missing 'lane' attribute")
    if not hasattr(cfg, "symbols") or not isinstance(getattr(cfg, "symbols", None), list):
        errors.append("missing or invalid 'symbols' (must be list[str])")
    if not hasattr(cfg, "windows"):
        errors.append("missing 'windows' (must be WindowConfig)")
    if not hasattr(cfg, "horizons"):
        errors.append("missing 'horizons' (must be HorizonConfig)")
    if not hasattr(cfg, "latency_bands_ms") or not isinstance(
        getattr(cfg, "latency_bands_ms", None), list
    ):
        errors.append("missing or invalid 'latency_bands_ms' (must be list[float])")
    if not hasattr(cfg, "tick_size"):
        errors.append("missing 'tick_size'")
    if not hasattr(cfg, "lot_size"):
        errors.append("missing 'lot_size'")
    if not hasattr(cfg, "test_paths") or not isinstance(
        getattr(cfg, "test_paths", None), list
    ):
        errors.append("missing or invalid 'test_paths' (must be list[str])")
    if not hasattr(cfg, "event_types") or not isinstance(
        getattr(cfg, "event_types", None), list
    ):
        errors.append("missing or invalid 'event_types' (must be list[str])")
    if not hasattr(cfg, "capability_profile"):
        errors.append("missing 'capability_profile'")
    return errors
