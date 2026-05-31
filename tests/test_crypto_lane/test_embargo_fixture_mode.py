"""Fixture mode embargo must stay feasible on short series."""
from __future__ import annotations

import yaml

from crypto_lane.src.features.feature_matrix import build_labeled_frame
from crypto_lane.src.ml.embargo import resolve_embargo_steps


def test_fixture_embargo_capped():
    bt = yaml.safe_load(
        open("backtests/configs/crypto_hypotheses/h1_basis_compression.yaml", encoding="utf-8")
    )
    df = build_labeled_frame(backtest_config=bt)
    n = df.height
    steps = resolve_embargo_steps(bt, {"embargo": True}, df, label_horizon_steps=1, n=n)
    assert steps <= max(1, n // 4 - 1)
    assert steps == bt["fixture_embargo_steps"]
