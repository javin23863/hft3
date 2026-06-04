"""Workbench evidence snapshot coverage."""

from __future__ import annotations

from pathlib import Path

from workbench.src.run.evidence_snapshot import load_run_evidence


REPO = Path(__file__).resolve().parents[2]


def test_crypto_snapshot_surfaces_bitcoin_edge_packet_gate() -> None:
    snapshot = load_run_evidence(REPO, "crypto_lane")

    edge_data = snapshot.data["bitcoin_edge_packets"]
    edge_latency = snapshot.latency["bitcoin_edge_packets"]

    assert edge_data["configured"] is True
    assert edge_data["transport"] == "length_prefixed_protobuf_tcp"
    assert edge_data["chicago_addr"] == "64.44.98.219:9876"
    assert edge_data["bitcoin_node_source_ip"] == "213.199.46.118"
    assert edge_latency["status"] == edge_data["status"]
    assert snapshot.decision["bitcoin_edge_packet_status"] == edge_data["status"]
    if not edge_data["observed"]:
        assert any(gate["gate"] == "bitcoin_edge_packets" for gate in snapshot.decision["blocking_gates"])
    assert snapshot.diagnostics["edge_packet_schema"]
    assert "proxy_leaderboard" in snapshot.backtest
    assert "equity_curves" in snapshot.backtest
