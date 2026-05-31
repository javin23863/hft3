"""Fan out per-hypothesis reports from one event_accurate_mbo pass."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from backtest_pipeline.src.pipeline_model_router import route
from hft3.validation.research_stamp import build_certification_stamp, format_stamp_footer


from features_engine.src.model_registry import get_slug_for_hyp_id


def hyp_id_to_model_id(hypothesis_id: int) -> str:
    return get_slug_for_hyp_id(hypothesis_id)


def _relative_repo_path(repo_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _write_hyp_artifact(
    artifact_dir: Path,
    *,
    event_id: str,
    model_id: str,
    row: Dict[str, Any],
    latency_ms: float,
    backend_label: str,
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = build_certification_stamp(
        event_id=event_id,
        model_id=model_id,
        latency_band=latency_ms,
        execution_mode="REPLAY",
        execution_adapter_mode="event_accurate_mbo",
    )
    payload = {
        "model_id": model_id,
        "event_id": event_id,
        "engine": "event_accurate_mbo",
        "engine_kind": "hyp_mbo",
        "backend_label": backend_label,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "latency_ms": latency_ms,
        "result": row,
        "certification_stamp": stamp,
        "certification_footer": format_stamp_footer(stamp),
    }
    (artifact_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md = [
        f"# {model_id} — {event_id}",
        "",
        backend_label,
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| hypothesis_name | {row.get('hypothesis_name', '')} |",
        f"| num_trades | {row.get('num_trades', 0)} |",
        f"| net_pnl_usd | {row.get('net_pnl_usd', 0)} |",
        f"| win_rate | {row.get('win_rate', 0)} |",
        f"| expectancy_usd | {row.get('expectancy_usd', 0)} |",
        "",
        "Single NPZ pass via `run_event_accurate_mbo`; no per-hypothesis NPZ re-run.",
    ]
    (artifact_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def fan_out_hyp_reports(
    mbo_result: Dict[str, Any],
    event_id: str,
    out_root: Path,
    *,
    repo_root: Path,
    only_model_ids: Optional[Set[str]] = None,
    latency_ms: float,
) -> List[Dict[str, Any]]:
    """Write artifact dirs and return executed model rows."""
    rows: List[Dict[str, Any]] = []
    backend_label = route("HYP_1").backend_label
    for hyp_row in mbo_result.get("all_hypotheses") or []:
        hyp_id = int(hyp_row["hypothesis_id"])
        model_id = hyp_id_to_model_id(hyp_id)
        if only_model_ids is not None and model_id not in only_model_ids:
            continue
        artifact_dir = out_root / f"{model_id}_{event_id}"
        _write_hyp_artifact(
            artifact_dir,
            event_id=event_id,
            model_id=model_id,
            row=hyp_row,
            latency_ms=latency_ms,
            backend_label=backend_label,
        )
        status = "FAIL" if hyp_row.get("error") else "PASS"
        rows.append(
            {
                "model_id": model_id,
                "engine_kind": "hyp_mbo",
                "status": status,
                "artifact_dir": _relative_repo_path(repo_root, artifact_dir),
                "num_trades": int(hyp_row.get("num_trades") or 0),
                "net_pnl_usd": float(hyp_row.get("net_pnl_usd") or 0.0),
                "backend_label": backend_label,
            }
        )
    return rows
