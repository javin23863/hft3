"""CHI404 measured latency summary for backtest replay (colo bare metal)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_CHI404_SUMMARY = _REPO / "runtime" / "latency_reports" / "latency_summary.json"

# BLUEPRINT.md § backtest realism; mirrors run_event_replay research cards.
BACKTEST_LATENCY_NOTE = (
    "TCP connect p99; order submit→ack not measured until Stage 3 paper harness"
)
LATENCY_BAND_MIN_MS = 0.5
LATENCY_BAND_MAX_MS = 10.0


def validate_replay_latency_ms(latency_ms: float, *, source: str) -> float:
    """Fail loud if latency is outside blueprint-mandated replay band."""
    ms = float(latency_ms)
    if not (LATENCY_BAND_MIN_MS <= ms <= LATENCY_BAND_MAX_MS):
        raise ValueError(
            f"Replay latency {ms} ms from {source} outside BLUEPRINT band "
            f"[{LATENCY_BAND_MIN_MS}, {LATENCY_BAND_MAX_MS}] ms"
        )
    return ms


def load_chi404_speed(summary_path: Path) -> dict[str, Any]:
    if not summary_path.is_file():
        raise FileNotFoundError(
            f"CHI404 latency summary missing: {summary_path}. "
            "Run scripts/latency_probe/run_all.sh on CHI404 or chi404_sync_trial_data.sh."
        )
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    network = s.get("network") or {}
    rithmic_tcp = network.get("rithmic_tcp_65000") or {}
    gateway = network.get("gateway_ping") or {}
    cyclictest = s.get("cyclictest") or {}
    trial = s.get("trial_order_ack_appendix") or {}

    rithmic_tcp_p99_ms = rithmic_tcp.get("p99_ms")
    if not isinstance(rithmic_tcp_p99_ms, (int, float)):
        raise ValueError("CHI404 summary has no rithmic_tcp_65000 p99_ms")

    return {
        "probe_run_id": s.get("run_id"),
        "probe_timestamp_utc": s.get("timestamp_utc"),
        "source": s.get("authoritative_source"),
        "cpu_loaded_p99_us": cyclictest.get("max_p99_us"),
        "gateway_ping_p99_ms": gateway.get("p99_ms"),
        "rithmic_tcp_65000_p99_ms": float(rithmic_tcp_p99_ms),
        "network_worst_p99_us": s.get("network_p99_us"),
        "network_worst_source": s.get("network_p99_worst_source"),
        "order_ack_p99_ms": s.get("order_ack_p99_ms"),
        "trial_order_ack_p99_ms": trial.get("order_ack_p99_ms"),
        "trial_order_ack_status": trial.get("status"),
        "backtest_latency_ms": float(rithmic_tcp_p99_ms),
        "backtest_latency_source": "CHI404 rithmic_tcp_65000 p99 (measured on colo bare metal)",
        "order_ack_measured": False,
    }


def resolve_replay_latency_ms(
    *,
    latency_ms: float | None,
    chi404_summary: Path = DEFAULT_CHI404_SUMMARY,
) -> tuple[float, str, dict[str, Any] | None]:
    """Return (latency_ms, source_label, chi404_payload_or_none) for replay scripts."""
    if latency_ms is not None:
        ms = validate_replay_latency_ms(float(latency_ms), source="CLI --latency-ms")
        return ms, "CLI --latency-ms", None
    chi404 = load_chi404_speed(chi404_summary.resolve())
    ms = validate_replay_latency_ms(
        float(chi404["backtest_latency_ms"]),
        source="CHI404 rithmic_tcp_65000 p99",
    )
    return ms, str(chi404.get("backtest_latency_source", "CHI404 summary")), chi404
