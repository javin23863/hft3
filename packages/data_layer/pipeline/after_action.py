"""Post-run after-action orchestrator."""

from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from data_layer.ingest_run import ingest_run, write_kg_slice
from data_layer.kg.query import find_similar_runs
from data_layer.kg.store import append_edges
from data_layer.llm import ollama_client
from data_layer.llm.prompts import SYSTEM_PROMPT, build_user_prompt, parse_annotations
from data_layer.packet.microstructure_aar_packet import (
    build_microstructure_aar_packet,
    validate_packet_schema,
)
from data_layer.symbolic.latency_invariants import check_latency_invariants


def _write_meta(artifact_dir: Path, meta: Dict[str, Any]) -> None:
    (artifact_dir / "after_action_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _load_config(artifact_dir: Path) -> Dict[str, Any]:
    path = artifact_dir / "config.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def run_after_action_report(
    artifact_dir: Path,
    repo_root: Path,
    *,
    skip_llm: bool = False,
) -> Dict[str, Any]:
    artifact_dir = Path(artifact_dir)
    repo_root = Path(repo_root)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    try:
        return _run_after_action_report_impl(artifact_dir, repo_root, skip_llm=skip_llm)
    except Exception as exc:
        meta = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "llm_status": "failed",
            "after_action_failed": str(exc),
            "traceback": traceback.format_exc(),
            "skip_reasons": ["AFTER_ACTION_FAILED"],
            "symbolic_passed": None,
            "report_written": False,
        }
        _write_meta(artifact_dir, meta)
        raise


def _run_after_action_report_impl(
    artifact_dir: Path,
    repo_root: Path,
    *,
    skip_llm: bool = False,
) -> Dict[str, Any]:
    packet, skip_reasons = build_microstructure_aar_packet(artifact_dir, repo_root)
    schema_errors = validate_packet_schema(packet)
    if schema_errors:
        raise ValueError(f"MicrostructureAARPacket schema errors: {schema_errors}")
    kg_slice, _, _ = ingest_run(artifact_dir, repo_root, packet)
    write_kg_slice(artifact_dir, kg_slice)

    lat = packet.get("latency_authority") or {}
    lat["net_pnl"] = kg_slice["nodes"][0].get("net_pnl")
    packet["latency_authority"] = lat

    symbolic = check_latency_invariants(packet)
    (artifact_dir / "after_action_packet.json").write_text(
        json.dumps(packet, indent=2), encoding="utf-8"
    )
    (artifact_dir / "after_action_symbolic.json").write_text(
        json.dumps(symbolic, indent=2), encoding="utf-8"
    )

    llm_status = "skipped"
    skip_llm_reasons: List[str] = list(skip_reasons)
    report_path: Optional[Path] = None
    annotations: List[Dict[str, Any]] = []
    llm_error: Optional[str] = None
    llm_model: Optional[str] = None
    llm_elapsed_s: Optional[float] = None
    similar: List[Dict[str, Any]] = []

    llm_blocked = (
        skip_llm
        or "HISTORY_GATE" in skip_reasons
        or "AUDIT_INCOMPLETE" in skip_reasons
        or not packet.get("pdf_citations_complete")
    )

    if llm_blocked:
        if skip_llm:
            llm_status = "skipped_flag"
        elif "HISTORY_GATE" in skip_reasons:
            llm_status = "skipped_history_gate"
        elif "AUDIT_INCOMPLETE" in skip_reasons:
            llm_status = "skipped_audit_incomplete"
        elif not packet.get("pdf_citations_complete"):
            llm_status = "skipped_pdf_incomplete"
            skip_llm_reasons.append("PDF_CITATIONS_INCOMPLETE")
    elif not ollama_client.ollama_available():
        llm_status = "unavailable"
        skip_llm_reasons.append("LLM_UNAVAILABLE")
        llm_error = "ollama unavailable or Hawkish model not in ollama list"
    else:
        config = _load_config(artifact_dir)
        evt = packet.get("event_context") or {}
        similar = find_similar_runs(
            repo_root,
            str(config.get("model_id") or diagnostics_model_id(artifact_dir)),
            str(evt.get("event_state") or ""),
            str(lat.get("lane_required") or ""),
            breakeven_us=lat.get("breakeven_us"),
            exclude_run_id=packet.get("run_id"),
            limit=5,
        )
        user = build_user_prompt(
            packet,
            symbolic,
            packet.get("pdf_citations", []),
            similar_runs=similar,
        )
        result = ollama_client.generate(SYSTEM_PROMPT, user)
        llm_model = result.model
        llm_elapsed_s = result.elapsed_s
        if result.text:
            report_path = artifact_dir / "after_action_report.md"
            report_path.write_text(result.text, encoding="utf-8")
            annotations = parse_annotations(result.text)
            llm_status = "ok"
        else:
            llm_status = "unavailable"
            skip_llm_reasons.append("LLM_UNAVAILABLE")
            llm_error = result.error or "empty generation"

    (artifact_dir / "after_action_annotations.json").write_text(
        json.dumps(annotations, indent=2), encoding="utf-8"
    )
    if annotations:
        valid = [
            e
            for e in annotations
            if isinstance(e, dict)
            and e.get("from")
            and e.get("to")
            and e.get("relation")
            and e.get("scope") in ("discovery_only", "infra", "latency_probe")
        ]
        if valid:
            append_edges(repo_root, valid)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skip_reasons": skip_llm_reasons,
        "llm_status": llm_status,
        "llm_model": llm_model,
        "llm_elapsed_s": llm_elapsed_s,
        "llm_error": llm_error,
        "similar_runs_count": len(similar),
        "symbolic_passed": symbolic.get("passed"),
        "vendor_shas": packet.get("openfoundry_meta", {}).get("vendor_shas"),
        "report_written": report_path is not None and report_path.is_file(),
    }
    _write_meta(artifact_dir, meta)
    return meta


def diagnostics_model_id(artifact_dir: Path) -> str:
    path = artifact_dir / "diagnostics.json"
    if not path.is_file():
        return ""
    return str(json.loads(path.read_text(encoding="utf-8")).get("model_id", ""))
