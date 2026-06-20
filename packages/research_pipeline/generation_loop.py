"""Thin multi-generation autoresearch coordinator."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
    autoresearch_campaign_dir,
    compute_config_hash,
    default_manifest,
    generation_dir,
    load_manifest,
    new_campaign_id,
    register_tested_hashes,
    save_manifest,
)
from research_pipeline.generation_gate_chain import run_generation_gate_chain
from research_pipeline.generation_gate_producers import (
    build_regular_walk_forward_gate_receipt,
    build_vectorbt_gate_receipt,
    build_walk_forward_correlation_gate_receipt,
    emit_candidate_gate_receipts,
    gate_receipt_path,
    run_ontology_gate_for_candidate,
    write_gate_receipt,
)
from research_pipeline.generation_summary import build_generation_summary, validate_generation_artifacts, write_generation_summary
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
                "metrics": dict(outcome.get("metrics") or {}),
                "campaign_id": outcome.get("campaign_id"),
            }
        )
    return results


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
    if cfg.stop_no_improvement_generations and len(summaries) >= 2:
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
    pipeline_run_id = _pipeline_run_id()
    artifact_dir = gen_dir / pipeline_run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    manifest["generation_status"] = GENERATION_STATUS_IN_PROGRESS
    save_manifest(repo_root, manifest)

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
    ontology_receipts: dict[str, dict[str, Any]] = {}
    ontology_pass: list[Any] = []
    for candidate in attached:
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
        ontology_receipts[candidate.candidate_id] = ontology_receipt
        write_gate_receipt(
            gate_receipt_path(gen_dir, candidate.candidate_id, "ontology_gate"),
            ontology_receipt,
        )
        if ontology_receipt.get("status") == "PASS":
            ontology_pass.append(candidate)
    frozen_manifests = [
        freeze_candidate_manifest(
            candidate=c,
            repo_root=repo_root,
            generation_index=generation_index,
            proposal_reason=str(c.metadata.get("proposal_reason") or "generation"),
        )
        for c in ontology_pass
    ]
    write_frozen_manifests(gen_dir / "candidate_manifests.jsonl", frozen_manifests)
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
    hft_summary = _run_hft_campaign(
        repo_root=repo_root,
        event_id=event_id,
        screening_path=screening_path,
        cfg=cfg,
        campaign_id=campaign_id,
        generation_index=generation_index,
        hft_fn=hft_fn,
    )
    manifest_by_id = {str(m["candidate_id"]): m for m in frozen_manifests}
    promoted_by_id = {
        str(row.get("candidate_id")): row
        for row in (screening.get("promoted") or [])
        if isinstance(row, dict) and row.get("candidate_id")
    }
    robustness_by_id = {str(r.get("candidate_id")): r for r in robustness_results if r.get("candidate_id")}
    gate_chain_by_id: dict[str, dict[str, Any]] = {}
    for cid, cand_manifest in manifest_by_id.items():
        rob = robustness_by_id.get(cid)
        promoted_row = promoted_by_id.get(cid) or {}
        vectorbt_receipt = build_vectorbt_gate_receipt(
            manifest=cand_manifest,
            promoted_row=promoted_row,
            screening_path=screening_path,
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
        emit_candidate_gate_receipts(
            gen_dir=gen_dir,
            manifest=cand_manifest,
            vectorbt_receipt=vectorbt_receipt,
            regular_wf_receipt=regular_wf_receipt,
            wfc_receipt=wfc_receipt,
        )
        chain_result = run_generation_gate_chain(
            candidate_manifest=cand_manifest,
            ontology_receipt=ontology_receipts.get(cid),
            vectorbt_receipt=vectorbt_receipt,
            surface_receipt=None,
            regular_walk_forward_receipt=regular_wf_receipt,
            walk_forward_correlation_receipt=wfc_receipt,
            statistical_receipt=None,
            hftbacktest_receipt=None,
            certification_mode=True,
        )
        gate_chain_by_id[cid] = chain_result
        chain_path = gate_receipt_path(gen_dir, cid, "gate_chain_result")
        chain_path.parent.mkdir(parents=True, exist_ok=True)
        chain_path.write_text(json.dumps(chain_result, indent=2) + "\n", encoding="utf-8")
    summary = build_generation_summary(
        repo_root=repo_root,
        campaign_id=campaign_id,
        generation_index=generation_index,
        screening_artifact=screening,
        robustness_results=robustness_results,
        hft_campaign_summary=hft_summary,
        gate_chain_by_id=gate_chain_by_id,
    )
    summary_path = write_generation_summary(gen_dir / "generation_summary.json", summary)
    (gen_dir / ".generation_complete").write_text("ok\n", encoding="utf-8")

    validation = validate_generation_artifacts(gen_dir=gen_dir, screening_path=screening_path)
    if validation:
        manifest["generation_status"] = GENERATION_STATUS_FAILED
        manifest["stop_reason"] = ",".join(validation)
    else:
        manifest["generation_status"] = GENERATION_STATUS_COMPLETE

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
    config_hash = compute_config_hash(
        {
            "max_generations": cfg.max_generations,
            "max_candidates_per_generation": cfg.max_candidates_per_generation,
            "screening_scope": cfg.screening_scope,
            "event_id": event_id,
            "symbol": cfg.symbol,
        }
    )
    summaries: list[dict[str, Any]] = []
    terminal_stop_reasons = {
        "max_generations",
        "target_score_reached",
        "no_improvement",
        "stop_file_present",
    }
    failure_stop_reasons = {
        "prior_generation_summary_missing",
        "no_candidates_after_dedup",
        "screening_artifact_missing",
        "screening_artifact_unreadable",
        "generation_complete_marker_missing",
    }

    if resume and not campaign_id:
        raise ValueError("--resume requires --campaign-id")
    if campaign_id and not resume and not (Path(repo_root) / "research_cards" / "autoresearch" / campaign_id).exists():
        pass
    elif campaign_id and not resume:
        raise ValueError("--campaign-id requires --resume for autoresearch continuation")

    if resume and campaign_id:
        manifest = load_manifest(repo_root, campaign_id)
        if manifest.get("generation_status") == GENERATION_STATUS_IN_PROGRESS:
            gen_idx = int(manifest["generation_index"])
            gen_dir = generation_dir(repo_root, campaign_id, gen_idx)
            summary_path = gen_dir / "generation_summary.json"
            if summary_path.is_file():
                summaries.append(json.loads(summary_path.read_text(encoding="utf-8")))
                manifest["generation_index"] = gen_idx + 1
                manifest["generation_status"] = GENERATION_STATUS_COMPLETE
                save_manifest(repo_root, manifest)
    elif campaign_id:
        manifest = load_manifest(repo_root, campaign_id)
        if str(manifest.get("config_hash") or "") not in ("", config_hash):
            raise ValueError("autoresearch config_hash mismatch; refuse to continue with changed config")
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
    if manifest.get("generation_status") == GENERATION_STATUS_COMPLETE:
        start_gen = int(manifest.get("generation_index", 0)) + 1
        manifest["generation_index"] = start_gen

    tested = set(manifest.get("tested_parameter_hashes") or [])
    for gen in range(start_gen, cfg.max_generations):
        manifest["generation_index"] = gen
        if gen == 0:
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
            manifest["stop_reason"] = "no_candidates_after_dedup"
            save_manifest(repo_root, manifest)
            break

        summary = run_single_generation(
            repo_root=repo_root,
            manifest=manifest,
            parsed=parsed,
            cfg=cfg,
            candidates=candidates,
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
