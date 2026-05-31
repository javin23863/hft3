"""Holdout gate runs Discovery tune and later evaluate-only stages."""
from __future__ import annotations

import yaml

from crypto_lane.src.features.feature_matrix import build_labeled_frame
from crypto_lane.src.ml.holdout_gate import run_holdout_gate


def test_holdout_gate_stages_pass_with_zero_floor():
    bt = yaml.safe_load(
        open("backtests/configs/crypto_hypotheses/h1_basis_compression.yaml", encoding="utf-8")
    )
    df = build_labeled_frame(backtest_config=bt, hypothesis_id="CRYPTO_H1")
    df = df.filter(__import__("polars").col("forward_basis_change").is_finite())
    feats = ["spot_perp_basis", "basis_zscore", "funding_rate"]
    out = run_holdout_gate(df, "forward_basis_change", feats, "ridge", min_ic=0.0)
    assert out["status"] == "PASS"
    assert out["stages"]["Discovery"]["mode"] == "tune"
    assert out["stages"]["Discovery"]["status"] == "PASS"
    for stage in ("Confirmation", "Holdout"):
        assert out["stages"][stage]["mode"] == "evaluate_only"
        assert out["stages"][stage]["status"] == "PASS"
