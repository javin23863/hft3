#!/usr/bin/env python3
"""Backfill catalog symbols and run workbench campaigns: all models × instruments."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
except ImportError:
    pass

CATALOG_SYMBOLS: tuple[str, ...] = (
    "MES.v.0",
    "ES.v.0",
    "NQ.v.0",
    "MNQ.v.0",
    "ZN.v.0",
    "ZB.v.0",
)


def _runnable_event_count(model_id: str, symbol: str, repo_root: Path) -> int:
    from workbench.src.data.event_catalog import list_campaign_events, load_periods

    total = 0
    for period in load_periods(repo_root):
        for ev in list_campaign_events(model_id, period, symbol, repo_root):
            if ev.npz_present:
                total += 1
    return total


def _select_models(
    repo_root: Path,
    *,
    include_diagnostics: bool,
    include_options: bool,
    model_filter: Optional[List[str]],
) -> List[str]:
    from workbench.src.data.event_catalog import load_model_binding
    from workbench.src.registry.unified_registry import build_models_config, list_models

    ids = model_filter or list_models()
    out: List[str] = []
    for mid in ids:
        if mid not in build_models_config():
            continue
        cfg = build_models_config()[mid]
        binding = load_model_binding(repo_root, mid)
        if binding.get("campaign_mode") == "options_lane" and not include_options:
            continue
        if getattr(cfg, "diagnostics_only", False) and not include_diagnostics:
            continue
        out.append(mid)
    return sorted(out)


def _union_missing_for_symbol(
    repo_root: Path,
    symbol: str,
    models: List[str],
) -> List[Any]:
    """Union missing EventSpec across models (same symbol, different allowed_contexts)."""
    from workbench.src.data.catalog_backfill import missing_for_campaign

    by_id: Dict[str, Any] = {}
    for mid in models:
        for ev in missing_for_campaign(repo_root, mid, symbol):
            by_id[ev.event_id] = ev
    return list(by_id.values())


def backfill_symbols(
    repo_root: Path,
    symbols: List[str],
    *,
    models: List[str],
    max_cost_usd: Optional[float],
    dry_run: bool,
) -> Dict[str, Any]:
    from workbench.src.data.catalog_backfill import download_events, estimate_download_cost_usd

    report: Dict[str, Any] = {
        "symbols": {},
        "downloaded_event_ids": [],
        "budget_cap_usd": max_cost_usd,
        "budget_spent_usd": 0.0,
    }
    budget_left = float(max_cost_usd) if max_cost_usd is not None else None
    has_key = bool(os.getenv("DATABENTO_API_KEY"))

    for sym in symbols:
        missing = _union_missing_for_symbol(repo_root, sym, models)
        est = estimate_download_cost_usd(missing) if has_key else 0.0
        entry: Dict[str, Any] = {
            "missing_count": len(missing),
            "estimated_cost_usd": est,
            "downloaded": [],
        }
        report["symbols"][sym] = entry
        print(f"[backfill] {sym}: {len(missing)} missing, est ${est:.2f}")
        if dry_run or not missing:
            continue
        if not has_key:
            entry["error"] = "DATABENTO_API_KEY not set"
            print(f"  SKIP: {entry['error']}")
            continue
        if len(missing) > 0 and est == 0.0 and has_key:
            entry["warning"] = "zero cost estimate with missing events; verify symbology"
            print(f"  WARN: {entry['warning']}")
        if budget_left is not None and est > budget_left:
            entry["error"] = (
                f"estimated ${est:.2f} exceeds remaining budget ${budget_left:.2f}"
            )
            print(f"  SKIP: {entry['error']}")
            continue
        try:
            done = download_events(repo_root, missing, max_cost_usd=budget_left)
        except Exception as exc:
            entry["error"] = str(exc)
            print(f"  ERROR: {exc}")
            continue
        entry["downloaded"] = done
        report["downloaded_event_ids"].extend(done)
        if budget_left is not None:
            budget_left = max(0.0, budget_left - est)
            report["budget_spent_usd"] = float(max_cost_usd) - budget_left
        print(f"  downloaded {len(done)} events")
    return report


def run_sweep(
    repo_root: Path,
    symbols: List[str],
    models: List[str],
    *,
    dry_run: bool,
    trial_mode: bool,
) -> Dict[str, Any]:
    from workbench.src.run.campaign_runner import run_campaign

    chi404 = repo_root / "runtime" / "latency_reports" / "latency_summary.json"
    chi404_path = chi404 if chi404.is_file() else None
    if not trial_mode and chi404_path is None:
        raise RuntimeError(
            "--full-wfc requires runtime/latency_reports/latency_summary.json on disk"
        )

    rows: List[Dict[str, Any]] = []
    for model_id in models:
        for symbol in symbols:
            runnable = _runnable_event_count(model_id, symbol, repo_root)
            row: Dict[str, Any] = {
                "model_id": model_id,
                "symbol": symbol,
                "runnable_events": runnable,
            }
            if runnable == 0:
                row["status"] = "SKIP_NO_NPZ"
                rows.append(row)
                print(f"[sweep] SKIP {model_id} {symbol} (0 runnable events)")
                continue
            if dry_run:
                row["status"] = "DRY_RUN"
                rows.append(row)
                print(f"[sweep] DRY_RUN {model_id} {symbol} ({runnable} events)")
                continue
            t0 = time.time()
            try:
                result = run_campaign(
                    repo_root,
                    model_id,
                    symbol,
                    chi404_summary=chi404_path,
                    audit_grade=not trial_mode,
                    allow_partial=trial_mode,
                    trial_mode=trial_mode,
                )
                row["status"] = result.status
                row["campaign_id"] = result.campaign_id
                row["artifact_dir"] = result.artifact_dir
                row["periods"] = [
                    {
                        "name": p.name,
                        "gate_pass": p.gate_pass,
                        "expectancy": p.expectancy,
                        "events_run": p.events_run,
                        "events_missing": p.events_missing,
                    }
                    for p in result.periods
                ]
                print(
                    f"[sweep] {model_id} {symbol} -> {result.status} "
                    f"({time.time() - t0:.0f}s)"
                )
            except Exception as exc:
                row["status"] = "ERROR"
                row["error"] = str(exc)
                print(f"[sweep] ERROR {model_id} {symbol}: {exc}")
            rows.append(row)

    return {"rows": rows, "model_count": len(models), "symbol_count": len(symbols)}


def _sweep_exit_code(sweep: Dict[str, Any]) -> int:
    bad: Set[str] = {"ERROR", "DATA_INSUFFICIENT", "CANCELLED"}
    for row in sweep.get("rows", []):
        if row.get("status") in bad:
            return 1
    return 0


def _backfill_exit_code(backfill: Dict[str, Any]) -> int:
    for entry in backfill.get("symbols", {}).values():
        if entry.get("error") and entry.get("missing_count", 0) > 0:
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Model × symbol workbench sweep")
    parser.add_argument(
        "--symbols",
        default=",".join(CATALOG_SYMBOLS),
        help="Comma-separated research symbols",
    )
    parser.add_argument("--models", default=None, help="Comma-separated model ids (default: all)")
    parser.add_argument("--backfill", action="store_true", help="Download missing NPZ per symbol")
    parser.add_argument("--sweep", action="store_true", help="Run campaigns")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=30.0,
        help="Global budget cap across all symbols (not per-symbol)",
    )
    parser.add_argument("--include-diagnostics", action="store_true")
    parser.add_argument("--include-options", action="store_true")
    parser.add_argument(
        "--full-wfc",
        action="store_true",
        help="Run full WFC matrix (slow; default is trial/fast sweep)",
    )
    parser.add_argument(
        "--vectorbt-pre-filter",
        action="store_true",
        help="Apply VectorBT cheap filter before per-model campaign",
    )
    args = parser.parse_args()

    if not args.backfill and not args.sweep:
        parser.error("Specify --backfill and/or --sweep (add --dry-run to preview without downloads)")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    model_filter = [m.strip() for m in args.models.split(",")] if args.models else None
    models = _select_models(
        _REPO,
        include_diagnostics=args.include_diagnostics,
        include_options=args.include_options,
        model_filter=model_filter,
    )
    trial_mode = not args.full_wfc

    out_dir = _REPO / "runtime" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "symbols": symbols,
        "models": models,
        "dry_run": args.dry_run,
        "trial_mode": trial_mode,
    }
    exit_code = 0

    if args.backfill:
        payload["backfill"] = backfill_symbols(
            _REPO,
            symbols,
            models=models,
            max_cost_usd=args.max_cost_usd,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            exit_code = max(exit_code, _backfill_exit_code(payload["backfill"]))

    if args.sweep:
        try:
            payload["sweep"] = run_sweep(
                _REPO,
                symbols,
                models,
                dry_run=args.dry_run,
                trial_mode=trial_mode,
            )
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if not args.dry_run:
            exit_code = max(exit_code, _sweep_exit_code(payload["sweep"]))

    out_path = out_dir / "model_symbol_sweep.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
