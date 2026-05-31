"""Catalog gate rollup: 55-model manifest + master report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from backtest_pipeline.src.pipeline_model_router import all_model_ids, route

RUNTIME_BUDGET_NOTES = {
    "smoke": "~10–20 min (hybrid optional + one MBO pass + HYP_1/HYP_5 fan-out)",
    "catalog": "~2–4 hr (full hybrid + 44 HYP fan-out + PDF structural/diagnostics/options)",
}


def finalize_catalog_models(executed_rows: List[Dict[str, Any]], tier: str) -> List[Dict[str, Any]]:
    """Merge executed rows with placeholders until len == len(all_model_ids())."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in executed_rows:
        mid = row["model_id"]
        if mid not in by_id:
            by_id[mid] = row
    out: List[Dict[str, Any]] = []
    for model_id in all_model_ids():
        if model_id in by_id:
            out.append(by_id[model_id])
            continue
        rt = route(model_id)
        status = "NOT_RUN_SMOKE" if tier == "smoke" else "NOT_RUN"
        reason = "smoke tier samples HYP_1/HYP_5 + PDF_MODEL_4 only" if tier == "smoke" else "not executed"
        out.append(
            {
                "model_id": model_id,
                "engine_kind": rt.engine_kind,
                "status": status,
                "artifact_dir": None,
                "num_trades": None,
                "net_pnl_usd": None,
                "backend_label": rt.backend_label,
                "reason": reason,
            }
        )
    return out


def write_catalog_artifacts(
    repo_root: Path,
    *,
    tier: str,
    event_id: str,
    symbol: str,
    models: List[Dict[str, Any]],
    steps: List[Dict[str, Any]],
    overall_pass: bool,
) -> Path:
    catalog_dir = repo_root / "research_cards" / "pipeline_runs"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    gate_path = repo_root / "runtime" / "reports" / "full_pipeline_gate.json"

    executed = [m for m in models if m.get("status") in ("PASS", "FAIL", "SKIP_NO_FIXTURE")]
    not_run = len(models) - len(executed)

    manifest: Dict[str, Any] = {
        "gate": "full_pipeline_catalog",
        "status": "PASS" if overall_pass else "FAIL",
        "validation_scope": (
            "smoke_sample" if tier == "smoke" else "full_catalog_execution"
        ),
        "validation_scope_note": (
            "PASS means smoke plumbing only (PDF_MODEL_4 + HYP_1/HYP_5 executed); "
            "55 rows include NOT_RUN_SMOKE placeholders — not a full-catalog backtest claim."
            if tier == "smoke"
            else "PASS means all model routes executed for this event/symbol tier."
        ),
        "runtime_tier": tier,
        "runtime_budget_note": RUNTIME_BUDGET_NOTES.get(tier, tier),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event_id": event_id,
        "symbol": symbol,
        "models": models,
        "models_executed": len(executed),
        "models_not_run": not_run,
        "steps": steps,
        "repeat_command": (
            f"python scripts/run_full_pipeline_gate.py --tier {tier} "
            f"--event-id {event_id} --symbol {symbol}"
        ),
    }
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    result_path = catalog_dir / "result.json"
    result_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    md_lines = [
        f"# Full pipeline catalog — {event_id}",
        "",
        f"- **tier:** {tier}",
        f"- **symbol:** {symbol}",
        f"- **status:** {manifest['status']}",
        f"- **models:** {len(models)} rows (executed {len(executed)}, not run {not_run})",
        f"- **validation_scope:** {manifest.get('validation_scope')} — {manifest.get('validation_scope_note')}",
        f"- **runtime budget:** {RUNTIME_BUDGET_NOTES.get(tier, tier)}",
        "",
        "| model_id | engine_kind | status | trades | net_pnl | backend (honest) |",
        "|----------|-------------|--------|--------|---------|------------------|",
    ]
    for m in models:
        trades = m.get("num_trades")
        trades_s = "" if trades is None else str(trades)
        pnl = m.get("net_pnl_usd")
        pnl_s = "" if pnl is None else f"{pnl:.4f}"
        md_lines.append(
            f"| {m['model_id']} | {m['engine_kind']} | {m.get('status')} | "
            f"{trades_s} | {pnl_s} | {m.get('backend_label', '')} |"
        )
    (catalog_dir / "report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return gate_path
