"""Walk Forward Correlation gate evaluation."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import stats

from workbench.src.robustness.wfc.metrics import metric_value


@dataclass
class WfcResult:
    run_id: str = ""
    model_id: str = ""
    strategy_id: str = ""
    wfc_status: str = "ERROR"
    pearson: float = 0.0
    spearman: float = 0.0
    kendall: float = 0.0
    pearson_ci: List[float] = field(default_factory=list)
    spearman_ci: List[float] = field(default_factory=list)
    p_value: float = 1.0
    cost_adjusted_pearson: float = 0.0
    cost_adjusted_spearman: float = 0.0
    n_parameter_combinations: int = 0
    n_folds: int = 0
    positive_fold_ratio: float = 0.0
    fold_correlations: Dict[str, float] = field(default_factory=dict)
    top_decile_oos_median: float = 0.0
    bottom_decile_oos_median: float = 0.0
    outlier_sensitivity_pass: bool = False
    cost_adjusted_pass: bool = False
    drawdown_pass: bool = False
    secondary_metrics_pass: bool = True
    regime_consistency_pass: bool = True
    universe_consistency_pass: bool = True
    rejection_reasons: List[str] = field(default_factory=list)
    artifact_paths: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _bootstrap_ci(
    values: Sequence[float],
    fn,
    *,
    n_samples: int,
    seed: int = 42,
) -> Tuple[float, List[float]]:
    rng = random.Random(seed)
    arr = list(values)
    n = len(arr)
    if n < 3:
        val = fn(arr) if arr else 0.0
        return val, [val, val]
    vals = []
    for _ in range(n_samples):
        idx = [rng.randrange(n) for _ in range(n)]
        sample = [arr[i] for i in idx]
        try:
            vals.append(fn(sample))
        except Exception:
            continue
    if not vals:
        return 0.0, [0.0, 0.0]
    return float(np.mean(vals)), [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def _permutation_pvalue(
    rows: List[Dict[str, Any]],
    metric: str,
    observed: float,
    *,
    n_perm: int = 500,
) -> float:
    vectors = _per_parameter_fold_vectors(rows, metric)
    if len(vectors) < 2:
        return 1.0
    rng = random.Random(42)
    count = 0
    for _ in range(n_perm):
        shuffled_corrs: List[float] = []
        for is_v, oos_v in vectors:
            oos_shuf = list(oos_v)
            rng.shuffle(oos_shuf)
            if np.std(is_v) > 1e-12 and np.std(oos_shuf) > 1e-12:
                shuffled_corrs.append(float(stats.pearsonr(is_v, oos_shuf)[0]))
        if not shuffled_corrs:
            continue
        med = float(np.median(shuffled_corrs))
        if abs(med) >= abs(observed):
            count += 1
    return count / max(n_perm, 1)


def _winsorize(values: Sequence[float], pct: float) -> List[float]:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return []
    lo = np.percentile(arr, pct * 100)
    hi = np.percentile(arr, (1.0 - pct) * 100)
    return [float(np.clip(v, lo, hi)) for v in arr]


def _aggregate_by_parameter(rows: List[Dict[str, Any]], primary: str) -> List[Dict[str, Any]]:
    by_hash: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        ph = str(row.get("parameter_hash", ""))
        bucket = by_hash.setdefault(
            ph,
            {
                "parameter_hash": ph,
                "params": row.get("params", {}),
                "is_vals": [],
                "oos_vals": [],
                "is_net": [],
                "oos_net": [],
                "oos_adj": [],
                "oos_pf": [],
                "oos_dd": [],
                "oos_dd_adj": [],
            },
        )
        bucket["is_vals"].append(metric_value(row.get("is_metrics", {}), primary))
        bucket["oos_vals"].append(metric_value(row.get("oos_metrics", {}), primary))
        bucket["is_net"].append(metric_value(row.get("is_metrics", {}), "net_return"))
        bucket["oos_net"].append(metric_value(row.get("oos_metrics", {}), "net_return"))
        bucket["oos_adj"].append(metric_value(row.get("oos_metrics", {}), "net_return_adjusted"))
        bucket["oos_pf"].append(metric_value(row.get("oos_metrics", {}), "profit_factor"))
        bucket["oos_dd"].append(metric_value(row.get("oos_metrics", {}), "max_drawdown"))
        bucket["oos_dd_adj"].append(metric_value(row.get("oos_metrics", {}), "max_drawdown_adj_return"))
    out: List[Dict[str, Any]] = []
    for bucket in by_hash.values():
        out.append(
            {
                "parameter_hash": bucket["parameter_hash"],
                "params": bucket["params"],
                "is_metrics": {
                    primary: float(np.mean(bucket["is_vals"])) if bucket["is_vals"] else 0.0,
                    "net_return": float(np.mean(bucket["is_net"])) if bucket["is_net"] else 0.0,
                },
                "oos_metrics": {
                    primary: float(np.mean(bucket["oos_vals"])) if bucket["oos_vals"] else 0.0,
                    "net_return": float(np.mean(bucket["oos_net"])) if bucket["oos_net"] else 0.0,
                    "net_return_adjusted": float(np.mean(bucket["oos_adj"])) if bucket["oos_adj"] else 0.0,
                    "profit_factor": float(np.mean(bucket["oos_pf"])) if bucket["oos_pf"] else 0.0,
                    "max_drawdown": float(np.mean(bucket["oos_dd"])) if bucket["oos_dd"] else 0.0,
                    "max_drawdown_adj_return": float(np.mean(bucket["oos_dd_adj"])) if bucket["oos_dd_adj"] else 0.0,
                },
            }
        )
    return out


def _per_parameter_fold_vectors(
    rows: List[Dict[str, Any]],
    metric: str,
) -> List[Tuple[List[float], List[float]]]:
    """For each parameter, IS/OOS vectors of length n_folds."""
    by_param: Dict[str, Dict[str, Tuple[float, float]]] = {}
    for row in rows:
        ph = str(row.get("parameter_hash", ""))
        fid = str(row.get("fold_id", "all"))
        by_param.setdefault(ph, {})[fid] = (
            metric_value(row.get("is_metrics", {}), metric),
            metric_value(row.get("oos_metrics", {}), metric),
        )
    vectors: List[Tuple[List[float], List[float]]] = []
    for fold_map in by_param.values():
        if len(fold_map) < 2:
            continue
        is_v = [v[0] for _, v in sorted(fold_map.items())]
        oos_v = [v[1] for _, v in sorted(fold_map.items())]
        vectors.append((is_v, oos_v))
    return vectors


def _median_correlation(vectors: List[Tuple[List[float], List[float]]], fn) -> float:
    corrs: List[float] = []
    for is_v, oos_v in vectors:
        if len(is_v) < 2:
            continue
        if np.std(is_v) < 1e-12 or np.std(oos_v) < 1e-12:
            continue
        try:
            corrs.append(float(fn(is_v, oos_v)))
        except Exception:
            continue
    if not corrs:
        return 0.0
    return float(np.median(corrs))


def _decile_split(agg: List[Dict[str, Any]], primary: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], float, float]:
    """Unified decile definition on parameter-aggregated rows."""
    if not agg:
        return [], [], 0.0, 0.0
    ranked = sorted(agg, key=lambda r: metric_value(r.get("is_metrics", {}), primary))
    n = len(ranked)
    n_decile = max(1, n // 10)
    bottom = ranked[:n_decile]
    top = ranked[-n_decile:]
    top_med = float(np.median([metric_value(r.get("oos_metrics", {}), primary) for r in top]))
    bot_med = float(np.median([metric_value(r.get("oos_metrics", {}), primary) for r in bottom]))
    return top, bottom, top_med, bot_med


def _fold_correlations(
    rows: List[Dict[str, Any]],
    metric: str,
) -> Dict[str, float]:
    by_fold: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        fid = str(row.get("fold_id", "all"))
        by_fold.setdefault(fid, []).append(row)
    out: Dict[str, float] = {}
    for fid, fold_rows in by_fold.items():
        is_m = [metric_value(r.get("is_metrics", {}), metric) for r in fold_rows]
        oos_m = [metric_value(r.get("oos_metrics", {}), metric) for r in fold_rows]
        if len(is_m) >= 3 and np.std(is_m) > 1e-12 and np.std(oos_m) > 1e-12:
            out[fid] = float(stats.pearsonr(is_m, oos_m)[0])
        else:
            out[fid] = 0.0
    return out


def _regime_consistency(rows: List[Dict[str, Any]], metric: str) -> bool:
    by_regime: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        label = str(row.get("regime_label") or "default")
        by_regime.setdefault(label, []).append(row)
    if len(by_regime) <= 1:
        return True
    signs: List[float] = []
    for regime_rows in by_regime.values():
        fold_corrs = _fold_correlations(regime_rows, metric)
        if fold_corrs:
            signs.append(float(np.median(list(fold_corrs.values()))))
    if len(signs) < 2:
        return True
    return all(s > 0 for s in signs) or all(s < 0 for s in signs)


def _universe_consistency(rows: List[Dict[str, Any]], metric: str) -> bool:
    assets = {str(r.get("asset", "")) for r in rows if r.get("asset")}
    if len(assets) <= 1:
        return True
    by_asset: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_asset.setdefault(str(row.get("asset", "")), []).append(row)
    signs = []
    for asset_rows in by_asset.values():
        fold_corrs = _fold_correlations(asset_rows, metric)
        if fold_corrs:
            signs.append(float(np.median(list(fold_corrs.values()))))
    if len(signs) < 2:
        return True
    return all(s > 0 for s in signs) or all(s < 0 for s in signs)


def _secondary_metrics_pass(
    top_decile: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    secondary = cfg.get("secondary_metrics") or []
    dd_limit = float(cfg.get("max_oos_drawdown_limit", -500.0))
    ok = True
    for row in top_decile:
        oos = row.get("oos_metrics") or {}
        if "profit_factor" in secondary and metric_value(oos, "profit_factor") <= 1.0:
            ok = False
            reasons.append("Top decile OOS profit_factor <= 1.0")
            break
        if "net_return_adjusted" in secondary and metric_value(oos, "net_return_adjusted") <= 0:
            ok = False
            reasons.append("Top decile OOS net_return_adjusted not positive")
            break
        if "max_drawdown_adj_return" in secondary and metric_value(oos, "max_drawdown_adj_return") < dd_limit:
            ok = False
            reasons.append(f"Top decile OOS max_drawdown_adj_return below {dd_limit}")
            break
    return ok, reasons


def evaluate_wfc_gate(
    rows: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    *,
    run_id: str = "",
    model_id: str = "",
    strategy_id: str = "",
) -> WfcResult:
    reasons: List[str] = []
    if not cfg.get("enabled", True):
        return WfcResult(
            run_id=run_id,
            model_id=model_id,
            strategy_id=strategy_id or model_id,
            wfc_status="ERROR",
            rejection_reasons=["WFC gate disabled in config"],
        )

    if not rows:
        return WfcResult(
            run_id=run_id,
            model_id=model_id,
            strategy_id=strategy_id or model_id,
            wfc_status="ERROR",
            rejection_reasons=["No parameter matrix rows"],
        )

    combos = {r.get("parameter_hash") for r in rows}
    folds = {r.get("fold_id") for r in rows}
    n_combos = len(combos)
    n_folds = len(folds)
    min_combos = int(cfg.get("min_parameter_combinations", 100))
    min_folds = int(cfg.get("min_walk_forward_folds", 3))

    if n_combos < min_combos:
        return WfcResult(
            run_id=run_id,
            model_id=model_id,
            strategy_id=strategy_id or model_id,
            wfc_status="ERROR",
            n_parameter_combinations=n_combos,
            n_folds=n_folds,
            rejection_reasons=[f"Insufficient parameter combinations: {n_combos} < {min_combos}"],
        )

    missing_oos = [r for r in rows if r.get("oos_metrics") is None]
    if missing_oos:
        return WfcResult(
            run_id=run_id,
            model_id=model_id,
            strategy_id=strategy_id or model_id,
            wfc_status="ERROR",
            n_parameter_combinations=n_combos,
            n_folds=n_folds,
            rejection_reasons=[f"Missing OOS metrics on {len(missing_oos)} rows"],
        )

    if n_folds < min_folds:
        reasons.append(f"Insufficient folds: {n_folds} < {min_folds}")

    primary = str(cfg.get("primary_metric", "sharpe"))
    vectors = _per_parameter_fold_vectors(rows, primary)
    if not vectors:
        return WfcResult(
            run_id=run_id,
            model_id=model_id,
            strategy_id=strategy_id or model_id,
            wfc_status="ERROR",
            n_parameter_combinations=n_combos,
            n_folds=n_folds,
            rejection_reasons=["No per-parameter fold vectors for correlation"],
        )

    pearson = _median_correlation(vectors, lambda a, b: stats.pearsonr(a, b)[0])
    spearman = _median_correlation(vectors, lambda a, b: stats.spearmanr(a, b)[0])
    kendall = _median_correlation(vectors, lambda a, b: stats.kendalltau(a, b)[0])
    p_value = _permutation_pvalue(
        rows,
        primary,
        pearson,
        n_perm=int(cfg.get("permutation_samples", 500)),
    )

    boot_n = int(cfg.get("bootstrap_samples", 500))
    per_param_pearsons = [
        float(stats.pearsonr(a, b)[0])
        for a, b in vectors
        if np.std(a) > 1e-12 and np.std(b) > 1e-12
    ]
    per_param_spearmans = [
        float(stats.spearmanr(a, b)[0])
        for a, b in vectors
        if np.std(a) > 1e-12 and np.std(b) > 1e-12
    ]
    _, pearson_ci = _bootstrap_ci(per_param_pearsons, lambda s: float(np.median(s)), n_samples=boot_n)
    _, spearman_ci = _bootstrap_ci(per_param_spearmans, lambda s: float(np.median(s)), n_samples=boot_n)

    fold_corrs = _fold_correlations(rows, primary)
    positive_folds = sum(1 for v in fold_corrs.values() if v > 0)
    positive_ratio = positive_folds / max(len(fold_corrs), 1)

    agg = _aggregate_by_parameter(rows, primary)
    is_agg = [metric_value(r.get("is_metrics", {}), primary) for r in agg]
    oos_agg = [metric_value(r.get("oos_metrics", {}), primary) for r in agg]

    winsor_pct = float(cfg.get("outlier_winsor_pct", 0.01))
    w_is = _winsorize(is_agg, winsor_pct)
    w_oos = _winsorize(oos_agg, winsor_pct)
    winsor_r = float(stats.pearsonr(w_is, w_oos)[0]) if len(w_is) >= 2 else 0.0

    trim_n = max(1, int(len(is_agg) * winsor_pct))
    order = np.argsort(is_agg)
    trimmed = [i for i in order[trim_n:-trim_n]] if len(order) > 2 * trim_n else list(range(len(is_agg)))
    trim_is = [is_agg[i] for i in trimmed]
    trim_oos = [oos_agg[i] for i in trimmed]
    trim_r = float(stats.pearsonr(trim_is, trim_oos)[0]) if len(trim_is) >= 2 else 0.0

    outlier_pass = winsor_r > 0 and trim_r > 0 and abs(winsor_r - pearson) < 0.5

    top_decile_rows, _bottom_decile_rows, top_decile, bottom_decile = _decile_split(agg, primary)
    decile_pass = top_decile > bottom_decile

    cost_pass = decile_pass
    if cfg.get("require_oos_net_profit_positive", True):
        if not all(metric_value(r.get("oos_metrics", {}), "net_return") > 0 for r in top_decile_rows):
            cost_pass = False
            reasons.append("Top IS decile OOS net return not uniformly positive")
    if cfg.get("require_oos_risk_adjusted_positive", True):
        if not all(metric_value(r.get("oos_metrics", {}), primary) > 0 for r in top_decile_rows):
            cost_pass = False
            reasons.append("Top IS decile OOS risk-adjusted metric not positive")

    dd_limit = float(cfg.get("max_oos_drawdown_limit", -500.0))
    secondary = cfg.get("secondary_metrics") or []
    dd_metric = (
        "max_drawdown_adj_return"
        if "max_drawdown_adj_return" in secondary
        else "max_drawdown"
    )
    drawdown_pass = all(
        metric_value(r.get("oos_metrics", {}), dd_metric) >= dd_limit for r in top_decile_rows
    )
    if not drawdown_pass:
        reasons.append(f"Top decile OOS {dd_metric} below limit {dd_limit}")

    secondary_ok, secondary_reasons = _secondary_metrics_pass(top_decile_rows, cfg)
    reasons.extend(secondary_reasons)

    cost_adj_vectors = _per_parameter_fold_vectors(rows, "net_return_adjusted")
    cost_adj_pearson = _median_correlation(cost_adj_vectors, lambda a, b: stats.pearsonr(a, b)[0])
    cost_adj_spearman = _median_correlation(cost_adj_vectors, lambda a, b: stats.spearmanr(a, b)[0])
    cost_adj_corr_pass = True
    if cfg.get("require_cost_adjusted_correlation", False):
        pearson_min = float(cfg.get("pearson_min", 0.20))
        spearman_min = float(cfg.get("spearman_min", 0.20))
        cost_adj_corr_pass = cost_adj_pearson >= pearson_min and cost_adj_spearman >= spearman_min
        if not cost_adj_corr_pass:
            reasons.append(
                f"Cost-adjusted correlation below threshold (r={cost_adj_pearson:.3f}, rho={cost_adj_spearman:.3f})"
            )

    regime_pass = _regime_consistency(rows, primary)
    if not regime_pass:
        reasons.append("Regime fold correlations disagree in sign")

    universe_pass = _universe_consistency(rows, primary)
    if not universe_pass:
        reasons.append("Universe/asset fold correlations disagree in sign")

    min_trades = int((cfg.get("min_oos_trade_count") or {}).get("default", 30))
    low_trades = sum(
        1 for r in rows if metric_value(r.get("oos_metrics", {}), "trade_count") < min_trades
    )
    if low_trades > len(rows) * 0.5:
        reasons.append(f"Too many rows with OOS trade_count < {min_trades}")

    pearson_min = float(cfg.get("pearson_min", 0.20))
    spearman_min = float(cfg.get("spearman_min", 0.20))
    p_max = float(cfg.get("correlation_p_value_max", 0.10))
    min_pos_ratio = float(cfg.get("min_positive_fold_ratio", 0.60))

    pass_core = (
        pearson >= pearson_min
        and spearman >= spearman_min
        and p_value <= p_max
        and positive_ratio >= min_pos_ratio
        and outlier_pass
        and decile_pass
        and cost_pass
        and drawdown_pass
        and secondary_ok
        and cost_adj_corr_pass
        and regime_pass
        and universe_pass
        and n_folds >= min_folds
    )

    weak_core = (
        pearson > 0
        and spearman > 0
        and p_value <= 0.20
        and positive_ratio >= 0.40
        and decile_pass
    )

    if pass_core:
        status = "PASS"
    elif weak_core and not any(
        r.startswith(
            (
                "Top decile",
                "Cost-adjusted",
                "Outlier",
                "Regime",
                "Universe",
                "Too many rows",
                "Insufficient",
            )
        )
        for r in reasons
    ):
        status = "CONDITIONAL"
        reasons.append("Weak but non-random positive IS-OOS correlation")
    else:
        status = "FAIL"
        if pearson < pearson_min:
            reasons.append(f"Pearson {pearson:.3f} < {pearson_min}")
        if spearman < spearman_min:
            reasons.append(f"Spearman {spearman:.3f} < {spearman_min}")
        if p_value > p_max:
            reasons.append(f"p-value {p_value:.3f} > {p_max}")
        if positive_ratio < min_pos_ratio:
            reasons.append(f"positive_fold_ratio {positive_ratio:.2f} < {min_pos_ratio}")
        if not outlier_pass:
            reasons.append("Outlier-sensitive correlation (winsorized/trimmed check failed)")
        if not decile_pass:
            reasons.append("Top IS decile OOS median not above bottom decile")
        if not cost_pass:
            reasons.append("Cost-adjusted OOS requirements failed")

    return WfcResult(
        run_id=run_id,
        model_id=model_id,
        strategy_id=strategy_id or model_id,
        wfc_status=status,
        pearson=pearson,
        spearman=spearman,
        kendall=kendall,
        pearson_ci=pearson_ci,
        spearman_ci=spearman_ci,
        p_value=p_value,
        cost_adjusted_pearson=cost_adj_pearson,
        cost_adjusted_spearman=cost_adj_spearman,
        n_parameter_combinations=n_combos,
        n_folds=n_folds,
        positive_fold_ratio=positive_ratio,
        fold_correlations=fold_corrs,
        top_decile_oos_median=top_decile,
        bottom_decile_oos_median=bottom_decile,
        outlier_sensitivity_pass=outlier_pass,
        cost_adjusted_pass=cost_pass and cost_adj_corr_pass,
        drawdown_pass=drawdown_pass,
        secondary_metrics_pass=secondary_ok,
        regime_consistency_pass=regime_pass,
        universe_consistency_pass=universe_pass,
        rejection_reasons=reasons,
    )
