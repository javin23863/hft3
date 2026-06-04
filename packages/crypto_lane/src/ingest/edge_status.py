"""Observable status for Bitcoin edge packets arriving in Chicago."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from crypto_lane.src.types import repo_root_from_lane

STATUS_DIR = Path("runtime") / "crypto_edge"
LATEST_PACKET = "latest_packet.json"
RECEIVER_STATUS = "receiver_status.json"
METRICS_SNAPSHOT = "metrics_snapshot.json"
PACKET_HISTORY = "packet_history.jsonl"
DEFAULT_FRESHNESS_WINDOW_SECONDS = 300.0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _read_jsonl_tail(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _artifact_observed_ns(
    latest_packet_path: Path,
    latest_packet: dict[str, Any],
    history_latest: dict[str, Any],
) -> int:
    for payload in (history_latest, latest_packet):
        value = payload.get("received_at_ns")
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    if latest_packet_path.is_file():
        return latest_packet_path.stat().st_mtime_ns
    return 0


def _parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _parse_receiver_service(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Environment="):
            env = _parse_env(line.removeprefix("Environment="))
            values.update(env)
        elif line.startswith("ExecStart="):
            values["exec_start"] = line.removeprefix("ExecStart=")
        elif line.startswith("ExecStartPre="):
            values["exec_start_pre"] = line.removeprefix("ExecStartPre=")
    return values


def _edge_packet_schema(proto_path: Path) -> list[dict[str, Any]]:
    text = _read_text(proto_path)
    if not text:
        return []
    rows: list[dict[str, Any]] = []
    in_packet = False
    field_re = re.compile(
        r"^\s*(repeated\s+)?([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+);"
    )
    for line in text.splitlines():
        if line.strip().startswith("message EdgeFeaturePacket"):
            in_packet = True
            continue
        if in_packet and line.strip() == "}":
            break
        if not in_packet:
            continue
        match = field_re.match(line)
        if match:
            repeated, type_name, field_name, number = match.groups()
            rows.append(
                {
                    "field": field_name,
                    "type": type_name,
                    "number": int(number),
                    "repeated": bool(repeated),
                }
            )
    return rows


def load_edge_packet_status(
    repo: Path | None = None,
    *,
    freshness_window_seconds: float = DEFAULT_FRESHNESS_WINDOW_SECONDS,
) -> dict[str, Any]:
    """Return configured and observed Bitcoin-node-to-Chicago edge packet state."""
    root = repo or repo_root_from_lane()
    runtime_dir = root / STATUS_DIR
    latest_packet_path = runtime_dir / LATEST_PACKET
    receiver_status_path = runtime_dir / RECEIVER_STATUS
    metrics_path = runtime_dir / METRICS_SNAPSHOT
    history_path = runtime_dir / PACKET_HISTORY

    infra = root / "infrastructure" / "crypto_lane"
    daemon_env_path = infra / "btc-edge-daemon.env"
    daemon_run_path = infra / "btc-edge-daemon-run"
    daemon_service_path = infra / "btc-edge-daemon.service"
    receiver_service_path = infra / "btc-edge-receiver.service"
    receiver_run_path = infra / "btc-edge-receiver-run"
    firewall_path = infra / "btc-edge-receiver-firewall"
    proto_path = root / "packages" / "crypto_lane" / "edge_daemon" / "proto" / "edge_features.proto"
    receiver_module_path = root / "packages" / "crypto_lane" / "src" / "ingest" / "edge_receiver.py"

    daemon_env = _parse_env(_read_text(daemon_env_path))
    receiver_env = _parse_receiver_service(_read_text(receiver_service_path))
    latest_packet = _read_json(latest_packet_path)
    receiver_status = _read_json(receiver_status_path)
    metrics = _read_json(metrics_path)
    packet_history = _read_jsonl_tail(history_path)

    packets_received = int(
        receiver_status.get("packets_received_total")
        or metrics.get("packets_received_total")
        or 0
    )
    history_latest = packet_history[-1] if packet_history else {}
    latest_sequence = (
        history_latest.get("sequence_number")
        if history_latest
        else (latest_packet.get("packet") or {}).get("sequence_number")
        or receiver_status.get("last_sequence_number")
    )
    latest_observed_ns = _artifact_observed_ns(latest_packet_path, latest_packet, history_latest)
    latest_age_seconds = (
        max(0.0, (time.time_ns() - latest_observed_ns) / 1_000_000_000)
        if latest_observed_ns
        else None
    )
    fresh = latest_age_seconds is not None and latest_age_seconds <= freshness_window_seconds
    observed = bool(latest_packet) and packets_received > 0 and fresh
    configured = bool(daemon_env.get("CHICAGO_ADDR")) and bool(receiver_env.get("BTC_EDGE_PORT"))
    if observed:
        status = "OBSERVED"
        reason = "Chicago receiver has durable packet evidence from the Bitcoin edge daemon."
    elif bool(latest_packet) and packets_received > 0 and not fresh:
        status = "STALE"
        reason = "Bitcoin edge packet artifacts exist, but they are outside the freshness window."
    elif configured:
        status = "CONFIGURED_NOT_OBSERVED"
        reason = "Bitcoin edge daemon and Chicago receiver are configured, but no packet artifact has been observed yet."
    else:
        status = "NOT_CONFIGURED"
        reason = "Bitcoin edge packet infrastructure config was not found."

    packet_schema = _edge_packet_schema(proto_path)
    chicago_addr = daemon_env.get("CHICAGO_ADDR", "")
    receiver_port = receiver_env.get("BTC_EDGE_PORT", "")
    return {
        "status": status,
        "observed": observed,
        "configured": configured,
        "reason": reason,
        "transport": "length_prefixed_protobuf_tcp",
        "source": "bitcoin_node_edge_daemon",
        "destination": "chicago_edge_receiver",
        "bitcoin_node_source_ip": receiver_env.get("BTC_EDGE_ALLOWED_SOURCE", ""),
        "chicago_addr": chicago_addr,
        "receiver_port": receiver_port,
        "packet_interval_tx_events": daemon_env.get("PACKET_INTERVAL", ""),
        "fee_filter_enabled": daemon_env.get("FEE_FILTER_ENABLED", ""),
        "fee_filter_blocks": daemon_env.get("FEE_FILTER_BLOCKS", ""),
        "metrics_port": daemon_env.get("METRICS_PORT", ""),
        "expected_packet_shape": "compact current-state protobuf packet with streaming fee stats, mempool state, and batched deltas",
        "expected_packet_size": "12-40KB from repo docs; actual size requires observed packet evidence",
        "latest_sequence_number": latest_sequence,
        "latest_observed_at_ns": latest_observed_ns or None,
        "latest_age_seconds": latest_age_seconds,
        "freshness_window_seconds": freshness_window_seconds,
        "packets_received_total": packets_received,
        "latest_packet": latest_packet,
        "receiver_status": receiver_status,
        "metrics": metrics,
        "packet_history": packet_history,
        "schema": packet_schema,
        "artifacts": {
            "runtime_dir": str(runtime_dir),
            "latest_packet": str(latest_packet_path),
            "receiver_status": str(receiver_status_path),
            "metrics_snapshot": str(metrics_path),
            "packet_history": str(history_path),
            "daemon_env": str(daemon_env_path),
            "daemon_run": str(daemon_run_path),
            "daemon_service": str(daemon_service_path),
            "receiver_service": str(receiver_service_path),
            "receiver_run": str(receiver_run_path),
            "receiver_firewall": str(firewall_path),
            "proto": str(proto_path),
            "receiver_module": str(receiver_module_path),
        },
    }
