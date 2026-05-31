"""CHI404 measured latency summary for backtest replay (colo bare metal)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_CHI404_SUMMARY = _REPO / "runtime" / "latency_reports" / "latency_summary.json"

PAPER_ORDER_MIN_PAIRED = 1000

BACKTEST_LATENCY_NOTE_MEASURED = (
    "Paper order submit→ack p99 from R|Trader log bridge (CHI404 colo)"
)
BACKTEST_LATENCY_NOTE_UNMEASURED = (
    "Paper order submit→ack not measured; pass --latency-ms or run "
    "scripts/chi404_run_paper_latency_sweep.sh on CHI404. "
    "TCP connect is network health only — not used for execution latency."
)
# Backward-compatible alias for scripts that reference a single note string.
BACKTEST_LATENCY_NOTE = BACKTEST_LATENCY_NOTE_UNMEASURED
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


def resolve_order_ack_ms(summary: dict[str, Any]) -> tuple[float | None, bool, str]:
    """Return (order_ack_p99_ms, measured, source_label)."""
    paper = summary.get("paper_order_latency") or {}
    if paper.get("measured") and isinstance(summary.get("order_ack_p99_ms"), (int, float)):
        return float(summary["order_ack_p99_ms"]), True, "paper_order_latency.authoritative"

    appendix = summary.get("trial_order_ack_appendix") or {}
    if appendix.get("status") == "ok" and appendix.get("authoritative"):
        ms = appendix.get("order_ack_p99_ms")
        if isinstance(ms, (int, float)):
            return float(ms), True, "trial_order_ack_appendix.authoritative"

    stats = appendix.get("order_submit_to_ack_us") or {}
    count = int(stats.get("count") or 0)
    p99_us = stats.get("p99_us")
    if (
        appendix.get("status") == "ok"
        and appendix.get("authoritative")
        and count >= PAPER_ORDER_MIN_PAIRED
        and isinstance(p99_us, (int, float))
    ):
        return float(p99_us) / 1000.0, True, "trial_order_ack_appendix.authoritative"

    return None, False, "unmeasured"


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
    network_health = s.get("network_health") or {}

    rithmic_tcp_p99_ms = rithmic_tcp.get("p99_ms")
    order_ack_ms, measured, ack_source = resolve_order_ack_ms(s)

    payload: dict[str, Any] = {
        "probe_run_id": s.get("run_id"),
        "probe_timestamp_utc": s.get("timestamp_utc"),
        "source": s.get("authoritative_source"),
        "cpu_loaded_p99_us": cyclictest.get("max_p99_us"),
        "gateway_ping_p99_ms": gateway.get("p99_ms"),
        "rithmic_tcp_65000_p99_ms": float(rithmic_tcp_p99_ms)
        if isinstance(rithmic_tcp_p99_ms, (int, float))
        else None,
        "network_health_only_tcp_p99_ms": network_health.get("rithmic_tcp_65000_p99_ms"),
        "network_worst_p99_us": s.get("network_p99_us"),
        "network_worst_source": s.get("network_p99_worst_source"),
        "order_ack_p99_ms": order_ack_ms,
        "order_ack_source": ack_source,
        "trial_order_ack_p99_ms": trial.get("order_ack_p99_ms"),
        "trial_order_ack_status": trial.get("status"),
        "order_ack_measured": measured,
        "paper_order_latency": s.get("paper_order_latency"),
    }

    if measured and order_ack_ms is not None:
        payload["backtest_latency_ms"] = order_ack_ms
        payload["backtest_latency_source"] = ack_source
        payload["backtest_latency_note"] = BACKTEST_LATENCY_NOTE_MEASURED
    else:
        payload["backtest_latency_ms"] = None
        payload["backtest_latency_source"] = None
        payload["backtest_latency_note"] = BACKTEST_LATENCY_NOTE_UNMEASURED

    return payload


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
    if not chi404.get("order_ack_measured") or chi404.get("backtest_latency_ms") is None:
        raise ValueError(
            f"{BACKTEST_LATENCY_NOTE_UNMEASURED} "
            f"Summary: {chi404_summary.resolve()}"
        )
    ms = validate_replay_latency_ms(
        float(chi404["backtest_latency_ms"]),
        source=str(chi404.get("backtest_latency_source", "measured order ack")),
    )
    return ms, str(chi404.get("backtest_latency_source", "CHI404 measured order ack")), chi404
