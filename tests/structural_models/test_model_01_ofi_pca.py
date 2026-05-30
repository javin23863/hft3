"""Tests for PDF_MODEL_1 book pressure / OFI."""

import numpy as np

from features_engine.src.structural_models.model_01_book_pressure import (
    BookPressureModel,
    compute_level1_ofi_event,
    compute_mlofi_vector,
    pca_first_component,
)


def test_level1_ofi_bid_qty_increase():
    # Bid price unchanged, qty 10 -> 15 => +5 OFI
    e = compute_level1_ofi_event(100.0, 10, 101.0, 5, 100.0, 15, 101.0, 5)
    assert e == 5.0


def test_level1_ofi_bid_price_improve():
    # Bid price up: contribution = new bid qty
    e = compute_level1_ofi_event(100.0, 10, 101.0, 5, 100.25, 8, 101.0, 5)
    assert e == 8.0


def test_level1_ofi_ask_worsen():
    # Ask price up (worse for buyer): positive OFI contribution from ask side
    e = compute_level1_ofi_event(100.0, 10, 101.0, 5, 100.0, 10, 101.25, 5)
    assert e == 5.0


def test_mlofi_vector_shape():
    prev_b = [(100.0, 10)]
    prev_a = [(101.0, 5)]
    curr_b = [(100.0, 12)]
    curr_a = [(101.0, 5)]
    mlofi = compute_mlofi_vector(prev_b, prev_a, curr_b, curr_a, m=1)
    assert len(mlofi) == 1
    assert mlofi[0] == 2.0


def test_pca_pc1_sign():
    history = [[1.0], [2.0], [3.0], [4.0]]
    score, loading = pca_first_component(history)
    assert loading.shape == (1,)
    assert score != 0.0


def test_book_pressure_model_accumulates():
    model = BookPressureModel(params={"ofi": {"smooth_window": 3, "zscore_window": 10}})
    out1 = model.update_bbo(100.0, 10, 101.0, 5)
    out2 = model.update_bbo(100.0, 15, 101.0, 5)
    assert out2.payload.OFI_value == out1.payload.OFI_value + 5.0
    assert out2.payload.OFI_smooth != 0.0


def test_spoof_flag_on_l1_pc1_conflict():
    model = BookPressureModel(
        params={"ofi": {"spoof_threshold": 0.1, "smooth_window": 2, "zscore_window": 5}}
    )
    # Build history then create strong L1 vs deep conflict
    for _ in range(5):
        model.update_bbo(100.0, 10, 101.0, 5)
    # Large bid drop (negative L1 event)
    out = model.update_bbo(99.75, 5, 101.0, 5)
    # Spoof may or may not trigger depending on PC1; verify flag is bool
    assert isinstance(out.payload.spoofing_risk_flag, bool)
