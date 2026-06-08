"""Behavioral tests for the offensive-defensive latency harness."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from equities_lane.src.experiments.offensive_defensive_latency import (  # noqa: E402
    ALL_MODES,
    DEF_BLOCK,
    DEF_ROUTE_SHIFT,
    DEF_SIZE_DOWN,
    DEF_SHADOW,
    ROUTE_NO_TRADE,
    ROUTE_OPTION_ONLY,
    ROUTE_STOCK_AND_OPTION,
    ROUTE_STOCK_ONLY,
    LatencyTrace,
    OffensiveDefensiveLatencyHarness,
    run_latency_suite,
)


REQUIRED_MODES = [
    "ALPHA_ONLY",
    "DEFENSE_SHADOW",
    "DEFENSE_HARD_BLOCK",
    "DEFENSE_SIZE_DOWN",
    "DEFENSE_ROUTE_SHIFT",
    "OPTION_STRESS",
    "SYNTHETIC_OPTION_ONLY_STRESS",
    "TOXIC_BOOK_STRESS",
    "STALE_DATA_STRESS",
    "BURST_LOAD_STRESS",
]


def _run(mode: str, ticks: int = 60, seed: int = 7) -> tuple[OffensiveDefensiveLatencyHarness, dict]:
    h = OffensiveDefensiveLatencyHarness(mode=mode, seed=seed)
    return h, h.run(n_ticks=ticks, run_id=f"unit-{mode}")


@pytest.mark.parametrize("mode", ALL_MODES)
def test_mode_runs_and_emits_required_report_fields(mode: str) -> None:
    h, report = _run(mode, ticks=40)
    assert h.traces
    assert report["mode"] == mode
    assert report["sessions"] == 1
    assert report["uses_real_route_comparator"] is True
    assert report["send_boundary_adapter"] == "OrderSendProbe"
    assert report["pass_fail_status"] == "PASS"
    assert report["summary_table"]
    row = report["summary_table"][0]
    for key in (
        "Mode",
        "Sessions",
        "Orders Sent",
        "Orders Blocked",
        "p50 tick-to-send",
        "p99 tick-to-send",
        "p99.9 tick-to-send",
        "Defense overhead p99",
        "Late vetoes",
        "Risk bypasses",
        "Stale decisions",
        "Synthetic option exec violations",
        "Final route distribution",
        "Pass/Fail",
    ):
        assert key in row


def test_required_mode_names_match_prompt() -> None:
    assert ALL_MODES == REQUIRED_MODES
    assert "STALE_DATA" not in ALL_MODES
    assert "BURST_LOAD" not in ALL_MODES


def test_alpha_only_baseline_has_no_defense_cost_or_option_routes() -> None:
    _, report = _run("ALPHA_ONLY", ticks=40)
    counters = report["counters"]
    assert counters["orders_sent"] > 0
    assert counters["orders_blocked"] > 0
    assert counters["hard_blocks"] == 0
    assert counters["shadow_alerts"] == 0
    assert counters["option_routes_sent"] == 0
    assert report["latency"]["p99_defensive_eval_ns"] == 0.0
    assert report["latency"]["defense_overhead_p99_ns"] == 0.0


def test_defense_hard_block_blocks_toxic_traces_before_send() -> None:
    h, report = _run("DEFENSE_HARD_BLOCK", ticks=45)
    counters = report["counters"]
    assert counters["hard_blocks"] > 0
    assert counters["toxic_events_blocked"] == counters["hard_blocks"]
    blocked = [t for t in h.traces if t.defensive_action == DEF_BLOCK]
    assert blocked
    assert all(t.order_blocked for t in blocked)
    assert all(not t.order_sent and t.order_send_ts_ns == 0 for t in blocked)
    assert counters["order_sent_before_defense_count"] == 0
    assert counters["order_sent_before_risk_count"] == 0


def test_shadow_mode_alerts_without_blocking_toxic_flow() -> None:
    h, report = _run("DEFENSE_SHADOW", ticks=45)
    counters = report["counters"]
    shadowed = [t for t in h.traces if t.defensive_action == DEF_SHADOW]
    assert shadowed
    assert counters["shadow_alerts"] == len(shadowed)
    assert counters["hard_blocks"] == 0
    assert all(t.order_sent for t in shadowed)


def test_size_down_mode_reduces_size_before_risk_and_send() -> None:
    h, report = _run("DEFENSE_SIZE_DOWN", ticks=30)
    counters = report["counters"]
    assert counters["size_down_count"] == 30
    sent_size_down = [t for t in h.traces if t.defensive_action == DEF_SIZE_DOWN and t.order_sent]
    assert sent_size_down
    assert all(t.final_order_qty < t.initial_order_qty for t in sent_size_down)
    assert all(t.defensive_done_ts_ns <= t.risk_start_ts_ns <= t.order_send_ts_ns for t in sent_size_down)


def test_route_shift_mode_shifts_option_comparator_route_before_risk() -> None:
    h, report = _run("DEFENSE_ROUTE_SHIFT", ticks=30)
    counters = report["counters"]
    shifted = [t for t in h.traces if t.defensive_action == DEF_ROUTE_SHIFT]
    assert shifted
    assert counters["route_shift_count"] == len(shifted)
    assert any(t.route_candidate in {ROUTE_OPTION_ONLY, ROUTE_STOCK_AND_OPTION} for t in shifted)
    assert all(t.final_route == ROUTE_STOCK_ONLY for t in shifted)
    assert counters["route_flip_after_risk_count"] == 0


def test_option_stress_sends_real_option_routes_and_downgrades_bad_quotes() -> None:
    _, report = _run("OPTION_STRESS", ticks=84)
    counters = report["counters"]
    option_sent = counters["option_routes_sent"] + counters["stock_and_option_routes_sent"]
    assert option_sent > 0
    assert counters["stale_quote_downgrade_count"] > 0
    assert counters["wide_spread_downgrade_count"] > 0
    assert counters["missing_nbbo_downgrade_count"] > 0
    assert counters["option_route_without_real_nbbo_count"] == 0
    assert counters["option_route_with_stale_quote_count"] == 0
    assert counters["option_route_with_wide_spread_count"] == 0


def test_synthetic_option_only_never_executes_option_route() -> None:
    h, report = _run("SYNTHETIC_OPTION_ONLY_STRESS", ticks=40)
    counters = report["counters"]
    assert counters["synthetic_option_downgrade_count"] == 40
    assert counters["synthetic_option_executable_violation_count"] == 0
    assert counters["option_routes_sent"] == 0
    assert counters["stock_and_option_routes_sent"] == 0
    assert all(t.final_route != ROUTE_OPTION_ONLY for t in h.traces)
    assert all(t.final_route != ROUTE_STOCK_AND_OPTION for t in h.traces)


def test_toxic_book_stress_blocks_every_toxic_order() -> None:
    h, report = _run("TOXIC_BOOK_STRESS", ticks=35)
    counters = report["counters"]
    assert counters["toxic_events_detected"] == 35
    assert counters["toxic_events_blocked"] == 35
    assert counters["orders_sent"] == 0
    assert counters["orders_blocked"] == 35
    assert report["routes"] == {ROUTE_NO_TRADE: 35}
    assert all(t.defensive_action == DEF_BLOCK for t in h.traces)


def test_stale_data_stress_does_not_produce_orders() -> None:
    h, report = _run("STALE_DATA_STRESS", ticks=25)
    counters = report["counters"]
    assert counters["orders_sent"] == 0
    assert counters["orders_blocked"] == 25
    assert counters["stale_decision_count"] == 25
    assert report["pass_fail_status"] == "PASS"
    assert all(t.stale_data_flag and not t.order_sent for t in h.traces)


def test_burst_load_stress_increases_deterministic_p99_latency() -> None:
    normal = OffensiveDefensiveLatencyHarness(mode="ALPHA_ONLY", seed=9).run(
        n_ticks=40,
        run_id="normal",
        compute_baseline=False,
    )
    burst = OffensiveDefensiveLatencyHarness(mode="BURST_LOAD_STRESS", seed=9).run(
        n_ticks=40,
        run_id="burst",
        baseline_latency=normal["latency"],
        compute_baseline=False,
    )
    assert burst["latency"]["p99_tick_to_send_ns"] > normal["latency"]["p99_tick_to_send_ns"]
    assert burst["latency"]["defense_overhead_p99_ns"] > 0
    assert burst["counters"]["risk_budget_breach_count"] > 0


def test_same_seed_produces_identical_report_counts_routes_and_latency() -> None:
    h1 = OffensiveDefensiveLatencyHarness(mode="OPTION_STRESS", seed=123)
    h2 = OffensiveDefensiveLatencyHarness(mode="OPTION_STRESS", seed=123)
    r1 = h1.run(120, "d1")
    r2 = h2.run(120, "d2")
    assert r1["counters"] == r2["counters"]
    assert r1["routes"] == r2["routes"]
    assert r1["latency"] == r2["latency"]
    assert [t.final_route for t in h1.traces] == [t.final_route for t in h2.traces]
    assert r1["trace_count"] == 120
    assert len(r1["trace_sample"]) == 20


def test_sent_traces_have_monotonic_pre_send_timestamps() -> None:
    h, _ = _run("OPTION_STRESS", ticks=50)
    sent = [t for t in h.traces if t.order_sent]
    assert sent
    for t in sent:
        ordered = [
            t.local_recv_ts_ns,
            t.decode_done_ts_ns,
            t.book_update_done_ts_ns,
            t.feature_ready_ts_ns,
            t.offensive_signal_ts_ns,
            t.defensive_start_ts_ns,
            t.defensive_done_ts_ns,
            t.route_selected_ts_ns,
            t.risk_start_ts_ns,
            t.risk_done_ts_ns,
            t.execution_eligibility_done_ts_ns,
            t.order_serialized_ts_ns,
            t.order_send_ts_ns,
        ]
        assert ordered == sorted(ordered)
        assert t.order_send_ts_ns >= t.defensive_done_ts_ns
        assert t.order_send_ts_ns >= t.risk_done_ts_ns


def test_required_trace_fields_exist() -> None:
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
        "option_iv_status",
        "route_comparator_used",
    ]
    t = LatencyTrace()
    for attr in required:
        assert hasattr(t, attr), f"missing {attr}"


@pytest.mark.parametrize(
    ("violation", "counter"),
    [
        ("send_before_defense", "order_sent_before_defense_count"),
        ("send_before_risk", "order_sent_before_risk_count"),
        ("route_flip_after_risk", "route_flip_after_risk_count"),
    ],
)
def test_invariant_checks_can_fail(violation: str, counter: str) -> None:
    h = OffensiveDefensiveLatencyHarness(
        mode="ALPHA_ONLY",
        seed=11,
        inject_violation=violation,
    )
    report = h.run(n_ticks=10, run_id=f"bad-{violation}", compute_baseline=False)
    assert report["pass_fail_status"] == "FAIL"
    assert report["counters"][counter] > 0
    assert report["failure_reasons"]


def test_latency_suite_emits_all_mode_table() -> None:
    suite = run_latency_suite(ticks=35, seed=5)
    assert suite["pass_fail_status"] == "PASS"
    assert suite["modes"] == REQUIRED_MODES
    assert len(suite["summary_table"]) == len(REQUIRED_MODES)
    assert {row["Mode"] for row in suite["summary_table"]} == set(REQUIRED_MODES)
    assert any(row["Defense overhead p99"] > 0 for row in suite["summary_table"] if row["Mode"] != "ALPHA_ONLY")


def test_cli_writes_all_mode_report_artifact(tmp_path: Path) -> None:
    out = tmp_path / "latency_suite.json"
    cmd = [
        sys.executable,
        str(_REPO / "packages" / "equities_lane" / "src" / "experiments" / "offensive_defensive_latency.py"),
        "--all-modes",
        "--ticks",
        "20",
        "--output",
        str(out),
    ]
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
    assert result.returncode == 0, result.stderr + result.stdout
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["pass_fail_status"] == "PASS"
    assert data["modes"] == REQUIRED_MODES
    assert len(data["summary_table"]) == len(REQUIRED_MODES)
