"""Embargo step resolution for walk-forward and purged CV."""
from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl


def _median_dt_ms(df: pl.DataFrame) -> int:
    ts = df["exchange_timestamp"].to_numpy()
    if ts.size < 2:
        return 1000
    return max(1, int(np.median(np.diff(ts))))


def _parse_duration_to_steps(duration: str, dt_ms: int) -> int:
    emb = str(duration).strip()
    if emb.endswith("h"):
        return max(1, int(float(emb[:-1]) * 3_600_000 / dt_ms))
    if emb.endswith("m"):
        return max(1, int(float(emb[:-1]) * 60_000 / dt_ms))
    if emb.endswith("s"):
        return max(1, int(float(emb[:-1]) * 1000 / dt_ms))
    try:
        return max(1, int(float(emb)))
    except ValueError:
        return 1


def resolve_embargo_steps(
    backtest: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    df: pl.DataFrame,
    *,
    label_horizon_steps: int,
    n: int | None = None,
) -> int:
    """
    Resolve embargo in row steps.

    Fixture mode uses fixture_embargo_steps; production parses backtest embargo.
    Always capped so folds remain feasible on short series.
    """
    bt = backtest or {}
    val = validation or {}
    row_n = n if n is not None else df.height
    dt_ms = _median_dt_ms(df)

    if bt.get("validation_mode") == "fixture":
        computed = int(bt.get("fixture_embargo_steps", 2))
    else:
        emb = val.get("embargo")
        if isinstance(emb, bool):
            emb = bt.get("embargo", "24h") if emb else "1"
        elif emb is None:
            emb = bt.get("embargo", "24h")
        computed = _parse_duration_to_steps(str(emb), dt_ms)

    cap = max(1, row_n // 4 - max(1, label_horizon_steps))
    return min(computed, cap)


def resolve_label_horizon_ms(
    horizons: list[str] | None,
    backtest: dict[str, Any] | None,
) -> int:
    bt = backtest or {}
    if bt.get("validation_mode") == "fixture":
        return int(bt.get("fixture_label_horizon_ms", 1000))

    ms_values: list[int] = []
    for raw in horizons or ["1s"]:
        token = str(raw).lstrip("+-")
        if token.endswith("h"):
            ms_values.append(int(float(token[:-1]) * 3_600_000))
        elif token.endswith("m"):
            ms_values.append(int(float(token[:-1]) * 60_000))
        elif token.endswith("s"):
            ms_values.append(int(float(token[:-1]) * 1000))
    return max(ms_values) if ms_values else 1000


def horizon_steps_from_ms(df: pl.DataFrame, horizon_ms: int) -> int:
    dt_ms = _median_dt_ms(df)
    return max(1, round(horizon_ms / dt_ms))
