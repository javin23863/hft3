"""Funding feature builder."""
from __future__ import annotations

import numpy as np
import polars as pl

from crypto_lane.src.math.funding_carry import (
    expected_net_carry,
    funding_zscore,
    hedge_drift_estimate,
    latent_funding_pressure,
)


def build_funding_features(ticks: pl.DataFrame, *, window: int = 24) -> pl.DataFrame:
    funding = ticks["funding_rate"].to_numpy()
    f_bin = ticks["funding_rate_binance"].to_numpy() if "funding_rate_binance" in ticks.columns else funding
    f_okx = ticks["funding_rate_okx"].to_numpy() if "funding_rate_okx" in ticks.columns else funding
    spot_r = ticks["spot_return"].to_numpy() if "spot_return" in ticks.columns else None
    perp_r = ticks["perp_return"].to_numpy() if "perp_return" in ticks.columns else None
    perp = ticks["perp_mid"].to_numpy()
    perp_ret = perp_r if perp_r is not None else np.zeros_like(funding)
    rows = []
    for i in range(len(funding)):
        hist = funding[: i + 1]
        drift = 0.0
        if spot_r is not None and perp_r is not None and i >= 1:
            drift = hedge_drift_estimate(spot_r[: i + 1], perp_r[: i + 1])
        rates = np.array([f_bin[i], f_okx[i], funding[i]])
        rank = float(np.argsort(np.argsort(rates))[np.where(rates == funding[i])[0][0]] + 1)
        rows.append({
            "exchange_timestamp": int(ticks["exchange_timestamp"][i]),
            "funding_level": float(funding[i]),
            "funding_slope": float(funding[i] - funding[i - 1]) if i > 0 else 0.0,
            "funding_zscore": funding_zscore(hist, min(window, len(hist))),
            "funding_rank_by_venue": rank,
            "funding_dispersion": float(np.std(rates)),
            "latent_funding_pressure": latent_funding_pressure(hist, min(window, len(hist))),
            "perp_return": float(perp_ret[i]),
            "hedge_drift_estimate": drift,
            "expected_net_funding_after_cost": expected_net_carry(
                hist, perp[: i + 1], k_steps=1, hedge_drift=drift, costs=0.0
            ),
            "liquidity_regime": int(ticks["bid_ask_spread"][i] > np.median(ticks["bid_ask_spread"].to_numpy()[: i + 1])),
        })
    return pl.DataFrame(rows)


def build_exchange_microstructure(ticks: pl.DataFrame) -> pl.DataFrame:
    return ticks.select([
        "exchange_timestamp",
        pl.col("bid_ask_spread").alias("exchange_spread"),
        pl.col("depth_btc").alias("exchange_depth"),
        (pl.col("spot_return").abs() + pl.col("perp_return").abs()).alias("exchange_volume"),
        pl.col("order_imbalance").alias("exchange_order_imbalance"),
    ])
