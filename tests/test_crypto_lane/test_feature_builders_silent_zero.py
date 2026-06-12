"""K4 silent-zero prohibition enforcement suite."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from crypto_lane.src.features.crypto.basis_features import build_basis_features
from crypto_lane.src.features.crypto.deribit_vol_features import build_deribit_vol_features

FIX = Path(__file__).resolve().parents[2] / "packages" / "crypto_lane" / "fixtures"

_PERP_DERIVED = (
    "spot_perp_basis",
    "basis_pct",
    "basis_zscore",
    "annualized_basis_yield",
    "funding_adjusted_basis",
    "cross_venue_basis_dispersion",
    "ou_basis_compression_signal",
    "basis_momentum",
    "basis_volatility",
)

_SURF_DERIVED = (
    "atm_iv",
    "iv_rv_spread",
    "iv_rv_zscore",
    "skew_25d",
    "term_structure_slope",
    "put_call_parity_residual",
)


def _make_ticks(flags: list[int]) -> pl.DataFrame:
    base = pl.read_csv(FIX / "spot_perp_ticks.csv")
    n = len(flags)
    rows = base.head(n)
    return rows.with_columns(pl.Series("perp_data_quality_flag", flags, dtype=pl.Int64))


def _make_surface(flags: list[int] | None = None) -> tuple[pl.DataFrame, list[float]]:
    surf = pl.read_csv(FIX / "deribit_surface.csv")
    ticks = pl.read_csv(FIX / "spot_perp_ticks.csv")
    log_r = ticks["spot_return"].to_numpy().tolist()
    n = surf.height
    if flags is not None:
        surf = surf.with_columns(pl.Series("vol_surface_quality_flag", flags[:n], dtype=pl.Int64))
    return surf, log_r


# ---------------------------------------------------------------------------
# 1. basis flag=0 rows nulled
# ---------------------------------------------------------------------------

def test_basis_flag0_rows_nulled():
    ticks = _make_ticks([1, 0, 1])
    out = build_basis_features(ticks)

    assert "perp_data_quality_flag" in out.columns

    flag0 = out.filter(pl.col("perp_data_quality_flag") == 0)
    flag1 = out.filter(pl.col("perp_data_quality_flag") == 1)

    for col in _PERP_DERIVED:
        if col in out.columns:
            assert flag0[col].null_count() == len(flag0), (
                f"{col}: flag=0 rows must be null"
            )

    for col in _PERP_DERIVED:
        if col in out.columns:
            assert flag1[col].null_count() == 0, (
                f"{col}: flag=1 rows must be non-null"
            )

    assert list(out["perp_data_quality_flag"]) == [1, 0, 1]

    assert out["ou_basis_compression_signal"].dtype == pl.Int64, (
        "ou_basis_compression_signal must be Int64 in flagged output"
    )
    _float_derived = [c for c in _PERP_DERIVED if c != "ou_basis_compression_signal"]
    for col in _float_derived:
        if col in out.columns:
            assert out[col].dtype == pl.Float64, (
                f"{col}: must be Float64 in flagged output"
            )


# ---------------------------------------------------------------------------
# 2. basis — no flag column → legacy behavior preserved
# ---------------------------------------------------------------------------

def test_basis_no_flag_column_unchanged():
    raw = pl.read_csv(FIX / "spot_perp_ticks.csv")
    ticks = raw.drop("perp_data_quality_flag") if "perp_data_quality_flag" in raw.columns else raw
    out = build_basis_features(ticks)

    assert "perp_data_quality_flag" not in out.columns

    for col in _PERP_DERIVED:
        if col in out.columns:
            assert out[col].null_count() == 0, (
                f"{col}: should be fully numeric without flag column"
            )

    assert out["ou_basis_compression_signal"].dtype == pl.Int64, (
        "ou_basis_compression_signal must be Int64 in legacy (no-flag) output"
    )


# ---------------------------------------------------------------------------
# 3. deribit flag=0 rows nulled (align branch)
# ---------------------------------------------------------------------------

def test_deribit_flag0_rows_nulled():
    surf, _ = _make_surface()
    n = surf.height
    flags = [1 if i % 2 == 0 else 0 for i in range(n)]
    surf = surf.with_columns(pl.Series("vol_surface_quality_flag", flags, dtype=pl.Int64))

    ticks = pl.read_csv(FIX / "spot_perp_ticks.csv")
    log_r = ticks["spot_return"].to_numpy().tolist()
    out = build_deribit_vol_features(
        surf, log_r, align_timestamps=ticks["exchange_timestamp"]
    )

    assert "vol_surface_quality_flag" in out.columns

    flag0 = out.filter(pl.col("vol_surface_quality_flag") == 0)
    flag1 = out.filter(pl.col("vol_surface_quality_flag") == 1)

    for col in _SURF_DERIVED:
        if col in out.columns:
            assert flag0[col].null_count() == len(flag0), (
                f"{col}: flag=0 rows must be null"
            )

    for col in _SURF_DERIVED:
        if col in out.columns:
            assert flag1[col].null_count() == 0, (
                f"{col}: flag=1 rows must be non-null"
            )

    # spot_realized_volatility always numeric
    assert out["spot_realized_volatility"].null_count() == 0


# ---------------------------------------------------------------------------
# 4. deribit — missing flag column → all rows flag=0, surface cols all None
# ---------------------------------------------------------------------------

def test_deribit_missing_flag_column_defaults_zero():
    surf = pl.read_csv(FIX / "deribit_surface.csv")
    # Drop the flag column entirely
    if "vol_surface_quality_flag" in surf.columns:
        surf = surf.drop("vol_surface_quality_flag")

    ticks = pl.read_csv(FIX / "spot_perp_ticks.csv")
    log_r = ticks["spot_return"].to_numpy().tolist()

    out = build_deribit_vol_features(
        surf, log_r, align_timestamps=ticks["exchange_timestamp"]
    )

    assert "vol_surface_quality_flag" in out.columns
    assert (out["vol_surface_quality_flag"] == 0).all(), (
        "missing flag column must default all rows to flag=0"
    )

    for col in _SURF_DERIVED:
        if col in out.columns:
            assert out[col].null_count() == len(out), (
                f"{col}: must be all-None when flag column absent"
            )


# ---------------------------------------------------------------------------
# 5. integrated matrix path — join produces no duplicate/_right columns
# ---------------------------------------------------------------------------

def test_integrated_matrix_path_consistent():
    ticks = pl.read_csv(FIX / "spot_perp_ticks.csv")
    log_r = ticks["spot_return"].to_numpy().tolist()
    surf = pl.read_csv(FIX / "deribit_surface.csv")

    basis = build_basis_features(ticks)
    vol = build_deribit_vol_features(
        surf, log_r, align_timestamps=ticks["exchange_timestamp"]
    )

    joined = basis.join(vol, on="exchange_timestamp", how="left")

    # No _right suffix duplicates
    right_cols = [c for c in joined.columns if c.endswith("_right")]
    assert right_cols == [], f"duplicate _right columns after join: {right_cols}"

    # perp_data_quality_flag carried from basis (build_basis_features adds it)
    if "perp_data_quality_flag" in ticks.columns:
        assert "perp_data_quality_flag" in joined.columns

    # vol_surface_quality_flag present from vol builder
    assert "vol_surface_quality_flag" in joined.columns

    # No column appears twice
    assert len(joined.columns) == len(set(joined.columns)), "duplicate column names in joined frame"
