"""Workbench data catalog backfill — event-window downloads only."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
except ImportError:
    pass

from workbench.src.data.catalog_backfill import (
    download_events,
    estimate_download_cost_usd,
    missing_for_campaign,
)
from workbench.src.data.event_catalog import campaign_preview
from workbench.src.artifacts.paths import artifact_root

AUTHORITY_REFS = [
    "BLUEPRINT.md §5",
    "chicago_cme_microstructure_a_plus_developer_handoff.pdf",
    "docs/REVIEWER_CHARTER.md B4",
    "AGENTS.md options quarantine",
]


def _options_discover_manifest() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "options_lane.pipeline", "discover"],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr or proc.stdout}
    return json.loads(proc.stdout)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Workbench catalog backfill (point-in-time windows)")
    parser.add_argument("--model", required=True)
    parser.add_argument("--symbol", default="MES.v.0")
    parser.add_argument("--dry-run", action="store_true", help="List missing NPZ paths per period")
    parser.add_argument("--download-missing", action="store_true", help="Download via Databento event window")
    parser.add_argument("--max-cost-usd", type=float, default=None, help="Abort if estimated cost exceeds cap")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Directory for workbench_catalog_manifest.json. Defaults to the Workbench artifact root.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    return parser


def run_backfill(args: argparse.Namespace) -> dict:
    if args.dry_run:
        preview = campaign_preview(args.model, args.symbol, _REPO)
        return {
            "mode": "dry_run",
            "model_id": args.model,
            "symbol": args.symbol,
            "catalog_years": preview["catalog_years"],
            "personal_locked": preview["personal_locked"],
            "periods": [
                {
                    "name": pname,
                    "start_year": pdata["start_year"],
                    "end_year": pdata["end_year"],
                    "events": [
                        {
                            "event_id": ev["event_id"],
                            "release_date": ev["release_date"],
                            "npz_present": bool(ev["npz_present"]),
                            "npz_symbol_used": ev.get("npz_symbol_used"),
                        }
                        for ev in pdata["events"]
                    ],
                }
                for pname, pdata in preview["periods"].items()
            ],
        }

    missing = missing_for_campaign(_REPO, args.model, args.symbol)
    est_cost = estimate_download_cost_usd(missing)
    manifest = {
        "model_id": args.model,
        "symbol": args.symbol,
        "authority_refs": AUTHORITY_REFS,
        "missing_count": len(missing),
        "estimated_cost_usd": est_cost,
        "max_cost_usd": args.max_cost_usd,
        "events": [
            {
                "event_id": e.event_id,
                "npz_path": str(e.npz_path),
                "start_utc": str(e.start_utc),
                "end_utc": str(e.end_utc),
                "release_year": int(e.release_date[:4]),
            }
            for e in missing
        ],
        "options_lane_discover": _options_discover_manifest(),
    }
    out_dir = args.out_dir if args.out_dir is not None else artifact_root()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "workbench_catalog_manifest.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    download_ids = []
    if args.download_missing and missing:
        download_ids = download_events(_REPO, missing, max_cost_usd=args.max_cost_usd)
    return {
        "mode": "download" if args.download_missing else "manifest",
        "model_id": args.model,
        "symbol": args.symbol,
        "manifest_path": str(out),
        "missing_count": len(missing),
        "estimated_cost_usd": est_cost,
        "max_cost_usd": args.max_cost_usd,
        "download_requested": bool(download_ids),
        "download_requested_for": download_ids,
    }


def _print_human_result(result: dict) -> None:
    if result.get("mode") == "dry_run":
        for period in result["periods"]:
            print(f"=== {period['name']} ({period['start_year']}-{period['end_year']}) ===")
            for ev in period["events"]:
                flag = "OK" if ev["npz_present"] else "MISSING"
                print(f"  [{flag}] {ev['event_id']} {ev['release_date']}")
        print(f"Catalog years with NPZ: {result['catalog_years']}")
        print(f"Personal locked: {result['personal_locked']}")
        return
    print(
        f"Wrote manifest: {result['manifest_path']} "
        f"({result['missing_count']} missing, est ${result['estimated_cost_usd']:.2f})"
    )
    if result.get("download_requested"):
        print(f"Download requested for: {result['download_requested_for']}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    result = run_backfill(args)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_human_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
