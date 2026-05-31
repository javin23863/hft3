"""Event-study labels for H7 congestion shock hypothesis."""
from __future__ import annotations

import math

import numpy as np
import polars as pl

from crypto_lane.src.ml.embargo import horizon_steps_from_ms


def _positive_horizon_ms(horizons: list[str] | None, default_ms: int = 1000) -> int:
    ms_values: list[int] = []
    for raw in horizons or ["+1s"]:
        token = str(raw).lstrip("+-")
        if token.endswith("h"):
            ms_values.append(int(float(token[:-1]) * 3_600_000))
        elif token.endswith("m"):
            ms_values.append(int(float(token[:-1]) * 60_000))
        elif token.endswith("s"):
            ms_values.append(int(float(token[:-1]) * 1000))
    return max(ms_values) if ms_values else default_ms


def attach_event_study_labels(
    df: pl.DataFrame,
    *,
    event_col: str = "btc_congestion_shock_event",
    horizons: list[str] | None = None,
    horizon_ms: int | None = None,
) -> pl.DataFrame:
    """
    Label event_window_return only on shock events: cumulative log return
    from event row through forward horizon.
    """
    if event_col not in df.columns or "spot_mid" not in df.columns:
        return df.with_columns(pl.lit(None).cast(pl.Float64).alias("event_window_return"))

    h_ms = horizon_ms if horizon_ms is not None else _positive_horizon_ms(horizons)
    h = horizon_steps_from_ms(df, h_ms)
    spot = df["spot_mid"].to_numpy()
    events = df[event_col].to_numpy()
    labels = np.full(len(spot), np.nan, dtype=float)

    for i in range(len(spot)):
        if int(events[i]) != 1:
            continue
        end = min(len(spot) - 1, i + h)
        if end <= i:
            continue
        labels[i] = math.log(spot[end] / spot[i]) if spot[i] > 0 else 0.0

    return df.with_columns(pl.Series("event_window_return", labels))


def randomized_event_control_ic(
    df: pl.DataFrame,
    target: str,
    feat_cols: list[str],
    *,
    event_col: str = "btc_congestion_shock_event",
    horizons: list[str] | None = None,
    horizon_ms: int | None = None,
    seed: int = 42,
) -> dict[str, float]:
    """Permute event flags, recompute labels, retrain, compare OOS IC."""
    from crypto_lane.src.ml.baselines import predict_baseline, train_baseline
    from crypto_lane.src.labels.forward_labels import information_coefficient

    rng = np.random.default_rng(seed)
    h_ms = horizon_ms if horizon_ms is not None else _positive_horizon_ms(horizons)

    base = df.drop_nulls(subset=feat_cols)
    labeled = attach_event_study_labels(base, event_col=event_col, horizon_ms=h_ms)
    if labeled.height < 8:
        return {"real_oos_ic": 0.0, "randomized_event_ic": 0.0, "randomized_degraded": False, "insufficient_event_rows": True}

    split = max(6, labeled.height // 2)
    train_df = labeled[:split]
    test_labeled = labeled[split:].filter(pl.col(target).is_finite())
    train_labeled = train_df.filter(pl.col(target).is_finite())
    if train_labeled.height < 3 or test_labeled.height < 2:
        return {"real_oos_ic": 0.0, "randomized_event_ic": 0.0, "randomized_degraded": False, "insufficient_event_rows": True}

    X_tr = train_labeled.select(feat_cols).to_numpy()
    y_tr = train_labeled[target].to_numpy()
    X_te = test_labeled.select(feat_cols).to_numpy()
    y_te = test_labeled[target].to_numpy()

    bm, kind, idx = train_baseline("ridge", X_tr, y_tr, feat_cols)
    pred_real = predict_baseline(bm, kind, idx, X_te)
    ic_real = information_coefficient(y_te, pred_real)

    shuffled_train = train_df.with_columns(
        pl.Series(event_col, rng.permutation(train_df[event_col].to_numpy()))
    )
    if "event_severity" in shuffled_train.columns:
        shuffled_train = shuffled_train.with_columns(
            pl.Series("event_severity", rng.permutation(train_df["event_severity"].to_numpy()))
        )
    shuffled_labeled = attach_event_study_labels(shuffled_train, event_col=event_col, horizon_ms=h_ms)
    shuffled_train_labeled = shuffled_labeled.filter(pl.col(target).is_finite())
    if shuffled_train_labeled.height < 3:
        return {"real_oos_ic": ic_real, "randomized_event_ic": 0.0, "randomized_degraded": False, "insufficient_shuffled_train_rows": True}

    X_tr_s = shuffled_train_labeled.select(feat_cols).to_numpy()
    y_tr_s = shuffled_train_labeled[target].to_numpy()
    bm2, kind2, idx2 = train_baseline("ridge", X_tr_s, y_tr_s, feat_cols)
    pred_rand = predict_baseline(bm2, kind2, idx2, X_te)
    ic_rand = information_coefficient(y_te, pred_rand)

    return {
        "real_oos_ic": ic_real,
        "randomized_event_ic": ic_rand,
        "randomized_degraded": (
            abs(ic_real - ic_rand) > 0.02
            or abs(ic_rand) < max(0.05, abs(ic_real) * 0.5)
            or abs(ic_real) > abs(ic_rand) + 0.03
        ),
    }
