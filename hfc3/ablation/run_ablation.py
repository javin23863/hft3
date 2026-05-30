"""Phase 8 — cross-asset MBO ablation harness (tests groups; does not assume alpha)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from hfc3.ablation.metrics import mbo_predictive_r2
from hfc3.events.l3_event_snapshot_tensor import build_l3_event_tensor
from hfc3.features.cross_asset_l3_event_features import build_cross_asset_l3_features
from hfc3.labels.l3_event_targets import build_l3_event_targets

ABLATION_GROUPS = {
    "baseline_a_event_only": [],
    "baseline_b_event_plus_target_mbo": ["MES", "ES"],
    "cross_equity_mbo": ["ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K"],
    "cross_rates_mbo": ["ZT", "ZF", "ZN", "ZB", "UB", "SR3", "ZQ"],
    "cross_metals_mbo": ["GC", "MGC", "HG"],
    "cross_energy_mbo": ["CL", "MCL", "NG"],
    "cross_fx_mbo": ["6E"],
    "cross_vol_sensors": [],  # sensor-only; not MBO
    "cross_full_hot": None,  # all symbols in tensor
    "cross_warm_event_triggered": ["RB", "HO", "SI", "6J", "6B", "6A", "6C", "ZC", "ZS", "ZW"],
}

DEFAULT_HORIZON_SEC = 30


@dataclass
class AblationResult:
    group: str
    feature_count: int
    target_instruments_with_labels: int
    predictive_r2: float
    verdict: str  # helped | hurt | noise | insufficient_data


def run_ablation_for_event(
    repo_root: Path,
    event_id: str,
    *,
    research_symbols: Optional[Sequence[str]] = None,
    horizon_sec: int = DEFAULT_HORIZON_SEC,
) -> List[AblationResult]:
    tensor_df = build_l3_event_tensor(repo_root, event_id, symbols=research_symbols)
    targets = build_l3_event_targets(tensor_df)

    baseline_b_r2 = mbo_predictive_r2(
        tensor_df,
        targets,
        group_canons=["MES", "ES"],
        horizon_sec=horizon_sec,
    )

    results: List[AblationResult] = []
    for group, canon_list in ABLATION_GROUPS.items():
        if group == "baseline_a_event_only":
            feats: Dict[str, float] = {}
            score = float("nan")
            verdict = "insufficient_data"
        elif group == "cross_vol_sensors":
            feats = {}
            score = float("nan")
            verdict = "insufficient_data"
        elif group == "baseline_b_event_plus_target_mbo":
            sub = tensor_df[tensor_df["canonical_symbol"].isin(["MES", "ES"])]
            feats = build_cross_asset_l3_features(sub if len(sub) else tensor_df, offset_sec=0)
            score = baseline_b_r2
            verdict = "noise"
        elif canon_list is None:
            sub = tensor_df
            feats = build_cross_asset_l3_features(sub, offset_sec=0)
            score = mbo_predictive_r2(tensor_df, targets, horizon_sec=horizon_sec)
            verdict = _verdict(score, baseline_b_r2)
        else:
            sub = tensor_df[tensor_df["canonical_symbol"].isin(canon_list)]
            feats = build_cross_asset_l3_features(sub if len(sub) else tensor_df, offset_sec=0)
            score = mbo_predictive_r2(
                tensor_df,
                targets,
                group_canons=canon_list,
                horizon_sec=horizon_sec,
            )
            verdict = _verdict(score, baseline_b_r2)

        inst_count = len(targets["canonical_symbol"].unique()) if len(targets) else 0
        results.append(AblationResult(group, len(feats), inst_count, score, verdict))
    return results


def _verdict(score: float, baseline: float) -> str:
    if score != score or baseline != baseline:
        return "insufficient_data"
    if baseline == baseline and score > baseline + 0.02:
        return "helped"
    if baseline == baseline and score < baseline - 0.02:
        return "hurt"
    return "noise"


def write_ablation_report(
    repo_root: Path,
    event_ids: Sequence[str],
) -> tuple[Path, Path]:
    all_rows: List[Dict[str, Any]] = []
    for eid in event_ids:
        for r in run_ablation_for_event(repo_root, eid):
            all_rows.append(
                {
                    "event_id": eid,
                    "group": r.group,
                    "feature_count": r.feature_count,
                    "target_instruments": r.target_instruments_with_labels,
                    "predictive_r2": r.predictive_r2,
                    "verdict": r.verdict,
                }
            )

    out_dir = repo_root / "runtime" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "hfc3_l3_cross_asset_ablation.json"
    md_path = out_dir / "hfc3_l3_cross_asset_ablation.md"

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "event_ids": list(event_ids),
        "results": all_rows,
        "metric": (
            f"Cross-sectional OLS R²: MBO snapshot @ T+0 -> forward return @ +{DEFAULT_HORIZON_SEC}s "
            f"(adaptive predictors, requires >=3 instruments with labels)"
        ),
        "note": (
            "Exploratory ablation — compares per-instrument MBO predictive R² by group; "
            "cross_asset feature dict is reported separately (feature_count), not in R²."
        ),
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# HFC3 L3 cross-asset ablation",
        "",
        f"Generated: {payload['generated_at_utc']}",
        "",
        f"Metric: `{payload['metric']}`",
        "",
        "| event_id | group | features | targets | predictive_r2 | verdict |",
        "|----------|-------|----------|---------|---------------|---------|",
    ]
    for row in all_rows:
        score = row["predictive_r2"]
        score_s = f"{score:.6f}" if score == score else "nan"
        lines.append(
            f"| {row['event_id']} | {row['group']} | {row['feature_count']} | "
            f"{row['target_instruments']} | {score_s} | {row['verdict']} |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path
