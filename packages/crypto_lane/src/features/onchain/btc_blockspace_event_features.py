"""Blockspace event flags for H6/H7."""
from __future__ import annotations

import polars as pl

from crypto_lane.src.math.event_study import fee_spike_event, rolling_fee_zscore


def build_blockspace_event_features(
    mempool_features: pl.DataFrame,
    *,
    fee_window: int = 48,
    z_threshold: float = 3.0,
) -> pl.DataFrame:
    fees = mempool_features["btc_mempool_min_fee"].to_numpy()
    stress = mempool_features["btc_blockspace_stress_score"].to_numpy()
    rows = []
    for i in range(len(fees)):
        hist = fees[: i + 1]
        z = rolling_fee_zscore(hist, fee_window)
        prev_z = rolling_fee_zscore(hist[:-1], fee_window) if i > 0 else 0.0
        clear = int(i > 0 and fees[i] < fees[i - 1] * 0.5 and prev_z > 1.0)
        post_clear_fee = float(fees[i] - fees[i - 1]) if i > 0 else 0.0
        post_clear_size = float(
            mempool_features["btc_mempool_bytes"][i] - mempool_features["btc_mempool_bytes"][i - 1]
        ) if i > 0 else 0.0
        rows.append({
            "exchange_timestamp": int(mempool_features["exchange_timestamp"][i]),
            "btc_fee_spike_event": fee_spike_event(hist, fee_window, z_threshold),
            "btc_mempool_clear_event": clear,
            "btc_congestion_shock_event": int(z > z_threshold and prev_z <= z_threshold),
            "btc_stress_increase_event": int(z > prev_z + 0.5),
            "btc_stress_collapse_event": int(z < prev_z - 0.5 and prev_z > 1.0),
            "btc_regime_transition_event": int(abs(z - prev_z) > 1.5),
            "prior_blockspace_stress_score": float(stress[i - 1]) if i > 0 else 0.0,
            "prior_fee_spike_zscore": float(prev_z),
            "prior_mempool_growth_rate": float(fees[i] - fees[i - 1]) if i > 0 else 0.0,
            "post_clear_fee_change": post_clear_fee,
            "post_clear_size_change": post_clear_size,
            "event_severity": float(abs(z)),
            "event_time": int(mempool_features["node_observation_time"][i]),
            "btc_blockspace_regime_label": int(stress[i] > 0.5),
            "btc_congestion_volatility_flag": int(z > 2.0),
            "btc_blockspace_liquidity_stress_flag": int(stress[i] > 0.6 and z > 1.5),
        })
    return pl.DataFrame(rows)
