"""Break-even latency using C++-injected latency sweep — not Python runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from workbench.src.core.trade_audit import TradeAuditRecord, summarize_latency_us
from workbench.src.sim.cpp_latency_profile import LATENCY_INJECTION_SWEEP_US, CppLatencyProfile
from workbench.src.sim.latency_injector import CppLatencyInjector

LANE_THRESHOLDS_MS = {
    "microsecond": (0.0, 0.1),
    "sub_10ms": (0.1, 10.0),
    "10_250ms": (10.0, 250.0),
    "multi_second": (250.0, float("inf")),
}


@dataclass
class LatencyViability:
    breakeven_us: float
    breakeven_ms: float
    measured_production_p99_us: float
    measured_production_p99_ms: float
    latency_profitability_buffer_us: float
    latency_buffer_ms: float
    lane_required: str
    lane_measured: str
    lane_pass: bool
    pnl_by_injection_us: Dict[int, float]
    pnl_by_latency: Dict[float, float]
    per_trade_latency: Dict[str, Dict[str, float]]
    python_research_runtime_us: float
    cpp_hot_path_runtime_us: float
    simulated_latency_adjusted_pnl: float
    survives_cpp_execution_delay: bool
    recommendation: str
    cpp_latency_profile: Dict[str, float] = field(default_factory=dict)


def classify_lane(latency_ms: float) -> str:
    for name, (lo, hi) in LANE_THRESHOLDS_MS.items():
        if lo <= latency_ms < hi:
            return name
    return "multi_second"


def find_break_even_us(pnl_by_injection_us: Dict[int, float]) -> float:
    if not pnl_by_injection_us:
        return 0.0
    points = sorted(pnl_by_injection_us.items())
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        if y0 >= 0 and y1 < 0:
            if abs(y1 - y0) < 1e-12:
                return float(x0)
            return float(x0 + (0 - y0) * (x1 - x0) / (y1 - y0))
    if points[-1][1] >= 0:
        return float(points[-1][0] * 2)
    return float(points[0][0] / 2.0)


def sweep_injection_pnl(
    base_pnl: float,
    profile: CppLatencyProfile,
    *,
    full_sweep: bool = False,
    run_fn=None,
) -> Dict[int, float]:
    injector = CppLatencyInjector(profile)
    out: Dict[int, float] = {}
    sweep = profile.injection_sweep_us or LATENCY_INJECTION_SWEEP_US
    for inj_us in sweep:
        if full_sweep and run_fn is not None:
            out[inj_us] = float(run_fn(inj_us / 1000.0).get("net_pnl", 0.0))
        else:
            out[inj_us] = injector.pnl_decay_heuristic(base_pnl, float(inj_us))
    return out


def analyze_latency_viability(
    base_pnl: float,
    profile: CppLatencyProfile,
    required_lane: str,
    *,
    pnl_by_injection_us: Optional[Dict[int, float]] = None,
    audit_records: Optional[List[TradeAuditRecord]] = None,
    python_research_runtime_us: float = 0.0,
) -> LatencyViability:
    measured_us = profile.measured_production_p99_us
    measured_ms = measured_us / 1000.0
    cpp_hot_us = measured_us

    if pnl_by_injection_us is None:
        pnl_by_injection_us = sweep_injection_pnl(base_pnl, profile)

    breakeven_us = find_break_even_us(pnl_by_injection_us)
    breakeven_ms = breakeven_us / 1000.0
    buffer_us = breakeven_us - measured_us
    buffer_ms = buffer_us / 1000.0

    measured_lane = classify_lane(measured_ms)
    required_lo, _ = LANE_THRESHOLDS_MS.get(required_lane, (0, 10))
    measured_lo, _ = LANE_THRESHOLDS_MS.get(measured_lane, (0, 10))
    lane_pass = (measured_lo <= required_lo or measured_lane == required_lane) and buffer_us > 0

    adj_pnl = pnl_by_injection_us.get(int(measured_us), base_pnl)
    if int(measured_us) not in pnl_by_injection_us:
        nearest = min(pnl_by_injection_us.keys(), key=lambda k: abs(k - measured_us))
        adj_pnl = pnl_by_injection_us[nearest]

    survives = adj_pnl > 0 and buffer_us > 0 and lane_pass
    if survives:
        rec = "VIABLE"
    elif buffer_us > 0:
        rec = "MARGINAL"
    else:
        rec = "REJECT"

    per_trade = {}
    if audit_records:
        per_trade = {
            "tick_to_ack_us": summarize_latency_us(audit_records, "tick_to_ack_us"),
            "decision_compute_us": summarize_latency_us(audit_records, "decision_compute_us"),
        }

    pnl_by_ms = {k / 1000.0: v for k, v in pnl_by_injection_us.items()}

    return LatencyViability(
        breakeven_us=breakeven_us,
        breakeven_ms=breakeven_ms,
        measured_production_p99_us=measured_us,
        measured_production_p99_ms=measured_ms,
        latency_profitability_buffer_us=buffer_us,
        latency_buffer_ms=buffer_ms,
        lane_required=required_lane,
        lane_measured=measured_lane,
        lane_pass=lane_pass,
        pnl_by_injection_us=pnl_by_injection_us,
        pnl_by_latency=pnl_by_ms,
        per_trade_latency=per_trade,
        python_research_runtime_us=python_research_runtime_us,
        cpp_hot_path_runtime_us=cpp_hot_us,
        simulated_latency_adjusted_pnl=adj_pnl,
        survives_cpp_execution_delay=survives,
        recommendation=rec,
        cpp_latency_profile=profile.to_report_dict(),
    )
