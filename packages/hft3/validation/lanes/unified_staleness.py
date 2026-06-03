"""Per-lane staleness paths.

Each lane registers its own critical file paths. The unified staleness
check consults this registry so changes in crypto/equities/options lane
packages invalidate a GREEN certification, not just CME paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .lane import Lane
from .lane_registry import LaneRegistry
from .registration import register_all_lanes

DEFAULT_LANE_PATHS: dict[str, list[str]] = {
    Lane.CME_FUTURES.value: [
        "packages/features_engine/src/features/mbo_features.py",
        "packages/features_engine/src/pipeline/market_state_pipeline.py",
        "packages/backtest_pipeline/src/hft_backtest_builder.py",
        "packages/backtest_pipeline/src/fee_model.py",
        "packages/backtest_pipeline/src/hft_strategy.py",
        "packages/backtest_pipeline/src/runner.py",
        "packages/backtest_pipeline/src/signal_backtester.py",
        "packages/replay/replay_session.py",
        "packages/hft3/validation",
        "scripts/run_event_replay.py",
    ],
    Lane.CRYPTO.value: [
        "packages/crypto_lane/src",
        "packages/crypto_lane/config",
        "backtests/configs/crypto_hypotheses",
        "models/candidates/crypto_h1_basis_compression.yaml",
        "models/candidates/crypto_h2_funding_capture.yaml",
        "models/candidates/crypto_h3_iv_rv_convergence.yaml",
        "models/candidates/crypto_h4_mempool_fee_regime.yaml",
        "models/candidates/crypto_h5_blockspace_stress.yaml",
        "models/candidates/crypto_h6_mempool_clear_reversion.yaml",
        "models/candidates/crypto_h7_congestion_event_study.yaml",
    ],
    Lane.EQUITIES.value: [
        "packages/equities_lane/src",
        "packages/equities_lane/config",
    ],
    Lane.OPTIONS.value: [
        "packages/options_lane/src",
        "packages/options_lane/config",
    ],
}


@dataclass
class LaneStalenessPaths:
    """Critical paths per lane."""

    paths_by_lane: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {lane: list(paths) for lane, paths in self.paths_by_lane.items()}


def get_lane_staleness_paths(auto_register: bool = True) -> LaneStalenessPaths:
    """Return the staleness path set, merging defaults with any registered paths."""
    if auto_register:
        register_all_lanes()
    paths = {k: list(v) for k, v in DEFAULT_LANE_PATHS.items()}
    reg = LaneRegistry.instance()
    for lane_reg in reg.all_registrations():
        lane_value = lane_reg.lane.value
        if not lane_reg.test_paths:
            continue
        if lane_value in paths:
            for tp in lane_reg.test_paths:
                if tp not in paths[lane_value]:
                    paths[lane_value].append(tp)
        else:
            paths[lane_value] = list(lane_reg.test_paths)
    return LaneStalenessPaths(paths_by_lane=paths)


def all_critical_paths(auto_register: bool = True) -> list[str]:
    """Flatten the per-lane critical paths into a single list."""
    return [p for paths in get_lane_staleness_paths(auto_register).paths_by_lane.values() for p in paths]
