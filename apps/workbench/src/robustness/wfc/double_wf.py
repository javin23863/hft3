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

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
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


def _matrix_fingerprint(rows: List[Dict[str, Any]]) -> str:
    canonical_rows = sorted(
        json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
        for row in rows
    )
    encoded = json.dumps(canonical_rows, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _same_nonempty_path(wf1_path: str, wf2_path: str) -> bool:
    if not wf1_path or not wf2_path:
        return False
    return Path(wf1_path).expanduser().resolve() == Path(wf2_path).expanduser().resolve()


def _aggregate_oos_by_param(
    rows: List[Dict[str, Any]], primary: str
) -> tuple[Dict[str, float], List[str]]:
    """Map parameter_hash → mean OOS primary metric across folds."""
    buckets: Dict[str, List[float]] = {}
    reasons: List[str] = []
    for r in rows:
        ph = str(r.get("parameter_hash", ""))
        if not ph:
            continue
        oos_metrics = r.get("oos_metrics", {})
        if not isinstance(oos_metrics, dict):
            reasons.append(f"Malformed oos_metrics for parameter_hash={ph}")
            continue
        val = oos_metrics.get(primary)
        if val is None:
            continue
        try:
            observed = float(val)
        except (TypeError, ValueError):
            reasons.append(f"Malformed OOS metric for parameter_hash={ph}")
            continue
        if not math.isfinite(observed):
            reasons.append(f"Non-finite OOS metric for parameter_hash={ph}")
            continue
        buckets.setdefault(ph, []).append(observed)
    return {ph: float(np.mean(vs)) for ph, vs in buckets.items() if vs}, reasons


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
    if join_keys != ["parameter_hash"]:
        return DoubleWfResult(
            wf1_matrix_path=wf1_path,
            wf2_matrix_path=wf2_path,
            matrix_join_keys=join_keys,
            correlation_method=method,
            minimum_required_score=min_score,
            rejection_reasons=["Unsupported double-WF join_keys; expected ['parameter_hash']"],
        )
    try:
        min_score = float(min_score)
    except (TypeError, ValueError):
        return DoubleWfResult(
            wf1_matrix_path=wf1_path,
            wf2_matrix_path=wf2_path,
            matrix_join_keys=join_keys,
            correlation_method=method,
            rejection_reasons=["Malformed minimum_required_score"],
        )
    if not math.isfinite(min_score) or not 0.0 < min_score <= 1.0:
        return DoubleWfResult(
            wf1_matrix_path=wf1_path,
            wf2_matrix_path=wf2_path,
            matrix_join_keys=join_keys,
            correlation_method=method,
            minimum_required_score=min_score,
            rejection_reasons=["minimum_required_score must be finite and within (0.0, 1.0]"],
        )
    if wf1_matrix is wf2_matrix or _same_nonempty_path(wf1_path, wf2_path):
        return DoubleWfResult(
            wf1_matrix_path=wf1_path,
            wf2_matrix_path=wf2_path,
            matrix_join_keys=join_keys,
            correlation_method=method,
            minimum_required_score=min_score,
            rejection_reasons=["WF1 and WF2 matrices must be independent artifacts"],
        )
    if wf1_matrix and wf2_matrix and _matrix_fingerprint(wf1_matrix) == _matrix_fingerprint(wf2_matrix):
        return DoubleWfResult(
            wf1_matrix_path=wf1_path,
            wf2_matrix_path=wf2_path,
            matrix_join_keys=join_keys,
            correlation_method=method,
            minimum_required_score=min_score,
            rejection_reasons=["WF1 and WF2 matrix contents are identical"],
        )
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

    wf1_agg, wf1_reasons = _aggregate_oos_by_param(wf1_matrix, primary_metric)
    wf2_agg, wf2_reasons = _aggregate_oos_by_param(wf2_matrix, primary_metric)
    if wf1_reasons or wf2_reasons:
        return DoubleWfResult(
            wf1_matrix_path=wf1_path,
            wf2_matrix_path=wf2_path,
            matrix_join_keys=join_keys,
            correlation_method=method,
            minimum_required_score=min_score,
            rejection_reasons=wf1_reasons + wf2_reasons,
        )

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
    if not math.isfinite(score):
        return DoubleWfResult(
            wf1_matrix_path=wf1_path,
            wf2_matrix_path=wf2_path,
            matrix_join_keys=join_keys,
            correlation_method=method,
            minimum_required_score=min_score,
            rejection_reasons=["Double-WF correlation score is non-finite"],
        )
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
    malformed = False
    try:
        threshold = float(result.minimum_required_score)
    except (TypeError, ValueError):
        threshold = 1.0
        malformed = True
    if not math.isfinite(threshold) or not 0.0 < threshold <= 1.0:
        threshold = 1.0
        malformed = True
    try:
        observed = float(result.correlation_score)
    except (TypeError, ValueError):
        observed = -1.0
        malformed = True
    if not math.isfinite(observed):
        observed = -1.0
        malformed = True
    gate_pass = bool(result.pass_fail) and not malformed and observed >= threshold
    if not gate_pass and observed >= threshold:
        observed = threshold - 1e-12
    blocking = not gate_pass
    severity = Severity.BLOCKING if blocking else Severity.INFO
    return GateResult(
        gate_name="double_wf_correlation",
        gate_category=GateCategory.WALK_FORWARD_CORRELATION,
        metric_name=result.correlation_method,
        threshold=threshold,
        observed_value=observed,
        comparison_operator=">=",
        pass_fail=gate_pass,
        severity=severity,
        blocking_status=blocking,
        reason_code=(
            "DOUBLE_WF_CORRELATION_PASS"
            if gate_pass
            else "DOUBLE_WF_CORRELATION_BELOW_MIN"
        ),
        artifact_reference=result.artifact_reference,
    )
