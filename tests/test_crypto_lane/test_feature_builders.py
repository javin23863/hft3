"""Feature builder smoke tests."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from crypto_lane.src.features.crypto.basis_features import build_basis_features
from crypto_lane.src.features.feature_matrix import build_labeled_frame
from crypto_lane.src.features.onchain.btc_node_mempool_features import build_mempool_features

FIX = Path(__file__).resolve().parents[2] / "packages" / "crypto_lane" / "fixtures"


def test_basis_features_columns():
    ticks = pl.read_csv(FIX / "spot_perp_ticks.csv")
    out = build_basis_features(ticks)
    assert "spot_perp_basis" in out.columns
    assert "ou_basis_compression_signal" in out.columns


def test_perp_data_quality_flag_in_fixture():
    ticks = pl.read_csv(FIX / "spot_perp_ticks.csv")
    assert "perp_data_quality_flag" in ticks.columns
    assert (ticks["perp_data_quality_flag"] == 1).all()


def test_perp_quality_flag_nulled_when_synthetic():
    ticks = pl.read_csv(FIX / "spot_perp_ticks.csv")
    ticks = ticks.with_columns(pl.lit(0).alias("perp_data_quality_flag"))
    basis = build_basis_features(ticks)
    out = basis.join(
        ticks.select(["exchange_timestamp", "perp_data_quality_flag"]),
        on="exchange_timestamp",
        how="left",
    )
    perp_ok = pl.col("perp_data_quality_flag") == 1
    basis_cols = ("spot_perp_basis", "basis_pct", "basis_zscore",
                  "annualized_basis_yield", "funding_adjusted_basis",
                  "cross_venue_basis_dispersion", "ou_basis_compression_signal",
                  "basis_momentum", "basis_volatility")
    for col in basis_cols:
        if col in out.columns:
            out = out.with_columns(pl.when(perp_ok).then(pl.col(col)).otherwise(None).alias(col))
    for col in basis_cols:
        if col in out.columns:
            assert out[col].null_count() > 0, f"{col} should be null when perp_data_quality_flag=0"


def test_mempool_features_pit_flag():
    snap = pl.read_csv(FIX / "mempool_snapshots.csv")
    out = build_mempool_features(snap)
    assert "btc_node_data_available_flag" in out.columns
