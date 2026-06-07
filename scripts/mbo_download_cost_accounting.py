#!/usr/bin/env python3
"""Accurate MBO macro download cost accounting (local + optional chi404 manifest)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


def _load_manifest(path: Path) -> tuple[set[tuple[str, str]], float, int]:
    import pandas as pd

    if not path.is_file():
        return set(), 0.0, 0
    df = pd.read_parquet(path)
    if "schema" in df.columns:
        df = df[df["schema"] == "mbo"]
    billed: set[tuple[str, str]] = set()
    for _, row in df.iterrows():
        eid = str(row.get("event_id", "")).strip()
        if not eid:
            continue
        req = str(row.get("requested_symbol", "") or row.get("symbols", "")).strip()
        sym = req.replace("[", "").replace("]", "").replace("'", "").split(",")[0].strip()
        if sym:
            billed.add((eid, sym))
    spend = float(df["cost"].sum()) if "cost" in df.columns and len(df) else 0.0
    return billed, spend, len(df)


def _raw_slots(repo: Path) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    root = repo / "data" / "mbo_release"
    if not root.is_dir():
        return out
    for p in root.glob("*/*/raw.dbn.zst"):
        if p.is_file() and p.stat().st_size > 0:
            out.add((p.parts[-3], p.parts[-2]))
    return out


def _fetch_chi404_manifest(repo: Path) -> Path | None:
    dest = repo / "runtime" / "data_downloads" / "chi404_manifest.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["scp", "chi404:/root/hft3/repo/data/manifest.parquet", str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
        return dest
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="MBO download cost accounting")
    parser.add_argument("--fetch-chi404", action="store_true", help="scp chi404 manifest")
    parser.add_argument("--estimate-remaining", action="store_true", help="Databento price remaining")
    parser.add_argument(
        "--priority-events",
        action="store_true",
        help="Scope to Tier 1–3 priority macro + UNEMPLOYMENT_CLAIMS only",
    )
    parser.add_argument("--output", type=Path, default=_REPO / "runtime" / "data_downloads" / "mbo_cost_accounting.json")
    args = parser.parse_args()

    from economic_event_universe.events_csv_builder import resolve_download_scope_windows
    from economic_event_universe.registry import default_cme_symbols
    from economic_event_universe.window_catalog import iter_missing_npz_slots
    from mbo_release_lane.constants import PRIORITY_DOWNLOAD_EVENT_TYPES
    from mbo_release_lane.download import filter_windows_by_event_type, resolve_download_exclusions

    repo = _REPO
    syms = default_cme_symbols()
    only_set = frozenset(PRIORITY_DOWNLOAD_EVENT_TYPES) if args.priority_events else None
    excl = resolve_download_exclusions() if not only_set else frozenset()
    windows = filter_windows_by_event_type(
        resolve_download_scope_windows(repo, "macro_releases", start_year=2018, end_year=2025),
        exclude_event_types=excl,
        only_event_types=only_set,
    )
    all_slots = {(w.event_id, sym) for w in windows for sym in syms}

    local_billed, local_spend, local_rows = _load_manifest(repo / "data" / "manifest.parquet")
    local_raw = _raw_slots(repo)
    local_npz_missing = {(w.event_id, s) for w, s in iter_missing_npz_slots(repo, windows, symbols=syms)}

    chi404_manifest = None
    if args.fetch_chi404:
        chi404_manifest = _fetch_chi404_manifest(repo)
    chi404_path = chi404_manifest or (repo / "runtime" / "data_downloads" / "chi404_manifest.parquet")
    chi404_billed, chi404_spend, chi404_rows = _load_manifest(chi404_path)

    billed_union = local_billed | chi404_billed
    overlap = local_billed & chi404_billed
    have_data = billed_union | local_raw  # chi404 raw not on local disk unless merged

    billed_in_scope = billed_union & all_slots
    remaining_billed = all_slots - billed_union
    remaining_any_local = all_slots - (local_raw | local_billed)

    total_spent = local_spend + chi404_spend
    completed_pct = 100.0 * len(billed_in_scope) / len(all_slots) if all_slots else 0.0
    avg_per_billed = total_spent / len(billed_union) if billed_union else 0.0
    empirical_remaining = len(remaining_billed) * avg_per_billed

    report: dict = {
        "scope": "macro_releases",
        "excluded_event_types": sorted(excl),
        "only_event_types": sorted(only_set) if only_set else [],
        "total_slots": len(all_slots),
        "total_windows": len(windows),
        "local": {
            "manifest_rows": local_rows,
            "manifest_spend_usd": round(local_spend, 4),
            "billed_unique_slots": len(local_billed),
            "raw_nonempty_slots": len(local_raw),
            "npz_missing_slots": len(local_npz_missing),
        },
        "chi404": {
            "manifest_path": str(chi404_path.relative_to(repo)).replace("\\", "/") if chi404_path.is_file() else None,
            "manifest_rows": chi404_rows,
            "manifest_spend_usd": round(chi404_spend, 4),
            "billed_unique_slots": len(chi404_billed),
        },
        "combined": {
            "billed_unique_slots": len(billed_union),
            "billed_in_scope_slots": len(billed_in_scope),
            "overlap_billed_slots": len(overlap),
            "total_spent_usd": round(total_spent, 2),
            "completed_pct_by_billed": round(completed_pct, 2),
            "avg_usd_per_billed_slot": round(avg_per_billed, 4),
            "remaining_billed_slots": len(remaining_billed),
            "empirical_remaining_cost_usd": round(empirical_remaining, 2),
        },
        "note": (
            "DEPRECATED for dollars: total_spent_usd sums manifest.cost (get_cost estimates) on "
            "local + chi404 and double-counts one Databento account. "
            "Use scripts/databento_portal_billing.py for portal-style GB × rate estimates."
        ),
    }

    if args.estimate_remaining and remaining_billed:
        sys.path.insert(0, str(_REPO / "scripts"))
        from estimate_full_macro_mbo_cost import CostSlot, estimate_missing_slots

        by_id = {w.event_id: w for w in windows}
        slots = []
        for eid, sym in sorted(remaining_billed):
            w = by_id[eid]
            slots.append(
                CostSlot(
                    event_id=eid,
                    event_type=w.event_type,
                    release_date=w.release_date,
                    symbol=sym,
                    start_utc=w.start_utc,
                    end_utc=w.end_utc,
                )
            )
        est = estimate_missing_slots(slots, sample_per_type=len(slots) > 400)
        report["databento_remaining_estimate"] = est

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    c = report["combined"]
    print(f"Total scope slots: {report['total_slots']}")
    print(f"Spent (local + chi404): ${c['total_spent_usd']:.2f}")
    print(f"Billed complete (in scope): {c['billed_in_scope_slots']} ({c['completed_pct_by_billed']:.1f}%)")
    print(f"Remaining billed slots: {c['remaining_billed_slots']}")
    print(f"Avg $/billed slot (empirical): ${c['avg_usd_per_billed_slot']:.4f}")
    print(f"Empirical remaining cost: ${c['empirical_remaining_cost_usd']:.2f}")
    print("NOTE: manifest spend is NOT invoice truth — run scripts/databento_portal_billing.py")
    if "databento_remaining_estimate" in report:
        de = report["databento_remaining_estimate"]
        print(f"Databento remaining estimate: ${de['estimated_cost_usd']:.2f}")
    print(f"Wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
