"""Double walk-forward correlation: WF1 ↔ WF2 agreement (Phase 10).

The Phase 10 spec requires comparing the parameter/result matrix from
the first walk-forward test against the matrix from the second walk-forward
test. The goal is to identify model configurations and parameter regions
whose performance remains correlated across walk-forward periods.

This module provides:
  - `DoubleWfResult` dataclass with the spec's required fields
  - `evaluate_double_wf()` function that computes cross-WF correlation
  - `to_gate_result()` helper that emits a Phase 8 `GateResult`

The double-WF correlator is **additive** to the existing single-WF
infrastructure (`evaluate_wfc_gate()` in `gate.py`). It does not
modify `WfcResult`. The campaign runner orchestrates both: single-WF
first, then double-WF if single-WF passes.

Strategy for deriving WF1 and WF2 from the existing infrastructure:
  - WF1 = early folds (e.g. D1 + D2)
  - WF2 = late folds (e.g. D3)
  - Split `matrix_rows` by `fold_id`, aggregate by `parameter_hash`,
    compute correlation between WF1 OOS and WF2 OOS vectors.

The config (`wfc_gate.yaml`) should specify `double_wf.wf1_fold_ids`
and `double_wf.wf2_fold_ids` to allow user-defined splits.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
from scipy import stats


@dataclass
class DoubleWfResult:
    """Result of the double-WF correlation (Phase 10 spec)."""

    wf1_matrix_path: str = ""
    wf2_matrix_path: str = ""
    matrix_join_keys: List[str] = field(default_factory=list)
    correlation_method: str = "spearman"
    correlation_score: float = 0.0
    minimum_required_score: float = 0.20
    pass_fail: bool = False
    eligible_parameter_regions: List[Dict[str, Any]] = field(default_factory=list)
    rejected_parameter_regions: List[Dict[str, Any]] = field(default_factory=list)
    stability_summary: Dict[str, Any] = field(default_factory=dict)
    artifact_reference: str = ""
    rejection_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "DoubleWfResult":
        return cls(
            wf1_matrix_path=str(raw.get("wf1_matrix_path", "")),
            wf2_matrix_path=str(raw.get("wf2_matrix_path", "")),
            matrix_join_keys=list(raw.get("matrix_join_keys", [])),
            correlation_method=str(raw.get("correlation_method", "spearman")),
            correlation_score=float(raw.get("correlation_score", 0.0)),
            minimum_required_score=float(raw.get("minimum_required_score", 0.20)),
            pass_fail=bool(raw.get("pass_fail", False)),
            eligible_parameter_regions=list(raw.get("eligible_parameter_regions", [])),
            rejected_parameter_regions=list(raw.get("rejected_parameter_regions", [])),
            stability_summary=dict(raw.get("stability_summary", {})),
            artifact_reference=str(raw.get("artifact_reference", "")),
            rejection_reasons=list(raw.get("rejection_reasons", [])),
        )


# Correlation functions: Pearson, Spearman, Kendall
_CORR_FNS = {
    "pearson": lambda a, b: float(stats.pearsonr(a, b)[0]),
    "spearman": lambda a, b: float(stats.spearmanr(a, b)[0]),
    "kendall": lambda a, b: float(stats.kendalltau(a, b)[0]),
}


def _aggregate_oos_by_param(
    rows: List[Dict[str, Any]], primary: str
) -> Dict[str, float]:
    """Map parameter_hash → mean OOS primary metric across folds."""
    buckets: Dict[str, List[float]] = {}
    for r in rows:
        ph = str(r.get("parameter_hash", ""))
        if not ph:
            continue
        oos_metrics = r.get("oos_metrics", {})
        if not isinstance(oos_metrics, dict):
            continue
        val = oos_metrics.get(primary)
        if val is None:
            continue
        try:
            buckets.setdefault(ph, []).append(float(val))
        except (TypeError, ValueError):
            continue
    return {ph: float(np.mean(vs)) for ph, vs in buckets.items() if vs}


def evaluate_double_wf(
    wf1_matrix: List[Dict[str, Any]],
    wf2_matrix: List[Dict[str, Any]],
    join_keys: List[str],
    method: str = "spearman",
    min_score: float = 0.20,
    *,
    primary_metric: str = "sharpe",
    wf1_path: str = "",
    wf2_path: str = "",
) -> DoubleWfResult:
    """Compute cross-WF correlation between WF1 and WF2 parameter/result matrices.

    Args:
        wf1_matrix: List of matrix rows from WF1 (each row has `parameter_hash`
                    and `oos_metrics` dict).
        wf2_matrix: List of matrix rows from WF2.
        join_keys: Parameter keys used to join WF1 and WF2 (default: `parameter_hash`).
        method: Correlation method (`pearson`, `spearman`, or `kendall`).
        min_score: Minimum required correlation score.
        primary_metric: The OOS metric to correlate (default: `sharpe`).
        wf1_path: Path to WF1 matrix artifact (for the result).
        wf2_path: Path to WF2 matrix artifact (for the result).

    Returns:
        DoubleWfResult with correlation score, pass/fail, eligible/rejected
        parameter regions, and stability summary.
    """
    reasons: List[str] = []
    corr_fn = _CORR_FNS.get(method)
    if corr_fn is None:
        return DoubleWfResult(
            wf1_matrix_path=wf1_path,
            wf2_matrix_path=wf2_path,
            matrix_join_keys=join_keys,
            correlation_method=method,
            minimum_required_score=min_score,
            rejection_reasons=[f"Unknown correlation method: {method}"],
        )

    wf1_agg = _aggregate_oos_by_param(wf1_matrix, primary_metric)
    wf2_agg = _aggregate_oos_by_param(wf2_matrix, primary_metric)

    shared = sorted(set(wf1_agg) & set(wf2_agg))
    if len(shared) < 3:
        return DoubleWfResult(
            wf1_matrix_path=wf1_path,
            wf2_matrix_path=wf2_path,
            matrix_join_keys=join_keys,
            correlation_method=method,
            minimum_required_score=min_score,
            rejection_reasons=[
                f"Insufficient shared parameters: {len(shared)} < 3"
            ],
        )

    v1 = [wf1_agg[ph] for ph in shared]
    v2 = [wf2_agg[ph] for ph in shared]

    if np.std(v1) < 1e-12 or np.std(v2) < 1e-12:
        return DoubleWfResult(
            wf1_matrix_path=wf1_path,
            wf2_matrix_path=wf2_path,
            matrix_join_keys=join_keys,
            correlation_method=method,
            minimum_required_score=min_score,
            rejection_reasons=["Zero variance in WF1 or WF2 aggregated metrics"],
        )

    score = corr_fn(v1, v2)
    passed = score >= min_score
    if not passed:
        reasons.append(
            f"Double-WF {method} {score:.3f} < {min_score}"
        )

    median1 = float(np.median(v1))
    median2 = float(np.median(v2))
    eligible: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for ph in shared:
        w1, w2 = wf1_agg[ph], wf2_agg[ph]
        if w1 >= median1 and w2 >= median2:
            eligible.append({"parameter_hash": ph, "wf1_oos": w1, "wf2_oos": w2})
        else:
            rejected.append({"parameter_hash": ph, "wf1_oos": w1, "wf2_oos": w2})

    return DoubleWfResult(
        wf1_matrix_path=wf1_path,
        wf2_matrix_path=wf2_path,
        matrix_join_keys=join_keys,
        correlation_method=method,
        correlation_score=score,
        minimum_required_score=min_score,
        pass_fail=passed,
        eligible_parameter_regions=eligible,
        rejected_parameter_regions=rejected,
        stability_summary={
            "n_shared": len(shared),
            "n_eligible": len(eligible),
            "n_rejected": len(rejected),
            "wf1_median": median1,
            "wf2_median": median2,
        },
        artifact_reference="walk_forward_correlation.json",
        rejection_reasons=reasons,
    )


def to_gate_result(result: DoubleWfResult) -> Any:
    """Convert a DoubleWfResult to a Phase 8 GateResult.

    The gate is BLOCKING iff the double-WF correlation fails (score < min).
    """
    from hft3.validation.gate_result import (
        GateCategory, GateResult, Severity,
    )
    blocking = not result.pass_fail
    severity = Severity.BLOCKING if blocking else Severity.INFO
    return GateResult(
        gate_name="double_wf_correlation",
        gate_category=GateCategory.WALK_FORWARD_CORRELATION,
        metric_name=result.correlation_method,
        threshold=result.minimum_required_score,
        observed_value=result.correlation_score,
        comparison_operator=">=",
        pass_fail=result.pass_fail,
        severity=severity,
        blocking_status=blocking,
        reason_code=(
            "DOUBLE_WF_CORRELATION_PASS"
            if result.pass_fail
            else "DOUBLE_WF_CORRELATION_BELOW_MIN"
        ),
        artifact_reference=result.artifact_reference,
    )
