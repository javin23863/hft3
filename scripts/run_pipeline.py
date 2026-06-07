#!/usr/bin/env python3
"""End-to-end autoresearch pipeline CLI."""

from __future__ import annotations

import argparse
import json
import os
import random
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
from research_pipeline.optimizer import propose_optimized_candidates
from research_pipeline.idea_generation import (
    candidates_from_ideas,
    generate_idea_set,
    idea_summary as summarize_ideas,
    mark_queued_ideas_without_candidates_failed,
    parsed_from_idea,
    update_idea_statuses_from_results,
)
from backtest_pipeline.src.vectorbt_adapter import filter_candidates
from backtest_pipeline.src.promotion_gate import PromotionGate
from research_pipeline.packets import (
    build_pipeline_request,
    build_pipeline_response,
    write_pipeline_packets,
)
from research_pipeline.types import (
    CandidateModel,
    EvaluationResult,
    GateThresholds,
    PipelineReport,
    ParsedHypothesis,
)
from data_layer.llm.openai_compatible_client import DEFAULT_MODEL_DEVELOPMENT_MODEL


def _run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"pipeline_{ts}_{uuid.uuid4().hex[:8]}"


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


def _promoted_to_candidates(
    promoted: list,
    *,
    parsed: ParsedHypothesis,
    source_meta: dict[str, dict],
) -> list[CandidateModel]:
    return [
        CandidateModel(
            candidate_id=p.candidate_id,
            model_id=p.hypothesis_id,
            strategy_params=p.param_values,
            thesis=parsed.thesis,
            metadata={
                **source_meta.get(p.candidate_id, {}),
                "strategy_family": p.strategy_family,
                "promoted": True,
                "vectorbt_run_id": p.vectorbt_run_id,
                "vectorbt_results": p.vectorbt_results,
                "asset_class": p.asset_class,
                "symbol": p.symbol,
            },
        )
        for p in promoted
    ]


