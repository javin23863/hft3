#!/usr/bin/env python3
"""CLI: run Stage A RESEARCH_ONLY pre-filter screen over the feature store.

Resolves --band from CHI404 measured order-ack latency (same path as
run_event_universe.py lines 742-753) when --band is not explicitly supplied.
Refuses if unmeasured and no --band given.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths  # noqa: E402

setup_repo_paths()

from backtest_pipeline.src.chi404_latency import (  # noqa: E402
    DEFAULT_CHI404_SUMMARY,
    resolve_order_ack_ms,
)


# ---------------------------------------------------------------------------
# Band resolution — exact mirror of run_event_universe.py:742-753
# ---------------------------------------------------------------------------

def _resolve_band(band_arg: float | None) -> float:
    """Return band_ms: from --band CLI arg, or CHI404 measured ack, or refuse."""
    if band_arg is not None:
        return float(band_arg)

    # Try CHI404 measured order-ack (mirrors run_event_universe._resolve_latency_bands)
    if DEFAULT_CHI404_SUMMARY.is_file():
        try:
            summary = json.loads(DEFAULT_CHI404_SUMMARY.read_text(encoding="utf-8"))
            ack_ms, measured_flag, _ = resolve_order_ack_ms(summary)
            if measured_flag and ack_ms is not None:
                return round(float(ack_ms), 6)
        except Exception:
            pass

    # Refuse
    print(
        "ERROR: --band not supplied and CHI404 measured order-ack not available.\n"
        "Either:\n"
        "  1) Pass --band <ms>  (e.g. --band 1.0)\n"
        "  2) Run scripts/chi404_run_paper_latency_sweep.sh on CHI404 colo to produce\n"
        f"     {DEFAULT_CHI404_SUMMARY}",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Stage A RESEARCH_ONLY pre-filter screen over the feature store."
    )
    p.add_argument(
        "--band",
        type=float,
        default=None,
        metavar="MS",
        help=(
            "Latency band in ms (default: resolve from CHI404 measured order-ack; "
            "refuse if unmeasured and not supplied)"
        ),
    )
    p.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbol filter (default: all symbols in feature store manifest)",
    )
    p.add_argument(
        "--event-type",
        default=None,
        dest="event_type",
        help="Filter to a single event_type (e.g. CPI)",
    )
    p.add_argument(
        "--max-units",
        type=int,
        default=None,
        dest="max_units",
        help="Cap total units processed (smoke runs)",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel worker processes (default 1; >1 uses spawn pool)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output dir (default: research_cards/stage_a_<utcstamp>)",
    )
    p.add_argument(
        "--feature-root",
        type=Path,
        default=None,
        dest="feature_root",
        help="Override feature store root path",
    )
    args = p.parse_args(argv)

    band_ms = _resolve_band(args.band)

    utcstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or (_REPO / "research_cards" / f"stage_a_{utcstamp}")

    symbol_filter: list[str] | None = None
    if args.symbols:
        symbol_filter = [s.strip() for s in args.symbols.split(",") if s.strip()]

    event_types: list[str] | None = None
    if args.event_type:
        event_types = [args.event_type.strip()]

    print(
        f"Stage A Screen  band_ms={band_ms}  out={out_dir}  workers={args.workers}",
        flush=True,
    )

    from research_pipeline.src.stage_a_screen import run_stage_a

    result = run_stage_a(
        repo_root=_REPO,
        feature_root=args.feature_root,
        out_dir=out_dir,
        band_ms=band_ms,
        symbols=symbol_filter,
        event_types=event_types,
        max_units=args.max_units,
        workers=args.workers,
    )

    # Summary
    n_survivors = len(result.get("cells", []))  # cells count
    corrections = result.get("corrections", {})
    bh = corrections.get("bh_010", {})
    holm = corrections.get("holm_005", {})
    survivors_path = Path(out_dir) / "stage_a_survivors.json"
    n_stat_survivors = len(bh.get("passed", []))

    print(
        f"\nSummary:\n"
        f"  units_run={result['units_run']}  units_errored={result['units_errored']}  "
        f"units_skipped={result['units_skipped']} (embargo)\n"
        f"  cells_tested={result.get('corrections', {}).get('bh_010', {}).get('total_tested', 0)}\n"
        f"  BH@0.10 survivors={n_stat_survivors}  "
        f"Holm@0.05 survivors={len(holm.get('passed', []))}\n"
        f"  pass_through={sorted([42, 43, 45])} (queue-sensitive, always in survivors)\n"
        f"  vix_coverage={result.get('vix_coverage', {})}\n"
        f"  out={out_dir}\n"
        f"  survivors JSON: {survivors_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
