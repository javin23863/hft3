"""Materialize workbench-shaped artifacts for hybrid-gate after-action reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


def _ablation_rows(ablation_matrix: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not ablation_matrix:
        return []
    rows: List[Dict[str, Any]] = []
    for mode in ablation_matrix.get("modes", []):
        metrics = mode.get("metrics") or {}
        rows.append(
            {
                "mode_id": mode.get("mode_id"),
                "use_ofi": mode.get("use_ofi"),
                "use_vpin": mode.get("use_vpin"),
                "num_trades": metrics.get("num_trades", 0),
                "net_pnl_after_fee": metrics.get("net_pnl_after_fee", metrics.get("net_pnl", 0)),
                "mean_vpin": metrics.get("mean_vpin"),
                "quote_refresh_count": metrics.get("quote_refresh_count"),
            }
        )
    return rows


def write_hybrid_aar_artifacts(
    artifact_dir: Path,
    *,
    event_id: str,
    symbol: str,
    hybrid_payload: Dict[str, Any],
    ablation_matrix: Optional[Dict[str, Any]],
    latency_ms: float,
    latency_source: str,
) -> Path:
    """Write diagnostics/manifest/config for run_after_action_report."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    result = hybrid_payload.get("result") or {}
    balance = float(result.get("balance", 0.0))
    fee = float(result.get("fee", 0.0))

    diagnostics: Dict[str, Any] = {
        "model_id": "PDF_MODEL_4",
        "engine": "pdf_hybrid",
        "engine_kind": "pdf_hybrid_replay",
        "execution_assumptions": "quote_engine",
        "event_id": event_id,
        "symbol": symbol,
        "num_trades": int(result.get("num_trades", 0)),
        "net_pnl": balance,
        "balance": balance,
        "fee": fee,
        "position": float(result.get("position", 0.0)),
        "steps": int(result.get("steps", 0)),
        "latency_ms": latency_ms,
        "latency_source": latency_source,
        "latency_authority": "workstation_replay",
        "data_sufficient": True,
        "eval_scope": "discovery_single_event_gate",
        "cpp_replay_available": False,
        "promote_candidate": False,
        "ablation_modes": _ablation_rows(ablation_matrix),
        "gate_latency_note": hybrid_payload.get("gate_latency_note"),
    }

    manifest = {
        "event_id": event_id,
        "symbol": symbol,
        "data_sufficient": True,
        "history_years_available": None,
        "eval_scope": "discovery_single_event_gate",
    }

    config = {
        "model_id": "PDF_MODEL_4",
        "event_id": event_id,
        "symbol": symbol,
        "latency_ms": latency_ms,
        "execution_assumptions": "quote_engine",
        "engine": "pdf_hybrid",
    }

    (artifact_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    (artifact_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (artifact_dir / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return artifact_dir
