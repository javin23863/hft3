"""PIT columns present and provenance does not overwrite join flags."""
from __future__ import annotations

import yaml

from crypto_lane.src.features.feature_matrix import build_labeled_frame


def test_pit_columns_in_labeled_frame():
    bt = yaml.safe_load(
        open("backtests/configs/crypto_hypotheses/h4_mempool_volatility.yaml", encoding="utf-8")
    )
    bt = {**bt, "btc_node_feature_availability_mode": "optional"}
    df = build_labeled_frame(
        include_btc_node=True,
        backtest_config=bt,
        hypothesis_id="CRYPTO_H4",
    )
    for col in ("btc_node_data_available_flag", "staleness_delta_ms", "is_pit_safe"):
        assert col in df.columns
    assert df["btc_node_data_available_flag"].null_count() == 0
