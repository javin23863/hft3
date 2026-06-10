"""Tests for the latency floor clamp (Finding 1).

Verifies:
- config_loader clamps latency_ms below LATENCY_FLOOR_MS at load time.
- execution_sim clamps at point of use even if a cloned config slips through.
- LATENCY_FLOOR_MS constant is 5.0.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from equities_lane.src.types import LATENCY_FLOOR_MS


def test_latency_floor_constant_value():
    assert LATENCY_FLOOR_MS == 5.0


def test_config_loader_clamps_below_floor(tmp_path: Path) -> None:
    """yaml latency_ms 1.0 → loaded ExecutionConfig.latency_ms == 5.0."""
    universe_yaml = tmp_path / "universe.yaml"
    universe_yaml.write_text(
        textwrap.dedent(
            """\
            repo_root: .
            execution:
              latency_ms: 1.0
              slippage_bps: 10.0
              fee_per_share: 0.005
              allow_short: false
              borrow_fee_bps: 50.0
              halt_on_limit_up: true
            """
        ),
        encoding="utf-8",
    )
    from equities_lane.src.config_loader import load_universe

    _, universe, _ = load_universe(universe_yaml)
    assert universe.execution.latency_ms == LATENCY_FLOOR_MS


def test_config_loader_preserves_above_floor(tmp_path: Path) -> None:
    """yaml latency_ms 10.0 → loaded ExecutionConfig.latency_ms == 10.0 (unaffected)."""
    universe_yaml = tmp_path / "universe.yaml"
    universe_yaml.write_text(
        textwrap.dedent(
            """\
            repo_root: .
            execution:
              latency_ms: 10.0
            """
        ),
        encoding="utf-8",
    )
    from equities_lane.src.config_loader import load_universe

    _, universe, _ = load_universe(universe_yaml)
    assert universe.execution.latency_ms == 10.0


def test_config_loader_default_equals_floor(tmp_path: Path) -> None:
    """When latency_ms is absent the default equals LATENCY_FLOOR_MS."""
    universe_yaml = tmp_path / "universe.yaml"
    universe_yaml.write_text("repo_root: .\n", encoding="utf-8")
    from equities_lane.src.config_loader import load_universe

    _, universe, _ = load_universe(universe_yaml)
    assert universe.execution.latency_ms == LATENCY_FLOOR_MS


def test_execution_sim_clamps_below_floor() -> None:
    """simulate_fill uses max(config.latency_ms, LATENCY_FLOOR_MS) so a swept clone
    with latency_ms=1.0 still produces a fill timestamped at floor latency."""
    from equities_lane.src.backtest.execution_sim import OrderIntent, simulate_fill
    from equities_lane.src.types import ExecutionConfig

    # Directly construct a config with sub-floor latency (simulates a swept clone).
    cfg = ExecutionConfig(
        latency_ms=1.0,  # below floor — should be clamped inside simulate_fill
        slippage_bps=0.0,
        fee_per_share=0.0,
        allow_short=False,
        borrow_fee_bps=0.0,
        halt_on_limit_up=False,
    )
    intent = OrderIntent(ts_ns=0, side="buy", qty=100)
    fill = simulate_fill(intent, bid_px=10.0, ask_px=10.0, config=cfg)
    assert fill is not None
    expected_ns = int(LATENCY_FLOOR_MS * 1_000_000)
    assert fill.ts_ns == expected_ns, (
        f"Expected exec_ts={expected_ns} ns (floor), got {fill.ts_ns}"
    )
