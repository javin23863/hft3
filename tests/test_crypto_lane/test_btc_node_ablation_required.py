"""H4-H7 require BTC node ablation flags."""
from __future__ import annotations

from crypto_lane.src.ml.candidate_registry import discover_candidates


def test_h4_h7_ablation_required():
    for c in discover_candidates():
        hid = c["hypothesis_id"]
        if hid in ("CRYPTO_H4", "CRYPTO_H5", "CRYPTO_H6", "CRYPTO_H7"):
            ab = c["ablation"]
            assert ab["run_with_btc_node_features"]
            assert ab["run_without_btc_node_features"]
