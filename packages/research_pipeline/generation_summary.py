"""Validated generation outcome aggregation for autoresearch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from research_pipeline.generation_gate_chain import FINAL_PASS

SCORE_WEIGHTS = {
    "oos_expectancy": 1.0,
    "drawdown_penalty": 0.5,
    "instability_penalty": 0.25,
    "execution_cost_penalty": 0.25,
}


def _walk_forward_config_path(repo_root: Path) -> Path | None:
    for candidate in (
        repo_root / "apps" / "workbench" / "config" / "walk_forward.yaml",
        repo_root / "workbench" / "config" / "walk_forward.yaml",
    ):
        if candidate.is_file():
            return candidate
    return None


def _holdout_period_names(repo_root: Path) -> set[str]:
    cfg_path = _walk_forward_config_path(repo_root)
    if cfg_path is None:
        return set()
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return set()
    return set(cfg.get("holdout_evaluate_only") or [])


def _metrics_exclude_holdout(metrics: Mapping[str, Any], holdout_names: set[str]) -> dict[str, Any]:
    if not holdout_names:
        return dict(metrics)
    out: dict[str, Any] = {}
    for key, value in metrics.items():
        if key in holdout_names:
            continue
        if isinstance(value, Mapping) and value.get("evaluate_only") is True:
            continue
        out[key] = value
    return out


def _composite_score(row: Mapping[str, Any], *, holdout_names: set[str] | None = None) -> float:
    metrics = row.get("metrics") or {}
    holdout = holdout_names or set()
    discovery_metrics = _metrics_exclude_holdout(metrics, holdout) if holdout else dict(metrics)
    disc = discovery_metrics.get("discovery")
    if isinstance(disc, Mapping):
        oos = float(disc.get("oos_expectancy") or disc.get("net_return") or 0.0)
    else:
        oos = float(discovery_metrics.get("oos_expectancy") or discovery_metrics.get("net_return") or 0.0)
    dd = abs(float(discovery_metrics.get("max_drawdown_pct") or 0.0))
    instability = float(discovery_metrics.get("instability_penalty") or 0.0)
    exec_cost = float(discovery_metrics.get("execution_cost_penalty") or 0.0)
    return (
        SCORE_WEIGHTS["oos_expectancy"] * oos
        - SCORE_WEIGHTS["drawdown_penalty"] * dd
        - SCORE_WEIGHTS["instability_penalty"] * instability
        - SCORE_WEIGHTS["execution_cost_penalty"] * exec_cost
    )


def _row_from_promoted(promoted: Mapping[str, Any], *, vectorbt_pass: bool = True) -> dict[str, Any]:
    vbt = promoted.get("vectorbt_results") if isinstance(promoted.get("vectorbt_results"), Mapping) else {}
    metrics = dict(vbt) if vbt else {}
    row = {
        "candidate_id": str(promoted.get("candidate_id") or ""),
        "model_id": str(promoted.get("hypothesis_id") or promoted.get("model_id") or ""),
        "strategy_params": dict(promoted.get("param_values") or promoted.get("strategy_params") or {}),
        "feature_recipe_hash": promoted.get("feature_recipe_hash") or metrics.get("feature_recipe_hash"),
        "feature_recipe": promoted.get("feature_recipe") or metrics.get("feature_recipe"),
        "research_clock": promoted.get("research_clock") or metrics.get("research_clock"),
        "vectorbt_pass": vectorbt_pass,
        "robustness_pass": promoted.get("robustness_pass"),
        "hft_replay_status": promoted.get("hft_replay_status"),
        "metrics": metrics,
    }
    return row


def validate_generation_artifacts(
    *,
    gen_dir: Path,
    screening_path: Path | None,
) -> list[str]:
    reasons: list[str] = []
    if screening_path is None or not screening_path.is_file():
        reasons.append("screening_artifact_missing")
        return reasons
    try:
        screening = json.loads(screening_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        reasons.append("screening_artifact_unreadable")
        return reasons
    marker = gen_dir / ".generation_complete"
    if not marker.is_file():
        reasons.append("generation_complete_marker_missing")
    return reasons


def build_generation_summary(
    *,
    repo_root: Path,
    campaign_id: str,
    generation_index: int,
    screening_artifact: Mapping[str, Any],
    robustness_results: list[Mapping[str, Any]] | None = None,
    hft_campaign_summary: Mapping[str, Any] | None = None,
    gate_chain_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    holdout_names = _holdout_period_names(repo_root)
    robustness_by_id = {
        str(r.get("candidate_id")): r for r in (robustness_results or []) if r.get("candidate_id")
    }
    gate_chains = dict(gate_chain_by_id or {})
    hft_status = str((hft_campaign_summary or {}).get("status") or "not_run")

    rows: list[dict[str, Any]] = []
    for promoted in screening_artifact.get("promoted") or []:
        if not isinstance(promoted, Mapping):
            continue
        row = _row_from_promoted(promoted)
        cid = row["candidate_id"]
        rob = robustness_by_id.get(cid)
        chain = gate_chains.get(cid) or {}
        if rob:
            regular_wf_pass = rob.get("regular_walk_forward_pass") is True
            wfc_pass = rob.get("wfc_pass") is True
            row["robustness_pass"] = regular_wf_pass and wfc_pass
            row["regular_walk_forward_pass"] = regular_wf_pass
            row["wfc_pass"] = wfc_pass
            row["metrics"].update(_metrics_exclude_holdout(dict(rob.get("metrics") or {}), holdout_names))
        row["metrics"] = _metrics_exclude_holdout(row["metrics"], holdout_names)
        hft_outcome = next(
            (
                o
                for o in (chain.get("gate_outcomes") or [])
                if str(o.get("gate_id") or "") == "hftbacktest_gate"
            ),
            None,
        )
        row["hft_replay_status"] = str(
            (hft_outcome or {}).get("effective_status") or "not_run"
        ).lower()
        row["composite_score"] = _composite_score(row, holdout_names=holdout_names)
        row["final_status"] = chain.get("final_status")
        row["gate_chain_final_pass"] = chain.get("final_status") == FINAL_PASS
        row["elite"] = chain.get("final_status") == FINAL_PASS
        rows.append(row)

    gate_passers = [r for r in rows if r.get("final_status") == FINAL_PASS]
    best = max(gate_passers, key=lambda r: r.get("composite_score", float("-inf")), default=None)
    return {
        "campaign_id": campaign_id,
        "generation_index": generation_index,
        "score_weights": dict(SCORE_WEIGHTS),
        "holdout_periods_excluded": sorted(holdout_names),
        "candidates": rows,
        "best_candidate_id": best.get("candidate_id") if best else None,
        "best_composite_score": best.get("composite_score") if best else None,
        "screening_artifact_hash": screening_artifact.get("screening_artifact_hash"),
        "hft_campaign_status": hft_status,
    }


def write_generation_summary(path: Path, summary: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(summary), indent=2) + "\n", encoding="utf-8")
    return path
