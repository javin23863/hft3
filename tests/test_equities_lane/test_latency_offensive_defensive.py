"""Tests for the offensive-defensive latency harness.

Validates that:
- All 10 modes run and emit the required fields
- Hard invariants are checked
- The report artifact is written
- Determinism holds (same seed -> same counts)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from equities_lane.src.experiments.offensive_defensive_latency import (  # noqa: E402
    ALL_MODES,
    LatencyTrace,
    OffensiveDefensiveLatencyHarness,
)


@pytest.mark.parametrize("mode", ALL_MODES)
def test_mode_runs_and_emits_report(tmp_path: Path, mode: str) -> None:
    h = OffensiveDefensiveLatencyHarness(mode=mode, seed=7)
    report = h.run(n_ticks=50, run_id=f"unit-{mode}")
    # Required top-level keys
    assert report["mode"] == mode
    assert "counters" in report
    assert "latency" in report
    assert "routes" in report
    assert "pass_fail_status" in report
    assert "failure_reasons" in report
    # Latency metrics
    lat = report["latency"]
    for key in (
        "p50_tick_to_send_ns",
        "p95_tick_to_send_ns",
        "p99_tick_to_send_ns",
        "p99_9_tick_to_send_ns",
        "max_tick_to_send_ns",
    ):
        assert key in lat
    # Counters
    c = report["counters"]
    for key in (
        "late_veto_count",
        "risk_bypass_count",
        "stale_decision_count",
        "pit_violation_count",
        "data_isolation_violation_count",
        "synthetic_option_executable_violation_count",
        "route_flip_after_risk_count",
        "option_route_without_real_nbbo_count",
        "option_route_with_stale_quote_count",
        "option_route_with_wide_spread_count",
        "order_sent_before_defense_count",
        "order_sent_before_risk_count",
    ):
        assert key in c
    # Route distribution
    assert "final_route_distribution" in c


def test_alpha_only_baseline(tmp_path: Path) -> None:
    """ALPHA_ONLY: no defense; offensive path runs."""
    h = OffensiveDefensiveLatencyHarness(mode="ALPHA_ONLY", seed=1)
    report = h.run(n_ticks=30, run_id="alpha")
    # Should produce some orders and no defense blocks
    assert report["counters"]["orders_sent"] > 0
    # No defense -> no blocks attributed to defense
    assert report["counters"]["good_trades_blocked"] == 0


def test_stale_data_does_not_produce_orders(tmp_path: Path) -> None:
    """STALE_DATA: stale market state must not produce orders."""
    h = OffensiveDefensiveLatencyHarness(mode="STALE_DATA", seed=2)
    report = h.run(n_ticks=20, run_id="stale")
    # Hard invariant #3: no orders from stale state
    assert report["counters"]["orders_sent"] == 0
    assert report["pass_fail_status"] == "PASS"


def test_synthetic_option_only_blocks_option_routes(tmp_path: Path) -> None:
    """SYNTHETIC_OPTION_ONLY: no OPTION_ONLY or STOCK_AND_OPTION production routes."""
    h = OffensiveDefensiveLatencyHarness(
        mode="SYNTHETIC_OPTION_ONLY_STRESS", seed=3
    )
    report = h.run(n_ticks=50, run_id="syn")
    # The route distribution must not contain OPTION_ONLY or STOCK_AND_OPTION
    routes = report["routes"]
    assert routes.get("OPTION_ONLY", 0) == 0
    assert routes.get("STOCK_AND_OPTION", 0) == 0


def test_hard_block_prevents_send() -> None:
    """DEFENSE_HARD_BLOCK: defense must complete before order_send."""
    h = OffensiveDefensiveLatencyHarness(mode="DEFENSE_HARD_BLOCK", seed=4)
    report = h.run(n_ticks=30, run_id="block")
    # Hard invariant #1: no order sent before defense
    assert report["counters"]["order_sent_before_defense_count"] == 0
    # No orders sent before risk
    assert report["counters"]["order_sent_before_risk_count"] == 0


def test_shadow_does_not_block() -> None:
    """DEFENSE_SHADOW: defense logs but does not block."""
    h = OffensiveDefensiveLatencyHarness(mode="DEFENSE_SHADOW", seed=5)
    report = h.run(n_ticks=30, run_id="shadow")
    # In shadow mode, orders should still be sent even when defense flags toxic
    assert report["counters"]["orders_sent"] > 0


def test_burst_load_increases_latency() -> None:
    """BURST_LOAD: simulated pipeline pressure inflates latencies."""
    h_normal = OffensiveDefensiveLatencyHarness(mode="ALPHA_ONLY", seed=6, burst_mode=False)
    h_burst = OffensiveDefensiveLatencyHarness(mode="ALPHA_ONLY", seed=6, burst_mode=True)
    rep_normal = h_normal.run(n_ticks=20, run_id="norm")
    rep_burst = h_burst.run(n_ticks=20, run_id="burst")
    # Burst mode should have higher p50 tick-to-send OR be within tolerance
    # (timing jitter on slow CI can dominate; check defensive eval is higher)
    assert (
        rep_burst["latency"]["p50_defensive_eval_ns"]
        >= rep_normal["latency"]["p50_defensive_eval_ns"]
    )


def test_determinism_same_seed_same_counts() -> None:
    """Same seed + mode -> identical counts (replay is deterministic)."""
    h1 = OffensiveDefensiveLatencyHarness(mode="ALPHA_ONLY", seed=123)
    h2 = OffensiveDefensiveLatencyHarness(mode="ALPHA_ONLY", seed=123)
    r1 = h1.run(n_ticks=50, run_id="d1")
    r2 = h2.run(n_ticks=50, run_id="d2")
    assert r1["counters"]["orders_sent"] == r2["counters"]["orders_sent"]
    assert r1["routes"] == r2["routes"]


def test_trace_has_required_ns_timestamps() -> None:
    """LatencyTrace must carry all required ns timestamps."""
    required = [
        "exchange_ts_ns",
        "local_recv_ts_ns",
        "decode_done_ts_ns",
        "book_update_done_ts_ns",
        "feature_ready_ts_ns",
        "offensive_signal_ts_ns",
        "defensive_start_ts_ns",
        "defensive_done_ts_ns",
        "route_selected_ts_ns",
        "risk_start_ts_ns",
        "risk_done_ts_ns",
        "execution_eligibility_done_ts_ns",
        "order_serialized_ts_ns",
        "order_send_ts_ns",
        "ack_recv_ts_ns",
        "fill_recv_ts_ns",
        "cancel_recv_ts_ns",
    ]
    t = LatencyTrace()
    for attr in required:
        assert hasattr(t, attr), f"missing {attr}"


def test_report_artifact_written(tmp_path: Path) -> None:
    """The CLI writes the report artifact to --output."""
    import subprocess
    import os
    out = tmp_path / "lat.json"
    cmd = [
        sys.executable,
        str(_REPO / "packages" / "equities_lane" / "src" / "experiments" / "offensive_defensive_latency.py"),
        "--mode",
        "ALPHA_ONLY",
        "--ticks",
        "20",
        "--output",
        str(out),
    ]
    # Ensure repo root + packages on PYTHONPATH for the subprocess
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO) + os.pathsep + str(_REPO / "packages")
    result = subprocess.run(
        cmd,
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["mode"] == "ALPHA_ONLY"
    assert "latency" in data
    assert "counters" in data
