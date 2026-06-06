#!/usr/bin/env python3
"""Estimate Databento MBO cost for all macro catalog event types (point-in-time windows × 7 CME symbols)."""

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

OUT_PATH = _REPO / "runtime" / "data_downloads" / "full_macro_catalog_mbo_estimate.json"


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
            "estimate_method": "no_api_key",
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


def main() -> int:
    from economic_event_universe.catalog_report import build_macro_catalog_summary
    from economic_event_universe.registry import catalog_event_type_count, default_cme_symbols
    from economic_event_universe.window_catalog import (
        count_windows_by_type,
        iter_catalog_windows,
        iter_missing_npz_slots,
    )

    parser = argparse.ArgumentParser(
        description="Estimate MBO download cost for all macro catalog event types"
    )
    parser.add_argument("--estimate", action="store_true", help="Call Databento get_cost for missing slots")
    parser.add_argument("--no-seed", action="store_true", help="Exclude SEED calendar scaffolds")
    parser.add_argument("--no-rule-based", action="store_true", help="Exclude FRIDAY/CASH_OPEN/PROP_REOPEN generators")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--sample-per-type", action="store_true", help="Price one window per type×symbol group")
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    windows = iter_catalog_windows(
        _REPO,
        include_seed=not args.no_seed,
        include_rule_based=not args.no_rule_based,
        start_year=args.start_year,
        end_year=args.end_year,
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

    summary = build_macro_catalog_summary(
        _REPO,
        include_seed_calendars=not args.no_seed,
        include_rule_based=not args.no_rule_based,
    )
    windows_by_type = count_windows_by_type(windows)

    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "macro_event_type_count": catalog_event_type_count(),
        "catalog_window_count": len(windows),
        "windows_by_event_type": windows_by_type,
        "cme_symbols": list(default_cme_symbols()),
        "total_npz_slots": len(windows) * len(default_cme_symbols()),
        "npz_slots_present": summary.npz_slots_present,
        "npz_slots_missing": summary.npz_slots_missing,
        "types_with_zero_windows": summary.types_with_zero_windows,
        "year_range": [args.start_year, args.end_year],
        "include_seed_calendars": not args.no_seed,
        "include_rule_based": not args.no_rule_based,
        "catalog_summary": summary.to_dict(),
    }

    if args.estimate:
        report["cost_estimate"] = estimate_missing_slots(slots, sample_per_type=args.sample_per_type)
    else:
        report["cost_estimate"] = {
            "pending_slots": len(slots),
            "note": "Pass --estimate to call Databento get_cost",
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    est = report.get("cost_estimate", {})
    print(f"Macro catalog: {catalog_event_type_count()} event types")
    print(f"Windows: {len(windows)} | NPZ slots missing: {len(slots)} / {len(windows) * len(default_cme_symbols())}")
    if args.estimate and "estimated_cost_usd" in est:
        print(f"Estimated download cost (missing slots): ${est['estimated_cost_usd']:.2f}")
        print(f"Method: {est.get('estimate_method')}")
    print(f"Wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
