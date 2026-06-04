"""Crypto smoke loop honesty checks."""

from __future__ import annotations


def test_crypto_smoke_decision_quarantines_without_venue_submit_ack() -> None:
    from workbench.src.run import crypto_smoke_runner

    decision = crypto_smoke_runner._decision(
        [
            {
                "candidate_id": "crypto_candidate",
                "pass_fail": "pass",
                "deflated_sharpe_cdf": 0.99,
                "oos_ic": 0.12,
                "n_rows": 512,
                "order_ack_status": "INSUFFICIENT crypto venue submit-to-ack samples",
            }
        ]
    )

    assert decision["action"] == "QUARANTINE"
    assert "crypto venue submit-to-ack" in decision["reason"]
    assert decision["top_research_candidate"] == "crypto_candidate"
    assert decision["live_registry_ready"] is False
    assert any(gate["gate"] == "bitcoin_edge_packets" for gate in decision["blocking_gates"])
