"""Deterministic latency invariant checks (AlphaGeometry neuro-symbolic pattern)."""

from __future__ import annotations

from typing import Any, Dict, List


def _chain_tolerance_us() -> float:
    return 5.0


def check_latency_invariants(packet: Dict[str, Any]) -> Dict[str, Any]:
    obligations: List[str] = []
    violations: List[str] = []

    lat = packet.get("latency_authority") or {}
    if lat.get("python_research_runtime_authoritative") is True:
        violations.append("python_research_runtime must not be authoritative")

    if lat.get("lane_pass") is True:
        obligations.append("lane_pass => latency_profitability_buffer_us > 0")
        buf = lat.get("latency_profitability_buffer_us")
        if buf is None or float(buf) <= 0:
            violations.append("lane_pass true but latency_profitability_buffer_us <= 0")

    if lat.get("promote_candidate") is True:
        obligations.append("promote_candidate => survives_cpp_execution_delay and robustness_passed")
        if not lat.get("survives_cpp_execution_delay"):
            violations.append("promote_candidate true but survives_cpp_execution_delay false")
        if lat.get("robustness_passed") is not True:
            violations.append("promote_candidate true but robustness_passed not true")

    for i, trade in enumerate(packet.get("per_trade_audit") or []):
        obligations.append(f"trade[{i}]: market_data_exchange_ts_ns <= market_data_receive_ts_ns")
        exch = trade.get("market_data_exchange_ts")
        recv = trade.get("market_data_receive_ts")
        if exch is not None and recv is not None and int(recv) < int(exch):
            violations.append(f"trade[{i}]: market_data_receive_ts before market_data_exchange_ts")

        obligations.append(f"trade[{i}]: decision_end_ts_ns >= market_data_receive_ts_ns")
        dend = trade.get("decision_end_ts")
        if recv is not None and dend is not None and int(dend) < int(recv):
            violations.append(f"trade[{i}]: decision_end_ts before market_data_receive_ts")

        obligations.append(f"trade[{i}]: fill_ts_ns >= order_send_ts_ns when fill present")
        send = trade.get("order_send_ts")
        fill = trade.get("fill_ts")
        if send is not None and fill is not None and int(fill) > 0 and int(send) > 0 and int(fill) < int(send):
            violations.append(f"trade[{i}]: fill_ts before order_send_ts")

        fd = float(trade.get("feed_delay_us", 0))
        dc = float(trade.get("decision_compute_us", 0))
        dts = float(trade.get("decision_to_send_us", 0))
        sta = float(trade.get("send_to_ack_us", 0))
        tta = float(trade.get("tick_to_ack_us", 0))
        expected = fd + dc + dts + sta
        obligations.append(f"trade[{i}]: tick_to_ack_us ≈ feed + decision + send + ack")
        if abs(tta - expected) > _chain_tolerance_us():
            violations.append(
                f"trade[{i}]: tick_to_ack_us={tta:.1f} != chain sum {expected:.1f} (tol {_chain_tolerance_us()} µs)"
            )

    passed = len(violations) == 0
    return {"passed": passed, "obligations": obligations, "violations": violations}
