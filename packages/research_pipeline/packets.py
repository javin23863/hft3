"""Build and validate research pipeline request/response packets."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from data_layer.openfoundry_bridge import validate_connector
from data_layer.packet.validate import validate_pipeline_request, validate_pipeline_response

from research_pipeline.types import PipelineReport


def build_pipeline_request(
    *,
    request_id: str,
    thesis: str,
    event_id: str,
    repo_root: Path,
    max_candidates: int,
    document_ref: Optional[str] = None,
) -> Dict[str, Any]:
    of = validate_connector(repo_root)
    req: Dict[str, Any] = {
        "schema_version": "1",
        "request_id": request_id,
        "thesis": thesis,
        "event_id": event_id,
        "openfoundry_meta": {
            "connector_id": of["connector"]["connector_id"],
            "asset_class": of["connector"]["asset_class"],
            "vendor_shas": of["vendor_shas"],
            "schema_version": of["connector"]["schema_version"],
        },
        "max_candidates": max_candidates,
    }
    if document_ref:
        req["document_ref"] = document_ref
    errors = validate_pipeline_request(req)
    if errors:
        raise ValueError(f"PipelineRequestPacket schema errors: {errors}")
    return req


def build_pipeline_response(
    report: PipelineReport,
    request: Dict[str, Any],
    *,
    llm_status: str = "ok",
    llm_model: Optional[str] = None,
    llm_error: Optional[str] = None,
) -> Dict[str, Any]:
    """Slim response — paths/refs only, no embedded workbench_out blobs."""
    resp: Dict[str, Any] = {
        "schema_version": "1",
        "request_id": request["request_id"],
        "run_id": report.run_id,
        "event_id": report.event_id,
        "llm_model": llm_model,
        "llm_status": llm_status,
        "openfoundry_meta": request["openfoundry_meta"],
        "parsed": {
            "primary_model_id": report.parsed.primary_model_id,
            "instrument_universe": list(report.parsed.instrument_universe),
            "indicators": list(report.parsed.indicators),
            "source": report.parsed.source,
        },
        "candidates_tested": report.candidates_tested,
        "selected_model_id": report.selected.model_id if report.selected else None,
        "artifact_dir": report.artifact_dir,
        "document_summary": report.document_summary,
        "generated_at": report.generated_at,
        "results": [
            {
                "candidate_id": r.candidate.candidate_id,
                "model_id": r.candidate.model_id,
                "net_pnl": r.net_pnl,
                "num_trades": r.num_trades,
                "passes": r.passes_all_gates(),
                "error": r.error,
            }
            for r in report.results
        ],
    }
    if llm_error:
        resp["llm_error"] = llm_error
    errors = validate_pipeline_response(resp)
    if errors:
        raise ValueError(f"PipelineResponsePacket schema errors: {errors}")
    return resp


def write_pipeline_packets(
    artifact_dir: Path,
    request: Dict[str, Any],
    response: Dict[str, Any],
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    import json

    (artifact_dir / "request_packet.json").write_text(
        json.dumps(request, indent=2), encoding="utf-8"
    )
    (artifact_dir / "response_packet.json").write_text(
        json.dumps(response, indent=2), encoding="utf-8"
    )
