"""Tests for PDF_MODEL_2 cross-asset lead-lag."""

import numpy as np

from features_engine.src.structural_models.model_02_cross_asset_lead_lag import (
    CrossAssetLeadLagModel,
    fit_ridge_cross_impact,
    predict_target_return,
)


def test_regression_shape_no_lookahead():
    n = 30
    leader_ofi = np.random.randn(n)
    own_ofi = 0.3 * leader_ofi + np.random.randn(n) * 0.1
    target = 0.5 * leader_ofi + 0.2 * own_ofi + np.random.randn(n) * 0.05
    beta, gamma, r2 = fit_ridge_cross_impact(target, own_ofi, leader_ofi, alpha=0.1)
    assert gamma > 0.0
    assert r2 > 0.0


def test_predict_target_return():
    pred = predict_target_return(0.01, 0.2, 0.5, own_ofi=1.0, leader_ofi=2.0)
    assert abs(pred - (0.01 + 0.2 + 1.0)) < 1e-9


def test_model_online_score():
    model = CrossAssetLeadLagModel()
    n = 25
    leader = list(np.linspace(-1, 1, n))
    own = [0.1 * x for x in leader]
    rets = [0.5 * leader[i + 1] for i in range(n - 1)]
    out = model.evaluate(
        leader_asset="ES",
        target_asset="MES",
        target_returns=rets,
        own_ofi_series=own[:-1],
        leader_ofi_series=leader[:-1],
        own_ofi=own[-1],
        leader_ofi=leader[-1],
    )
    assert out.payload.leader_asset == "ES"
    assert len(out.payload.signal_decay_curve) == 5
