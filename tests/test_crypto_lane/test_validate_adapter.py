"""Crypto validation adapter tests."""

from __future__ import annotations


def test_yaml_candidate_adapts_to_candidate_model() -> None:
    from crypto_lane.pipeline import _candidate_model_from_yaml

    candidate = _candidate_model_from_yaml(
        {
            "candidate_id": "crypto_h1_basis_compression",
            "hypothesis_id": "CRYPTO_H1",
            "target": "forward_basis_change",
            "features": ["spot_perp_basis"],
            "horizons": ["30s"],
            "btc_node_required": False,
        }
    )

    assert candidate.candidate_id == "crypto_h1_basis_compression"
    assert candidate.model_id == "CRYPTO_H1"
    assert candidate.metadata["asset_class"] == "CRYPTO"
    assert candidate.metadata["symbol"] == "BTCUSDT"
