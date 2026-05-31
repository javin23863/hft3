"""H7 event-aligned labels only on congestion shock rows."""
from __future__ import annotations

import numpy as np
import yaml

from crypto_lane.src.features.feature_matrix import build_labeled_frame


def test_h7_labels_only_on_events():
    bt = yaml.safe_load(
        open("backtests/configs/crypto_hypotheses/h7_congestion_event_study.yaml", encoding="utf-8")
    )
    df = build_labeled_frame(
        include_btc_node=True,
        backtest_config=bt,
        hypothesis_id="CRYPTO_H7",
        horizons=["+300s", "+1h"],
    )
    shock = df.filter(__import__("polars").col("btc_congestion_shock_event") == 1)
    assert shock.height > 0
    non_shock = df.filter(__import__("polars").col("btc_congestion_shock_event") != 1)
    vals = non_shock["event_window_return"].to_numpy()
    assert np.isnan(vals).all()
    labeled = df.filter(__import__("polars").col("event_window_return").is_finite())
    assert labeled.height == shock.height
    assert np.isfinite(labeled["event_window_return"].to_numpy()).all()
