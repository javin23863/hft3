"""Transitional VectorBT handoff reader (dev/parity only, non-certifying)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backtest_pipeline.src.vectorbt_adapter import compute_screening_artifact_hash


def load_screening_artifact(path: Path) -> tuple[dict[str, Any], list[str], bool]:
    """Load screening artifact; fall back to transitional format when needed."""
    path = Path(path)
    reasons: list[str] = []
    if not path.is_file():
        return {}, [f"screening_artifact_missing:{path}"], False

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("screening_backend") == "vectorbt" and payload.get("screening_artifact_hash"):
        return payload, reasons, False

    if path.name == "screening_artifact.json":
        return payload, reasons, False

    transitional, t_reasons = _build_transitional_screening(path.parent, payload)
    reasons.extend(t_reasons)
    return transitional, reasons, True


def _build_transitional_screening(run_dir: Path, filter_payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    reasons: list[str] = ["transitional_handoff:true"]
    promoted_rows: list[dict[str, Any]] = []
    promotion_dir = run_dir.parent.parent / "promotion"
    for row in filter_payload.get("promoted") or []:
        candidate_id = str(row.get("candidate_id", ""))
        promo_path = promotion_dir / f"{candidate_id}.json"
        if promo_path.is_file():
            promoted_rows.append(json.loads(promo_path.read_text(encoding="utf-8")))
        else:
            promoted_rows.append(dict(row))
            reasons.append(f"transitional_promotion_json_missing:{candidate_id}")

    artifact = {
        "screening_backend": "vectorbt",
        "vectorbt_version": filter_payload.get("vectorbt_version", "unknown"),
        "vectorbt_engine": filter_payload.get("backend", "unknown"),
        "engine_parity_status": "transitional_not_certifying",
        "rust_engine_required_for_scope": False,
        "rust_engine_available": False,
        "license_review": "transitional",
        "candidate_ids": [str(r.get("candidate_id", "")) for r in promoted_rows],
        "promoted_ids": [str(r.get("candidate_id", "")) for r in promoted_rows],
        "rejected_ids": [str(r.get("candidate_id", "")) for r in filter_payload.get("rejected") or []],
        "candidate_reasons": {},
        "promoted_reasons": {},
        "rejected_reasons": {},
        "no_lookahead_signal_shift_proof": {"status": "not_run", "transitional": True},
        "promoted": promoted_rows,
        "transitional_handoff": True,
        "certification_status": "accelerated_not_certifying",
    }
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    return artifact, reasons
