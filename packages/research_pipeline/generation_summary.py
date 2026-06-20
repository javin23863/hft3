"""Validated generation outcome aggregation for autoresearch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from research_pipeline.generation_gate_chain import (
    FINAL_BLOCKED_MISSING,
    FINAL_HFT_REJECTED,
    FINAL_INFRASTRUCTURE_FAILED,
    FINAL_ONTOLOGY_REJECTED,
    FINAL_PASS,
    FINAL_REGULAR_WF_REJECTED,
    FINAL_STATISTICAL_REJECTED,
    FINAL_SURFACE_REJECTED,
    FINAL_VECTORBT_REJECTED,
    FINAL_WFC_REJECTED,
    GATE_CHAIN_ORDER,
    validate_gate_receipt_schema,
)
from research_pipeline.generation_gate_producers import gate_receipt_path

TERMINAL_FINAL_STATUSES: frozenset[str] = frozenset(
    {
        FINAL_PASS,
        FINAL_ONTOLOGY_REJECTED,
        FINAL_VECTORBT_REJECTED,
        FINAL_SURFACE_REJECTED,
        FINAL_REGULAR_WF_REJECTED,
        FINAL_WFC_REJECTED,
        FINAL_STATISTICAL_REJECTED,
        FINAL_HFT_REJECTED,
        FINAL_BLOCKED_MISSING,
        FINAL_INFRASTRUCTURE_FAILED,
    }
)

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


def _proposal_type(metadata: Mapping[str, Any] | None, manifest: Mapping[str, Any] | None) -> str:
    meta = dict(metadata or {})
    if meta.get("refinement"):
        return str(meta["refinement"])
    reason = str(meta.get("proposal_reason") or (manifest or {}).get("proposal_reason") or "generation")
    if reason.startswith("family_variant:"):
        return "family_variant"
    if reason.startswith("failure_driven:"):
        return "failure_driven"
    return reason.split(":")[0] if ":" in reason else reason


def _feature_family_mutation(metadata: Mapping[str, Any] | None) -> str | None:
    meta = dict(metadata or {})
    if meta.get("family_variant_id"):
        return str(meta["family_variant_id"])
    reason = str(meta.get("proposal_reason") or "")
    if reason.startswith("family_variant:"):
        return reason.split(":", 1)[1]
    if reason.startswith("failure_driven:"):
        return reason.split(":", 1)[1]
    return None


def _execution_parameter_mutation(
    strategy_params: Mapping[str, Any] | None,
    parent_params: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not strategy_params or not parent_params:
        return None
    changed = {
        key: strategy_params[key]
        for key in strategy_params
        if key in parent_params and strategy_params[key] != parent_params[key]
    }
    return changed or None


def _gate_statuses(chain: Mapping[str, Any] | None) -> dict[str, str]:
    if not chain:
        return {}
    return {
        str(outcome.get("gate_id") or ""): str(outcome.get("effective_status") or "NOT_RUN")
        for outcome in (chain.get("gate_outcomes") or [])
        if outcome.get("gate_id")
    }


def _gate_receipt_paths(gen_dir: Path | None, candidate_id: str) -> dict[str, str]:
    if gen_dir is None:
        return {}
    return {
        gate_id: str(gate_receipt_path(gen_dir, candidate_id, gate_id))
        for gate_id in GATE_CHAIN_ORDER
    }


def _rejection_reasons(
    chain: Mapping[str, Any] | None,
    ontology_receipt: Mapping[str, Any] | None = None,
) -> list[str]:
    reasons: list[str] = []
    if chain:
        reasons.extend(list(chain.get("failure_reasons") or []))
        for outcome in chain.get("gate_outcomes") or []:
            if outcome.get("effective_status") in ("REJECT", "BLOCKED", "NOT_RUN"):
                reasons.extend(list(outcome.get("failure_reasons") or []))
    elif ontology_receipt and ontology_receipt.get("status") != "PASS":
        reasons.extend(list(ontology_receipt.get("failure_reasons") or []))
    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        text = str(reason)
        if text and text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


def _wfc_metrics(rob: Mapping[str, Any] | None) -> dict[str, Any]:
    if not rob:
        return {}
    metrics = dict(rob.get("metrics") or {})
    campaign = dict(rob.get("campaign_summary") or {})
    wfc = dict(campaign.get("wfc") or metrics.get("wfc") or {})
    out: dict[str, Any] = {}
    if "pearson" in wfc:
        out["wfc_pearson"] = wfc.get("pearson")
    if "spearman" in wfc:
        out["wfc_spearman"] = wfc.get("spearman")
    return out


def _row_from_candidate(
    *,
    candidate_id: str,
    manifest: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
    promoted: Mapping[str, Any] | None,
    rob: Mapping[str, Any] | None,
    chain: Mapping[str, Any] | None,
    ontology_receipt: Mapping[str, Any] | None,
    gen_dir: Path | None,
    holdout_names: set[str],
    parent_params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    manifest_dict = dict(manifest or {})
    promoted_row = dict(promoted or {})
    vbt = promoted_row.get("vectorbt_results") if isinstance(promoted_row.get("vectorbt_results"), Mapping) else {}
    metrics = dict(vbt) if vbt else {}
    strategy_params = dict(
        promoted_row.get("param_values")
        or promoted_row.get("strategy_params")
        or manifest_dict.get("execution_assumptions")
        or {}
    )
    if rob:
        regular_wf_pass = rob.get("regular_walk_forward_pass") is True
        wfc_pass = rob.get("wfc_pass") is True
        metrics.update(_metrics_exclude_holdout(dict(rob.get("metrics") or {}), holdout_names))
    else:
        regular_wf_pass = None
        wfc_pass = None
    metrics = _metrics_exclude_holdout(metrics, holdout_names)

    if chain:
        final_status = chain.get("final_status")
        hft_outcome = next(
            (o for o in (chain.get("gate_outcomes") or []) if str(o.get("gate_id") or "") == "hftbacktest_gate"),
            None,
        )
        hft_replay_status = str((hft_outcome or {}).get("effective_status") or "not_run").lower()
        robustness_pass = regular_wf_pass is True and wfc_pass is True if rob else None
    elif ontology_receipt and ontology_receipt.get("status") != "PASS":
        final_status = FINAL_ONTOLOGY_REJECTED
        hft_replay_status = "not_run"
        robustness_pass = None
    else:
        final_status = None
        hft_replay_status = "not_run"
        robustness_pass = None

    row: dict[str, Any] = {
        "candidate_id": candidate_id,
        "parent_candidate_id": manifest_dict.get("parent_candidate_id") or meta.get("elite_parent"),
        "feature_recipe_hash": manifest_dict.get("feature_recipe_hash")
        or meta.get("feature_recipe_hash")
        or promoted_row.get("feature_recipe_hash"),
        "manifest_hash": manifest_dict.get("manifest_hash"),
        "proposal_type": _proposal_type(meta, manifest_dict),
        "feature_family_mutation": _feature_family_mutation(meta),
        "execution_parameter_mutation": _execution_parameter_mutation(strategy_params, parent_params),
        "gate_statuses": _gate_statuses(chain),
        "gate_receipt_paths": _gate_receipt_paths(gen_dir, candidate_id),
        "rejection_reasons": _rejection_reasons(chain, ontology_receipt),
        "model_id": str(
            promoted_row.get("hypothesis_id")
            or promoted_row.get("model_id")
            or manifest_dict.get("model_id")
            or meta.get("model_id")
            or ""
        ),
        "strategy_params": strategy_params,
        "feature_recipe": manifest_dict.get("feature_recipe") or meta.get("feature_recipe"),
        "research_clock": manifest_dict.get("research_clock") or meta.get("research_clock"),
        "vectorbt_pass": bool(promoted_row),
        "robustness_pass": robustness_pass,
        "regular_walk_forward_pass": regular_wf_pass,
        "wfc_pass": wfc_pass,
        "hft_replay_status": hft_replay_status,
        "metrics": metrics,
        "wfc_metrics": _wfc_metrics(rob),
        "final_status": final_status,
        "elite": final_status == FINAL_PASS,
    }
    row["research_score"] = _composite_score(row, holdout_names=holdout_names)
    row["composite_score"] = row["research_score"]
    return row


def load_gate_receipt(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.is_file():
        return None, ["receipt_missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, ["receipt_unreadable"]
    if not isinstance(payload, dict):
        return None, ["receipt_not_object"]
    schema_errors = validate_gate_receipt_schema(payload)
    if schema_errors:
        return None, [f"receipt_schema_invalid:{';'.join(schema_errors)}"]
    return payload, []


def gate_receipt_is_reusable(path: Path) -> bool:
    _, errors = load_gate_receipt(path)
    return not errors


def validate_generation_completion(
    *,
    gen_dir: Path,
    screening_path: Path | None,
    summary: Mapping[str, Any] | None,
    proposed_candidate_ids: list[str] | None = None,
) -> list[str]:
    """Validate all required evidence before writing `.generation_complete`."""
    reasons: list[str] = []
    if screening_path is None or not screening_path.is_file():
        reasons.append("screening_artifact_missing")
    else:
        try:
            screening = json.loads(screening_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            reasons.append("screening_artifact_unreadable")
            screening = None
        if isinstance(screening, dict):
            if not screening.get("screening_artifact_hash"):
                reasons.append("screening_artifact_hash_missing")

    summary_path = gen_dir / "generation_summary.json"
    summary_payload = dict(summary) if summary else None
    if summary_payload is None:
        if not summary_path.is_file():
            reasons.append("generation_summary_missing")
        else:
            try:
                loaded = json.loads(summary_path.read_text(encoding="utf-8"))
                summary_payload = dict(loaded) if isinstance(loaded, dict) else None
            except (OSError, json.JSONDecodeError):
                reasons.append("generation_summary_unreadable")
    if summary_payload is None and "generation_summary_missing" not in reasons:
        reasons.append("generation_summary_invalid")

    candidate_ids = list(proposed_candidate_ids or [])
    rows_by_id: dict[str, Mapping[str, Any]] = {}
    if summary_payload:
        for row in summary_payload.get("candidates") or []:
            if isinstance(row, Mapping) and row.get("candidate_id"):
                cid = str(row["candidate_id"])
                rows_by_id[cid] = row
                if cid not in candidate_ids:
                    candidate_ids.append(cid)
    if proposed_candidate_ids:
        for cid in proposed_candidate_ids:
            if cid not in rows_by_id:
                reasons.append(f"candidate_missing_from_summary:{cid}")

    for cid in candidate_ids:
        row = rows_by_id.get(cid)
        if row is None:
            continue
        final_status = row.get("final_status")
        if final_status not in TERMINAL_FINAL_STATUSES:
            reasons.append(f"candidate_non_terminal_status:{cid}:{final_status}")

        if final_status == FINAL_ONTOLOGY_REJECTED:
            _, gate_errors = load_gate_receipt(gate_receipt_path(gen_dir, cid, "ontology_gate"))
            if gate_errors:
                reasons.extend(f"{cid}:ontology:{e}" for e in gate_errors)
            continue

        for gate_id in GATE_CHAIN_ORDER:
            _, gate_errors = load_gate_receipt(gate_receipt_path(gen_dir, cid, gate_id))
            if gate_errors:
                reasons.extend(f"{cid}:{gate_id}:{e}" for e in gate_errors)

        chain_path = gate_receipt_path(gen_dir, cid, "gate_chain_result")
        if not chain_path.is_file():
            reasons.append(f"gate_chain_result_missing:{cid}")
        else:
            try:
                chain = json.loads(chain_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                reasons.append(f"gate_chain_result_unreadable:{cid}")
            else:
                if isinstance(chain, dict) and chain.get("final_status") not in TERMINAL_FINAL_STATUSES:
                    reasons.append(f"gate_chain_non_terminal:{cid}")

    frozen_path = gen_dir / "candidate_manifests.jsonl"
    if frozen_path.is_file():
        for line in frozen_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                manifest_row = json.loads(line)
            except json.JSONDecodeError:
                reasons.append("frozen_manifest_unreadable")
                break
            if isinstance(manifest_row, dict) and manifest_row.get("candidate_id"):
                cid = str(manifest_row["candidate_id"])
                if cid in rows_by_id and rows_by_id[cid].get("final_status") != FINAL_ONTOLOGY_REJECTED:
                    _, gate_errors = load_gate_receipt(gate_receipt_path(gen_dir, cid, "manifest_gate"))
                    if gate_errors:
                        reasons.extend(f"{cid}:manifest_gate:{e}" for e in gate_errors)

    return reasons


def validate_generation_artifacts(
    *,
    gen_dir: Path,
    screening_path: Path | None,
    summary: Mapping[str, Any] | None = None,
    proposed_candidate_ids: list[str] | None = None,
) -> list[str]:
    reasons = validate_generation_completion(
        gen_dir=gen_dir,
        screening_path=screening_path,
        summary=summary,
        proposed_candidate_ids=proposed_candidate_ids,
    )
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
    proposed_candidate_ids: list[str] | None = None,
    manifests_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    candidate_metadata_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    ontology_receipts_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    gen_dir: Path | None = None,
    parent_strategy_params_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    holdout_names = _holdout_period_names(repo_root)
    robustness_by_id = {
        str(r.get("candidate_id")): r for r in (robustness_results or []) if r.get("candidate_id")
    }
    gate_chains = dict(gate_chain_by_id or {})
    manifests = dict(manifests_by_id or {})
    metadata_by_id = dict(candidate_metadata_by_id or {})
    ontology_receipts = dict(ontology_receipts_by_id or {})
    parent_params_by_id = dict(parent_strategy_params_by_id or {})
    hft_status = str((hft_campaign_summary or {}).get("status") or "not_run")

    promoted_by_id = {
        str(row.get("candidate_id")): row
        for row in (screening_artifact.get("promoted") or [])
        if isinstance(row, Mapping) and row.get("candidate_id")
    }

    candidate_ids = list(proposed_candidate_ids or [])
    if not candidate_ids:
        seen: set[str] = set()
        for cid in list(manifests.keys()) + list(promoted_by_id.keys()):
            if cid not in seen:
                seen.add(cid)
                candidate_ids.append(cid)

    rows: list[dict[str, Any]] = []
    for cid in candidate_ids:
        parent_id = str(
            (manifests.get(cid) or {}).get("parent_candidate_id")
            or (metadata_by_id.get(cid) or {}).get("elite_parent")
            or ""
        )
        parent_params = parent_params_by_id.get(parent_id) if parent_id else None
        row = _row_from_candidate(
            candidate_id=cid,
            manifest=manifests.get(cid),
            metadata=metadata_by_id.get(cid),
            promoted=promoted_by_id.get(cid),
            rob=robustness_by_id.get(cid),
            chain=gate_chains.get(cid),
            ontology_receipt=ontology_receipts.get(cid),
            gen_dir=gen_dir,
            holdout_names=holdout_names,
            parent_params=parent_params,
        )
        rows.append(row)

    gate_passers = [r for r in rows if r.get("final_status") == FINAL_PASS]
    best = max(gate_passers, key=lambda r: r.get("research_score", float("-inf")), default=None)
    return {
        "campaign_id": campaign_id,
        "generation_index": generation_index,
        "score_weights": dict(SCORE_WEIGHTS),
        "holdout_periods_excluded": sorted(holdout_names),
        "candidates": rows,
        "best_candidate_id": best.get("candidate_id") if best else None,
        "best_composite_score": best.get("research_score") if best else None,
        "best_research_score": best.get("research_score") if best else None,
        "screening_artifact_hash": screening_artifact.get("screening_artifact_hash"),
        "hft_campaign_status": hft_status,
        "proposed_candidate_count": len(rows),
        "final_pass_count": len(gate_passers),
    }


def write_generation_summary(path: Path, summary: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(summary), indent=2) + "\n", encoding="utf-8")
    return path
