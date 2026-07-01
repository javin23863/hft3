"""
Walk-forward training on real MBO-derived features; exports model.bin for C++.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))

from decision_engine.python.src.feature_store import build_feature_parquet
from decision_engine.python.src.multi_loss import MultiLossObjective
from decision_engine.python.src.walk_forward import WalkForwardValidator, export_weights_to_cpp

FEATURE_COUNT = 64


def _load_period(df: pd.DataFrame, start_year: int, end_year: int) -> dict:
    ts = pd.to_datetime(df["timestamp_ns"], unit="ns", utc=True)
    mask = (ts.dt.year >= start_year) & (ts.dt.year <= end_year)
    sub = df.loc[mask]
    if sub.empty:
        raise ValueError(
            f"No rows for walk-forward period {start_year}-{end_year}; "
            "need multi-year parquet or narrower WF windows"
        )
    feat_cols = [c for c in sub.columns if c.startswith("f_")]
    y_ret = sub.get("y_return_500ms", pd.Series(np.nan, index=sub.index))
    valid = y_ret.notna()
    sub = sub.loc[valid]
    y_ret = y_ret.loc[valid]
    if sub.empty:
        raise ValueError("All labels NaN after dropping forward-return tail rows")
    X = sub[feat_cols].values.astype(np.float64)
    return {
        "X": X,
        "y_return": y_ret.values,
        "filled": np.ones(len(sub)),
        "pnl": y_ret.values * 0.25,
        "adverse_selection": np.zeros(len(sub)),
    }


def _train_linear_ridge(data: dict, l2: float = 1e-3) -> dict:
    X = data["X"]
    y = data["y_return"]
    if len(X) < FEATURE_COUNT + 1:
        w = np.zeros(FEATURE_COUNT)
    else:
        XtX = X.T @ X + l2 * np.eye(X.shape[1])
        w = np.linalg.solve(XtX, X.T @ y)
    return {"kind": "ridge", "weights": w.tolist(), "feature_count": X.shape[1]}


def _train_lightgbm(data: dict, params: dict | None = None) -> dict:
    """Gradient-boosted trees beside ridge. Research/paper lane only.

    LightGBM is an optional dependency (`pip install .[ml]`); ridge remains
    the sole C++-exportable path until certified evidence justifies a native
    GBM inference path. Missing dependency fails loud, never silently
    degrades to ridge.
    """
    try:
        import lightgbm as lgb
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "lightgbm is not installed; install the 'ml' extra "
            "(pip install .[ml]) to train the GBM model"
        ) from exc

    X = data["X"]
    y = data["y_return"]
    train_params = {
        "objective": "regression",
        "metric": "l2",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "verbosity": -1,
        "seed": 7,
        "deterministic": True,
    }
    train_params.update(params or {})
    booster = lgb.train(train_params, lgb.Dataset(X, label=y), num_boost_round=200)
    return {
        "kind": "lightgbm",
        "predict": booster.predict,
        "booster_model_str": booster.model_to_string(),
        "feature_count": X.shape[1],
        "params": train_params,
    }


def _model_predictions(model: dict, X: np.ndarray) -> np.ndarray:
    predict = model.get("predict")
    if callable(predict):
        return np.asarray(predict(X), dtype=np.float64)
    return X @ np.array(model["weights"])


def _eval_model(model: dict, data: dict) -> dict:
    X = data["X"]
    pred = _model_predictions(model, X)
    y = data["y_return"]
    pnl = np.sign(pred) * np.abs(y)
    net = float(np.sum(pnl))
    tail = float(np.percentile(pnl, 5)) if len(pnl) else 0.0
    loss_fn = MultiLossObjective()
    loss = loss_fn.calculate_loss(
        {
            "return": pred,
            "fill_prob": np.clip(np.abs(pred), 0, 1),
            "pnl": pnl,
            "adverse_selection": np.zeros_like(pred),
        },
        {
            "return": y,
            "filled": data["filled"],
            "adverse_selection": data["adverse_selection"],
        },
    )
    return {
        "net_expectancy": net / max(len(pnl), 1),
        "tail_loss": tail,
        "loss": float(loss),
        "net_pnl": net,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npz", default=str(_REPO / "data/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"))
    parser.add_argument("--features", default=str(_REPO / "data/features/mes_cpi_features.parquet"))
    parser.add_argument("--output", default=str(_REPO / "models/model.bin"))
    parser.add_argument(
        "--model",
        choices=("ridge", "lightgbm"),
        default="ridge",
        help=(
            "ridge: linear model, C++-exportable (default). lightgbm: GBM "
            "research/paper lane; walk-forward gated but never exported to "
            "model.bin."
        ),
    )
    args = parser.parse_args()

    if not Path(args.features).exists():
        print(f"Building feature store from {args.npz}...")
        build_feature_parquet(args.npz, args.features)

    df = pd.read_parquet(args.features)

    def loader(start_year: int, end_year: int) -> dict:
        return _load_period(df, start_year, end_year)

    validator = WalkForwardValidator()

    if args.model == "lightgbm":
        def train_fn(data: dict) -> dict:
            return _train_lightgbm(data)
    else:
        def train_fn(data: dict) -> dict:
            return _train_linear_ridge(data)

    result = validator.run_validation(train_fn, _eval_model, loader)
    if result.get("status") != "PASS":
        print("Walk-forward did not pass strict gates; refusing to export model.bin.")
        sys.exit(1)
    model = result["model"]

    if model.get("kind") == "lightgbm":
        # GBM stays research/paper lane: persist the booster text next to the
        # requested output for review, never the C++ model.bin surface.
        gbm_path = Path(args.output).with_suffix(".lgb.txt")
        gbm_path.parent.mkdir(parents=True, exist_ok=True)
        gbm_path.write_text(model["booster_model_str"], encoding="utf-8")
        print(
            "LightGBM walk-forward PASS; booster saved to "
            f"{gbm_path} (research lane; model.bin export is ridge-only)."
        )
        return

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    export_weights_to_cpp(
        model["weights"],
        str(out_path),
        model_id=1,
        feature_count=min(model["feature_count"], FEATURE_COUNT),
    )
    print(f"Exported weights to {out_path}")


if __name__ == "__main__":
    main()
