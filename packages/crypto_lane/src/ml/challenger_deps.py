"""ML challenger imports and fit helpers."""
from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.linear_model import ElasticNet, Ridge


class ChallengerUnavailableError(RuntimeError):
    """Raised when a named challenger cannot be imported or fit."""


def challenger_import_status() -> dict[str, str]:
    """Report importability for each challenger model family."""
    out: dict[str, str] = {}
    for name in ("lightgbm", "xgboost"):
        try:
            if name == "lightgbm":
                import lightgbm  # noqa: F401
            else:
                import xgboost  # noqa: F401
            out[name] = "available"
        except ImportError as exc:
            out[name] = f"missing: {exc}"
    for name in ("elastic_net", "ridge"):
        out[name] = "available"
    return out


def fit_challenger(
    name: str,
    X: np.ndarray,
    y: np.ndarray,
    columns: list[str],
) -> tuple[Any, str, str]:
    """Fit challenger `name`; raise ChallengerUnavailableError on missing tree libs."""
    _ = columns
    if name == "lightgbm":
        try:
            import lightgbm as lgb
        except ImportError as exc:
            raise ChallengerUnavailableError(f"lightgbm not installed: {exc}") from exc
        model = lgb.LGBMRegressor(n_estimators=30, max_depth=4, verbose=-1)
        model.fit(X, y)
        return model, "regression", "lightgbm"
    if name == "xgboost":
        try:
            import xgboost as xgb
        except ImportError as exc:
            raise ChallengerUnavailableError(f"xgboost not installed: {exc}") from exc
        model = xgb.XGBRegressor(n_estimators=30, max_depth=4, verbosity=0)
        model.fit(X, y)
        return model, "regression", "xgboost"
    if name == "elastic_net":
        model = ElasticNet(max_iter=3000)
        model.fit(X, y)
        return model, "regression", "elastic_net"
    if name == "ridge":
        model = Ridge()
        model.fit(X, y)
        return model, "regression", "ridge"
    raise ChallengerUnavailableError(f"unknown challenger: {name!r}")
