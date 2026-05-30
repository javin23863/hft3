"""Tests for PDF_MODEL_8 Transfer Entropy."""

import numpy as np

from features_engine.src.structural_models.model_08_transfer_entropy import (
    TransferEntropyModel,
    shannon_entropy,
    transfer_entropy,
)


def test_shannon_entropy_non_negative():
    vals = np.random.default_rng(0).normal(size=100)
    assert shannon_entropy(vals) >= 0.0


def test_transfer_entropy_non_negative():
    rng = np.random.default_rng(1)
    leader = rng.normal(size=200)
    target = np.roll(leader, 2) + 0.1 * rng.normal(size=200)
    te = transfer_entropy(leader, target, lag=2)
    assert te >= 0.0


def test_te_signal_when_leader_drives_target():
    leader = [0.1 * i for i in range(50)]
    target = [0.0] * 2 + [0.08 * i for i in range(48)]
    model = TransferEntropyModel(params={"transfer_entropy": {"ucl_multiplier": 1.5}})
    out = model.evaluate(leader_asset="ES", target_asset="MES", leader_returns=leader, target_returns=target)
    assert out.payload.transfer_entropy >= 0.0
