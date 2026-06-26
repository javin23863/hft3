#!/usr/bin/env python3
"""Pre-check all NPZ files in the configured lake for OHLCV data validity.

Iterates every ``*_mbo.npz`` (or ``*.npz``) file under
``HFT3_NPZ_ROOT`` (or ``<repo>/data/npz``), runs
``research_pipeline.data_quality.check_npz_ohlcv`` on each, and writes a
JSON report with valid/invalid unit IDs and error reasons.

Output JSON structure::

    {
      "checked": 63944,
      "valid": 63943,
      "invalid": 1,
      "invalid_units": [
        {"unit_id": "ZN.v.0_EIA_NATGAS_2019_11_28", "path": "...", "reason": "no_ohlcv_data: only 1 events (need >=2 to build a bar)"}
      ]
    }

Usage::

    python3 scripts/check_lake_data.py
    python3 scripts/check_lake_data.py --pattern ZN
    python3 scripts/check_lake_data.py --out runtime/reports/lake_data_quality.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from data_system.src.npz_resolver import npz_root
from research_pipeline.data_quality import check_npz_ohlcv


def _npz_unit_id(stem: str) -> str:
    """Derive a unit-style ID from an NPZ filename stem.

    ``ZN.v.0_EIA_NATGAS_2019_11_28_mbo`` -> ``ZN.v.0_EIA_NATGAS_2019_11_28``
    Falls back to the full stem if no ``_mbo`` suffix.
    """
    if stem.endswith("_mbo"):
        return stem[:-4]
    return stem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-check NPZ lake files for OHLCV data validity."
    )
    parser.add_argument(
        "--npz-root",
        type=Path,
        default=None,
        help="NPZ lake root (default: HFT3_NPZ_ROOT or <repo>/data/npz)",
    )
    parser.add_argument(
        "--pattern",
        default="",
        help="Substring filter for NPZ filenames (e.g. ZN, EIA_NATGAS)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON path (default: runtime/reports/lake_data_quality.json)",
    )
    parser.add_argument(
        "--ext",
        default=".npz",
        help="File extension to scan (default: .npz)",
    )
    args = parser.parse_args(argv)

    root = args.npz_root or npz_root(_REPO)
    if not root.is_dir():
        print(f"ERROR: NPZ root not found: {root}", file=sys.stderr)
        return 1

    out_path = args.out or _REPO / "runtime" / "reports" / "lake_data_quality.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pattern = args.pattern.strip().upper() if args.pattern else ""
    npz_files = sorted(root.glob(f"*{args.ext}"))

    if pattern:
        npz_files = [p for p in npz_files if pattern in p.name.upper()]

    valid: list[dict] = []
    invalid: list[dict] = []
    checked = 0

    for npz_path in npz_files:
        unit_id = _npz_unit_id(npz_path.stem)
        checked += 1
        try:
            ok, reason = check_npz_ohlcv(npz_path)
        except Exception as exc:
            ok = False
            reason = f"{type(exc).__name__}: {exc}"

        if ok:
            valid.append({"unit_id": unit_id, "path": str(npz_path)})
        else:
            invalid.append({"unit_id": unit_id, "path": str(npz_path), "reason": reason})

        if checked % 1000 == 0:
            print(f"  checked {checked}/{len(npz_files)} ...", flush=True)

    report = {
        "checked": checked,
        "valid": len(valid),
        "invalid": len(invalid),
        "invalid_units": invalid,
    }
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"Checked: {checked}  Valid: {len(valid)}  Invalid: {len(invalid)}")
    if invalid:
        print(f"\nInvalid NPZ files ({len(invalid)}):")
        for entry in invalid[:50]:
            print(f"  {entry['unit_id']}: {entry['reason']}")
        if len(invalid) > 50:
            print(f"  ... and {len(invalid) - 50} more (see {out_path})")
    print(f"\nReport: {out_path}")
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())