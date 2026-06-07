#!/usr/bin/env python3
"""Report Databento spend using portal-style GB × rate (not manifest.cost sum)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


def _print_line_items(title: str, items: list[dict], subtotal: float) -> None:
    print(title)
    print("=" * 60)
    for item in items:
        slots = item.get("files_or_slots")
        suffix = f"  ({slots} slots)" if slots else ""
        print(
            f"{item['label']:42} "
            f"{item['usage_gb']:8.2f} GB  @ ${item['rate_usd_per_gb']:.2f}/GB  "
            f"= ${item['data_cost_usd']:.2f}{suffix}"
        )
    print("-" * 60)
    print(f"{'Subtotal':42} {'':8}          = ${subtotal:.2f}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Portal-style Databento billing (deduped get_cost + compressed disk GB)"
    )
    parser.add_argument(
        "--no-chi404",
        action="store_true",
        help="Skip chi404 ssh scan for compressed disk bytes",
    )
    parser.add_argument(
        "--fetch-chi404-manifest",
        action="store_true",
        help="scp chi404 manifest.parquet before analysis",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_REPO / "runtime" / "data_downloads" / "databento_portal_billing.json",
    )
    args = parser.parse_args()

    if args.fetch_chi404_manifest:
        import subprocess

        dest = _REPO / "runtime" / "data_downloads" / "chi404_manifest.parquet"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["scp", "chi404:/root/hft3/repo/data/manifest.parquet", str(dest)],
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    from data_system.src.portal_billing import build_portal_report

    report = build_portal_report(_REPO, include_chi404=not args.no_chi404)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    est = report["deduped_get_cost"]
    disk = report["compressed_disk_bytes"]
    mc = report["manifest_get_cost"]
    rec = report["reconciliation"]

    _print_line_items(
        "Recommended: deduped get_cost -> implied portal GB",
        est["line_items"],
        est["subtotal_usd"],
    )
    _print_line_items(
        "Lower bound: compressed .dbn.zst bytes on disk",
        disk["line_items"],
        disk["subtotal_usd"],
    )

    print("Manifest audit")
    print(f"  local manifest rows:     {mc['local_rows']}")
    print(f"  chi404 manifest rows:    {mc['chi404_rows']}")
    print(f"  overlap slots:           {mc['overlap_slots']}")
    print(f"  naive sum (WRONG):         ${rec['naive_manifest_sum_usd']:.2f}")
    print(f"  duplicate row overhead:    ${rec['naive_minus_deduped_usd']:.2f}")
    print(f"  deduped get_cost:          ${rec['deduped_get_cost_usd']:.2f}")
    print(f"  compressed disk estimate:  ${rec['compressed_disk_usd']:.2f}")
    print(f"  deduped - disk gap:        ${rec['deduped_minus_disk_usd']:.2f}")
    if not args.no_chi404:
        print(f"  chi404-only disk files:    {disk['chi404_only_files']}")
    print()
    print(report["note"])
    print(f"Wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
