"""Tests for Walk Forward Correlation gate."""

from __future__ import annotations

import random
from typing import List

import pytest

from workbench.src.robustness.wfc.gate import evaluate_wfc_gate


def _cfg(**overrides):
    base = {
        "enabled": True,
        "primary_metric": "sharpe",
        "pearson_min": 0.20,
        "spearman_min": 0.20,
        "correlation_p_value_max": 0.10,
        "min_parameter_combinations": 20,
        "min_walk_forward_folds": 2,
        "min_positive_fold_ratio": 0.50,
        "require_oos_net_profit_positive": False,
        "require_oos_risk_adjusted_positive": False,
        "require_cost_adjusted_correlation": False,
        "secondary_metrics": [],
        "bootstrap_samples": 50,
        "permutation_samples": 50,
        "outlier_winsor_pct": 0.01,
        "min_oos_trade_count": {"default": 1},
        "max_oos_drawdown_limit": -500.0,
    }
    base.update(overrides)
    return base


def _metric_dict(sharpe: float, *, net: float | None = None) -> dict:
    net = net if net is not None else sharpe
    return {
        "sharpe": sharpe,
        "net_return": net,
        "net_return_adjusted": net,
        "profit_factor": 1.5,
        "max_drawdown": -10.0,
        "max_drawdown_adj_return": -10.0,
        "trade_count": 50,
    }


def _rows(is_vals: List[float], oos_vals: List[float], *, fold: str = "D1", fold_idx: int = 0) -> List[dict]:
    rows = []
    fold_shift = fold_idx * 0.05
    for i, (iv, ov) in enumerate(zip(is_vals, oos_vals)):
        rows.append(
            {
                "parameter_hash": f"h{i}",
                "fold_id": fold,
                "asset": "MES.v.0",
                "regime_label": "macro",
                "is_metrics": _metric_dict(iv + fold_shift),
                "oos_metrics": _metric_dict(ov + fold_shift * 0.8),
            }
        )
    return rows


def _rows_negative_fold(is_vals: List[float], oos_vals: List[float], *, fold: str = "D1", fold_idx: int = 0) -> List[dict]:
    rows = []
    for i, (iv, ov) in enumerate(zip(is_vals, oos_vals)):
        is_v = iv + fold_idx * 0.05
        oos_v = ov - fold_idx * 0.05
        rows.append(
            {
                "parameter_hash": f"h{i}",
                "fold_id": fold,
                "asset": "MES.v.0",
                "regime_label": "macro",
                "is_metrics": _metric_dict(is_v),
                "oos_metrics": _metric_dict(oos_v),
            }
        )
    return rows


def _multi_fold_rows(is_vals: List[float], oos_vals: List[float]) -> List[dict]:
    out = []
    for fi, fold in enumerate(("D1", "D2", "D3")):
        out.extend(_rows(is_vals, oos_vals, fold=fold, fold_idx=fi))
    return out


def test_wfc_strong_positive_passes():
    n = 25
    is_vals = [float(i) for i in range(n)]
    oos_vals = [v * 0.8 + 1.0 for v in is_vals]
    result = evaluate_wfc_gate(_multi_fold_rows(is_vals, oos_vals), _cfg(), model_id="HYP_5")
    assert result.wfc_status == "PASS"
    assert result.pearson > 0.9


def test_wfc_random_cloud_fails():
    rng = random.Random(0)
    n = 30
    is_vals = [rng.random() for _ in range(n)]
    oos_vals = [rng.random() for _ in range(n)]
    result = evaluate_wfc_gate(_multi_fold_rows(is_vals, oos_vals), _cfg(), model_id="HYP_5")
    assert result.wfc_status == "FAIL"


def test_wfc_negative_correlation_fails():
    n = 25
    is_vals = [float(i) for i in range(n)]
    oos_vals = [float(n - i) for i in range(n)]
    rows = []
    for fi, fold in enumerate(("D1", "D2", "D3")):
        rows.extend(_rows_negative_fold(is_vals, oos_vals, fold=fold, fold_idx=fi))
    result = evaluate_wfc_gate(rows, _cfg(), model_id="HYP_5")
    assert result.wfc_status == "FAIL"
    assert result.pearson < 0


def test_wfc_insufficient_combinations_error():
    result = evaluate_wfc_gate(
        _rows([1.0, 2.0], [1.0, 2.0]),
        _cfg(min_parameter_combinations=100),
        model_id="HYP_5",
    )
    assert result.wfc_status == "ERROR"