def _validate_crypto_candidates(candidates: list[CandidateModel], repo_root: Path) -> None:
    crypto_data = repo_root / "data" / "crypto"
    for cand in candidates:
        ac = cand.metadata.get("asset_class", "").upper()
        if ac not in ("CRYPTO",):
            continue
        try:
            from crypto_lane.src.validation.crypto_validation_workflow import validate_crypto_candidate  # noqa: F811
            from backtest_pipeline.src.promotion_gate import set_execution_classification  # noqa: F811

            print(f"  Validating crypto execution for {cand.candidate_id}...")
            report = validate_crypto_candidate(cand, crypto_data)
            error = report.result.error
            classification = report.result.execution_classification if not error else "NO_EXECUTION"
            cand.metadata["execution_classification"] = classification
            cand.metadata["execution_quality"] = {
                "mean_jump_bps": report.result.mean_jump_bps,
                "mean_qqe": report.result.mean_qqe,
                "total_fills": report.result.total_fills,
                "error": error,
            }
            set_execution_classification(cand.candidate_id, classification)
            print(
                f"    {cand.candidate_id}: {classification}, "
                f"fills={report.result.total_fills}, "
                f"jump={report.result.mean_jump_bps:.2f}bps, "
                f"qqe={report.result.mean_qqe:.2f}"
            )
        except ImportError:
            print(
                f"  Skipping crypto validation for {cand.candidate_id}: crypto_lane not installed",
                file=sys.stderr,
            )
            cand.metadata["execution_classification"] = "NO_EXECUTION"
        except Exception as exc:
            print(f"  Crypto validation failed for {cand.candidate_id}: {exc}", file=sys.stderr)
            cand.metadata["execution_classification"] = "NO_EXECUTION"
            cand.metadata["execution_quality"] = {"error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Autoresearch pipeline")
    parser.add_argument("--thesis", required=True, help="Natural-language trading thesis")
    parser.add_argument("--doc", type=Path, help="Optional research document (PDF/DOCX/URL)")
    parser.add_argument("--event-id", required=True, help="Explicit catalog event id from events.csv")
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--chi404-summary", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Parse and generate only")
    parser.add_argument("--no-llm", action="store_true", help="Heuristic hypothesis parse only")
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument(
        "--lane",
        choices=["cme", "equities", "crypto"],
        default="equities",
        help="Execution lane (cme, equities, crypto); crypto lane includes additional execution validation.",
    )
    parser.add_argument(
        "--vectorbt",
        action="store_true",
        help="(ignored) VectorBT pre-filter is always enabled for all runs",
    )
    parser.add_argument(
        "--vectorbt-only",
        action="store_true",
        help="(ignored) VectorBT-only mode is no longer supported",
    )
    parser.add_argument(
        "--idea-set",
        action="store_true",
        help="(ignored) Idea generation is always enabled for all runs",
    )
    parser.add_argument("--max-ideas", type=int, default=None, help="Maximum idea records to accept before static filtering")
    parser.add_argument("--review-memory-limit", type=int, default=5, help="Prior AAR/KG memory facts to include")
    parser.add_argument("--idea-temperature", type=float, default=None, help="Sampling temperature for idea generation only")
    parser.add_argument("--idea-top-p", type=float, default=None, help="Top-p sampling for idea generation only")
    parser.add_argument(
        "--search-mode",
        choices=["grid", "random"],
        default="grid",
        help="Parameter search mode for candidate generation.",
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=5,
        help="Number of random samples when --search-mode=random.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=2,
        help="Maximum candidate generation/evaluation iterations before giving up.",
    )
    parser.add_argument(
        "--optimizer-backend",
        choices=["heuristic", "optuna"],
        default="heuristic",
        help="Hyperparameter optimizer backend for retry iterations.",
    )
    parser.add_argument(
        "--optimizer-top-k",
        type=int,
        default=3,
        help="Number of best prior candidates used as heuristic optimizer anchors.",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=None,
        help="Seed for reproducible random search.",
    )
    parser.add_argument("--min-sharpe", type=float, default=None, help="Minimum Sharpe ratio gate.")
    parser.add_argument("--max-drawdown-bps", type=float, default=None, help="Maximum drawdown gate in basis points.")
    parser.add_argument("--max-avg-latency-us", type=float, default=None, help="Maximum average execution latency gate in microseconds.")
    args = parser.parse_args()
    # Enforce mandatory pipeline components.
    args.vectorbt = True
    args.vectorbt_only = False
    args.idea_set = True
    if args.random_seed is not None:
        random.seed(args.random_seed)

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
            search_mode=args.search_mode,
            num_samples=args.num_samples,
            max_iterations=1,
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
            search_mode=args.search_mode,
            num_samples=args.num_samples,
            max_iterations=1,
        ))

    if args.vectorbt or args.vectorbt_only:
        print(f"Running VectorBT filter on {len(candidates)} candidates x grid...")
        source_meta = {c.candidate_id: dict(c.metadata) for c in candidates}
        vbt_gates = PromotionGate(
            min_oos_expectancy=0.0,
            max_drawdown_pct=-50.0,
            min_trades=3 if args.vectorbt_only else 10,
        )
        filter_result = filter_candidates(
            candidates=candidates,
            parsed=parsed,
            event_id=args.event_id,
            repo_root=repo_root,
            gates=vbt_gates,
        )
        print(f"  Promoted: {len(filter_result.promoted)}, Rejected: {len(filter_result.rejected)}")
        for r in filter_result.rejected:
            print(f"  REJECTED {r.candidate_id}: {r.reject_reason}")
        if filter_result.promoted:
            candidates = _promoted_to_candidates(
                filter_result.promoted,
                parsed=parsed,
                source_meta=source_meta,
            )
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
            (artifact_dir / "vectorbt_filter.json").write_text(
                json.dumps(filter_result.to_dict(), indent=2), encoding="utf-8"
            )
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
                "vectorbt_filter": filter_result.to_dict(),
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
            print(json.dumps(payload, indent=2))
            return 0 if candidates else 1

    # === Crypto execution validation for promoted candidates ===
    if args.vectorbt and not args.vectorbt_only:
        _validate_crypto_candidates(candidates, repo_root)

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

    gates = GateThresholds(
        min_trades=0,
        min_sharpe=args.min_sharpe,
        max_drawdown_bps=args.max_drawdown_bps,
        max_avg_latency_us=args.max_avg_latency_us,
    )

    def _evaluate_current(candidate_pool: list[CandidateModel]) -> list[EvaluationResult]:
        evaluated = []
        for cand in candidate_pool:
            print(f"Evaluating {cand.model_id} threshold={cand.strategy_params.get('signal_threshold')}...")
            evaluated.append(
                evaluate_model(cand, args.event_id, repo_root, chi404_summary=chi404, gates=gates)
            )
        return evaluated

    results = _evaluate_current(candidates)
    optimization_trace: list[dict] = []

    max_iterations = max(1, int(args.max_iterations))
    for iteration in range(2, max_iterations + 1):
        if any(r.passes_all_gates() for r in results):
            break
        retry_sample_count = max(1, int(args.num_samples)) * iteration
        retry_max_candidates = max(args.max_candidates, retry_sample_count)
        print(
            "No candidates passed gates; optimizing parameter search "
            f"(iteration {iteration}/{max_iterations}) with "
            f"{args.optimizer_backend} backend, {retry_sample_count} samples, "
            f"and up to {retry_max_candidates} candidates..."
        )
        retry_candidates, trace = propose_optimized_candidates(
            parsed,
            results,
            max_candidates=retry_max_candidates,
            iteration=iteration,
            backend=args.optimizer_backend,
            random_seed=args.random_seed,
            top_k=args.optimizer_top_k,
        )
        optimization_trace.append(trace.to_dict())
        if not retry_candidates:
            print("Optimizer generated no candidates.")
            continue

        print(f"Running VectorBT filter on {len(retry_candidates)} optimized candidates...")
        source_meta = {c.candidate_id: dict(c.metadata) for c in retry_candidates}
        retry_filter_result = filter_candidates(
            candidates=retry_candidates,
            parsed=parsed,
            event_id=args.event_id,
            repo_root=repo_root,
            gates=PromotionGate(
                min_oos_expectancy=0.0,
                max_drawdown_pct=-50.0,
                min_trades=10,
            ),
        )
        print(
            f"  Adaptive promoted: {len(retry_filter_result.promoted)}, "
            f"Rejected: {len(retry_filter_result.rejected)}"
        )
        for rejected in retry_filter_result.rejected:
            print(f"  OPTIMIZER REJECTED {rejected.candidate_id}: {rejected.reject_reason}")
        if not retry_filter_result.promoted:
            print("No optimized candidates survived VectorBT filter.")
            continue

        candidates = _promoted_to_candidates(
            retry_filter_result.promoted,
            parsed=parsed,
            source_meta=source_meta,
        )
        if args.vectorbt and not args.vectorbt_only:
            _validate_crypto_candidates(candidates, repo_root)
        results.extend(_evaluate_current(candidates))

    if optimization_trace:
        (artifact_dir / "optimization_trace.json").write_text(
            json.dumps(optimization_trace, indent=2), encoding="utf-8"
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
