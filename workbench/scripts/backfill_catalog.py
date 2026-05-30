"""Workbench data catalog backfill — event-window downloads only."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from workbench.src.data.catalog_backfill import (
    download_events,
    estimate_download_cost_usd,
    missing_for_campaign,
)
from workbench.src.data.event_catalog import campaign_preview

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Workbench catalog backfill (point-in-time windows)")
    parser.add_argument("--model", required=True)
    parser.add_argument("--symbol", default="MES.v.0")
    parser.add_argument("--dry-run", action="store_true", help="List missing NPZ paths per period")
    parser.add_argument("--download-missing", action="store_true", help="Download via Databento event window")
    parser.add_argument("--max-cost-usd", type=float, default=None, help="Abort if estimated cost exceeds cap")
    args = parser.parse_args()

    if args.dry_run:
        preview = campaign_preview(args.model, args.symbol, _REPO)
        for pname, pdata in preview["periods"].items():
            print(f"=== {pname} ({pdata['start_year']}-{pdata['end_year']}) ===")
            for ev in pdata["events"]:
                flag = "OK" if ev["npz_present"] else "MISSING"
                print(f"  [{flag}] {ev['event_id']} {ev['release_date']}")
        print(f"Catalog years with NPZ: {preview['catalog_years']}")
        print(f"Personal locked: {preview['personal_locked']}")
        return 0

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
    out = _REPO / "research_cards" / "workbench_catalog_manifest.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote manifest: {out} ({len(missing)} missing, est ${est_cost:.2f})")

    if args.download_missing and missing:
        ids = download_events(_REPO, missing, max_cost_usd=args.max_cost_usd)
        print(f"Download requested for: {ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
