#!/usr/bin/env python3
"""End-to-end autoresearch pipeline CLI."""

from __future__ import annotations

import argparse
import json
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
from backtest_pipeline.src.vectorbt_adapter import filter_candidates
from backtest_pipeline.src.promotion_gate import PromotionGate
from research_pipeline.packets import (
    build_pipeline_request,
    build_pipeline_response,
    write_pipeline_packets,
)
from research_pipeline.types import CandidateModel, GateThresholds, PipelineReport, ParsedHypothesis


def _run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"pipeline_{ts}_{uuid.uuid4().hex[:8]}"


def _pipeline_llm_status(parsed: ParsedHypothesis, *, no_llm: bool) -> str:
    if no_llm:
        return "skipped_no_llm"
    if parsed.llm_status:
        return parsed.llm_status
    return "ok" if parsed.source == "ollama" else "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser(description="Autoresearch pipeline")
    parser.add_argument("--thesis", required=True, help="Natural-language trading thesis")
    parser.add_argument("--doc", type=Path, help="Optional research document (PDF/DOCX/URL)")
    parser.add_argument("--event-id", default="CPI_2024_09_11_TIGHT")
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--chi404-summary", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Parse and generate only")
    parser.add_argument("--no-llm", action="store_true", help="Heuristic hypothesis parse only")
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument("--vectorbt", action="store_true", help="Enable VectorBT pre-filter before HftBacktest")
    parser.add_argument("--vectorbt-only", action="store_true", help="Run VectorBT filter only, skip HftBacktest")
    args = parser.parse_args()

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
        text = extract_text(args.doc)
        doc_summary = summarise_text(text)
        doc_id = f"doc:{args.doc.stem}"
        kg = build_knowledge_graph(text, doc_id=doc_id)
        persist_graph_slice(repo_root, kg)

    parsed = parse_hypothesis(
        args.thesis,
        use_llm=not args.no_llm,
        pipeline_request=request,
        repo_root=repo_root,
    )
    candidates = list(generate_candidates(
        parsed, max_candidates=args.max_candidates,
        expand_for_vectorbt=bool(args.vectorbt or args.vectorbt_only),
    ))

    if args.vectorbt or args.vectorbt_only:
        print(f"Running VectorBT filter on {len(candidates)} candidates x grid...")
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
            candidates = [
                CandidateModel(
                    candidate_id=p.candidate_id,
                    model_id=p.hypothesis_id,
                    strategy_params=p.param_values,
                    thesis=parsed.thesis,
                    metadata={"strategy_family": p.strategy_family, "promoted": True, "vectorbt_run_id": p.vectorbt_run_id, "vectorbt_results": p.vectorbt_results},
                )
                for p in filter_result.promoted
            ]
        else:
            print("No candidates survived VectorBT filter.")
            if args.vectorbt_only:
                return 1

    if args.dry_run:
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
        llm_status = _pipeline_llm_status(parsed, no_llm=args.no_llm)
        response = build_pipeline_response(
            report,
            request,
            llm_status=llm_status,
            llm_model=None if llm_status != "ok" else "glm-5.1:cloud",
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
        print(json.dumps(payload, indent=2))
        return 0

    chi404 = args.chi404_summary
    if chi404 is None:
        default_lat = repo_root / "runtime" / "latency_reports" / "latency_summary.json"
        chi404 = default_lat if default_lat.is_file() else None

    gates = GateThresholds(min_trades=0)
    results = []
    for cand in candidates:
        print(f"Evaluating {cand.model_id} threshold={cand.strategy_params.get('signal_threshold')}...")
        results.append(
            evaluate_model(cand, args.event_id, repo_root, chi404_summary=chi404, gates=gates)
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

    artifact = deploy_best(repo_root, report)
    llm_status = _pipeline_llm_status(parsed, no_llm=args.no_llm)
    response = build_pipeline_response(
        report,
        request,
        llm_status=llm_status,
        llm_model=None if llm_status != "ok" else "glm-5.1:cloud",
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
