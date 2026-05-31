"""Feature builder smoke tests."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from crypto_lane.src.features.crypto.basis_features import build_basis_features
from crypto_lane.src.features.onchain.btc_node_mempool_features import build_mempool_features

FIX = Path(__file__).resolve().parents[2] / "packages" / "crypto_lane" / "fixtures"


def test_basis_features_columns():
    ticks = pl.read_csv(FIX / "spot_perp_ticks.csv")
    out = build_basis_features(ticks)
    assert "spot_perp_basis" in out.columns
    assert "ou_basis_compression_signal" in out.columns


def test_mempool_features_pit_flag():
    snap = pl.read_csv(FIX / "mempool_snapshots.csv")
    out = build_mempool_features(snap)
    assert "btc_node_data_available_flag" in out.columns