def test_wfc_missing_oos_error():
    rows = _rows([1.0] * 25, [1.0] * 25)
    rows[0]["oos_metrics"] = None
    result = evaluate_wfc_gate(rows, _cfg(min_parameter_combinations=20), model_id="HYP_5")
    assert result.wfc_status == "ERROR"


def test_wfc_outlier_fake_pass_fails():
    n = 25
    is_vals = [0.1] * n
    oos_vals = [0.1] * n
    is_vals[-1] = 100.0
    oos_vals[-1] = 100.0
    result = evaluate_wfc_gate(
        _multi_fold_rows(is_vals, oos_vals),
        _cfg(min_parameter_combinations=20),
        model_id="HYP_5",
    )
    assert result.outlier_sensitivity_pass is False or result.wfc_status != "PASS"


def test_wfc_weak_positive_conditional():
    n = 30
    rng = random.Random(1)
    rows = []
    for fi, fold in enumerate(("D1", "D2", "D3")):
        for i in range(n):
            base_is = float(i) + rng.random()
            is_v = base_is + fi * 0.2
            oos_v = base_is * 0.45 + rng.random() * 4.0 + fi * 0.15
            rows.append(
                {
                    "parameter_hash": f"h{i}",
                    "fold_id": fold,
                    "asset": "MES.v.0",
                    "regime_label": "macro",
                    "is_metrics": _metric_dict(is_v),
                    "oos_metrics": _metric_dict(oos_v),
                }
            )
    result = evaluate_wfc_gate(
        rows,
        _cfg(
            pearson_min=0.80,
            spearman_min=0.80,
            correlation_p_value_max=0.20,
            min_parameter_combinations=20,
            min_positive_fold_ratio=0.40,
        ),
        model_id="HYP_5",
    )
    assert result.wfc_status == "CONDITIONAL"
    assert 0 < result.pearson < 0.80
    assert 0 < result.spearman < 0.80


def test_wfc_per_parameter_not_pooled():
    """Pooled (param×fold) correlation differs from per-parameter fold correlation."""
    rows = []
    for fold_idx, fold in enumerate(("D1", "D2", "D3")):
        for i in range(25):
            # Per-fold offset breaks pooled correlation but preserves per-param fold trend
            is_v = float(i) + fold_idx * 10.0
            oos_v = is_v * 0.9 + 1.0
            rows.append(
                {
                    "parameter_hash": f"h{i}",
                    "fold_id": fold,
                    "is_metrics": _metric_dict(is_v),
                    "oos_metrics": _metric_dict(oos_v),
                }
            )
    result = evaluate_wfc_gate(rows, _cfg(min_parameter_combinations=20), model_id="HYP_5")
    assert result.pearson > 0.5


def test_wfc_drawdown_limit_fails():
    rows = _multi_fold_rows([float(i) for i in range(25)], [float(i) + 1 for i in range(25)])
    for r in rows:
        r["oos_metrics"]["max_drawdown"] = -1000.0
    result = evaluate_wfc_gate(rows, _cfg(min_parameter_combinations=20), model_id="HYP_5")
    assert result.wfc_status == "FAIL"
    assert not result.drawdown_pass


def test_wfc_drawdown_adj_return_limit_fails():
    rows = _multi_fold_rows([float(i) for i in range(25)], [float(i) + 1 for i in range(25)])
    for r in rows:
        r["oos_metrics"]["max_drawdown_adj_return"] = -1000.0
    result = evaluate_wfc_gate(
        rows,
        _cfg(
            min_parameter_combinations=20,
            secondary_metrics=["max_drawdown_adj_return"],
        ),
        model_id="HYP_5",
    )
    assert result.wfc_status == "FAIL"
    assert not result.drawdown_pass


def test_wfc_cost_adjusted_correlation_required():
    rows = _multi_fold_rows([float(i) for i in range(25)], [float(i) * 0.8 + 1 for i in range(25)])
    for r in rows:
        r["is_metrics"]["net_return_adjusted"] = -1.0
        r["oos_metrics"]["net_return_adjusted"] = -2.0
    result = evaluate_wfc_gate(
        rows,
        _cfg(min_parameter_combinations=20, require_cost_adjusted_correlation=True),
        model_id="HYP_5",
    )
    assert result.wfc_status == "FAIL"
    assert not result.cost_adjusted_pass
