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
from data_layer.llm.packet_runner import run_llm_on_aar_packet
from data_layer.packet.microstructure_aar_packet import (
    build_microstructure_aar_packet,
    validate_packet_schema,
)


def _write_meta(artifact_dir: Path, meta: Dict[str, Any]) -> None:
    (artifact_dir / "after_action_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _load_config(artifact_dir: Path) -> Dict[str, Any]:
    path = artifact_dir / "config.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _valid_kg_annotations(annotations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter to closed-claim kg_annotations only (post phase 7 contract).

    The packet_runner already drops invalid annotations before they reach
    `kg_annotations` in the response. This is a defense-in-depth re-filter
    so a malformed response can't pollute the KG slice on disk.
    """
    from data_layer.packet.validate import drop_uncited_kg_annotations

    return drop_uncited_kg_annotations(annotations)


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
            "response_written": False,
        }
        _write_meta(artifact_dir, meta)
        raise


def _run_after_action_report_impl(
    artifact_dir: Path,
    repo_root: Path,
    *,
    skip_llm: bool = False,
) -> Dict[str, Any]:
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    packet, skip_reasons = build_microstructure_aar_packet(artifact_dir, repo_root)
    schema_errors = validate_packet_schema(packet)
    if schema_errors:
        raise ValueError(f"MicrostructureAARPacket schema errors: {schema_errors}")
    kg_slice, _, _ = ingest_run(artifact_dir, repo_root, packet)
    write_kg_slice(artifact_dir, kg_slice)

    lat = packet.get("latency_authority") or {}
    lat["net_pnl"] = kg_slice["nodes"][0].get("net_pnl")
    packet["latency_authority"] = lat

    similar: List[Dict[str, Any]] = []
    if not skip_llm and "HISTORY_GATE" not in skip_reasons and "AUDIT_INCOMPLETE" not in skip_reasons:
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
        if similar:
            packet["similar_prior_runs"] = similar

    (artifact_dir / "after_action_packet.json").write_text(
        json.dumps(packet, indent=2), encoding="utf-8"
    )

    symbolic = check_latency_invariants(packet)
    (artifact_dir / "after_action_symbolic.json").write_text(
        json.dumps(symbolic, indent=2), encoding="utf-8"
    )

    response = run_llm_on_aar_packet(
        packet,
        symbolic,
        repo_root=repo_root,
        similar_prior_runs=similar,
        skip_llm=skip_llm,
        skip_reasons=skip_reasons,
    )

    from data_layer.packet.validate import validate_aar_packet_out

    out_errors = validate_aar_packet_out(response)
    if out_errors:
        raise ValueError(f"after_action_response failed schema validation: {out_errors}")

    response_path = artifact_dir / "after_action_response.json"
    response_path.write_text(json.dumps(response, indent=2), encoding="utf-8")

    narrative = str(response.get("narrative_md") or "")
    report_path: Optional[Path] = None
    if narrative:
        report_path = artifact_dir / "after_action_report.md"
        report_path.write_text(narrative, encoding="utf-8")

    annotations = _valid_kg_annotations(list(response.get("kg_annotations") or []))
    (artifact_dir / "after_action_annotations.json").write_text(
        json.dumps(annotations, indent=2), encoding="utf-8"
    )
    if annotations and response.get("llm_status") == "ok":
        append_edges(repo_root, annotations)

    llm_status = str(response.get("llm_status", "unavailable"))
    skip_llm_reasons: List[str] = list(skip_reasons)
    if llm_status == "skipped_pdf":
        skip_llm_reasons.append("PDF_CITATIONS_INCOMPLETE")
    elif llm_status.startswith("skipped"):
        skip_llm_reasons.append(llm_status.upper())

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skip_reasons": skip_llm_reasons,
        "llm_status": llm_status,
        "llm_model": response.get("llm_model"),
        "llm_elapsed_s": response.get("llm_elapsed_s"),
        "llm_error": response.get("llm_error"),
        "similar_runs_count": len(similar),
        "symbolic_passed": response.get("symbolic_passed", symbolic.get("passed")),
        "vendor_shas": packet.get("openfoundry_meta", {}).get("vendor_shas"),
        "input_schema_version": packet.get("schema_version"),
        "output_schema_version": response.get("schema_version"),
        "report_written": report_path is not None and report_path.is_file(),
        "response_written": response_path.is_file(),
    }
    _write_meta(artifact_dir, meta)
    return meta


def diagnostics_model_id(artifact_dir: Path) -> str:
    path = artifact_dir / "diagnostics.json"
    if not path.is_file():
        return ""
    return str(json.loads(path.read_text(encoding="utf-8")).get("model_id", ""))
