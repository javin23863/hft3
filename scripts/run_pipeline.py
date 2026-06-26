#!/usr/bin/env python3
"""End-to-end autoresearch pipeline CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages"), str(REPO / "apps")]

from research_pipeline.deployment import deploy_best
from research_pipeline.document_ingestion import (
    build_knowledge_graph,
    extract_text,
    summarise_text,
)
from research_pipeline.evaluation import evaluate_model
from research_pipeline.hypothesis_parser import parse_hypothesis
from research_pipeline.knowledge_graph import persist_graph_slice
from research_pipeline.model_generation import generate_candidates
from research_pipeline.idea_generation import (
    candidates_from_ideas,
    generate_idea_set,
    idea_summary as summarize_ideas,
    mark_queued_ideas_without_candidates_failed,
    parsed_from_idea,
    update_idea_statuses_from_results,
)
from backtest_pipeline.src.fs_v1_screen_path import FS_V1_BAR_CONSTRUCTION_ID
from backtest_pipeline.src.vectorbt_adapter import filter_candidates, persist_screening_artifact
from data_system.src.feature_store import feature_store_root
from backtest_pipeline.src.hftbacktest_realism import write_hftbacktest_realism_artifacts
from backtest_pipeline.src.promotion_gate import PromotionGate
from research_pipeline.packets import (
    build_pipeline_request,
    build_pipeline_response,
    write_pipeline_packets,
)
from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds, PipelineReport, ParsedHypothesis
from data_layer.llm.openai_compatible_client import DEFAULT_MODEL_DEVELOPMENT_MODEL


def _run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"pipeline_{ts}_{uuid.uuid4().hex[:8]}"


_PIPELINE_RESULT_MARKER = "HFT3_PIPELINE_RESULT="


def _emit_pipeline_payload(payload: dict, *, orchestrator_result: bool) -> None:
    if orchestrator_result:
        slim = {
            "run_id": payload.get("run_id"),
            "artifact_dir": payload.get("artifact_dir"),
            "status": payload.get("status"),
            "paths": payload.get("paths"),
        }
        print(_PIPELINE_RESULT_MARKER + json.dumps(slim))
    else:
        print(json.dumps(payload, indent=2))


def _pipeline_llm_status(parsed: ParsedHypothesis, *, no_llm: bool) -> str:
    if no_llm:
        return "skipped_no_llm"
    if parsed.llm_status:
        return parsed.llm_status
    return "ok" if parsed.source == "openai_compatible" else "unavailable"


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _deployment_allowed(idea_set_enabled: bool, results: list[EvaluationResult]) -> bool:
    return (not idea_set_enabled) or any(r.passes_all_gates() for r in results)


def _idea_set_missing_prefilter(
    *,
    idea_set_enabled: bool,
    dry_run: bool,
    vectorbt: bool,
    vectorbt_only: bool,
) -> bool:
    return idea_set_enabled and not dry_run and not (vectorbt or vectorbt_only)


def _missing_hftbacktest_realism_inputs(args: argparse.Namespace) -> list[str]:
    required = {
        "hftbacktest_data_npz": "--hftbacktest-data-npz",
        "hftbacktest_latency_model": "--hftbacktest-latency-model",
        "hftbacktest_fill_queue_model": "--hftbacktest-fill-queue-model",
        "hftbacktest_upstream_ref": "--hftbacktest-upstream-ref",
    }
    missing = [flag for attr, flag in required.items() if getattr(args, attr) is None]
    if not args.native_hot_path_evidence:
        missing.append("--native-hot-path-evidence")
    return missing


def _optional_resolved_path(path: Path | None) -> Path | None:
    return path.resolve() if path is not None else None


def _run_continuous_lane(args: argparse.Namespace) -> int:
    """Continuous lane: coverage manifest and optional relationship graph."""
    from research_pipeline.continuous_data_manifest import (
        build_coverage_manifest,
        write_coverage_manifest,
    )
    from research_pipeline.continuous_universe import validate_universe_profile
    from research_pipeline.continuous_feature_store import (
        build_continuous_feature_store_stub,
        write_continuous_feature_store,
    )
    from research_pipeline.relationship_graph import (
        build_relationship_graph_stub,
        write_relationship_graph,
    )

    if not args.rithmic_week:
        print("Error: --rithmic-week is required for --lane continuous.", file=sys.stderr)
        return 2
    try:
        profile = validate_universe_profile(args.universe_profile)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    repo_root = args.repo_root.resolve()
    manifest = build_coverage_manifest(
        repo_root=repo_root,
        rithmic_week=args.rithmic_week,
        universe_profile=profile,
    )
    manifest_path = write_coverage_manifest(repo_root, manifest)
    payload: dict = {
        "status": "continuous_manifest",
        "lane": "continuous",
        "rithmic_week": args.rithmic_week,
        "universe_profile": profile,
        "manifest_path": str(manifest_path),
    }

    graph_path: Path | None = None
    if args.build_relationship_graph:
        graph = build_relationship_graph_stub(
            repo_root=repo_root,
            rithmic_week=args.rithmic_week,
            universe_profile=profile,
        )
        graph_path = write_relationship_graph(repo_root, graph)
        payload["status"] = "continuous_manifest_and_graph"
        payload["relationship_graph_path"] = str(graph_path)
        payload["graph_summary"] = graph.get("summary")

    if args.build_feature_store:
        matrix = build_continuous_feature_store_stub(
            repo_root=repo_root,
            rithmic_week=args.rithmic_week,
            universe_profile=profile,
            relationship_graph_path=graph_path,
        )
        fs_path = write_continuous_feature_store(repo_root, matrix)
        payload["feature_store_path"] = str(fs_path)
        payload["feature_store_summary"] = matrix.get("summary")
        if payload["status"] == "continuous_manifest":
            payload["status"] = "continuous_manifest_and_feature_store"
        elif payload["status"] == "continuous_manifest_and_graph":
            payload["status"] = "continuous_manifest_graph_and_feature_store"

    _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Autoresearch pipeline")
    parser.add_argument(
        "--lane",
        choices=("event", "continuous"),
        default="event",
        help="Pipeline lane: event-driven (default) or continuous CME microstructure",
    )
    parser.add_argument("--thesis", default=None, help="Natural-language trading thesis (event lane)")
    parser.add_argument("--doc", type=Path, help="Optional research document (PDF/DOCX/URL)")
    parser.add_argument(
        "--event-id",
        default=None,
        help="Explicit catalog event id from events.csv (event lane)",
    )
    parser.add_argument(
        "--rithmic-week",
        default=None,
        help="ISO week label e.g. 2026-W27 (continuous lane)",
    )
    parser.add_argument(
        "--universe-profile",
        default="full_cme_research",
        help="Continuous lane universe profile (default full_cme_research)",
    )
    parser.add_argument(
        "--build-relationship-graph",
        action="store_true",
        help="Continuous lane: also write relationship graph stub for --rithmic-week",
    )
    parser.add_argument(
        "--build-feature-store",
        action="store_true",
        help="Continuous lane: also write PIT-validated feature store stub for --rithmic-week",
    )
    parser.add_argument(
        "--symbol",
        default="MES",
        help="Target symbol for feature-store fs_v1 VectorBT path (default MES)",
    )
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--chi404-summary", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Parse and generate only")
    parser.add_argument("--no-llm", action="store_true", help="Heuristic hypothesis parse only")
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument("--vectorbt", action="store_true", help="Enable VectorBT pre-filter before HftBacktest")
    parser.add_argument("--vectorbt-only", action="store_true", help="Run VectorBT filter only, skip HftBacktest")
    parser.add_argument(
        "--vectorbt-scope",
        choices=[
            "pilot",
            "screen",
            "refine",
            "paid",
            "paid-compute",
            "paid_compute",
            "broad",
            "broad-screen",
            "broad_screen",
            "all-model",
            "all_model",
            "all-models",
            "all_models",
        ],
        default="pilot",
        help="VectorBT screening scope; all non-pilot broad/refine/paid scopes require the Rust engine",
    )
    parser.add_argument("--vectorbt-max-trials", type=int, default=None)
    parser.add_argument("--vectorbt-max-models", type=int, default=None)
    parser.add_argument("--vectorbt-max-symbols", type=int, default=None)
    parser.add_argument("--vectorbt-max-feature-sets", type=int, default=None)
    parser.add_argument("--vectorbt-max-total-trials", type=int, default=None)
    parser.add_argument("--vectorbt-max-wall-clock-seconds", type=int, default=None)
    parser.add_argument("--vectorbt-max-peak-memory-mb", type=int, default=None)
    parser.add_argument(
        "--hftbacktest-realism",
        action="store_true",
        help="Opt in to official HftBacktest realism handoff after VectorBT screening",
    )
    parser.add_argument("--hftbacktest-data-npz", type=Path, default=None)
    parser.add_argument("--hftbacktest-latency-model", type=Path, default=None)
    parser.add_argument("--hftbacktest-fill-queue-model", type=Path, default=None)
    parser.add_argument("--hftbacktest-observation-artifact", type=Path, default=None)
    parser.add_argument("--hftbacktest-candidate-id", default=None)
    parser.add_argument("--hftbacktest-upstream-ref", default=None)
    parser.add_argument("--native-hot-path-evidence", action="append", default=[])
    parser.add_argument("--idea-set", action="store_true", help="Use packet-strict LLM idea set before candidate tests")
    parser.add_argument("--max-ideas", type=int, default=None, help="Maximum idea records to accept before static filtering")
    parser.add_argument("--review-memory-limit", type=int, default=5, help="Prior AAR/KG memory facts to include")
    parser.add_argument("--idea-temperature", type=float, default=None, help="Sampling temperature for idea generation only")
    parser.add_argument("--idea-top-p", type=float, default=None, help="Top-p sampling for idea generation only")
    parser.add_argument(
        "--orchestrator-result",
        action="store_true",
        help="Emit single-line HFT3_PIPELINE_RESULT for paid-screen worker subprocesses",
    )
    parser.add_argument("--autoresearch", action="store_true", help="Run multi-generation autoresearch loop")
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO / "config" / "autoresearch" / "default.yaml",
        help="Autoresearch loop YAML config",
    )
    parser.add_argument("--resume", action="store_true", help="Resume autoresearch campaign from manifest")
    parser.add_argument("--campaign-id", default=None, help="Autoresearch campaign id (required with --resume)")
    parser.add_argument("--max-generations", type=int, default=None, help="Override config max_generations")
    parser.add_argument("--stop-file", type=Path, default=None, help="Stop autoresearch loop when this file exists")
    parser.add_argument(
        "--skip-bad-units-file",
        type=Path,
        default=None,
        help="JSON report from scripts/check_lake_data.py; invalid_unit_ids are skipped",
    )
    args = parser.parse_args()

    if args.lane == "event" and args.build_relationship_graph:
        print(
            "Error: --build-relationship-graph requires --lane continuous.",
            file=sys.stderr,
        )
        return 2
    if args.lane == "event" and args.build_feature_store:
        print(
            "Error: --build-feature-store requires --lane continuous.",
            file=sys.stderr,
        )
        return 2

    if args.lane == "continuous":
        return _run_continuous_lane(args)

    if not args.thesis or not args.event_id:
        print(
            "Error: --thesis and --event-id are required for --lane event.",
            file=sys.stderr,
        )
        return 2

    if args.autoresearch:
        from research_pipeline.generation_loop import (
            load_autoresearch_config,
            make_default_robustness_fn,
            run_autoresearch_loop,
        )

        repo_root = args.repo_root.resolve()
        overrides = {
            "max_generations": args.max_generations,
            "stop_file": str(args.stop_file) if args.stop_file else None,
        }
        cfg = load_autoresearch_config(args.config, overrides=overrides)
        skip_file = args.skip_bad_units_file
        if skip_file is None and cfg.skip_bad_units_file is not None:
            skip_file = cfg.skip_bad_units_file
        if skip_file is not None:
            from research_pipeline.data_quality import skipped_unit_id_set

            skip_path = skip_file if skip_file.is_absolute() else repo_root / skip_file
            if skip_path.is_file():
                overrides["skip_bad_units_file"] = str(skip_path)
                cfg = load_autoresearch_config(args.config, overrides=overrides)
                skip_ids = skipped_unit_id_set(
                    skip_bad_units_file=skip_path,
                    skipped_unit_ids=list(cfg.skipped_unit_ids),
                )
                print(
                    f"[autoresearch] skip_bad_units_file={skip_path} "
                    f"skip_ids={len(skip_ids)} (wired into generation_loop)",
                    flush=True,
                )
            else:
                print(f"Warning: skip_bad_units_file not found: {skip_path}", file=sys.stderr)
        chi404 = args.chi404_summary
        if chi404 is None:
            default_lat = repo_root / "runtime" / "latency_reports" / "latency_summary.json"
            chi404 = default_lat if default_lat.is_file() else None
        robustness_fn = make_default_robustness_fn(chi404_summary=chi404) if cfg.run_robustness else None
        code, report = run_autoresearch_loop(
            repo_root=repo_root,
            thesis=args.thesis,
            event_id=args.event_id,
            cfg=cfg,
            campaign_id=args.campaign_id,
            resume=bool(args.resume),
            no_llm=args.no_llm,
            robustness_fn=robustness_fn,
        )
        _emit_pipeline_payload(
            {
                "status": "autoresearch_complete" if code == 0 else "autoresearch_failed",
                "autoresearch_report": report,
            },
            orchestrator_result=args.orchestrator_result,
        )
        return code

    if args.resume and not args.autoresearch:
        print("Error: --resume requires --autoresearch.", file=sys.stderr)
        return 2

    if args.hftbacktest_realism and args.vectorbt_only:
        print(
            "Error: --hftbacktest-realism cannot be combined with --vectorbt-only.",
            file=sys.stderr,
        )
        return 2
    if args.hftbacktest_realism and not args.vectorbt:
        print(
            "Error: --hftbacktest-realism requires --vectorbt so the handoff has a terminal screening_artifact.json.",
            file=sys.stderr,
        )
        return 2
    if args.doc and not args.dry_run and not (args.vectorbt or args.vectorbt_only):
        print(
            "Error: --doc without --vectorbt/--vectorbt-only is dry-run only; add --dry-run or use the VectorBT/HftBacktest handoff.",
            file=sys.stderr,
        )
        return 2

    repo_root = args.repo_root.resolve()
    run_id = _run_id()
    doc_summary = None
    doc_ref = str(args.doc) if args.doc else None

    request = build_pipeline_request(
        request_id=run_id,
        thesis=args.thesis,
        event_id=args.event_id,
        repo_root=repo_root,
        max_candidates=args.max_candidates,
        document_ref=doc_ref,
    )
    artifact_dir = repo_root / "research_cards" / "pipeline_runs" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "request_packet.json").write_text(
        json.dumps(request, indent=2), encoding="utf-8"
    )

    if args.doc:
        try:
            text = extract_text(args.doc)
            doc_summary = summarise_text(text)
            doc_id = f"doc:{args.doc.stem}"
            kg = build_knowledge_graph(text, doc_id=doc_id)
            persist_graph_slice(repo_root, kg)
        except Exception as exc:
            print(f"Warning: document ingestion failed, continuing without doc: {exc}", file=sys.stderr)
            doc_summary = {"error": str(exc)}

    idea_packet = None
    idea_candidates_count = 0
    if args.idea_set:
        idea_packet = generate_idea_set(
            request,
            thesis=args.thesis,
            repo_root=repo_root,
            max_ideas=args.max_ideas or min(3, args.max_candidates),
            max_candidates=args.max_candidates,
            review_memory_limit=args.review_memory_limit,
            use_llm=not args.no_llm,
            temperature=(
                args.idea_temperature
                if args.idea_temperature is not None
                else _optional_float(os.environ.get("HFT3_IDEA_TEMPERATURE")) or 0.7
            ),
            top_p=(
                args.idea_top_p
                if args.idea_top_p is not None
                else _optional_float(os.environ.get("HFT3_IDEA_TOP_P")) or 0.95
            ),
        )
        candidates = candidates_from_ideas(
            idea_packet,
            max_candidates=args.max_candidates,
            expand_for_vectorbt=bool(args.vectorbt or args.vectorbt_only),
        )
        idea_candidates_count = len(candidates)
        queued = [idea for idea in idea_packet.get("ideas", []) if idea.get("status") == "queued_for_test"]
        parsed = parsed_from_idea(queued[0]) if queued else parse_hypothesis(args.thesis, use_llm=False)
        (artifact_dir / "review_memory.json").write_text(
            json.dumps(
                {
                    "refs": idea_packet.get("refs", {}),
                    "review_memory": idea_packet.get("review_memory", []),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (artifact_dir / "idea_set_packet.json").write_text(
            json.dumps(idea_packet, indent=2), encoding="utf-8"
        )
        if _idea_set_missing_prefilter(
            idea_set_enabled=args.idea_set,
            dry_run=args.dry_run,
            vectorbt=args.vectorbt,
            vectorbt_only=args.vectorbt_only,
        ):
            print(
                "Error: --idea-set full runs require --vectorbt so generated ideas pass the prefilter before evaluation.",
                file=sys.stderr,
            )
            return 1
    else:
        parsed = parse_hypothesis(
            args.thesis,
            use_llm=not args.no_llm,
            pipeline_request=request,
            repo_root=repo_root,
        )
        candidates = list(generate_candidates(
            parsed,
            max_candidates=args.max_candidates,
            expand_for_vectorbt=bool(args.vectorbt or args.vectorbt_only),
            target_event_id=args.event_id,
            target_symbol=args.symbol,
        ))

    if args.vectorbt or args.vectorbt_only:
        print(f"Running VectorBT filter on {len(candidates)} candidates x grid...")
        source_meta = {c.candidate_id: dict(c.metadata) for c in candidates}
        vbt_gates = PromotionGate(
            min_oos_expectancy=0.0,
            max_drawdown_pct=-50.0,
            min_trades=3 if args.vectorbt_only else 10,
        )
        run_budget = {
            key: value
            for key, value in {
                "max_trials": args.vectorbt_max_trials,
                "max_models": args.vectorbt_max_models,
                "max_symbols": args.vectorbt_max_symbols,
                "max_feature_sets": args.vectorbt_max_feature_sets,
                "max_total_trials": args.vectorbt_max_total_trials,
                "max_wall_clock_seconds": args.vectorbt_max_wall_clock_seconds,
                "max_peak_memory_mb_or_null": args.vectorbt_max_peak_memory_mb,
            }.items()
            if value is not None
        }
        filter_result = filter_candidates(
            candidates=candidates,
            parsed=parsed,
            event_id=args.event_id,
            repo_root=repo_root,
            gates=vbt_gates,
            screening_scope=args.vectorbt_scope,
            run_budget=run_budget or None,
            feature_store_root=feature_store_root(repo_root),
            symbol=args.symbol,
        )
        vectorbt_artifact = filter_result.to_dict()
        print(
            f"  bar_construction_id: {vectorbt_artifact.get('bar_construction_id')}"
            + (
                " (fs_v1 row loop)"
                if vectorbt_artifact.get("bar_construction_id") == FS_V1_BAR_CONSTRUCTION_ID
                else " (ohlcv bar stub fallback)"
            )
        )
        print(f"  feature_plane_status: {vectorbt_artifact.get('feature_plane_status')}")
        print(f"  model_feature_usage_status: {vectorbt_artifact.get('model_feature_usage_status')}")
        screening_path = persist_screening_artifact(
            vectorbt_artifact,
            artifact_dir / "screening_artifact.json",
        )
        vectorbt_artifact = json.loads(screening_path.read_text(encoding="utf-8"))
        (artifact_dir / "vectorbt_filter.json").write_text(
            json.dumps(vectorbt_artifact, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  Promoted: {len(filter_result.promoted)}, Rejected: {len(filter_result.rejected)}")
        for r in filter_result.rejected:
            print(f"  REJECTED {r.candidate_id}: {r.reject_reason}")
        if filter_result.promoted:
            def _promotion_source_meta(promoted):
                base_id = (
                    promoted.vectorbt_results.get("base_candidate_id")
                    if isinstance(promoted.vectorbt_results, dict)
                    else None
                )
                embedded_meta = (
                    promoted.vectorbt_results.get("base_candidate_metadata")
                    if isinstance(promoted.vectorbt_results, dict)
                    else None
                )
                if isinstance(embedded_meta, dict):
                    return dict(embedded_meta)
                if base_id and base_id in source_meta:
                    return dict(source_meta[base_id])
                if promoted.candidate_id in source_meta:
                    return dict(source_meta[promoted.candidate_id])
                return {}

            candidates = [
                CandidateModel(
                    candidate_id=p.candidate_id,
                    model_id=p.hypothesis_id,
                    strategy_params=p.param_values,
                    thesis=parsed.thesis,
                    metadata={
                        **_promotion_source_meta(p),
                        "strategy_family": p.strategy_family,
                        "promoted": True,
                        "vectorbt_run_id": p.vectorbt_run_id,
                        "vectorbt_results": p.vectorbt_results,
                        "asset_class": p.asset_class,
                        "symbol": p.symbol,
                    },
                )
                for p in filter_result.promoted
            ]
        else:
            print("No candidates survived VectorBT filter.")
            candidates = []
            if idea_packet:
                mark_queued_ideas_without_candidates_failed(idea_packet, [])
                (artifact_dir / "idea_set_packet.json").write_text(
                    json.dumps(idea_packet, indent=2), encoding="utf-8"
                )
        if idea_packet and candidates:
            mark_queued_ideas_without_candidates_failed(
                idea_packet,
                {
                    str(candidate.metadata.get("idea_id"))
                    for candidate in candidates
                    if candidate.metadata.get("idea_id")
                },
            )
            (artifact_dir / "idea_set_packet.json").write_text(
                json.dumps(idea_packet, indent=2), encoding="utf-8"
            )
        if args.vectorbt_only:
            idea_summary = (
                summarize_ideas(idea_packet, candidates_from_ideas_count=idea_candidates_count)
                if idea_packet
                else None
            )
            report = PipelineReport(
                run_id=run_id,
                thesis=args.thesis,
                event_id=args.event_id,
                parsed=parsed,
                candidates_tested=int(filter_result.total_candidates),
                results=[],
                selected=None,
                artifact_dir=str(artifact_dir),
                document_summary=doc_summary,
            )
            llm_status = (
                str(idea_packet.get("llm_status"))
                if idea_packet
                else _pipeline_llm_status(parsed, no_llm=args.no_llm)
            )
            response = build_pipeline_response(
                report,
                request,
                llm_status=llm_status,
                llm_model=(
                    idea_packet.get("llm_model")
                    if idea_packet and llm_status == "ok"
                    else None if llm_status != "ok" else DEFAULT_MODEL_DEVELOPMENT_MODEL
                ),
                idea_summary=idea_summary,
            )
            write_pipeline_packets(artifact_dir, request, response)
            payload = {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "request_packet": request,
                "response_packet": response,
                "vectorbt_filter": vectorbt_artifact,
                "screening_artifact": vectorbt_artifact,
                "parsed": {
                    "primary_model_id": parsed.primary_model_id,
                    "source": parsed.source,
                    "param_ranges": parsed.param_ranges,
                },
                "candidates": [
                    {
                        "candidate_id": c.candidate_id,
                        "model_id": c.model_id,
                        "params": c.strategy_params,
                    }
                    for c in candidates
                ],
                "document_summary": doc_summary,
            }
            if idea_packet:
                payload["idea_summary"] = idea_summary
                payload["idea_set_packet"] = idea_packet
            _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
            return 0 if candidates else 1
        paths = {
            "screening_artifact_path": str(screening_path),
            "vectorbt_filter_path": str(artifact_dir / "vectorbt_filter.json"),
        }
        if not args.hftbacktest_realism:
            payload = {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "status": "blocked_downstream_realism_opt_in_required",
                "detail": (
                    "screening_artifact.json was written; pass --hftbacktest-realism "
                    "with required HftBacktest input artifacts to run the official realism handoff"
                ),
                "vectorbt_filter": vectorbt_artifact,
                "screening_artifact": vectorbt_artifact,
                "paths": paths,
            }
            _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
            return 2
        promoted_ids = list(vectorbt_artifact.get("promoted_ids") or [])
        if not promoted_ids:
            replay_summary = {
                "run_id": run_id,
                "replay_realism_status": "fail",
                "fail_closed_reasons": ["screening_artifact_has_no_promoted_candidate"],
            }
            payload = {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "status": "blocked_hftbacktest_realism_no_promoted_candidates",
                "detail": "HftBacktest realism handoff requires at least one VectorBT-promoted candidate",
                "vectorbt_filter": vectorbt_artifact,
                "screening_artifact": vectorbt_artifact,
                "hftbacktest_realism": None,
                "replay_summary": replay_summary,
                "paths": paths,
            }
            _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
            return 2
        missing_hbt_inputs = _missing_hftbacktest_realism_inputs(args)
        if missing_hbt_inputs:
            replay_summary = {
                "run_id": run_id,
                "replay_realism_status": "fail",
                "fail_closed_reasons": [
                    f"missing_hftbacktest_realism_input:{flag}" for flag in missing_hbt_inputs
                ],
            }
            payload = {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "status": "blocked_hftbacktest_realism_inputs_missing",
                "detail": "HftBacktest realism handoff was opted in but required source-lock, native, or input artifacts were not provided",
                "missing_hftbacktest_inputs": missing_hbt_inputs,
                "vectorbt_filter": vectorbt_artifact,
                "screening_artifact": vectorbt_artifact,
                "hftbacktest_realism": None,
                "replay_summary": replay_summary,
                "paths": paths,
            }
            _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
            return 2

        hftbacktest_out_dir = artifact_dir / "hftbacktest_realism"
        hftbacktest_realism = write_hftbacktest_realism_artifacts(
            repo_root=repo_root,
            out_dir=hftbacktest_out_dir,
            screening_artifact_path=screening_path,
            data_npz_path=_optional_resolved_path(args.hftbacktest_data_npz),
            latency_model_path=_optional_resolved_path(args.hftbacktest_latency_model),
            fill_queue_model_path=_optional_resolved_path(args.hftbacktest_fill_queue_model),
            observation_artifact_path=_optional_resolved_path(args.hftbacktest_observation_artifact),
            candidate_id=args.hftbacktest_candidate_id,
            upstream_ref=args.hftbacktest_upstream_ref,
            native_hot_path_evidence=list(args.native_hot_path_evidence or []),
            run_id=run_id,
        )
        replay_summary = hftbacktest_realism["replay_summary"]
        paths.update(
            {
                "hftbacktest_realism_dir": str(hftbacktest_out_dir),
                "source_lock_path": hftbacktest_realism.get("source_lock_path"),
                "latency_model_path": hftbacktest_realism.get("latency_model_path"),
                "fill_queue_model_path": hftbacktest_realism.get("fill_queue_model_path"),
                "official_replay_path": hftbacktest_realism.get("official_replay_path"),
                "replay_summary_path": hftbacktest_realism.get("replay_summary_path"),
            }
        )
        payload = {
            "run_id": run_id,
            "artifact_dir": str(artifact_dir),
            "status": (
                "hftbacktest_realism_pass"
                if replay_summary.get("replay_realism_status") == "pass"
                else "hftbacktest_realism_fail_closed"
            ),
            "vectorbt_filter": vectorbt_artifact,
            "screening_artifact": vectorbt_artifact,
            "hftbacktest_realism": hftbacktest_realism,
            "replay_summary": replay_summary,
            "paths": paths,
        }
        _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
        return 0 if replay_summary.get("replay_realism_status") == "pass" else 2

    if args.dry_run:
        idea_summary = (
            summarize_ideas(idea_packet, candidates_from_ideas_count=idea_candidates_count)
            if idea_packet
            else None
        )
        report = PipelineReport(
            run_id=run_id,
            thesis=args.thesis,
            event_id=args.event_id,
            parsed=parsed,
            candidates_tested=0,
            results=[],
            selected=None,
            artifact_dir=str(artifact_dir),
            document_summary=doc_summary,
        )
        llm_status = (
            str(idea_packet.get("llm_status"))
            if idea_packet
            else _pipeline_llm_status(parsed, no_llm=args.no_llm)
        )
        response = build_pipeline_response(
            report,
            request,
            llm_status=llm_status,
            llm_model=(
                idea_packet.get("llm_model")
                if idea_packet and llm_status == "ok"
                else None if llm_status != "ok" else DEFAULT_MODEL_DEVELOPMENT_MODEL
            ),
            idea_summary=idea_summary,
        )
        write_pipeline_packets(artifact_dir, request, response)
        payload = {
            "run_id": run_id,
            "artifact_dir": str(artifact_dir),
            "request_packet": request,
            "response_packet": response,
            "parsed": {
                "primary_model_id": parsed.primary_model_id,
                "source": parsed.source,
                "param_ranges": parsed.param_ranges,
            },
            "candidates": [
                {"candidate_id": c.candidate_id, "model_id": c.model_id, "params": c.strategy_params}
                for c in candidates
            ],
            "document_summary": doc_summary,
        }
        if idea_packet:
            payload["idea_summary"] = idea_summary
            payload["idea_set_packet"] = idea_packet
        print(json.dumps(payload, indent=2))
        return 0

    chi404 = args.chi404_summary
    if chi404 is None:
        default_lat = repo_root / "runtime" / "latency_reports" / "latency_summary.json"
        chi404 = default_lat if default_lat.is_file() else None
    if chi404 is None:
        print("Warning: no latency data available; backtest will run without CHI404 latency", file=sys.stderr)

    gates = GateThresholds(min_trades=0)
    results = []
    for cand in candidates:
        print(f"Evaluating {cand.model_id} threshold={cand.strategy_params.get('signal_threshold')}...")
        results.append(
            evaluate_model(cand, args.event_id, repo_root, chi404_summary=chi404, gates=gates)
        )

    if idea_packet:
        update_idea_statuses_from_results(idea_packet, results)
        (artifact_dir / "idea_set_packet.json").write_text(
            json.dumps(idea_packet, indent=2), encoding="utf-8"
        )

    report = PipelineReport(
        run_id=run_id,
        thesis=args.thesis,
        event_id=args.event_id,
        parsed=parsed,
        candidates_tested=len(results),
        results=results,
        selected=None,
        artifact_dir=str(artifact_dir),
        document_summary=doc_summary,
    )

    artifact = None
    if _deployment_allowed(args.idea_set, results):
        artifact = deploy_best(repo_root, report)
    if artifact is None:
        print("Note: deploy_best returned None (no passing candidates)", file=sys.stderr)
    llm_status = (
        str(idea_packet.get("llm_status"))
        if idea_packet
        else _pipeline_llm_status(parsed, no_llm=args.no_llm)
    )
    response = build_pipeline_response(
        report,
        request,
        llm_status=llm_status,
        llm_model=(
            idea_packet.get("llm_model")
            if idea_packet and llm_status == "ok"
            else None if llm_status != "ok" else DEFAULT_MODEL_DEVELOPMENT_MODEL
        ),
        idea_summary=(
            summarize_ideas(idea_packet, candidates_from_ideas_count=idea_candidates_count)
            if idea_packet
            else None
        ),
    )
    write_pipeline_packets(artifact_dir, request, response)
    print(json.dumps({"report": report.to_dict(), "response_packet": response}, indent=2))
    if artifact:
        print(f"Artifacts: {artifact}")
    else:
        print("No candidate deployed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
