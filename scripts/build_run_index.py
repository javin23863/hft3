#!/usr/bin/env python
"""Build the deterministic run index over HftBacktest-only run artifacts.

One JSONL row per run directory under the given roots, joining
stats_summary.json with the run manifest, gate reports, and promotion
decision. Receipts stay the source of truth: the index is derived,
rebuildable, and self-verifying (every row carries the sha256 of the
stats file it was built from; the index file ends with a summary row
carrying the sha256 of all data rows, so two builds over identical
artifacts are byte-identical).

Consumers: Gate-4 chronological event grouping, self-learning loop
feedback (hbt_results injection), and the trader dashboard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

INDEX_SCHEMA_VERSION = "hft3_run_index_v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _row_from_run_dir(run_dir: Path) -> dict[str, Any] | None:
    stats_path = run_dir / "stats_summary.json"
    manifest = _load_json(run_dir / "run_manifest.json")
    if not stats_path.is_file() and not manifest:
        return None
    stats = _load_json(stats_path)
    gate3 = _load_json(run_dir / "gate3_sensitivity.json")
    gate4 = _load_json(run_dir / "robustness_report.json")
    decision = _load_json(run_dir / "promotion_decision.json")
    audit = _load_json(run_dir / "data_validation.json")

    gate4_status = str(gate4.get("status") or "")
    if gate4_status == "not_run":
        gate4_status = ""
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "run_id": stats.get("run_id") or manifest.get("run_id") or run_dir.name,
        "canonical_model_id": stats.get("canonical_model_id") or manifest.get("canonical_model_id") or "",
        "strategy_id": stats.get("strategy_id") or manifest.get("strategy_id") or "",
        "strategy_surface_version": stats.get("strategy_surface_version") or "",
        "symbol": stats.get("symbol") or manifest.get("symbol") or "",
        "contract": stats.get("contract") or manifest.get("contract") or "",
        "event_id": stats.get("event_id") or manifest.get("event_id") or "",
        "strategy_params": manifest.get("strategy_params") or {},
        "data_validation_status": audit.get("data_validation_status") or "missing",
        "mechanical_validity_status": stats.get("mechanical_validity_status") or "missing",
        "economic_result_status": stats.get("economic_result_status") or "missing",
        "economic_gate_metric": stats.get("economic_gate_metric") or "",
        "realized_closed_trade_pnl": stats.get("realized_closed_trade_pnl"),
        "net_pnl": stats.get("net_pnl"),
        "unrealized_pnl_marked_to_mid": stats.get("unrealized_pnl_marked_to_mid"),
        "end_position": stats.get("end_position"),
        "max_inventory_excursion": stats.get("max_inventory_excursion"),
        "exit_leg_enabled": stats.get("exit_leg_enabled", False),
        "exit_reason": stats.get("exit_reason") or "",
        "orders_submitted": stats.get("orders_submitted"),
        "fills_count": stats.get("fills_count"),
        "fill_rate": stats.get("fill_rate"),
        "gate3_status": str(gate3.get("status") or ""),
        "gate3_min_realized": gate3.get("min_realized_closed_trade_pnl"),
        "gate3_axes_unavailable_upstream": list(gate3.get("axes_unavailable_upstream") or []),
        "gate4_status": gate4_status,
        "gate4_psr": gate4.get("psr"),
        "gate4_dsr": gate4.get("dsr"),
        "gate4_pbo": (gate4.get("cscv") or {}).get("pbo"),
        "promotion_decision": decision.get("decision") or "",
        "promotion_allowed": decision.get("promotion_allowed", False),
        "fail_closed_reasons": list(stats.get("fail_closed_reasons") or []),
        "artifact_dir": str(run_dir),
        "stats_summary_sha256": _sha256_file(stats_path) if stats_path.is_file() else "",
    }


def build_run_index(roots: list[Path], out_path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    scanned = 0
    for root in roots:
        if not root.is_dir():
            continue
        for run_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            scanned += 1
            row = _row_from_run_dir(run_dir)
            if row is not None:
                rows.append(row)
    rows.sort(key=lambda r: str(r["run_id"]))

    lines = [json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) for row in rows]
    data_digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    summary = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "row_kind": "index_summary",
        "rows": len(rows),
        "run_dirs_scanned": scanned,
        "data_sha256": data_digest,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(line + "\n")
        handle.write(json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the HBT run index (derived, rebuildable)")
    parser.add_argument(
        "--roots",
        type=Path,
        nargs="+",
        default=[REPO / "artifacts" / "hbt_runs"],
        help="Run-directory roots to scan (each child dir = one run).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "runtime" / "run_index" / "hbt_run_index.jsonl",
    )
    args = parser.parse_args(argv)
    summary = build_run_index([r.resolve() for r in args.roots], args.out.resolve())
    print(json.dumps({**summary, "out": str(args.out.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
