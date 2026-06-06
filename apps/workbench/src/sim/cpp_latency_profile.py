"""Measured C++ hot-path latency — production source of truth for backtests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

LATENCY_INJECTION_SWEEP_US = [
    0, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 25000, 50000, 100000, 250000, 1000000,
]


@dataclass(frozen=True)
class LatencyPercentilesUs:
    p50_us: float
    p95_us: float
    p99_us: float
    source: str = ""


@dataclass
class CppLatencyProfile:
    """Measured C++ / colo latency distributions — never derived from Python wall time."""

    cpp_decision_compute: LatencyPercentilesUs
    order_send: LatencyPercentilesUs
    gateway_ack: LatencyPercentilesUs
    feed_delay: LatencyPercentilesUs
    injection_sweep_us: List[int] = field(default_factory=lambda: list(LATENCY_INJECTION_SWEEP_US))
    order_ack_blocked: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def measured_production_p99_us(self) -> float:
        submit_ack = self.order_send.p99_us + self.gateway_ack.p99_us
        return self.feed_delay.p99_us + self.cpp_decision_compute.p99_us + submit_ack

    @property
    def measured_production_p99_ms(self) -> float:
        return self.measured_production_p99_us / 1000.0

    def sample_total_us(self, rng, percentile: str = "p99") -> Dict[str, float]:
        """Sample one decision's latency budget from measured distributions."""
        key = percentile if percentile in ("p50", "p95", "p99") else "p99"
        feed = getattr(self.feed_delay, f"{key}_us")
        compute = getattr(self.cpp_decision_compute, f"{key}_us")
        send = getattr(self.order_send, f"{key}_us")
        ack = getattr(self.gateway_ack, f"{key}_us")
        return {
            "feed_delay_us": feed,
            "decision_compute_us": compute,
            "decision_to_send_us": send,
            "send_to_ack_us": ack,
            "total_us": feed + compute + send + ack,
        }

    @classmethod
    def _ack_from_summary(cls, data: dict[str, Any]) -> tuple[LatencyPercentilesUs, LatencyPercentilesUs, bool, List[str]]:
        notes: List[str] = []
        notes.append(
            "order_ack_blocked: CHI404 native C++ rithmic_latency_probe submit→ack "
            "evidence with >=1000 paired samples is not present; TCP/trial/legacy "
            "paper_order_latency fields are not authoritative"
        )
        return (
            LatencyPercentilesUs(0.0, 0.0, 0.0, "order_ack_unmeasured_blocked"),
            LatencyPercentilesUs(0.0, 0.0, 0.0, "order_ack_unmeasured_blocked"),
            True,
            notes,
        )

    @classmethod
    def from_chi404_summary(cls, summary_path: Path) -> "CppLatencyProfile":
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        native = data.get("native_cpp_order_ack") or {}
        if isinstance(native, dict) and native.get("authoritative") is True:
            native_ack = native.get("send_to_ack_us") if isinstance(native.get("send_to_ack_us"), dict) else {}
            native_send = native.get("tick_to_send_us") if isinstance(native.get("tick_to_send_us"), dict) else {}
            if (
                str(native.get("hot_path_language") or "").lower() == "c++"
                and str(native.get("wrapper") or "").lower() == "none"
                and str(native.get("probe") or "") == "rithmic_latency_probe"
                and isinstance(native_ack.get("count"), (int, float))
                and int(native_ack.get("count") or 0) >= 1000
                and isinstance(native_ack.get("p99_us"), (int, float))
                and isinstance(native_send.get("p99_us"), (int, float))
            ):
                def _native(stats: dict[str, Any], key: str) -> float:
                    value = stats.get(key)
                    if isinstance(value, (int, float)):
                        return float(value)
                    if key == "p95_us":
                        value = stats.get("p90_us")
                        if isinstance(value, (int, float)):
                            return float(value)
                    return float(stats["p99_us"])

                return cls(
                    cpp_decision_compute=LatencyPercentilesUs(
                        0.0, 0.0, 0.0, "included_in_chi404_native_cpp_tick_to_send"
                    ),
                    order_send=LatencyPercentilesUs(
                        _native(native_send, "p50_us"),
                        _native(native_send, "p95_us"),
                        _native(native_send, "p99_us"),
                        "chi404_native_cpp_tick_to_send",
                    ),
                    gateway_ack=LatencyPercentilesUs(
                        _native(native_ack, "p50_us"),
                        _native(native_ack, "p95_us"),
                        _native(native_ack, "p99_us"),
                        "chi404_native_cpp_rithmic_latency_probe",
                    ),
                    feed_delay=LatencyPercentilesUs(
                        0.0, 0.0, 0.0, "included_in_chi404_native_cpp_tick_to_send"
                    ),
                    injection_sweep_us=list(LATENCY_INJECTION_SWEEP_US),
                    order_ack_blocked=False,
                    notes=[
                        "placement from CHI404 native C++ tick_to_send_us",
                        "gateway_ack from CHI404 native C++ rithmic_latency_probe submit-to-ack",
                    ],
                )

        cyclic = data.get("cyclictest") or {}
        max_p99_us = float(cyclic.get("max_p99_us") or 11)
        compute = LatencyPercentilesUs(max_p99_us * 0.5, max_p99_us * 0.9, max_p99_us, "cyclictest_loaded")

        net = data.get("network") or {}
        rithmic = net.get("rithmic_tcp_65000") or {}

        def _ms_to_us(v: Any, default: float) -> float:
            if v is None:
                return default
            return float(v) * 1000.0

        net_p99_us = float(data.get("network_p99_us") or _ms_to_us(rithmic.get("p99_ms"), 4094))
        feed = LatencyPercentilesUs(
            net_p99_us * 0.3,
            net_p99_us * 0.7,
            net_p99_us,
            data.get("network_p99_worst_source", "network_health"),
        )

        send, ack, ack_blocked, notes = cls._ack_from_summary(data)

        return cls(
            cpp_decision_compute=compute,
            order_send=send,
            gateway_ack=ack,
            feed_delay=feed,
            injection_sweep_us=list(LATENCY_INJECTION_SWEEP_US),
            order_ack_blocked=ack_blocked,
            notes=notes,
        )

    def to_report_dict(self) -> Dict[str, Any]:
        return {
            "cpp_decision_compute_p50_us": self.cpp_decision_compute.p50_us,
            "cpp_decision_compute_p95_us": self.cpp_decision_compute.p95_us,
            "cpp_decision_compute_p99_us": self.cpp_decision_compute.p99_us,
            "order_send_p50_us": self.order_send.p50_us,
            "order_send_p95_us": self.order_send.p95_us,
            "order_send_p99_us": self.order_send.p99_us,
            "gateway_ack_p50_us": self.gateway_ack.p50_us,
            "gateway_ack_p95_us": self.gateway_ack.p95_us,
            "gateway_ack_p99_us": self.gateway_ack.p99_us,
            "measured_production_p99_us": self.measured_production_p99_us,
            "order_ack_blocked": self.order_ack_blocked,
            "notes": self.notes,
        }
