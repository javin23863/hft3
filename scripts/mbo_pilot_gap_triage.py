"""Classify pilot-basket NPZ gaps; optional local reconvert for partial windows."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

MANIFEST_PATH = _REPO / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
DEFAULT_RAW_DIR = Path(r"C:\Users\MSI\Documents\New project\data\raw\databento_mbo\mbo_pilot_basket_20260605")


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def triage(manifest: dict[str, Any]) -> dict[str, Any]:
    partial = manifest.get("partial_windows") or []
    no_market = list(manifest.get("no_market_windows") or [])
    partial_slots = sum(len(row.get("missing_symbols") or []) for row in partial)
    return {
        "total_missing_slots": manifest.get("coverage", {}).get("missing_or_unavailable_slots", 0),
        "no_market_windows": len(no_market),
        "no_market_slots_estimate": len(no_market) * 7,
        "partial_windows": len(partial),
        "partial_symbol_slots": partial_slots,
        "fillable_by_redownload_estimate": partial_slots,
        "recommendation": "Do not spend on no_market windows; optional local reconvert for partial windows only.",
        "no_market_event_ids": no_market,
        "partial_window_rows": partial,
    }


def reconvert_partials(repo: Path, manifest: dict[str, Any], raw_dir: Path) -> list[dict[str, Any]]:
    from backtest_pipeline.src.converter import DatabentoConverter

    converter = DatabentoConverter(str(repo / "data" / "npz"))
    actions: list[dict[str, Any]] = []
    for row in manifest.get("partial_windows") or []:
        event_id = row.get("event_id")
        raw_path = raw_dir / f"{event_id}_mbo.dbn.zst"
        if not raw_path.is_file():
            actions.append({"event_id": event_id, "action": "skipped_missing_raw", "raw_path": str(raw_path)})
            continue
        for symbol in row.get("missing_symbols") or []:
            try:
                converter.convert_file(str(raw_path), symbol)
                actions.append({"event_id": event_id, "symbol": symbol, "action": "reconverted"})
            except Exception as exc:
                actions.append({"event_id": event_id, "symbol": symbol, "action": "failed", "error": str(exc)})
    return actions


def update_manifest_status(manifest: dict[str, Any], triage_report: dict[str, Any]) -> dict[str, Any]:
    manifest = dict(manifest)
    manifest["gap_triage_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["gap_triage_status"] = "documented_permanent_gaps"
    manifest["gap_triage"] = triage_report
    manifest["agent_instructions"] = list(manifest.get("agent_instructions") or []) + [
        "no_market_windows are permanent holiday/weekend gaps — do not queue for paid download.",
        "partial_windows may be retried via local reconvert only unless raw DBN contains the symbol.",
    ]
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--reconvert-partials", action="store_true")
    parser.add_argument("--write-manifest", action="store_true", help="Update pilot manifest with triage status")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest()
    report = triage(manifest)
    if args.reconvert_partials:
        report["reconvert_actions"] = reconvert_partials(args.repo_root.resolve(), manifest, args.raw_dir)
    if args.write_manifest:
        updated = update_manifest_status(manifest, report)
        MANIFEST_PATH.write_text(json.dumps(updated, indent=2), encoding="utf-8")
        report["manifest_updated"] = str(MANIFEST_PATH)
    out_path = args.repo_root / "runtime" / "data_audits" / "mbo_pilot_gap_triage.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["report_path"] = str(out_path)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"no_market_windows={report['no_market_windows']}")
        print(f"partial_symbol_slots={report['partial_symbol_slots']}")
        print(f"recommendation: {report['recommendation']}")
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
