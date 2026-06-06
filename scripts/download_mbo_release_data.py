#!/usr/bin/env python3
"""MBO-only HFT release data download — full macro catalog, T-60s to T+10s."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

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

logger = logging.getLogger(__name__)

OUT_REPORT = _REPO / "runtime" / "data_downloads" / "mbo_download_report.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download MBO release windows (T-60s to T+10s) → validated DBN → optional NPZ"
    )
    parser.add_argument("--download", action="store_true", help="Execute Databento MBO downloads")
    parser.add_argument(
        "--scope",
        choices=("campaign", "macro_releases", "backtest", "full_catalog"),
        default="macro_releases",
        help="macro_releases (default)=all sourced Fed/macro minus speakers; campaign=same (workbench backfill)",
    )
    parser.add_argument("--import-only", metavar="DBN", help="Import existing raw DBN into MBO lane")
    parser.add_argument("--release-id", default=None, help="Release id for --import-only")
    parser.add_argument("--symbol", default="MES.v.0", help="Symbol for --import-only")
    parser.add_argument("--no-seed", action="store_true", help="Exclude SEED calendar scaffolds")
    parser.add_argument("--rule-based", action="store_true", help="Include rule-based windows")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--max-cost-usd", type=float, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Max release×symbol slots to process")
    parser.add_argument("--derive-npz", action="store_true", help="Derive HftBacktest NPZ for valid paths")
    parser.add_argument("--output", type=Path, default=OUT_REPORT)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.import_only:
        from mbo_release_lane.import_pipeline import import_release_window

        if not args.release_id:
            parser.error("--release-id required with --import-only")
        result = import_release_window(
            _REPO,
            release_id=args.release_id,
            release_name=args.release_id.split("_")[0],
            symbol=args.symbol,
            raw_dbn_src=Path(args.import_only),
            window_start="",
            window_end="",
            scheduled_release_timestamp="",
        )
        print(json.dumps({"validation_status": result.validation_status, "event_count": result.event_count}, indent=2))
        return 0 if result.validation_status == "valid" else 1

    from economic_event_universe.catalog_report import build_macro_catalog_summary
    from economic_event_universe.registry import catalog_event_type_count, default_download_window
    from mbo_release_lane.download import run_catalog_download

    start_off, end_off = default_download_window()
    summary = build_macro_catalog_summary(
        _REPO,
        include_seed_calendars=not args.no_seed,
        include_rule_based=args.rule_based,
    )

    report_body: dict = {
        "scope": args.scope,
        "macro_event_type_count": catalog_event_type_count(),
        "catalog_summary": summary.to_dict(),
        "window_offsets_seconds": {"start": start_off, "end": end_off},
    }

    if args.download:
        dl_report = run_catalog_download(
            _REPO,
            scope=args.scope,
            include_seed=not args.no_seed,
            include_rule_based=args.rule_based,
            start_year=args.start_year,
            end_year=args.end_year,
            max_cost_usd=args.max_cost_usd,
            limit=args.limit,
        )
        report_body.update(dl_report.to_dict())

        if args.derive_npz:
            from mbo_release_lane.npz_adapter import derive_npz_from_release

            derived = []
            for rel in dl_report.valid_release_paths:
                parts = Path(rel).parts
                if len(parts) >= 2:
                    release_id = parts[-2]
                    symbol = parts[-1]
                    npz = derive_npz_from_release(_REPO, release_id, symbol)
                    if npz:
                        derived.append(str(npz))
            report_body["derived_npz"] = derived
    else:
        report_body["note"] = "Pass --download to fetch MBO data from Databento"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report_body, indent=2), encoding="utf-8")
    print(f"Scope: {args.scope} | Catalog: {catalog_event_type_count()} event types | window {start_off}s to {end_off}s")
    if args.download:
        rep = report_body.get("mbo_download_report", {})
        print(f"Valid paths: {len(rep.get('valid_release_paths', []))}")
        print(f"Invalid/blocked: {len(rep.get('invalid_release_paths', []))}")
    print(f"Wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
