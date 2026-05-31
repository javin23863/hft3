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

    rows: list[dict[str, Any]] = []
    for i in range(len(spot)):
        hist = b[: i + 1]
        dt = _dt_hours(ts, i)
        theta, mu, sigma = fit_ou_ar1(hist, dt=dt) if len(hist) >= 3 else (0.0, float(hist[-1]), 0.0)
        cross_disp = float(abs(perp_b[i] - perp_o[i]))
        rows.append({
            "exchange_timestamp": int(ts[i]),
            "spot_mid": float(spot[i]),
            "perp_mid": float(perp[i]),
            "spot_perp_basis": float(b[i]),
            "basis_pct": float(b[i] / spot[i]) if spot[i] else 0.0,
            "basis_zscore": float((b[i] - mu) / sigma) if sigma > 0 else 0.0,
            "annualized_basis_yield": annualized_continuous_basis_yield(
                float(spot[i]), float(perp[i]), funding_horizon_hours=funding_horizon_hours
            ),
            "funding_rate": float(funding[i]),
            "funding_adjusted_basis": float(b[i]) - float(funding[i]) * float(perp[i]),
            "cross_venue_basis_dispersion": cross_disp,
            "ou_theta": theta,
            "ou_mu": mu,
            "ou_sigma": sigma,
            "ou_basis_compression_signal": forward_basis_compression_flag(theta, horizon_hours),
            "basis_momentum": float(b[i] - b[i - 1]) if i > 0 else 0.0,
            "basis_volatility": sigma,
            "basis_regime_label": int(abs((b[i] - mu) / sigma) > 2.0) if sigma > 0 else 0,
        })
    return pl.DataFrame(rows)


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
