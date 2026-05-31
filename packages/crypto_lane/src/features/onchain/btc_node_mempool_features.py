"""BTC node mempool features from local Bitcoin Core snapshots."""
from __future__ import annotations

import polars as pl

from crypto_lane.src.align.pit_join import apply_pit_alignment
from crypto_lane.src.math.event_study import rolling_fee_zscore
from crypto_lane.src.math.jump_intensity import jump_intensity_lambda


def build_mempool_features(
    snapshots: pl.DataFrame,
    *,
    fee_window: int = 48,
    max_staleness_ms: int = 15000,
    strict: bool = False,
) -> pl.DataFrame:
    df = snapshots.with_columns([
        (pl.col("mempool_bytes") / pl.col("mempool_max_bytes").clip(lower_bound=1)).alias(
            "btc_mempool_usage_bytes"
        ),
    ])
    fees = df["min_fee_sat"].to_numpy() if "min_fee_sat" in df.columns else df["btc_mempool_min_fee"].to_numpy()
    rows = []
    for i, row in enumerate(df.iter_rows(named=True)):
        if "exchange_timestamp" not in row or row["exchange_timestamp"] is None:
            if strict:
                continue
            raise ValueError("exchange_timestamp required for mempool PIT join")
        hist = fees[: i + 1]
        z = rolling_fee_zscore(hist, fee_window)
        usage = float(row.get("btc_mempool_usage_bytes", row.get("mempool_bytes", 0.0)))
        stress = float(row.get("btc_blockspace_stress_score", usage))
        rows.append({
            "node_observation_time": int(row["node_observation_time"]),
            "exchange_timestamp": int(row["exchange_timestamp"]),
            "node_clock_drift_ms": float(row.get("node_clock_drift_ms", 0.0)),
            "network_latency_ms": float(row.get("network_latency_ms", 5.0)),
            "processing_latency_ms": float(row.get("processing_latency_ms", 2.0)),
            "exchange_clock_drift_ms": float(row.get("exchange_clock_drift_ms", 0.0)),
            "btc_mempool_size_txs": int(row.get("mempool_tx_count", 0)),
            "btc_mempool_bytes": float(row.get("mempool_bytes", 0.0)),
            "btc_mempool_usage_bytes": usage,
            "btc_mempool_min_fee": float(fees[i]),
            "btc_fee_spike_zscore": z,
            "btc_mempool_growth_rate": float(fees[i] - fees[i - 1]) if i > 0 else 0.0,
            "btc_blockspace_stress_score": stress,
            "jump_intensity_lambda": jump_intensity_lambda(usage, z),
            "btc_node_snapshot_latency_ms": float(row.get("estimated_latency_ms", 0.0)),
        })
    out = pl.DataFrame(rows)
    return apply_pit_alignment(out, max_staleness_ms=max_staleness_ms, strict=strict)
