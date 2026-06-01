"""Deploy selected candidate to research artifacts (no live gateway)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from data_layer.ingest_run import ingest_run
from data_layer.openfoundry_bridge import read_vendor_lock, validate_connector

from research_pipeline.types import CandidateModel, EvaluationResult, PipelineReport


def _build_packet(
    run_id: str,
    event_id: str,
    candidate: CandidateModel,
    eval_result: EvaluationResult,
    repo_root: Path,
) -> Dict[str, Any]:
    try:
        of = validate_connector(repo_root)
    except Exception as exc:
        print(f"Warning: connector validation failed, using defaults: {exc}", file=sys.stderr)
        of = {"connector": {"connector_id": "unknown", "asset_class": "CME_FUTURES", "schema_version": "0"}, "vendor_shas": {}}
    return {
        "run_id": run_id,
        "openfoundry_meta": {
            "connector_id": of["connector"]["connector_id"],
            "asset_class": of["connector"]["asset_class"],
            "schema_version": of["connector"]["schema_version"],
            "vendor_shas": of["vendor_shas"],
        },
        "event_context": {
            "event_id": event_id,
            "event_state": "during",
            "event_state_heuristic": True,
        },
        "latency_authority": {
            "lane_required": "research",
            "breakeven_us": None,
            "lane_pass": True,
            "promote_candidate": eval_result.passes_all_gates(),
            "net_pnl": eval_result.net_pnl,
        },
        "config": {
            "model_id": candidate.model_id,
            "strategy_params": candidate.strategy_params,
            "pipeline": True,
        },
    }


def deploy_model(
    repo_root: Path,
    report: PipelineReport,
    eval_result: EvaluationResult,
    *,
    live_deploy: bool = False,
) -> Path:
    if live_deploy:
        raise RuntimeError("Live deploy blocked until CHI404 is online (BLUEPRINT §4)")

    artifact_dir = repo_root / "research_cards" / "pipeline_runs" / report.run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "run_id": report.run_id,
        "thesis": report.thesis,
        "event_id": report.event_id,
        "model_id": eval_result.candidate.model_id,
        "strategy_params": eval_result.candidate.strategy_params,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (artifact_dir / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )

    result_payload = {
        "report": report.to_dict(),
        "evaluation": {
            "net_pnl": eval_result.net_pnl,
            "num_trades": eval_result.num_trades,
            "win_rate": eval_result.win_rate,
            "passes_gates": eval_result.passes_all_gates(),
        },
    }
    (artifact_dir / "result.json").write_text(
        json.dumps(result_payload, indent=2), encoding="utf-8"
    )

    md_lines = [
        f"# Pipeline run {report.run_id}",
        "",
        f"**Thesis:** {report.thesis}",
        f"**Event:** {report.event_id}",
        f"**Model:** {eval_result.candidate.model_id}",
        f"**Net PnL:** {eval_result.net_pnl:.4f}",
        f"**Passes gates:** {eval_result.passes_all_gates()}",
        "",
    ]
    if report.document_summary:
        md_lines.extend(["## Document summary", "", report.document_summary, ""])
    (artifact_dir / "report.md").write_text("\n".join(md_lines), encoding="utf-8")

    packet = _build_packet(
        report.run_id,
        report.event_id,
        eval_result.candidate,
        eval_result,
        repo_root,
    )
    try:
        ingest_run(artifact_dir, repo_root, packet)
    except Exception as exc:
        print(f"Warning: data layer ingest failed: {exc}", file=sys.stderr)
    return artifact_dir


def deploy_best(
    repo_root: Path,
    report: PipelineReport,
    *,
    live_deploy: bool = False,
) -> Optional[Path]:
    passing = [r for r in report.results if r.passes_all_gates()]
    if not passing and report.results:
        passing = [max(report.results, key=lambda r: r.net_pnl)]
    if not passing:
        return None
    best = max(passing, key=lambda r: r.net_pnl)
    report.selected = best.candidate
    path = deploy_model(repo_root, report, best, live_deploy=live_deploy)
    report.artifact_dir = str(path)
    return path
