"""Measured C++ hot-path latency — production source of truth for backtests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "cpp_latency_profile.yaml"

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
        paper = data.get("paper_order_latency") or {}
        measured = bool(data.get("order_ack_measured") or paper.get("measured"))
        order_ack_ms = data.get("order_ack_p99_ms")

        appendix = data.get("trial_order_ack_appendix") or {}
        stats = appendix.get("order_submit_to_ack_us") or {}

        def _us(key: str, fallback_ms: float) -> float:
            v = stats.get(key)
            if isinstance(v, (int, float)):
                return float(v)
            if key == "p95_us":
                v90 = stats.get("p90_us")
                if isinstance(v90, (int, float)):
                    return float(v90)
            if isinstance(order_ack_ms, (int, float)) and key == "p99_us":
                return float(order_ack_ms) * 1000.0
            return fallback_ms * 1000.0

        if measured and (stats.get("count") or order_ack_ms is not None):
            ack = LatencyPercentilesUs(
                _us("p50_us", float(order_ack_ms or 0)),
                _us("p90_us", float(order_ack_ms or 0)),
                _us("p99_us", float(order_ack_ms or 0)),
                "paper_order_submit_to_ack",
            )
            send = LatencyPercentilesUs(0.0, 0.0, 0.0, "included_in_gateway_ack")
            notes.append("gateway_ack from measured R|Trader paper submit→ack")
            return send, ack, False, notes

        yaml_cfg = cls.from_yaml_defaults()
        notes.append(
            "order_ack_blocked: paper submit→ack not measured; "
            "TCP connect is network health only — not used for gateway_ack"
        )
        return yaml_cfg.order_send, yaml_cfg.gateway_ack, True, notes

    @classmethod
    def from_chi404_summary(cls, summary_path: Path) -> "CppLatencyProfile":
        data = json.loads(summary_path.read_text(encoding="utf-8"))
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

        sweep = list(LATENCY_INJECTION_SWEEP_US)
        yaml_cfg = cls.from_yaml_defaults()
        if yaml_cfg.injection_sweep_us:
            sweep = yaml_cfg.injection_sweep_us

        return cls(
            cpp_decision_compute=compute,
            order_send=send,
            gateway_ack=ack,
            feed_delay=feed,
            injection_sweep_us=sweep,
            order_ack_blocked=ack_blocked,
            notes=notes,
        )

    @classmethod
    def from_yaml_defaults(cls) -> "CppLatencyProfile":
        if not _CONFIG.is_file():
            return cls._fallback()
        raw = yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}

        def _block(name: str) -> LatencyPercentilesUs:
            b = raw.get(name, {})
            return LatencyPercentilesUs(
                float(b.get("p50_us", 0)),
                float(b.get("p95_us", 0)),
                float(b.get("p99_us", 0)),
                str(b.get("source", "yaml")),
            )

        return cls(
            cpp_decision_compute=_block("cpp_decision_compute"),
            order_send=_block("order_send"),
            gateway_ack=_block("gateway_ack"),
            feed_delay=_block("feed_delay"),
            injection_sweep_us=[int(x) for x in raw.get("injection_sweep_us", LATENCY_INJECTION_SWEEP_US)],
            order_ack_blocked=True,
            notes=["yaml defaults; paper order ack not measured"],
        )

    @classmethod
    def _fallback(cls) -> "CppLatencyProfile":
        return cls.from_yaml_defaults()

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
