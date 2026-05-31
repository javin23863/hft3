"""Phase 6 — forward targets from MBO mid prices; filtration-safe (no lookahead)."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import pandas as pd

TARGET_HORIZONS_SEC: Sequence[int] = (1, 5, 10, 30, 60, 120, 300)

TARGET_INSTRUMENTS = (
    "ES",
    "MES",
    "NQ",
    "YM",
    "RTY",
    "ZN",
    "ZB",
    "CL",
    "GC",
    "6E",
)


def _mid_at_offset(tensor_df: pd.DataFrame, canonical: str, offset_sec: int) -> Optional[float]:
    sub = tensor_df[
        (tensor_df["canonical_symbol"] == canonical) & (tensor_df["offset_sec"] == offset_sec)
    ]
    if sub.empty or bool(sub.iloc[0].get("mbo_missing")):
        return None
    mid = float(sub.iloc[0]["mid_price"])
    return mid if mid > 0 else None


def build_l3_event_targets(
    tensor_df: pd.DataFrame,
    *,
    anchor_offset_sec: int = 0,
    horizons_sec: Sequence[int] = TARGET_HORIZONS_SEC,
    instruments: Sequence[str] = TARGET_INSTRUMENTS,
) -> pd.DataFrame:
    """
    Labels use mid_price at T+horizon vs mid at anchor T only.
    Features at T must not use these columns (filtration integrity).
    """
    rows: List[Dict[str, float | str | int | None]] = []
    for canonical in instruments:
        base = _mid_at_offset(tensor_df, canonical, anchor_offset_sec)
        if base is None:
            continue
        for h in horizons_sec:
            future_off = anchor_offset_sec + h
            fut = _mid_at_offset(tensor_df, canonical, future_off)
            if fut is None:
                continue
            ret = (fut - base) / base if base else 0.0
            rows.append(
                {
                    "canonical_symbol": canonical,
                    "horizon_sec": h,
                    "anchor_offset_sec": anchor_offset_sec,
                    "forward_return": ret,
                    "direction": 1 if ret > 0 else (-1 if ret < 0 else 0),
                    "volatility_expansion": abs(ret),
                    "spread_expansion_proxy": abs(ret),
                    "base_mid": base,
                    "future_mid": fut,
                }
            )
    return pd.DataFrame(rows)
