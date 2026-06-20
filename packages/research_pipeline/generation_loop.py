"""Thin multi-generation autoresearch coordinator."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from backtest_pipeline.src.hft_campaign.config import HftCampaignConfig
from backtest_pipeline.src.hft_campaign.manifest import ManifestGenerationConfig, generate_scenario_manifest
from backtest_pipeline.src.hft_campaign.runner import load_scenarios_from_manifest, run_hftbacktest_campaign
from backtest_pipeline.src.promotion_gate import PromotionGate
from backtest_pipeline.src.vectorbt_adapter import filter_candidates, persist_screening_artifact
from research_pipeline.candidate_manifest import freeze_candidate_manifest, write_frozen_manifests
from research_pipeline.elite_refinement import propose_next_candidates
from research_pipeline.generation_state import (
    GENERATION_STATUS_COMPLETE,
    GENERATION_STATUS_FAILED,
    GENERATION_STATUS_IN_PROGRESS,
    append_pointer,
    assert_config_hash_matches,
    autoresearch_campaign_dir,
    collect_semantic_config_inputs,
    compute_config_hash,
    default_manifest,
    generation_dir,
    load_frozen_manifests,
    load_manifest,
    new_campaign_id,
    register_tested_hashes,
    save_manifest,
)
from research_pipeline.generation_gate_chain import run_generation_gate_chain, passes_gates_before_hft
from research_pipeline.generation_gate_producers import (
    build_hftbacktest_gate_receipt,
    build_manifest_gate_receipt,
    build_regular_walk_forward_gate_receipt,
    build_statistical_robustness_gate_receipt,
    build_surface_stability_gate_receipt,
    build_vectorbt_gate_receipt,
    build_walk_forward_correlation_gate_receipt,
    emit_candidate_gate_receipts,
    gate_receipt_path,
    run_ontology_gate_for_candidate,
    write_gate_receipt,
)
from research_pipeline.generation_summary import (
    build_generation_summary,
    gate_receipt_is_reusable,
    load_gate_receipt,
    validate_generation_completion,
    write_generation_summary,
)
from research_pipeline.hypothesis_parser import parse_hypothesis
from research_pipeline.model_generation import generate_candidates
from research_pipeline.review_memory import append_generation_memory
from research_pipeline.types import CandidateModel, ParsedHypothesis
from research_pipeline.feature_recipe import attach_feature_recipe_to_candidate, candidate_identity_hash

FilterFn = Callable[..., Any]
PersistFn = Callable[..., Path]
RobustnessFn = Callable[..., Any]
HftFn = Callable[..., Any]


@dataclass
class AutoresearchConfig:
    max_generations: int = 3
    max_candidates_per_generation: int = 5
    robustness_max_candidates: int = 3
    exploration_fraction: float = 0.2
    hft_workers: int = 1
    stop_no_improvement_generations: int = 2
    target_score: float | None = None
    symbol: str = "MES"
    screening_scope: str = "pilot"
    run_robustness: bool = True
    run_hft_campaign: bool = False
    hft_stages: tuple[int, ...] = (0,)
    vectorbt_min_trades: int = 3
    hft_source_npz: Path | None = None
    hft_latency_model: Path | None = None
    hft_fill_queue_model: Path | None = None
    native_hot_path_evidence: list[str] | None = None
    stop_file: Path | None = None
    family_search_enabled: bool = True
    family_search_fraction: float = 0.4


def load_autoresearch_config(path: Path, *, overrides: dict[str, Any] | None = None) -> AutoresearchConfig:
    raw: dict[str, Any] = {}
    if path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if overrides:
        raw.update({k: v for k, v in overrides.items() if v is not None})
    stages = raw.get("hft_stages") or [0]
    return AutoresearchConfig(
        max_generations=int(raw.get("max_generations", 3)),
        max_candidates_per_generation=int(raw.get("max_candidates_per_generation", 5)),
        robustness_max_candidates=int(raw.get("robustness_max_candidates", 3)),
        exploration_fraction=min(1.0, max(0.0, float(raw.get("exploration_fraction", 0.2)))),
        hft_workers=int(raw.get("hft_workers", 1)),
        stop_no_improvement_generations=int(raw.get("stop_no_improvement_generations", 2)),
        target_score=raw.get("target_score"),
        symbol=str(raw.get("symbol") or "MES"),
        screening_scope=str(raw.get("screening_scope") or "pilot"),
        run_robustness=bool(raw.get("run_robustness", True)),
        run_hft_campaign=bool(raw.get("run_hft_campaign", False)),
        hft_stages=tuple(int(s) for s in stages),
        vectorbt_min_trades=int(raw.get("vectorbt_min_trades", 3)),
        hft_source_npz=Path(raw["hft_source_npz"]) if raw.get("hft_source_npz") else None,
        hft_latency_model=Path(raw["hft_latency_model"]) if raw.get("hft_latency_model") else None,
        hft_fill_queue_model=Path(raw["hft_fill_queue_model"]) if raw.get("hft_fill_queue_model") else None,
        native_hot_path_evidence=list(raw.get("native_hot_path_evidence") or []),
        stop_file=Path(raw["stop_file"]) if raw.get("stop_file") else None,
        family_search_enabled=bool(raw.get("family_search_enabled", True)),
        family_search_fraction=min(1.0, max(0.0, float(raw.get("family_search_fraction", 0.4)))),
    )


def _cfg_semantic_dict(cfg: AutoresearchConfig) -> dict[str, Any]:
    return {
        "max_candidates_per_generation": cfg.max_candidates_per_generation,
        "robustness_max_candidates": cfg.robustness_max_candidates,
        "exploration_fraction": cfg.exploration_fraction,
        "family_search_enabled": cfg.family_search_enabled,
        "family_search_fraction": cfg.family_search_fraction,
        "screening_scope": cfg.screening_scope,
        "vectorbt_min_trades": cfg.vectorbt_min_trades,
        "symbol": cfg.symbol,
        "run_robustness": cfg.run_robustness,
        "run_hft_campaign": cfg.run_hft_campaign,
        "hft_stages": list(cfg.hft_stages),
        "hft_workers": cfg.hft_workers,
        "hft_source_npz": cfg.hft_source_npz,
        "hft_latency_model": cfg.hft_latency_model,
        "hft_fill_queue_model": cfg.hft_fill_queue_model,
    }


def _campaign_config_hash(*, repo_root: Path, event_id: str, cfg: AutoresearchConfig) -> str:
    payload = collect_semantic_config_inputs(
        repo_root=repo_root,
        event_id=event_id,
        campaign_cfg=_cfg_semantic_dict(cfg),
    )
    return compute_config_hash(payload)


def _serialize_candidates(candidates: list[CandidateModel]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": c.candidate_id,
            "model_id": c.model_id,
            "strategy_params": dict(c.strategy_params or {}),
            "thesis": c.thesis,
            "metadata": dict(c.metadata or {}),
            "feature_recipe": dict(c.feature_recipe or {}) if c.feature_recipe else None,
            "feature_recipe_hash": c.feature_recipe_hash,
        }
        for c in candidates
    ]


def _load_candidates_from_checkpoint(gen_dir: Path, proposed_ids: list[str]) -> list[CandidateModel]:
    snapshot_path = gen_dir / "proposed_candidates.json"
    if not snapshot_path.is_file():
        return []
    try:
        rows = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(rows, list):
        return []
    allowed = set(proposed_ids)
    out: list[CandidateModel] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("candidate_id"):
            continue
        if allowed and str(row["candidate_id"]) not in allowed:
            continue
        out.append(
            CandidateModel(
                candidate_id=str(row["candidate_id"]),
                model_id=str(row.get("model_id") or ""),
                strategy_params=dict(row.get("strategy_params") or {}),
                thesis=str(row.get("thesis") or ""),
                metadata=dict(row.get("metadata") or {}),
                feature_recipe=dict(row["feature_recipe"]) if row.get("feature_recipe") else None,
                feature_recipe_hash=row.get("feature_recipe_hash"),
            )
        )
    return out


def _write_generation_checkpoint(
    gen_dir: Path,
    *,
    proposed_candidate_ids: list[str],
    pipeline_run_id: str,
    generation_index: int,
    candidates: list[CandidateModel] | None = None,
) -> Path:
    path = gen_dir / "generation_checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "proposed_candidate_ids": proposed_candidate_ids,
                "pipeline_run_id": pipeline_run_id,
                "generation_index": generation_index,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if candidates:
        (gen_dir / "proposed_candidates.json").write_text(
            json.dumps(_serialize_candidates(candidates), indent=2) + "\n",
            encoding="utf-8",
        )
    return path


def _load_reusable_receipt(path: Path) -> dict[str, Any] | None:
    receipt, errors = load_gate_receipt(path)
    return receipt if not errors else None


def _try_resume_completed_generation(
    *,
    repo_root: Path,
    campaign_id: str,
    generation_index: int,
) -> dict[str, Any] | None:
    gen_dir = generation_dir(repo_root, campaign_id, generation_index)
    summary_path = gen_dir / "generation_summary.json"
    if not summary_path.is_file():
        return None
    checkpoint_path = gen_dir / "generation_checkpoint.json"
    proposed_ids: list[str] | None = None
    screening_path: Path | None = None
    if checkpoint_path.is_file():
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            proposed_ids = list(checkpoint.get("proposed_candidate_ids") or [])
            pipeline_run_id = str(checkpoint.get("pipeline_run_id") or "")
            if pipeline_run_id:
                candidate_screening = gen_dir / pipeline_run_id / "screening_artifact.json"
                if candidate_screening.is_file():
                    screening_path = candidate_screening
        except (OSError, json.JSONDecodeError):
            proposed_ids = None
    if screening_path is None:
        for path_str in _manifest_screening_paths_for_generation(repo_root, campaign_id, generation_index):
            candidate = Path(path_str)
            if candidate.is_file():
                screening_path = candidate
                break
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    validation = validate_generation_completion(
        gen_dir=gen_dir,
        screening_path=screening_path,
        summary=summary,
        proposed_candidate_ids=proposed_ids,
    )
    if validation:
        return None
    if not (gen_dir / ".generation_complete").is_file():
        (gen_dir / ".generation_complete").write_text("ok\n", encoding="utf-8")
    return summary


def _manifest_screening_paths_for_generation(
    repo_root: Path,
    campaign_id: str,
    generation_index: int,
) -> list[str]:
    try:
        manifest = load_manifest(repo_root, campaign_id)
    except (FileNotFoundError, ValueError):
        return []
    gen_prefix = f"generation_{generation_index:03d}"
    return [
        str(path)
        for path in (manifest.get("screening_artifact_paths") or [])
        if gen_prefix in str(path)
    ]


def _pipeline_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"pipeline_{ts}_{uuid.uuid4().hex[:8]}"


def _candidate_hashes(candidates: list[CandidateModel]) -> list[str]:
    return [candidate_identity_hash(c) for c in candidates]


def make_default_robustness_fn(*, chi404_summary: Path | None = None) -> RobustnessFn:
    """Return a callable that runs workbench robustness via existing run_campaign."""

    def _run(
        *,
        repo_root: Path,
        model_id: str,
        symbol: str,
        campaign_id: str,
        param_values: dict[str, Any] | None = None,
        screening_metrics: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        from workbench.src.run.campaign_runner import run_campaign

        screened = dict(screening_metrics or {})
        params = dict(param_values or {})
        result = run_campaign(
            repo_root,
            model_id,
            symbol,
            chi404_summary=chi404_summary,
            campaign_id=campaign_id,
            allow_partial=False,
            dry_run=False,
            frozen_strategy_params=params or None,
        )
        summary_path = Path(result.artifact_dir) / "summary.json"
        metrics: dict[str, Any] = dict(screened)
        if params:
            metrics["screened_param_values"] = params
        campaign_summary: dict[str, Any] | None = None
        regular_walk_forward_pass = False
        wfc_pass = False
        if summary_path.is_file():
            campaign_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            campaign_summary["artifact_dir"] = str(result.artifact_dir)
            wfc_metrics = dict(campaign_summary.get("metrics") or {})
            for key, value in wfc_metrics.items():
                if key not in metrics:
                    metrics[key] = value
            regular_walk_forward_pass = str(campaign_summary.get("status") or "") == "PASS"
            wfc_pass = str(campaign_summary.get("wfc_status") or "") == "PASS"
        strict_robustness_pass = regular_walk_forward_pass and wfc_pass
        return {
            "robustness_pass": strict_robustness_pass,
            "regular_walk_forward_pass": regular_walk_forward_pass,
            "wfc_pass": wfc_pass,
            "metrics": metrics,
            "campaign_id": result.campaign_id,
            "campaign_summary": campaign_summary,
            "artifact_dir": str(result.artifact_dir),
        }

    return _run


def _run_vectorbt_screen(
    *,
    candidates: list[CandidateModel],
    parsed: ParsedHypothesis,
    event_id: str,
    repo_root: Path,
    cfg: AutoresearchConfig,
    artifact_dir: Path,
    filter_fn: FilterFn = filter_candidates,
    persist_fn: PersistFn = persist_screening_artifact,
) -> tuple[dict[str, Any], Path]:
    from data_system.src.feature_store import feature_store_root

    gates = PromotionGate(min_oos_expectancy=0.0, max_drawdown_pct=-50.0, min_trades=cfg.vectorbt_min_trades)
    result = filter_fn(
        candidates=candidates,
        parsed=parsed,
        event_id=event_id,
        repo_root=repo_root,
        gates=gates,
        screening_scope=cfg.screening_scope,
        feature_store_root=feature_store_root(repo_root),
        symbol=cfg.symbol,
    )
    artifact = result.to_dict()
    screening_path = persist_fn(artifact, artifact_dir / "screening_artifact.json")
    screening = json.loads(screening_path.read_text(encoding="utf-8"))
    (artifact_dir / "vectorbt_filter.json").write_text(json.dumps(screening, indent=2) + "\n", encoding="utf-8")
    return screening, screening_path


def _run_robustness_top_k(
    *,
    repo_root: Path,
    screening: dict[str, Any],
    cfg: AutoresearchConfig,
    generation_index: int,
    campaign_id: str,
    robustness_fn: RobustnessFn | None = None,
) -> list[dict[str, Any]]:
    if not cfg.run_robustness:
        return []
    promoted = list(screening.get("promoted") or [])[: cfg.robustness_max_candidates]
    if not promoted:
        return []
    results: list[dict[str, Any]] = []
    if robustness_fn is None:
        return results
    for row in promoted:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("candidate_id") or "")
        model_id = str(row.get("hypothesis_id") or row.get("model_id") or "")
        if not cid or not model_id:
            continue
        outcome = robustness_fn(
            repo_root=repo_root,
            model_id=model_id,
            symbol=cfg.symbol,
            campaign_id=f"{campaign_id}_g{generation_index}_rob_{cid[:8]}",
            param_values=dict(row.get("param_values") or row.get("strategy_params") or {}),
            screening_metrics=dict(row.get("vectorbt_results") or {}),
        )
        results.append(
            {
                "candidate_id": cid,
                "robustness_pass": outcome.get("robustness_pass"),
                "regular_walk_forward_pass": outcome.get("regular_walk_forward_pass"),
                "wfc_pass": outcome.get("wfc_pass"),
                "metrics": dict(outcome.get("metrics") or {}),
                "campaign_id": outcome.get("campaign_id"),
                "campaign_summary": outcome.get("campaign_summary"),
                "artifact_dir": outcome.get("artifact_dir"),
            }
        )
    return results


def _scenario_results_by_candidate(
    scenario_results: list[Any],
    scenarios: list[Any],
) -> dict[str, list[dict[str, Any]]]:
    by_scenario_id = {s.scenario_id: s for s in scenarios}
    by_cid: dict[str, list[dict[str, Any]]] = {}
    for result in scenario_results:
        scenario = by_scenario_id.get(getattr(result, "scenario_id", ""))
        cid = getattr(scenario, "candidate_id", "unknown") if scenario else "unknown"
        by_cid.setdefault(cid, []).append(
            {
                "scenario_id": getattr(result, "scenario_id", ""),
                "status": getattr(result, "status", ""),
                "replay_result": dict(getattr(result, "replay_result", None) or {}),
                "artifact_dir": getattr(result, "artifact_dir", None),
            }
        )
    return by_cid


def _load_hft_artifact_json(artifact_dir: Path | None, filename: str) -> dict[str, Any]:
    if artifact_dir is None or not artifact_dir.is_dir():
        return {}
    path = artifact_dir / filename
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _enrich_hft_scenario_results(
    scenario_results: Sequence[Mapping[str, Any]] | None,
    *,
    manifest: Mapping[str, Any],
    screening_artifact_hash: str,
    robustness_artifact_hash: str,
) -> list[dict[str, Any]]:
    """Hydrate replay artifacts and inject upstream identity for Gate 7 parity."""
    enriched: list[dict[str, Any]] = []
    for result in list(scenario_results or []):
        row = dict(result)
        status = str(row.get("status") or "")
        if status == "cached":
            row["status"] = "completed"
        artifact_dir = Path(str(row.get("artifact_dir") or "")) if row.get("artifact_dir") else None
        replay = dict(row.get("replay_result") or {})
        if artifact_dir is not None and artifact_dir.is_dir():
            if not replay:
                replay = _load_hft_artifact_json(artifact_dir, "replay_result.json")
            summary = _load_hft_artifact_json(artifact_dir, "replay_summary.json")
            if summary.get("certification_status") and not replay.get("certification_status"):
                replay["certification_status"] = summary["certification_status"]
            for key in ("candidate_id", "feature_recipe_hash"):
                if summary.get(key) and not replay.get(key):
                    replay[key] = summary[key]
        replay.setdefault("candidate_id", str(manifest.get("candidate_id") or ""))
        replay.setdefault("manifest_hash", str(manifest.get("manifest_hash") or ""))
        replay.setdefault("feature_recipe_hash", str(manifest.get("feature_recipe_hash") or ""))
        if screening_artifact_hash:
            replay.setdefault("screening_artifact_hash", screening_artifact_hash)
        if robustness_artifact_hash:
            replay.setdefault("robustness_artifact_hash", robustness_artifact_hash)
        row["replay_result"] = replay
        enriched.append(row)
    return enriched


def _robustness_artifact_hash(rob: Mapping[str, Any] | None) -> str:
    if not rob:
        return ""
    artifact_dir = rob.get("artifact_dir")
    if not artifact_dir:
        return ""
    summary_path = Path(str(artifact_dir)) / "summary.json"
    if not summary_path.is_file():
        return ""
    try:
        from backtest_pipeline.src.hft_campaign._hashing import sha256_hex

        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        return sha256_hex(payload)
    except Exception:
        return ""


def _run_hft_for_candidates(
    *,
    repo_root: Path,
    event_id: str,
    screening_path: Path,
    cfg: AutoresearchConfig,
    campaign_id: str,
    generation_index: int,
    candidate_ids: list[str],
    hft_fn: HftFn | None = None,
) -> tuple[dict[str, Any] | None, dict[str, list[dict[str, Any]]]]:
    """Run HftBacktest only for candidates that passed gates 0–6."""
    if not cfg.run_hft_campaign or not candidate_ids:
        return None, {}
    missing = [
        name
        for name, value in (
            ("hft_source_npz", cfg.hft_source_npz),
            ("hft_latency_model", cfg.hft_latency_model),
            ("hft_fill_queue_model", cfg.hft_fill_queue_model),
        )
        if value is None or not Path(value).is_file()
    ]
    if missing:
        return {"status": "blocked", "missing_inputs": missing}, {}
    manifest_cfg = ManifestGenerationConfig(
        screening_artifact_path=screening_path,
        repo_root=repo_root,
        event_id=event_id,
        source_npz_path=Path(cfg.hft_source_npz),
        latency_model_path=Path(cfg.hft_latency_model),
        fill_queue_model_path=Path(cfg.hft_fill_queue_model),
        candidate_ids=tuple(candidate_ids),
        select_all_replay_eligible=False,
    )
    scenarios, reasons = generate_scenario_manifest(manifest_cfg)
    if reasons:
        return {"status": "fail", "manifest_reasons": reasons}, {}
    hft_campaign_id = f"{campaign_id}_g{generation_index}_hft"
    out_dir = autoresearch_campaign_dir(repo_root, campaign_id) / f"generation_{generation_index:03d}" / "hft_campaign"
    manifest_path = out_dir / "scenario_manifest.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"scenarios": [s.to_dict() for s in scenarios]}, indent=2) + "\n",
        encoding="utf-8",
    )
    hft_cfg = HftCampaignConfig(
        campaign_id=hft_campaign_id,
        repo_root=repo_root,
        workers=cfg.hft_workers,
        stages=cfg.hft_stages,
        resume=True,
        accelerated_mode=False,
        select_all_replay_eligible=False,
        candidate_ids=tuple(candidate_ids),
        out_dir=out_dir,
    )
    loaded = load_scenarios_from_manifest(manifest_path)
    if hft_fn is not None:
        result = hft_fn(scenarios=loaded or scenarios, config=hft_cfg)
    else:
        result = run_hftbacktest_campaign(loaded or scenarios, hft_cfg)
    by_cid = _scenario_results_by_candidate(result.scenario_results, loaded or scenarios)
    return (
        {
            "status": result.status,
            "campaign_id": hft_campaign_id,
            "summary": dict(result.summary or {}),
            "candidate_ids": list(candidate_ids),
        },
        by_cid,
    )


def _run_hft_campaign(
    *,
    repo_root: Path,
    event_id: str,
    screening_path: Path,
    cfg: AutoresearchConfig,
    campaign_id: str,
    generation_index: int,
    hft_fn: HftFn | None = None,
) -> dict[str, Any] | None:
    if not cfg.run_hft_campaign:
        return None
    missing = [
        name
        for name, value in (
            ("hft_source_npz", cfg.hft_source_npz),
            ("hft_latency_model", cfg.hft_latency_model),
            ("hft_fill_queue_model", cfg.hft_fill_queue_model),
        )
        if value is None or not Path(value).is_file()
    ]
    if missing:
        return {"status": "blocked", "missing_inputs": missing}
    manifest_cfg = ManifestGenerationConfig(
        screening_artifact_path=screening_path,
        repo_root=repo_root,
        event_id=event_id,
        source_npz_path=Path(cfg.hft_source_npz),
        latency_model_path=Path(cfg.hft_latency_model),
        fill_queue_model_path=Path(cfg.hft_fill_queue_model),
        select_all_replay_eligible=True,
    )
    scenarios, reasons = generate_scenario_manifest(manifest_cfg)
    if reasons:
        return {"status": "fail", "manifest_reasons": reasons}
    hft_campaign_id = f"{campaign_id}_g{generation_index}_hft"
    out_dir = autoresearch_campaign_dir(repo_root, campaign_id) / f"generation_{generation_index:03d}" / "hft_campaign"
    manifest_path = out_dir / "scenario_manifest.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"scenarios": [s.to_dict() for s in scenarios]}, indent=2) + "\n",
        encoding="utf-8",
    )
    hft_cfg = HftCampaignConfig(
        campaign_id=hft_campaign_id,
        repo_root=repo_root,
        workers=cfg.hft_workers,
        stages=cfg.hft_stages,
        resume=True,
        select_all_replay_eligible=True,
        out_dir=out_dir,
    )
    if hft_fn is not None:
        result = hft_fn(scenarios=scenarios, config=hft_cfg)
    else:
        loaded = load_scenarios_from_manifest(manifest_path)
        result = run_hftbacktest_campaign(loaded or scenarios, hft_cfg)
    return {
        "status": result.status,
        "campaign_id": hft_campaign_id,
        "summary": dict(result.summary or {}),
    }


def _should_stop(
    *,
    cfg: AutoresearchConfig,
    generation_index: int,
    summaries: list[dict[str, Any]],
    stop_file: Path | None,
) -> tuple[bool, str | None]:
    if stop_file and stop_file.is_file():
        return True, "stop_file_present"
    if generation_index + 1 >= cfg.max_generations:
        return True, "max_generations"
    if cfg.target_score is not None:
        last = summaries[-1] if summaries else {}
        best = last.get("best_composite_score")
        if best is not None and float(best) >= float(cfg.target_score):
            return True, "target_score_reached"
    if cfg.stop_no_improvement_generations and len(summaries) >= cfg.stop_no_improvement_generations:
        window = summaries[-cfg.stop_no_improvement_generations :]
        scores = [s.get("best_composite_score") for s in window]
        if len(scores) == len(window) and all(s is not None for s in scores):
            if len(set(scores)) == 1:
                return True, "no_improvement"
    return False, None


def run_single_generation(
    *,
    repo_root: Path,
    manifest: dict[str, Any],
    parsed: ParsedHypothesis,
    cfg: AutoresearchConfig,
    candidates: list[CandidateModel],
    resume: bool = False,
    filter_fn: FilterFn = filter_candidates,
    persist_fn: PersistFn = persist_screening_artifact,
    robustness_fn: RobustnessFn | None = None,
    hft_fn: HftFn | None = None,
) -> dict[str, Any]:
    campaign_id = str(manifest["campaign_id"])
    generation_index = int(manifest["generation_index"])
    event_id = str(manifest["event_id"])
    gen_dir = generation_dir(repo_root, campaign_id, generation_index)
    gen_dir.mkdir(parents=True, exist_ok=True)
    proposed_candidate_ids = [c.candidate_id for c in candidates]

    if resume:
        resumed = _try_resume_completed_generation(
            repo_root=repo_root,
            campaign_id=campaign_id,
            generation_index=generation_index,
        )
        if resumed is not None:
            manifest["generation_status"] = GENERATION_STATUS_COMPLETE
            save_manifest(repo_root, manifest)
            return resumed

    pipeline_run_id = _pipeline_run_id()
    artifact_dir = gen_dir / pipeline_run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    manifest["generation_status"] = GENERATION_STATUS_IN_PROGRESS
    save_manifest(repo_root, manifest)

    frozen_manifest_path = gen_dir / "candidate_manifests.jsonl"
    existing_frozen = load_frozen_manifests(frozen_manifest_path) if resume else []
    frozen_by_id = {str(m["candidate_id"]): m for m in existing_frozen if m.get("candidate_id")}

    attached = [
        c
        if c.feature_recipe
        else attach_feature_recipe_to_candidate(
            c,
            parsed=parsed,
            target_event_id=event_id,
            target_symbol=cfg.symbol,
            research_clock="scheduled_event",
        )
        for c in candidates
    ]
    _write_generation_checkpoint(
        gen_dir,
        proposed_candidate_ids=proposed_candidate_ids,
        pipeline_run_id=pipeline_run_id,
        generation_index=generation_index,
        candidates=attached,
    )
    ontology_receipts: dict[str, dict[str, Any]] = {}
    ontology_pass: list[Any] = []
    for candidate in attached:
        cid = candidate.candidate_id
        ontology_path = gate_receipt_path(gen_dir, cid, "ontology_gate")
        if resume and gate_receipt_is_reusable(ontology_path):
            ontology_receipt = _load_reusable_receipt(ontology_path) or {}
        else:
            recipe_hash = (
                candidate.feature_recipe_hash
                or (candidate.feature_recipe or {}).get("feature_recipe_hash")
                or candidate_identity_hash(candidate)
            )
            pre_manifest: dict[str, Any] = {
                "candidate_id": candidate.candidate_id,
                "feature_recipe_hash": recipe_hash,
                "manifest_hash": "pending_pre_freeze",
                "feature_recipe": dict(candidate.feature_recipe or {}),
                "ontology_citations": candidate.metadata.get("ontology_citations"),
            }
            ontology_receipt = run_ontology_gate_for_candidate(
                manifest=pre_manifest,
                repo_root=repo_root,
            )
            write_gate_receipt(ontology_path, ontology_receipt)
        ontology_receipts[cid] = ontology_receipt
        if ontology_receipt.get("status") == "PASS":
            ontology_pass.append(candidate)
    frozen_manifests: list[dict[str, Any]] = []
    if resume and frozen_by_id:
        for candidate in ontology_pass:
            cid = candidate.candidate_id
            if cid in frozen_by_id:
                frozen_manifests.append(dict(frozen_by_id[cid]))
            else:
                frozen_manifests.append(
                    freeze_candidate_manifest(
                        candidate=candidate,
                        repo_root=repo_root,
                        generation_index=generation_index,
                        parent_candidate_id=str(candidate.metadata.get("elite_parent") or candidate.metadata.get("parent_candidate_id") or "") or None,
                        proposal_reason=str(candidate.metadata.get("proposal_reason") or candidate.metadata.get("refinement") or "generation"),
                    )
                )
        if len(frozen_manifests) > len(frozen_by_id):
            write_frozen_manifests(frozen_manifest_path, frozen_manifests)
    else:
        frozen_manifests = [
            freeze_candidate_manifest(
                candidate=c,
                repo_root=repo_root,
                generation_index=generation_index,
                parent_candidate_id=str(c.metadata.get("elite_parent") or c.metadata.get("parent_candidate_id") or "") or None,
                proposal_reason=str(c.metadata.get("proposal_reason") or c.metadata.get("refinement") or "generation"),
            )
            for c in ontology_pass
        ]
        write_frozen_manifests(frozen_manifest_path, frozen_manifests)
    manifest_receipts: dict[str, dict[str, Any]] = {}
    for cand_manifest in frozen_manifests:
        cid = str(cand_manifest["candidate_id"])
        manifest_receipt = build_manifest_gate_receipt(
            manifest=cand_manifest,
            frozen_manifest_path=frozen_manifest_path,
        )
        manifest_receipts[cid] = manifest_receipt
        write_gate_receipt(
            gate_receipt_path(gen_dir, cid, "manifest_gate"),
            manifest_receipt,
        )
    register_tested_hashes(manifest, _candidate_hashes(attached))
    screening, screening_path = _run_vectorbt_screen(
        candidates=ontology_pass,
        parsed=parsed,
        event_id=event_id,
        repo_root=repo_root,
        cfg=cfg,
        artifact_dir=artifact_dir,
        filter_fn=filter_fn,
        persist_fn=persist_fn,
    )
    robustness_results = _run_robustness_top_k(
        repo_root=repo_root,
        screening=screening,
        cfg=cfg,
        generation_index=generation_index,
        campaign_id=campaign_id,
        robustness_fn=robustness_fn,
    )
    screening_hash = str(screening.get("screening_artifact_hash") or "")
    manifest_by_id = {str(m["candidate_id"]): m for m in frozen_manifests}
    promoted_by_id = {
        str(row.get("candidate_id")): row
        for row in (screening.get("promoted") or [])
        if isinstance(row, dict) and row.get("candidate_id")
    }
    robustness_by_id = {str(r.get("candidate_id")): r for r in robustness_results if r.get("candidate_id")}
    pre_hft_receipts_by_id: dict[str, dict[str, Any]] = {}
    hft_eligible_ids: list[str] = []
    for cid, cand_manifest in manifest_by_id.items():
        rob = robustness_by_id.get(cid)
        promoted_row = promoted_by_id.get(cid) or {}
        vectorbt_receipt = build_vectorbt_gate_receipt(
            manifest=cand_manifest,
            promoted_row=promoted_row,
            screening_path=screening_path,
            screening=screening,
        )
        surface_receipt = build_surface_stability_gate_receipt(
            manifest=cand_manifest,
            promoted_row=promoted_row,
            screening_path=screening_path,
        )
        statistical_receipt = build_statistical_robustness_gate_receipt(
            manifest=cand_manifest,
            promoted_row=promoted_row,
            allow_partial=False,
        )
        campaign_summary = dict(rob.get("campaign_summary") or {}) if rob else None
        regular_wf_receipt = build_regular_walk_forward_gate_receipt(
            manifest=cand_manifest,
            campaign_summary=campaign_summary,
        )
        wfc_receipt = build_walk_forward_correlation_gate_receipt(
            manifest=cand_manifest,
            campaign_summary=campaign_summary,
        )
        pre_chain = run_generation_gate_chain(
            candidate_manifest=cand_manifest,
            ontology_receipt=ontology_receipts.get(cid),
            vectorbt_receipt=vectorbt_receipt,
            surface_receipt=surface_receipt,
            regular_walk_forward_receipt=regular_wf_receipt,
            walk_forward_correlation_receipt=wfc_receipt,
            statistical_receipt=statistical_receipt,
            hftbacktest_receipt=None,
            certification_mode=True,
        )
        if passes_gates_before_hft(pre_chain):
            hft_eligible_ids.append(cid)
        pre_hft_receipts_by_id[cid] = {
            "vectorbt": vectorbt_receipt,
            "surface": surface_receipt,
            "regular_wf": regular_wf_receipt,
            "wfc": wfc_receipt,
            "statistical": statistical_receipt,
        }
    hft_summary, hft_by_candidate = _run_hft_for_candidates(
        repo_root=repo_root,
        event_id=event_id,
        screening_path=screening_path,
        cfg=cfg,
        campaign_id=campaign_id,
        generation_index=generation_index,
        candidate_ids=hft_eligible_ids,
        hft_fn=hft_fn,
    )
    gate_chain_by_id: dict[str, dict[str, Any]] = {}
    for cid, cand_manifest in manifest_by_id.items():
        rob = robustness_by_id.get(cid)
        receipts = pre_hft_receipts_by_id[cid]
        if cid in hft_eligible_ids and cfg.run_hft_campaign:
            hft_receipt = build_hftbacktest_gate_receipt(
                manifest=cand_manifest,
                scenario_results=_enrich_hft_scenario_results(
                    hft_by_candidate.get(cid),
                    manifest=cand_manifest,
                    screening_artifact_hash=screening_hash,
                    robustness_artifact_hash=_robustness_artifact_hash(rob),
                ),
                screening_path=screening_path,
                screening_artifact_hash=screening_hash,
                robustness_artifact_hash=_robustness_artifact_hash(rob),
                accelerated_mode=False,
            )
        elif not cfg.run_hft_campaign:
            hft_receipt = build_hftbacktest_gate_receipt(
                manifest=cand_manifest,
                skipped_reason="hft_campaign_disabled",
            )
        else:
            hft_receipt = build_hftbacktest_gate_receipt(
                manifest=cand_manifest,
                skipped_reason="upstream_gates_not_passed",
            )
        emit_candidate_gate_receipts(
            gen_dir=gen_dir,
            manifest=cand_manifest,
            ontology_receipt=ontology_receipts.get(cid),
            manifest_receipt=manifest_receipts.get(cid),
            vectorbt_receipt=receipts["vectorbt"],
            surface_receipt=receipts["surface"],
            regular_wf_receipt=receipts["regular_wf"],
            wfc_receipt=receipts["wfc"],
            statistical_receipt=receipts["statistical"],
            hft_receipt=hft_receipt,
        )
        chain_result = run_generation_gate_chain(
            candidate_manifest=cand_manifest,
            ontology_receipt=ontology_receipts.get(cid),
            vectorbt_receipt=receipts["vectorbt"],
            surface_receipt=receipts["surface"],
            regular_walk_forward_receipt=receipts["regular_wf"],
            walk_forward_correlation_receipt=receipts["wfc"],
            statistical_receipt=receipts["statistical"],
            hftbacktest_receipt=hft_receipt,
            certification_mode=True,
        )
        gate_chain_by_id[cid] = chain_result
        chain_path = gate_receipt_path(gen_dir, cid, "gate_chain_result")
        chain_path.parent.mkdir(parents=True, exist_ok=True)
        chain_path.write_text(json.dumps(chain_result, indent=2) + "\n", encoding="utf-8")
    parent_strategy_params_by_id: dict[str, dict[str, Any]] = {}
    if generation_index > 0:
        prior_summary_path = generation_dir(repo_root, campaign_id, generation_index - 1) / "generation_summary.json"
        if prior_summary_path.is_file():
            prior_summary = json.loads(prior_summary_path.read_text(encoding="utf-8"))
            for row in prior_summary.get("candidates") or []:
                if not isinstance(row, dict):
                    continue
                cid = str(row.get("candidate_id") or "")
                params = row.get("strategy_params")
                if cid and isinstance(params, dict):
                    parent_strategy_params_by_id[cid] = dict(params)
    summary = build_generation_summary(
        repo_root=repo_root,
        campaign_id=campaign_id,
        generation_index=generation_index,
        screening_artifact=screening,
        robustness_results=robustness_results,
        hft_campaign_summary=hft_summary,
        gate_chain_by_id=gate_chain_by_id,
        proposed_candidate_ids=[c.candidate_id for c in attached],
        manifests_by_id=manifest_by_id,
        candidate_metadata_by_id={c.candidate_id: dict(c.metadata or {}) for c in attached},
        ontology_receipts_by_id=ontology_receipts,
        gen_dir=gen_dir,
        parent_strategy_params_by_id=parent_strategy_params_by_id,
    )
    summary_path = write_generation_summary(gen_dir / "generation_summary.json", summary)

    validation = validate_generation_completion(
        gen_dir=gen_dir,
        screening_path=screening_path,
        summary=summary,
        proposed_candidate_ids=proposed_candidate_ids,
    )
    complete_marker = gen_dir / ".generation_complete"
    if validation:
        if complete_marker.is_file():
            complete_marker.unlink()
        manifest["generation_status"] = GENERATION_STATUS_FAILED
        manifest["stop_reason"] = ",".join(validation[:8])
    else:
        complete_marker.write_text("ok\n", encoding="utf-8")
        manifest["generation_status"] = GENERATION_STATUS_COMPLETE
        manifest["stop_reason"] = None

    append_pointer(manifest, "pipeline_run_ids", pipeline_run_id)
    append_pointer(manifest, "screening_artifact_paths", str(screening_path))
    append_pointer(manifest, "generation_summary_paths", str(summary_path))
    for rob in robustness_results:
        append_pointer(manifest, "robustness_campaign_ids", str(rob.get("campaign_id") or ""))
    if hft_summary and hft_summary.get("campaign_id"):
        append_pointer(manifest, "hft_campaign_ids", str(hft_summary["campaign_id"]))
    save_manifest(repo_root, manifest)
    append_generation_memory(repo_root, summary, generation_index=generation_index)
    return summary


def run_autoresearch_loop(
    *,
    repo_root: Path,
    thesis: str,
    event_id: str,
    cfg: AutoresearchConfig,
    parsed: ParsedHypothesis | None = None,
    campaign_id: str | None = None,
    resume: bool = False,
    no_llm: bool = False,
    filter_fn: FilterFn = filter_candidates,
    persist_fn: PersistFn = persist_screening_artifact,
    robustness_fn: RobustnessFn | None = None,
    hft_fn: HftFn | None = None,
) -> tuple[int, dict[str, Any]]:
    repo_root = repo_root.resolve()
    parsed = parsed or parse_hypothesis(thesis, use_llm=not no_llm)
    config_hash = _campaign_config_hash(repo_root=repo_root, event_id=event_id, cfg=cfg)
    summaries: list[dict[str, Any]] = []
    terminal_stop_reasons = {
        "max_generations",
        "target_score_reached",
        "no_improvement",
        "stop_file_present",
        "no_supported_exploration_remaining",
    }
    failure_stop_reasons = {
        "prior_generation_summary_missing",
        "no_candidates_after_dedup",
        "screening_artifact_missing",
        "screening_artifact_unreadable",
        "generation_complete_marker_missing",
        "config_hash_mismatch",
        "resume_checkpoint_missing_candidates",
    }

    if resume and not campaign_id:
        raise ValueError("--resume requires --campaign-id")
    if campaign_id and not resume and not (Path(repo_root) / "research_cards" / "autoresearch" / campaign_id).exists():
        pass
    elif campaign_id and not resume:
        raise ValueError("--campaign-id requires --resume for autoresearch continuation")

    resume_in_progress = False
    resume_recovered_complete = False
    resume_candidates: list[CandidateModel] | None = None

    if resume and campaign_id:
        manifest = load_manifest(repo_root, campaign_id)
        assert_config_hash_matches(manifest, config_hash)
        if manifest.get("generation_status") == GENERATION_STATUS_IN_PROGRESS:
            gen_idx = int(manifest["generation_index"])
            resumed_summary = _try_resume_completed_generation(
                repo_root=repo_root,
                campaign_id=campaign_id,
                generation_index=gen_idx,
            )
            if resumed_summary is not None:
                summaries.append(resumed_summary)
                resume_recovered_complete = True
                manifest["generation_index"] = gen_idx + 1
                manifest["generation_status"] = GENERATION_STATUS_COMPLETE
                save_manifest(repo_root, manifest)
            else:
                resume_in_progress = True
                checkpoint_path = generation_dir(repo_root, campaign_id, gen_idx) / "generation_checkpoint.json"
                if checkpoint_path.is_file():
                    try:
                        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                        resume_candidates = _load_candidates_from_checkpoint(
                            generation_dir(repo_root, campaign_id, gen_idx),
                            list(checkpoint.get("proposed_candidate_ids") or []),
                        )
                    except (OSError, json.JSONDecodeError):
                        resume_candidates = None
    elif campaign_id:
        manifest = load_manifest(repo_root, campaign_id)
        assert_config_hash_matches(manifest, config_hash)
    else:
        campaign_id = new_campaign_id(thesis=thesis, event_id=event_id)
        manifest = default_manifest(
            campaign_id=campaign_id,
            event_id=event_id,
            symbol=cfg.symbol,
            thesis=thesis,
            config_hash=config_hash,
        )
        save_manifest(repo_root, manifest)

    start_gen = int(manifest.get("generation_index") or 0)
    if manifest.get("generation_status") == GENERATION_STATUS_COMPLETE and not resume_in_progress and not resume_recovered_complete:
        # Bump only when index still points at a finished generation (1137 already
        # advanced index after a normal loop iteration; terminal stop may not).
        complete_at_index = generation_dir(repo_root, campaign_id, start_gen) / ".generation_complete"
        if complete_at_index.is_file():
            start_gen += 1
            manifest["generation_index"] = start_gen

    tested = set(manifest.get("tested_parameter_hashes") or [])
    for gen in range(start_gen, cfg.max_generations):
        manifest["generation_index"] = gen
        generation_resume = resume_in_progress and gen == start_gen
        if generation_resume:
            if not resume_candidates:
                manifest["generation_status"] = GENERATION_STATUS_FAILED
                manifest["stop_reason"] = "resume_checkpoint_missing_candidates"
                save_manifest(repo_root, manifest)
                break
            candidates = resume_candidates
            resume_in_progress = False
        elif gen == 0:
            candidates = list(
                generate_candidates(
                    parsed,
                    max_candidates=cfg.max_candidates_per_generation,
                    expand_for_vectorbt=True,
                    target_event_id=event_id,
                    target_symbol=cfg.symbol,
                )
            )
        else:
            prior_path = generation_dir(repo_root, campaign_id, gen - 1) / "generation_summary.json"
            if not prior_path.is_file():
                manifest["generation_status"] = GENERATION_STATUS_FAILED
                manifest["stop_reason"] = "prior_generation_summary_missing"
                save_manifest(repo_root, manifest)
                break
            prior_summary = json.loads(prior_path.read_text(encoding="utf-8"))
            candidates = propose_next_candidates(
                parsed=parsed,
                generation_summary=prior_summary,
                tested_hashes=tested,
                max_candidates=cfg.max_candidates_per_generation,
                exploration_fraction=cfg.exploration_fraction,
                target_event_id=event_id,
                target_symbol=cfg.symbol,
                family_search_enabled=cfg.family_search_enabled,
                family_search_fraction=cfg.family_search_fraction,
            )
        if not candidates:
            if gen > 0:
                manifest["stop_reason"] = "no_supported_exploration_remaining"
            else:
                manifest["stop_reason"] = "no_candidates_after_dedup"
            save_manifest(repo_root, manifest)
            break

        summary = run_single_generation(
            repo_root=repo_root,
            manifest=manifest,
            parsed=parsed,
            cfg=cfg,
            candidates=candidates,
            resume=generation_resume,
            filter_fn=filter_fn,
            persist_fn=persist_fn,
            robustness_fn=robustness_fn,
            hft_fn=hft_fn,
        )
        summaries.append(summary)
        tested.update(manifest.get("tested_parameter_hashes") or [])
        stop, reason = _should_stop(
            cfg=cfg,
            generation_index=gen,
            summaries=summaries,
            stop_file=cfg.stop_file,
        )
        if stop:
            manifest["stop_reason"] = reason
            save_manifest(repo_root, manifest)
            break
        manifest["generation_index"] = gen + 1
        save_manifest(repo_root, manifest)

    final_report = {
        "campaign_id": campaign_id,
        "generations_run": len(summaries),
        "summaries": summaries,
        "stop_reason": manifest.get("stop_reason"),
        "manifest_path": str(autoresearch_campaign_dir(repo_root, campaign_id) / "autoresearch_manifest.json"),
    }
    report_path = autoresearch_campaign_dir(repo_root, campaign_id) / "final_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(final_report, indent=2) + "\n", encoding="utf-8")
    stop_reason = manifest.get("stop_reason")
    if not summaries:
        status = 1
    elif manifest.get("generation_status") == GENERATION_STATUS_FAILED:
        status = 1
    elif stop_reason in failure_stop_reasons:
        status = 1
    elif stop_reason in terminal_stop_reasons or stop_reason is None:
        status = 0
    else:
        status = 1
    return status, final_report
