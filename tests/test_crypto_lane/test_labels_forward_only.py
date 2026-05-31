"""Labels must not appear in feature columns."""
from __future__ import annotations

from crypto_lane.src.features.feature_matrix import build_labeled_frame
from crypto_lane.src.labels.forward_labels import LABEL_COLUMNS
from crypto_lane.src.ml.walk_forward_runner import feature_columns


from crypto_lane.src.ml.candidate_registry import candidate_by_id


def test_fixture_features_exclude_labels():
    df = build_labeled_frame(
        backtest_config={"validation_mode": "fixture", "cost_assumptions": {"fee_bps": 2, "spread_bps": 1}},
        hypothesis_id="CRYPTO_H1",
    )
    cols = set(feature_columns(df))
    overlap = cols & LABEL_COLUMNS
    assert not overlap, f"labels in features: {overlap}"
    h1_feats = set(candidate_by_id("crypto_h1_basis_compression")["features"])
    assert "ou_basis_compression_signal" not in h1_feats
