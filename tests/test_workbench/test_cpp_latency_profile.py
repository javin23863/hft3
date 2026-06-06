"""C++ latency profile loader tests."""

import json
from pathlib import Path

from workbench.src.sim.cpp_latency_profile import CppLatencyProfile, LATENCY_INJECTION_SWEEP_US

REPO = Path(__file__).resolve().parents[2]
SUMMARY = REPO / "runtime" / "latency_reports" / "latency_summary.json"


def test_injection_sweep_complete():
    assert LATENCY_INJECTION_SWEEP_US == [
        0, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 25000, 50000, 100000, 250000, 1000000
    ]


def test_from_chi404_summary():
    if not SUMMARY.is_file():
        return
    prof = CppLatencyProfile.from_chi404_summary(SUMMARY)
    assert prof.cpp_decision_compute.p99_us > 0
    d = prof.to_report_dict()
    assert "gateway_ack_p99_us" in d
    if prof.order_ack_blocked:
        assert prof.gateway_ack.p99_us == 0
        assert prof.gateway_ack.source == "order_ack_unmeasured_blocked"
    else:
        assert prof.measured_production_p99_us > prof.cpp_decision_compute.p99_us
