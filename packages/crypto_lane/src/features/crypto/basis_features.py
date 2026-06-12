"""Basis feature builder."""
from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from crypto_lane.src.math.basis_ou import (
    annualized_continuous_basis_yield,
    basis_series,
    fit_ou_ar1,
    forward_basis_compression_flag,
)
from crypto_lane.src.types import FeatureProvenance

_PERP_DERIVED_COLS = (
    "spot_perp_basis",
    "basis_pct",
    "basis_zscore",
    "annualized_basis_yield",
    "funding_adjusted_basis",
    "cross_venue_basis_dispersion",
    "basis_momentum",
    "basis_volatility",
)
_PERP_INT_COLS = ("ou_basis_compression_signal",)


def _dt_hours(ts: np.ndarray, i: int) -> float:
    if i <= 0:
        return 1.0 / 3600.0
    delta_ms = float(ts[i] - ts[i - 1])
    return max(delta_ms / 3_600_000.0, 1.0 / 3600.0)


def build_basis_features(
    ticks: pl.DataFrame,
    *,
    funding_horizon_hours: float = 8.0,
    horizon_hours: float = 1.0,
) -> pl.DataFrame:
    """Compute causal basis features from spot_mid, perp_mid (filtration-safe through t)."""
    spot = ticks["spot_mid"].to_numpy()
    perp = ticks["perp_mid"].to_numpy()
    ts = ticks["exchange_timestamp"].to_numpy()
    b = basis_series(spot, perp)
    perp_b = ticks["perp_mid_binance"].to_numpy() if "perp_mid_binance" in ticks.columns else perp
    perp_o = ticks["perp_mid_okx"].to_numpy() if "perp_mid_okx" in ticks.columns else perp
    funding = ticks["funding_rate"].to_numpy() if "funding_rate" in ticks.columns else np.zeros_like(spot)

    has_quality = "perp_data_quality_flag" in ticks.columns
    flags = ticks["perp_data_quality_flag"].to_numpy() if has_quality else None

    rows: list[dict[str, Any]] = []
    for i in range(len(spot)):
        hist = b[: i + 1]
        dt = _dt_hours(ts, i)
        theta, mu, sigma = fit_ou_ar1(hist, dt=dt) if len(hist) >= 3 else (0.0, float(hist[-1]), 0.0)
        cross_disp = float(abs(perp_b[i] - perp_o[i]))
        flag_val = int(flags[i]) if has_quality else None
        null_perp = has_quality and flag_val == 0
        row: dict[str, Any] = {
            "exchange_timestamp": int(ts[i]),
            "spot_mid": float(spot[i]),
            "perp_mid": float(perp[i]),
            "spot_perp_basis": None if null_perp else float(b[i]),
            "basis_pct": None if null_perp else (float(b[i] / spot[i]) if spot[i] else 0.0),
            "basis_zscore": None if null_perp else (float((b[i] - mu) / sigma) if sigma > 0 else 0.0),
            "annualized_basis_yield": None if null_perp else annualized_continuous_basis_yield(
                float(spot[i]), float(perp[i]), funding_horizon_hours=funding_horizon_hours
            ),
            "funding_rate": float(funding[i]),
            "funding_adjusted_basis": None if null_perp else (float(b[i]) - float(funding[i]) * float(perp[i])),
            "cross_venue_basis_dispersion": None if null_perp else cross_disp,
            "ou_theta": theta,
            "ou_mu": mu,
            "ou_sigma": sigma,
            "ou_basis_compression_signal": None if null_perp else forward_basis_compression_flag(theta, horizon_hours),
            "basis_momentum": None if null_perp else (float(b[i] - b[i - 1]) if i > 0 else 0.0),
            "basis_volatility": None if null_perp else sigma,
            "basis_regime_label": int(abs((b[i] - mu) / sigma) > 2.0) if sigma > 0 else 0,
        }
        if has_quality:
            row["perp_data_quality_flag"] = flag_val
        rows.append(row)
    df = pl.DataFrame(rows)
    if has_quality:
        for col in _PERP_DERIVED_COLS:
            if col in df.columns and df[col].dtype != pl.Float64:
                df = df.with_columns(pl.col(col).cast(pl.Float64))
    for col in _PERP_INT_COLS:
        if col in df.columns and df[col].dtype != pl.Int64:
            df = df.with_columns(pl.col(col).cast(pl.Int64))
    return df


def attach_provenance(df: pl.DataFrame, prov: FeatureProvenance) -> pl.DataFrame:
    out = df.with_columns([
        pl.lit(prov.source).alias("feature_source"),
        pl.lit(prov.t_avail_ns).alias("T_avail_ns"),
    ])
    if "staleness_delta_ms" not in out.columns:
        out = out.with_columns(pl.lit(prov.staleness_delta_ms).alias("staleness_delta_ms"))
    if "is_pit_safe" not in out.columns:
        out = out.with_columns(pl.lit(prov.is_pit_safe).alias("is_pit_safe"))
    if "btc_node_data_available_flag" not in out.columns:
        out = out.with_columns(
            pl.lit(prov.btc_node_data_available_flag).alias("btc_node_data_available_flag")
        )
    return out
