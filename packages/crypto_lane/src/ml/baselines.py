"""Economically named baseline models."""
from __future__ import annotations

import re

import numpy as np
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import ElasticNet, LogisticRegression, Ridge


_BASIS_PAT = re.compile(r"basis|ou_|spot_perp|cross_venue", re.I)
_FUNDING_PAT = re.compile(r"funding|carry|hedge|latent_funding", re.I)
_VOL_PAT = re.compile(r"iv_|vol|rv|jump|realized", re.I)


def feature_subset(name: str, columns: list[str]) -> list[str]:
    if name == "basis_only":
        return [c for c in columns if _BASIS_PAT.search(c)]
    if name == "funding_only":
        return [c for c in columns if _FUNDING_PAT.search(c)]
    if name == "volatility_only":
        return [c for c in columns if _VOL_PAT.search(c)]
    return columns


def train_baseline(
    name: str,
    X: np.ndarray,
    y: np.ndarray,
    columns: list[str],
):
    cols = feature_subset(name, columns)
    if not cols:
        cols = columns
    idx = [columns.index(c) for c in cols if c in columns]
    Xb = X[:, idx] if idx else X

    if name == "logistic_regression":
        model = LogisticRegression(max_iter=500)
        y_fit = (y > np.median(y)).astype(int)
        model.fit(Xb, y_fit)
        return model, "classification", idx

    if name == "naive_previous_value":
        preds = np.roll(y, 1)
        preds[0] = y[0]
        class _Naive:
            def predict(self, X_in):
                n = X_in.shape[0]
                return preds[-n:]

        return _Naive(), "regression", idx

    if name == "elastic_net":
        model = ElasticNet(max_iter=3000)
        model.fit(Xb, y)
        return model, "regression", idx

    if name == "ridge":
        model = Ridge()
        model.fit(Xb, y)
        return model, "regression", idx

    if name in ("basis_only", "funding_only", "volatility_only"):
        model = Ridge()
        model.fit(Xb, y)
        return model, "regression", idx

    model = DummyRegressor(strategy="mean")
    model.fit(Xb, y)
    return model, "regression", idx


def predict_baseline(model, kind: str, idx: list[int], X: np.ndarray) -> np.ndarray:
    Xb = X[:, idx] if idx else X
    pred = model.predict(Xb)
    if kind == "classification":
        return pred.astype(float)
    return pred
