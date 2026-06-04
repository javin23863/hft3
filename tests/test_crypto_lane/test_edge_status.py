"""Bitcoin edge packet status discovery."""

from __future__ import annotations

import json
from pathlib import Path

from crypto_lane.src.ingest.edge_status import load_edge_packet_status


REPO = Path(__file__).resolve().parents[2]


def test_repo_edge_packet_infra_is_configured_for_chicago() -> None:
    status = load_edge_packet_status(REPO)

    assert status["configured"] is True
    assert status["transport"] == "length_prefixed_protobuf_tcp"
    assert status["chicago_addr"] == "64.44.98.219:9876"
    assert status["bitcoin_node_source_ip"] == "213.199.46.118"
    assert status["receiver_port"] == "9876"
    assert status["packet_interval_tx_events"] == "100"
    assert "current-state protobuf" in status["expected_packet_shape"]
    assert {row["field"] for row in status["schema"]} >= {
        "timestamp_ns",
        "sequence_number",
        "fee_mean_sat_vb",
        "mempool_tx_count",
        "mempool_bytes",
        "packets_sent",
        "bytes_sent",
    }


def test_edge_packet_status_becomes_observed_from_receiver_artifacts(tmp_path: Path) -> None:
    infra = tmp_path / "infrastructure" / "crypto_lane"
    infra.mkdir(parents=True)
    (infra / "btc-edge-daemon.env").write_text(
        "CHICAGO_ADDR=64.44.98.219:9876\n"
        "PACKET_INTERVAL=100\n"
        "FEE_FILTER_ENABLED=true\n"
        "FEE_FILTER_BLOCKS=1\n"
        "METRICS_PORT=9090\n",
        encoding="utf-8",
    )
    (infra / "btc-edge-receiver.service").write_text(
        "[Service]\n"
        "Environment=BTC_EDGE_ALLOWED_SOURCE=213.199.46.118\n"
        "Environment=BTC_EDGE_PORT=9876\n",
        encoding="utf-8",
    )
    proto = tmp_path / "packages" / "crypto_lane" / "edge_daemon" / "proto"
    proto.mkdir(parents=True)
    (proto / "edge_features.proto").write_text(
        "message EdgeFeaturePacket {\n"
        "  uint64 timestamp_ns = 1;\n"
        "  uint64 sequence_number = 2;\n"
        "}\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime" / "crypto_edge"
    runtime.mkdir(parents=True)
    (runtime / "latest_packet.json").write_text(
        json.dumps({"packet": {"sequence_number": 42, "mempool_tx_count": 1000}}),
        encoding="utf-8",
    )
    (runtime / "receiver_status.json").write_text(
        json.dumps({"status": "OBSERVED", "packets_received_total": 7, "last_sequence_number": 42}),
        encoding="utf-8",
    )
    (runtime / "packet_history.jsonl").write_text(
        json.dumps({"sequence_number": 41, "wire_bytes": 1000}) + "\n"
        + json.dumps({"sequence_number": 42, "wire_bytes": 1001}) + "\n",
        encoding="utf-8",
    )

    status = load_edge_packet_status(tmp_path)

    assert status["status"] == "OBSERVED"
    assert status["observed"] is True
    assert status["latest_sequence_number"] == 42
    assert status["packets_received_total"] == 7
    assert [row["sequence_number"] for row in status["packet_history"]] == [41, 42]


def test_edge_packet_status_rejects_stale_receiver_artifacts(tmp_path: Path) -> None:
    infra = tmp_path / "infrastructure" / "crypto_lane"
    infra.mkdir(parents=True)
    (infra / "btc-edge-daemon.env").write_text("CHICAGO_ADDR=64.44.98.219:9876\n", encoding="utf-8")
    (infra / "btc-edge-receiver.service").write_text(
        "[Service]\nEnvironment=BTC_EDGE_PORT=9876\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime" / "crypto_edge"
    runtime.mkdir(parents=True)
    (runtime / "latest_packet.json").write_text(
        json.dumps({"received_at_ns": 1, "packet": {"sequence_number": 42}}),
        encoding="utf-8",
    )
    (runtime / "receiver_status.json").write_text(
        json.dumps({"packets_received_total": 7, "last_sequence_number": 42}),
        encoding="utf-8",
    )
    (runtime / "packet_history.jsonl").write_text(
        json.dumps({"received_at_ns": 1, "sequence_number": 42}) + "\n",
        encoding="utf-8",
    )

    status = load_edge_packet_status(tmp_path, freshness_window_seconds=1.0)

    assert status["status"] == "STALE"
    assert status["observed"] is False
    assert "freshness window" in status["reason"]


def test_edge_packet_status_is_not_observed_without_receiver_artifacts() -> None:
    status = load_edge_packet_status(REPO)

    if status["observed"]:
        assert status["status"] == "OBSERVED"
    elif status["status"] == "STALE":
        assert status["observed"] is False
        assert "freshness window" in status["reason"]
    else:
        assert status["status"] == "CONFIGURED_NOT_OBSERVED"
        assert "no packet artifact" in status["reason"]
