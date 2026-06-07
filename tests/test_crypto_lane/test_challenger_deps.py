from __future__ import annotations

import numpy as np
import pytest

from crypto_lane.src.ml.challenger_deps import (
    ChallengerUnavailableError,
    challenger_import_status,
    fit_challenger,
)


def test_challenger_import_status_includes_sklearn_models():
    status = challenger_import_status()
    assert status["elastic_net"] == "available"
    assert status["ridge"] == "available"
    assert "lightgbm" in status
    assert "xgboost" in status


def test_fit_ridge_and_elastic_net():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, 3))
    y = X[:, 0] + rng.normal(scale=0.1, size=40)
    cols = ["a", "b", "c"]
    for name in ("ridge", "elastic_net"):
        model, kind, actual = fit_challenger(name, X, y, cols)
        assert actual == name
        assert kind == "regression"
        assert model.predict(X[:5]).shape == (5,)


def test_fit_unknown_raises():
    X = np.zeros((10, 2))
    y = np.zeros(10)
    with pytest.raises(ChallengerUnavailableError):
        fit_challenger("not_a_model", X, y, ["a", "b"])
