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
    FULL_UNIVERSE_SCOPE,
    PROMOTION_CAMPAIGN_SCOPE,
    SOURCED_RUNNABLE_SCOPE,
    download_events,
    events_for_scope,
    estimate_download_cost_usd,
    missing_for_campaign,
    normalize_universe_scope,
    summarize_event_specs,
)
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
        "--scope",
        choices=(FULL_UNIVERSE_SCOPE, SOURCED_RUNNABLE_SCOPE, PROMOTION_CAMPAIGN_SCOPE),
        default=FULL_UNIVERSE_SCOPE,
        help=(
            "Event universe scope. full_universe shows all canonical calendar rows; "
            "sourced_runnable shows sourced rows; promotion_campaign uses generated events.csv."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Directory for workbench_catalog_manifest.json. Defaults to the Workbench artifact root.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    return parser


def _event_payload(period_name: str, event) -> dict:
    return {
        "period": period_name,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "event_context": event.event_context,
        "release_date": event.release_date,
        "symbol": event.symbol,
        "row_status": event.row_status,
        "runnable_eligible": event.runnable_eligible,
        "model_eligible": event.model_eligible,
        "symbol_eligible": event.symbol_eligible,
        "npz_present": bool(event.npz_present),
        "npz_symbol_used": event.npz_symbol_used,
        "npz_path": str(event.npz_path),
        "start_utc": str(event.start_utc),
        "end_utc": str(event.end_utc),
        "source": event.source,
        "source_url": event.source_url,
        "source_file": event.source_file,
    }


def run_backfill(args: argparse.Namespace) -> dict:
    scope = normalize_universe_scope(args.scope)
    period_events = events_for_scope(_REPO, args.model, args.symbol, universe_scope=scope)
    summary = summarize_event_specs(period_events)
    if args.dry_run:
        periods: dict[str, dict] = {}
        for period_name, event in period_events:
            payload = _event_payload(period_name, event)
            periods.setdefault(period_name, {"name": period_name, "events": []})["events"].append(payload)
        return {
            "mode": "dry_run",
            "scope": scope,
            "model_id": args.model,
            "symbol": args.symbol,
            "summary": summary,
            "periods": list(periods.values()),
        }

    missing = missing_for_campaign(_REPO, args.model, args.symbol, universe_scope=scope)
    cost_estimate_requested = args.download_missing or args.max_cost_usd is not None
    if cost_estimate_requested:
        est_cost = estimate_download_cost_usd(missing)
        cost_estimate_status = "estimated"
    else:
        est_cost = 0.0
        cost_estimate_status = "not_requested_manifest_only"
    manifest = {
        "model_id": args.model,
        "symbol": args.symbol,
        "scope": scope,
        "authority_refs": AUTHORITY_REFS,
        "summary": summary,
        "missing_count": len(missing),
        "estimated_cost_usd": est_cost,
        "cost_estimate_status": cost_estimate_status,
        "max_cost_usd": args.max_cost_usd,
        "events": [
            {
                **_event_payload("", e),
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
        "scope": scope,
        "model_id": args.model,
        "symbol": args.symbol,
        "manifest_path": str(out),
        "summary": summary,
        "missing_count": len(missing),
        "estimated_cost_usd": est_cost,
        "cost_estimate_status": cost_estimate_status,
        "max_cost_usd": args.max_cost_usd,
        "download_requested": bool(download_ids),
        "download_requested_for": download_ids,
    }


def _print_human_result(result: dict) -> None:
    if result.get("mode") == "dry_run":
        print(f"Scope: {result.get('scope')}")
        summary = result.get("summary") or {}
        print(
            "Visible events: "
            f"{summary.get('visible_events', 0)} "
            f"({summary.get('visible_event_types', 0)} event types), "
            f"missing NPZ: {summary.get('missing_count', 0)}"
        )
        if summary.get("row_status_counts"):
            print(f"Row statuses: {summary['row_status_counts']}")
        for period in result["periods"]:
            print(f"=== {period['name']} ===")
            for ev in period["events"]:
                flag = "OK" if ev["npz_present"] else "MISSING"
                status = ev.get("row_status") or "UNKNOWN"
                runnable = "runnable" if ev.get("runnable_eligible") else "visible"
                print(f"  [{flag}] [{status}/{runnable}] {ev['event_id']} {ev['release_date']}")
        return
    print(
        f"Wrote manifest: {result['manifest_path']} "
        f"({result['missing_count']} missing, est ${result['estimated_cost_usd']:.2f}, "
        f"scope {result.get('scope')})"
    )
    if result.get("download_requested"):
        print(f"Download requested for: {result['download_requested_for']}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    result = run_backfill(args)
    try:
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _print_human_result(result)
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
