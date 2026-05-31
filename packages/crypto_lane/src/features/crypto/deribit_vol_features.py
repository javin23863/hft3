"""Realized variance, VRP, and put-call parity residual."""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import polars as pl

from crypto_lane.src.math.vol_rv_vrp import (
    put_call_parity_residual,
    realized_volatility,
    volatility_risk_premium,
)


def realized_volatility(log_returns: Sequence[float], annualization: float = 365.0 * 24.0) -> float:
    r = np.asarray(log_returns, dtype=float)
    if r.size == 0:
        return 0.0
    return math.sqrt(max(0.0, annualization * np.mean(r ** 2)))


def build_deribit_vol_features(
    surface: pl.DataFrame,
    log_returns: list[float],
    *,
    annualization: float = 365.0 * 24.0,
    align_timestamps: pl.Series | None = None,
    rv_window: int = 32,
) -> pl.DataFrame:
    if align_timestamps is not None:
        surf = surface.sort("exchange_timestamp")
        rows = []
        s_idx = 0
        surf_rows = list(surf.iter_rows(named=True))
        for i, ts in enumerate(align_timestamps.to_list()):
            while s_idx + 1 < len(surf_rows) and surf_rows[s_idx + 1]["exchange_timestamp"] <= ts:
                s_idx += 1
            row = surf_rows[min(s_idx, len(surf_rows) - 1)]
            causal_r = log_returns[max(0, i + 1 - rv_window): i + 1]
            rv = realized_volatility(causal_r, annualization)
            atm_iv = float(row.get("atm_iv", 0.0))
            rows.append({
                "exchange_timestamp": int(ts),
                "atm_iv": atm_iv,
                "spot_realized_volatility": rv,
                "realized_vol_forecast": rv,
                "iv_rv_spread": volatility_risk_premium(atm_iv, rv),
                "iv_rv_zscore": float(row.get("iv_rv_zscore", 0.0)),
                "skew_25d": float(row.get("skew_25d", 0.0)),
                "term_structure_slope": float(row.get("term_structure_slope", 0.0)),
                "put_call_parity_residual": put_call_parity_residual(
                    float(row.get("call_mid", 0.0)),
                    float(row.get("put_mid", 0.0)),
                    float(row.get("spot_mid", 0.0)),
                    float(row.get("strike", 0.0)),
                    rate=float(row.get("rate", 0.0)),
                    yield_q=float(row.get("yield_q", 0.0)),
                    tau_years=float(row.get("tau_years", 0.0)),
                ),
                "vol_surface_quality_flag": int(row.get("vol_surface_quality_flag", 1)),
            })
        return pl.DataFrame(rows)

    rows = []
    for i, row in enumerate(surface.iter_rows(named=True)):
        causal_r = log_returns[max(0, i + 1 - rv_window): i + 1]
        rv = realized_volatility(causal_r, annualization)
        atm_iv = float(row.get("atm_iv", 0.0))
        rows.append({
            "exchange_timestamp": int(row["exchange_timestamp"]),
            "atm_iv": atm_iv,
            "spot_realized_volatility": rv,
            "realized_vol_forecast": rv,
            "iv_rv_spread": volatility_risk_premium(atm_iv, rv),
            "iv_rv_zscore": float(row.get("iv_rv_zscore", 0.0)),
            "skew_25d": float(row.get("skew_25d", 0.0)),
            "term_structure_slope": float(row.get("term_structure_slope", 0.0)),
            "put_call_parity_residual": put_call_parity_residual(
                float(row.get("call_mid", 0.0)),
                float(row.get("put_mid", 0.0)),
                float(row.get("spot_mid", 0.0)),
                float(row.get("strike", 0.0)),
                rate=float(row.get("rate", 0.0)),
                yield_q=float(row.get("yield_q", 0.0)),
                tau_years=float(row.get("tau_years", 0.0)),
            ),
            "vol_surface_quality_flag": int(row.get("vol_surface_quality_flag", 1)),
        })
    return pl.DataFrame(rows)
