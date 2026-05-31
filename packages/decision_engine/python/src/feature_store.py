"""Build parquet feature/label store from NPZ MBO replay."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from decision_engine.python.src.targets import build_forward_returns, leakage_audit
from features_engine.src.features.feature_index import FEATURE_DIM
from features_engine.src.features.npz_feed import iter_mbo_events, load_npz_events
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline


def build_feature_parquet(npz_path: str, output_path: str, tick_size: float = 0.25) -> str:
    raw = load_npz_events(npz_path)
    pipeline = MarketStatePipeline(tick_size=tick_size)
    rows = []
    mids = []

    for i, mbo in enumerate(iter_mbo_events(raw)):
        state = pipeline.process_event(mbo)
        mid = state.f("mid_price", 0.0)
        mids.append(mid)
        row = {
            "timestamp_ns": mbo.timestamp_ns,
            "mid_price": mid,
            "event_context": state.event_context,
            "regime_state": state.regime_state,
        }
        if state.feature_vector is not None:
            for j in range(FEATURE_DIM):
                row[f"f_{j}"] = float(state.feature_vector[j])
        rows.append(row)

    df = pd.DataFrame(rows)
    ts = df["timestamp_ns"].values
    mid_arr = df["mid_price"].values
    labels = []
    for i in range(len(df)):
        labels.append(build_forward_returns(mid_arr, ts, i, tick_size))
    label_df = pd.DataFrame(labels)
    if not leakage_audit(label_df):
        raise ValueError("Feature store label frame failed leakage audit")
    out = pd.concat([df, label_df], axis=1)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(output_path, index=False)
    return output_path
