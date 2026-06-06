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


def test_legacy_paper_order_latency_does_not_clear_native_ack_gate(tmp_path: Path):
    summary = tmp_path / "latency_summary.json"
    summary.write_text(
        json.dumps(
            {
                "cyclictest": {"max_p99_us": 11},
                "network": {"rithmic_tcp_65000": {"p99_ms": 4.0}},
                "order_ack_measured": True,
                "order_ack_p99_ms": 2.0,
                "paper_order_latency": {
                    "measured": True,
                    "authoritative": True,
                    "paired_count": 1200,
                },
            }
        ),
        encoding="utf-8",
    )

    prof = CppLatencyProfile.from_chi404_summary(summary)

    assert prof.order_ack_blocked is True
    assert prof.gateway_ack.p99_us == 0.0
    assert prof.gateway_ack.source == "order_ack_unmeasured_blocked"


def test_native_cpp_summary_uses_native_placement_and_ack(tmp_path: Path):
    summary = tmp_path / "latency_summary.json"
    summary.write_text(
        json.dumps(
            {
                "native_cpp_order_ack": {
                    "authoritative": True,
                    "source": "chi404_native_cpp_rithmic_latency_probe",
                    "hot_path_language": "c++",
                    "wrapper": "none",
                    "probe": "rithmic_latency_probe",
                    "tick_to_send_us": {
                        "count": 1000,
                        "p50_us": 20.0,
                        "p95_us": 30.0,
                        "p99_us": 40.0,
                    },
                    "send_to_ack_us": {
                        "count": 1000,
                        "p50_us": 1000.0,
                        "p95_us": 1500.0,
                        "p99_us": 2000.0,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    prof = CppLatencyProfile.from_chi404_summary(summary)

    assert prof.order_ack_blocked is False
    assert prof.order_send.p99_us == 40.0
    assert prof.gateway_ack.p99_us == 2000.0
    assert prof.measured_production_p99_us == 2040.0


def test_native_ack_without_native_placement_does_not_clear_gate(tmp_path: Path):
    summary = tmp_path / "latency_summary.json"
    summary.write_text(
        json.dumps(
            {
                "native_cpp_order_ack": {
                    "authoritative": True,
                    "hot_path_language": "c++",
                    "wrapper": "none",
                    "probe": "rithmic_latency_probe",
                    "send_to_ack_us": {
                        "count": 1000,
                        "p50_us": 1000.0,
                        "p95_us": 1500.0,
                        "p99_us": 2000.0,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    prof = CppLatencyProfile.from_chi404_summary(summary)

    assert prof.order_ack_blocked is True
    assert prof.gateway_ack.p99_us == 0.0
    assert prof.order_send.source == "order_ack_unmeasured_blocked"
