"""Tests for PDF_MODEL_3 VPIN / BVC."""

import numpy as np

from features_engine.src.structural_models.model_03_vpin_toxicity import (
    VPINToxicityModel,
    bvc_buy_volume,
    compute_vpin,
)


def test_bvc_splits_volume():
    buy = bvc_buy_volume(100.0, delta_p=0.5, sigma=0.1, df=5.0)
    sell = 100.0 - buy
    assert abs(buy + sell - 100.0) < 1e-9
    assert buy > 50.0  # positive return => more buy


def test_bvc_student_t_symmetric_at_zero():
    buy = bvc_buy_volume(200.0, delta_p=0.0, sigma=0.2, df=5.0)
    assert abs(buy - 100.0) < 1.0


def test_compute_vpin_hand():
    buy = [60.0, 55.0, 70.0]
    sell = [40.0, 45.0, 30.0]
    vpin = compute_vpin(buy, sell)
    assert 0.0 < vpin < 1.0


def test_vpin_alert_at_high_percentile():
    model = VPINToxicityModel(
        params={"vpin": {"lookback_bars": 10, "alert_percentile": 0.99, "student_t_df": 5}}
    )
    bucket_vol = 100.0
    last = None
    for i in range(40):
        mid = 100.0 + 0.01 * (i % 3)
        vol = 100.0
        out = model.ingest_bar(mid, vol, bucket_volume=bucket_vol)
        if out is not None:
            last = out
    assert last is not None
    assert 0.0 <= last.payload.VPIN_percentile <= 1.0


def test_bucket_boundaries():
    model = VPINToxicityModel(params={"vpin": {"lookback_bars": 5}})
    assert model.ingest_bar(100.0, 50.0, bucket_volume=100.0) is None
    out = model.ingest_bar(100.1, 50.0, bucket_volume=100.0)
    assert out is not None
    assert out.payload.VPIN_value >= 0.0
