#!/usr/bin/env python3
"""Estimate Databento MBO cost for macro event windows (point-in-time × CME symbols)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
for sub in ("packages", "apps"):
    p = str(_REPO / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
    import os

    if not os.getenv("DATABENTO_API_KEY"):
        env_path = _REPO / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABENTO_API_KEY="):
                    os.environ["DATABENTO_API_KEY"] = line.split("=", 1)[1].strip()
                    break
except ImportError:
    pass

logger = logging.getLogger(__name__)

OUT_BACKTEST = _REPO / "runtime" / "data_downloads" / "backtest_mbo_estimate.json"
OUT_CAMPAIGN = _REPO / "runtime" / "data_downloads" / "campaign_mbo_estimate.json"
OUT_MACRO = _REPO / "runtime" / "data_downloads" / "macro_releases_mbo_estimate.json"
OUT_FULL_CATALOG = _REPO / "runtime" / "data_downloads" / "full_macro_catalog_mbo_estimate.json"
OUT_SUMMARY = _REPO / "runtime" / "data_downloads" / "mbo_cost_summary.json"

ALL_SCOPES = ("macro_releases", "backtest", "full_catalog", "campaign")


@dataclass(frozen=True)
class CostSlot:
    event_id: str
    event_type: str
    release_date: str
    symbol: str
    start_utc: Any
    end_utc: Any


def _as_datetime(value: Any):
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _event_spec(slot: CostSlot):
    from workbench.src.data.event_catalog import EventSpec
    from economic_event_universe.registry import default_cme_symbols

    syms = default_cme_symbols()
    return EventSpec(
        event_id=slot.event_id,
        event_type=slot.event_type,
        release_date=slot.release_date,
        event_context="TIGHT",
        symbol=slot.symbol,
        npz_path=Path(),
        npz_present=False,
        start_utc=slot.start_utc,
        end_utc=slot.end_utc,
        parsed_symbols=syms,
    )


def estimate_missing_slots(
    slots: list[CostSlot],
    *,
    sample_per_type: bool = False,
) -> dict[str, Any]:
    from workbench.src.data.catalog_backfill import resolve_download_symbol
    from data_system.src.databento_client import DatabentoResearchClient

    if not slots:
        return {
            "pending_slots": 0,
            "estimated_cost_usd": 0.0,
            "unpriced": 0,
            "estimate_method": "none",
            "by_event_type": {},
        }

    try:
        client = DatabentoResearchClient()
    except ValueError as exc:
        return {
            "pending_slots": len(slots),
            "estimated_cost_usd": 0.0,
            "unpriced": len(slots),
            "error": str(exc),
            "estimate_method": "client_init_failed",
            "by_event_type": {},
        }

    by_type: dict[str, list[CostSlot]] = {}
    for s in slots:
        by_type.setdefault(s.event_type, []).append(s)

    total = 0.0
    unpriced = 0
    by_type_cost: dict[str, float] = {}

    if sample_per_type or len(slots) > 400:
        method = "sample_per_event_type"
        by_type_slots: dict[str, list[CostSlot]] = {}
        for s in slots:
            by_type_slots.setdefault(s.event_type, []).append(s)
        for et, et_slots in sorted(by_type_slots.items()):
            try:
                _, unit = resolve_download_symbol(client, _event_spec(et_slots[0]))
                et_total = unit * len(et_slots)
            except Exception as exc:
                unpriced += len(et_slots)
                logger.warning("unpriced %s: %s", et, exc)
                continue
            by_type_cost[et] = round(et_total, 4)
            total += et_total
    else:
        method = "full"
        for slot in slots:
            try:
                _, cost = resolve_download_symbol(client, _event_spec(slot))
                total += cost
                by_type_cost[slot.event_type] = by_type_cost.get(slot.event_type, 0.0) + cost
            except Exception as exc:
                unpriced += 1
                logger.warning("unpriced %s %s: %s", slot.symbol, slot.event_id, exc)

    return {
        "pending_slots": len(slots),
        "estimated_cost_usd": round(total, 4),
        "unpriced": unpriced,
        "estimate_method": method,
        "by_event_type": {k: round(v, 4) for k, v in sorted(by_type_cost.items())},
    }


def _type_slot_breakdown(
    windows_by_type: dict[str, int],
    missing_slots: list[CostSlot],
    cost_by_type: dict[str, float],
    *,
    symbol_count: int,
) -> dict[str, dict[str, Any]]:
    """Per-type windows, NPZ gaps, and cost — includes cached types at $0."""
    missing_by_type: dict[str, int] = {}
    for s in missing_slots:
        missing_by_type[s.event_type] = missing_by_type.get(s.event_type, 0) + 1
    out: dict[str, dict[str, Any]] = {}
    for et in sorted(windows_by_type.keys()):
        if windows_by_type[et] <= 0:
            continue
        windows = windows_by_type[et]
        slots_total = windows * symbol_count
        slots_missing = missing_by_type.get(et, 0)
        out[et] = {
            "windows": windows,
            "npz_slots_total": slots_total,
            "npz_slots_missing": slots_missing,
            "npz_slots_present": slots_total - slots_missing,
            "estimated_cost_usd": round(cost_by_type.get(et, 0.0), 4),
            "cache_status": "complete" if slots_missing == 0 else "needs_download",
        }
    return out


def _print_type_breakdown(breakdown: dict[str, dict[str, Any]], total_cost: float) -> None:
    print("\nCost by event type (full scoped universe):")
    print(f"{'TYPE':<28} {'WINS':>5} {'NPZ miss':>9} {'COST $':>10} {'STATUS':<14}")
    print("-" * 70)
    for et, row in breakdown.items():
        print(
            f"{et:<28} {row['windows']:>5} {row['npz_slots_missing']:>9} "
            f"{row['estimated_cost_usd']:>10.2f} {row['cache_status']:<14}"
        )
    print("-" * 70)
    print(f"{'TOTAL':<28} {'':>5} {'':>9} {total_cost:>10.2f}")


def _scope_params(
    scope: str,
    *,
    wf_start: int,
    wf_end: int,
    start_year: int | None,
    end_year: int | None,
    no_seed: bool,
    no_rule_based: bool,
) -> tuple[int, int, bool, bool]:
    if scope == "full_catalog":
        sy = start_year if start_year is not None else 2018
        ey = end_year if end_year is not None else 2025
        return sy, ey, not no_seed, not no_rule_based
    sy = start_year if start_year is not None else wf_start
    ey = end_year if end_year is not None else wf_end
    return sy, ey, False, False


def build_scope_report(
    scope: str,
    *,
    start_year: int,
    end_year: int,
    include_seed: bool,
    include_rule_based: bool,
    wf_start: int,
    wf_end: int,
    do_estimate: bool,
    sample_per_type: bool,
) -> dict[str, Any]:
    from economic_event_universe.catalog_report import build_macro_catalog_summary
    from economic_event_universe.events_csv_builder import resolve_download_scope_windows
    from economic_event_universe.registry import catalog_event_type_count, default_cme_symbols
    from economic_event_universe.window_catalog import (
        count_windows_by_type,
        iter_missing_npz_slots,
        npz_slot_coverage,
    )

    windows = resolve_download_scope_windows(
        _REPO,
        scope,
        start_year=start_year,
        end_year=end_year,
        include_seed=include_seed,
        include_rule_based=include_rule_based,
    )

    missing = iter_missing_npz_slots(_REPO, windows)
    slots = [
        CostSlot(
            event_id=w.event_id,
            event_type=w.event_type,
            release_date=w.release_date,
            symbol=sym,
            start_utc=w.start_utc,
            end_utc=w.end_utc,
        )
        for w, sym in missing
    ]

    npz_present, npz_missing = npz_slot_coverage(_REPO, windows)
    summary = build_macro_catalog_summary(
        _REPO,
        include_seed_calendars=include_seed,
        include_rule_based=include_rule_based,
        windows=windows,
    )
    windows_by_type = count_windows_by_type(windows)
    scoped_types = sorted(et for et, n in windows_by_type.items() if n > 0)

    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "macro_event_type_count": catalog_event_type_count(),
        "scoped_event_types": scoped_types,
        "scoped_window_count": len(windows),
        "windows_by_event_type": {k: v for k, v in windows_by_type.items() if v > 0},
        "cme_symbols": list(default_cme_symbols()),
        "total_npz_slots": len(windows) * len(default_cme_symbols()),
        "npz_slots_present": npz_present,
        "npz_slots_missing": npz_missing,
        "year_range": [start_year, end_year],
        "walk_forward_year_range": [wf_start, wf_end],
        "include_seed_calendars": include_seed,
        "include_rule_based": include_rule_based,
        "events_csv_row_count": summary.events_csv_rows,
        "events_csv_type_count": summary.events_csv_types,
        "note": (
            "npz_slots_missing counts local cache gaps for this scope only; "
            "pass --estimate for Databento pricing."
        ),
    }

    if do_estimate:
        sample = sample_per_type or len(slots) > 400
        report["cost_estimate"] = estimate_missing_slots(slots, sample_per_type=sample)
        cost_by_type = report["cost_estimate"].get("by_event_type") or {}
        report["cost_estimate"]["by_event_type_full"] = _type_slot_breakdown(
            {k: v for k, v in windows_by_type.items() if v > 0},
            slots,
            cost_by_type,
            symbol_count=len(default_cme_symbols()),
        )
    else:
        report["cost_estimate"] = {
            "pending_slots": len(slots),
            "note": "Pass --estimate to call Databento get_cost",
        }
    return report


def _print_scope_report(report: dict[str, Any], *, do_estimate: bool) -> None:
    scope = report["scope"]
    start_year, end_year = report["year_range"]
    scoped_types = report["scoped_event_types"]
    windows = report["scoped_window_count"]
    est = report.get("cost_estimate", {})
    slots_pending = est.get("pending_slots", report["npz_slots_missing"])

    print(f"\n=== Scope: {scope} | Years: {start_year}-{end_year} ===")
    print(
        f"Macro catalog: {report['macro_event_type_count']} event types | "
        f"Scoped: {len(scoped_types)} types, {windows} windows"
    )
    print(f"Event types: {', '.join(scoped_types)}")
    print(
        f"NPZ slots missing (local cache): {slots_pending} / {report['total_npz_slots']}"
    )
    if do_estimate and "estimated_cost_usd" in est:
        print(f"Estimated download cost (missing slots): ${est['estimated_cost_usd']:.2f}")
        print(f"Method: {est.get('estimate_method')}")
        full = est.get("by_event_type_full")
        if full:
            _print_type_breakdown(full, float(est["estimated_cost_usd"]))
    elif not do_estimate:
        print("Pass --estimate to call Databento get_cost for missing slots")


def main() -> int:
    from economic_event_universe.registry import catalog_event_type_count
    from economic_event_universe.walk_forward_years import backtest_year_range

    wf_start, wf_end = backtest_year_range(_REPO)

    parser = argparse.ArgumentParser(
        description="Estimate MBO download cost for macro event windows"
    )
    parser.add_argument(
        "--scope",
        choices=("campaign", "macro_releases", "backtest", "full_catalog"),
        default="macro_releases",
        help=(
            "macro_releases (default) = all sourced Fed/macro in events.csv minus FED_SPEAKER; "
            "campaign = same as macro_releases (workbench backfill); backtest = all SOURCED calendars; full_catalog = entire catalog"
        ),
    )
    parser.add_argument(
        "--all-scopes",
        action="store_true",
        help="Run macro_releases, backtest, full_catalog, and campaign; write mbo_cost_summary.json",
    )
    parser.add_argument("--estimate", action="store_true", help="Call Databento get_cost for missing slots")
    parser.add_argument("--no-seed", action="store_true", help="Exclude SEED calendar scaffolds (full_catalog only)")
    parser.add_argument(
        "--no-rule-based",
        action="store_true",
        help="Exclude FRIDAY/CASH_OPEN/PROP_REOPEN generators (full_catalog only)",
    )
    parser.add_argument("--start-year", type=int, default=None)
    parser.add_argument("--end-year", type=int, default=None)
    parser.add_argument("--sample-per-type", action="store_true", help="Price one window per type×symbol group")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    scope_outputs = {
        "campaign": OUT_CAMPAIGN,
        "macro_releases": OUT_MACRO,
        "backtest": OUT_BACKTEST,
        "full_catalog": OUT_FULL_CATALOG,
    }

    scopes = list(ALL_SCOPES) if args.all_scopes else [args.scope]
    summary_reports: dict[str, Any] = {}

    for scope in scopes:
        start_year, end_year, include_seed, include_rule_based = _scope_params(
            scope,
            wf_start=wf_start,
            wf_end=wf_end,
            start_year=args.start_year,
            end_year=args.end_year,
            no_seed=args.no_seed,
            no_rule_based=args.no_rule_based,
        )
        report = build_scope_report(
            scope,
            start_year=start_year,
            end_year=end_year,
            include_seed=include_seed,
            include_rule_based=include_rule_based,
            wf_start=wf_start,
            wf_end=wf_end,
            do_estimate=args.estimate,
            sample_per_type=args.sample_per_type,
        )
        out_path = scope_outputs[scope]
        if not args.all_scopes and args.output:
            out_path = args.output
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        summary_reports[scope] = {
            "output_path": str(out_path.relative_to(_REPO)).replace("\\", "/"),
            "scoped_event_types": report["scoped_event_types"],
            "scoped_window_count": report["scoped_window_count"],
            "npz_slots_missing": report["npz_slots_missing"],
            "estimated_cost_usd": report.get("cost_estimate", {}).get("estimated_cost_usd"),
            "by_event_type_full": report.get("cost_estimate", {}).get("by_event_type_full"),
        }
        _print_scope_report(report, do_estimate=args.estimate)
        print(f"Wrote: {out_path}")

    if args.all_scopes:
        master = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "macro_event_type_count": catalog_event_type_count(),
            "walk_forward_year_range": [wf_start, wf_end],
            "primary_scope_for_downloads": "macro_releases",
            "note": (
                "Use macro_releases for sourced Fed/BLS macro backtest data. "
                "campaign mirrors macro_releases for workbench backfill. "
                "full_catalog includes SEED scaffolds for all catalog types."
            ),
            "scopes": summary_reports,
        }
        OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
        OUT_SUMMARY.write_text(json.dumps(master, indent=2), encoding="utf-8")
        print(f"\nMaster summary: {OUT_SUMMARY}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
