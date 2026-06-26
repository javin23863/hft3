#!/usr/bin/env python3
"""Scan lake units or manifest NPZ paths for OHLCV viability (Phase 0)."""

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

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from data_system.src.lake_manifest import load_manifest, resolve_npz_path
from data_system.src.npz_resolver import resolve_npz_for_event
from research_pipeline.data_quality import check_npz_ohlcv


def _load_units_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _check_unit_row(row: dict[str, Any], repo_root: Path, symbols: tuple[str, ...]) -> tuple[str, str, str | None]:
    unit_id = str(row["unit_id"])
    symbol = str(row["symbol"])
    event_id = str(row["event_id"])
    path, present, _ = resolve_npz_for_event(repo_root, event_id, symbol, symbols)
    if not present or path is None:
        return unit_id, "missing_npz", None
    result = check_npz_ohlcv(path)
    if result.valid:
        return unit_id, "ok", str(path)
    return unit_id, result.reason or "invalid_npz", str(path)


def _check_manifest_row(row: dict[str, Any], repo_root: Path) -> tuple[str, str, str | None]:
    symbol = str(row.get("symbol") or "")
    event_id = str(row.get("event_id") or "")
    unit_id = f"{symbol}_{event_id}" if symbol and event_id else str(row.get("npz_path") or "unknown")
    npz_path = resolve_npz_path(repo_root, str(row["npz_path"]))
    result = check_npz_ohlcv(npz_path)
    if result.valid:
        return unit_id, "ok", str(npz_path)
    return unit_id, result.reason or "invalid_npz", str(npz_path)


def scan_lake_data(
    *,
    repo_root: Path,
    units_jsonl: Path | None = None,
    manifest_only: bool = False,
    symbols: tuple[str, ...],
) -> dict[str, Any]:
    valid_unit_ids: list[str] = []
    invalid_unit_ids: dict[str, str] = {}
    checked_paths: dict[str, str] = {}

    if units_jsonl is not None:
        for row in _load_units_jsonl(units_jsonl):
            unit_id, reason, npz_path = _check_unit_row(row, repo_root, symbols)
            if npz_path:
                checked_paths[unit_id] = npz_path
            if reason == "ok":
                valid_unit_ids.append(unit_id)
            else:
                invalid_unit_ids[unit_id] = reason
        source = str(units_jsonl)
    elif manifest_only:
        for row in load_manifest(repo_root):
            unit_id, reason, npz_path = _check_manifest_row(row, repo_root)
            if npz_path:
                checked_paths[unit_id] = npz_path
            if reason == "ok":
                valid_unit_ids.append(unit_id)
            else:
                invalid_unit_ids[unit_id] = reason
        source = "lake_manifest"
    else:
        raise ValueError("Provide --units-jsonl or --manifest-only")

    return {
        "valid_unit_ids": valid_unit_ids,
        "invalid_unit_ids": invalid_unit_ids,
        "checked_paths": checked_paths,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "valid_count": len(valid_unit_ids),
        "invalid_count": len(invalid_unit_ids),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check lake NPZ OHLCV viability for paid-screen units")
    parser.add_argument("--repo-root", type=Path, default=_REPO)
    parser.add_argument("--units-jsonl", type=Path, default=None, help="Paid-screen units JSONL to validate")
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Validate every row in lake manifest.json instead of units JSONL",
    )
    parser.add_argument(
        "--symbols",
        default="MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0",
        help="Comma-separated symbols for NPZ resolution when using --units-jsonl",
    )
    parser.add_argument("--out", type=Path, required=True, help="Write JSON report (valid/invalid unit ids)")
    args = parser.parse_args(argv)

    if bool(args.units_jsonl) == bool(args.manifest_only):
        print("ERROR: specify exactly one of --units-jsonl or --manifest-only", file=sys.stderr)
        return 2

    repo_root = args.repo_root.resolve()
    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    units_path = None
    if args.units_jsonl:
        units_path = args.units_jsonl if args.units_jsonl.is_absolute() else repo_root / args.units_jsonl
        if not units_path.is_file():
            print(f"ERROR: units jsonl not found: {units_path}", file=sys.stderr)
            return 1

    report = scan_lake_data(
        repo_root=repo_root,
        units_jsonl=units_path,
        manifest_only=bool(args.manifest_only),
        symbols=symbols,
    )
    out_path = args.out if args.out.is_absolute() else repo_root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"checked={report['valid_count'] + report['invalid_count']} "
        f"valid={report['valid_count']} invalid={report['invalid_count']} "
        f"out={out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
