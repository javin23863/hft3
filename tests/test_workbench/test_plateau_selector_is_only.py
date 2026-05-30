"""IS-only plateau selection (no OOS leakage)."""

from __future__ import annotations

from workbench.src.optimization.plateau_selector import select_robust_plateau
from workbench.src.run.campaign_runner import _plateau_matrix_rows


def test_plateau_selector_ignores_oos_metrics():
    rows = [
        {
            "parameter_hash": "high_oos",
            "fold_id": "D1",
            "params": {"signal_threshold": 0.99},
            "is_metrics": {"sharpe": 1.0},
            "oos_metrics": {"sharpe": 100.0},
        },
        {
            "parameter_hash": "high_is",
            "fold_id": "D1",
            "params": {"signal_threshold": 0.1},
            "is_metrics": {"sharpe": 10.0},
            "oos_metrics": {"sharpe": 0.01},
        },
    ]
    picked = select_robust_plateau(rows, primary_metric="sharpe")
    assert picked is not None
    assert picked["signal_threshold"] == 0.1


def test_plateau_selector_aggregates_is_across_folds():
    rows = [
        {
            "parameter_hash": "stable",
            "fold_id": "D1",
            "params": {"signal_threshold": 0.2},
            "is_metrics": {"sharpe": 8.0},
            "oos_metrics": {"sharpe": 50.0},
        },
        {
            "parameter_hash": "stable",
            "fold_id": "D2",
            "params": {"signal_threshold": 0.2},
            "is_metrics": {"sharpe": 7.0},
            "oos_metrics": {"sharpe": 50.0},
        },
        {
            "parameter_hash": "spiky",
            "fold_id": "D1",
            "params": {"signal_threshold": 0.3},
            "is_metrics": {"sharpe": 9.0},
            "oos_metrics": {"sharpe": 50.0},
        },
        {
            "parameter_hash": "spiky",
            "fold_id": "D2",
            "params": {"signal_threshold": 0.3},
            "is_metrics": {"sharpe": 1.0},
            "oos_metrics": {"sharpe": 50.0},
        },
    ]
    picked = select_robust_plateau(rows, primary_metric="sharpe")
    assert picked["signal_threshold"] == 0.2


def test_plateau_matrix_rows_excludes_plateau_exclude_folds():
    rows = [
        {
            "parameter_hash": "a",
            "fold_id": "D3",
            "params": {"signal_threshold": 0.99},
            "is_metrics": {"sharpe": 100.0},
        },
        {
            "parameter_hash": "b",
            "fold_id": "D1",
            "params": {"signal_threshold": 0.1},
            "is_metrics": {"sharpe": 5.0},
        },
    ]
    wfc_cfg = {"folds": [{"id": "D3", "plateau_exclude": True}]}
    filtered = _plateau_matrix_rows(rows, wfc_cfg)
    assert len(filtered) == 1
    assert filtered[0]["fold_id"] == "D1"
    picked = select_robust_plateau(filtered, primary_metric="sharpe")
    assert picked["signal_threshold"] == 0.1
