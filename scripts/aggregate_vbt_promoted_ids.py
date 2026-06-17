#!/usr/bin/env python3
"""Aggregate promoted_ids from a completed VectorBT paid-screen manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


def _load_manifest(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def aggregate(manifest_path: Path) -> Dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    out_dir = Path(str(manifest.get("out_dir") or manifest_path.parent))
    promoted: Set[str] = set()
    by_unit: Dict[str, List[str]] = {}
    errors: List[str] = []

    for row in manifest.get("unit_results") or []:
        if row.get("status") not in {"OK", "OK_CACHED"}:
            continue
        rel = row.get("screening_artifact_relpath")
        unit_id = str(row.get("unit_id") or "")
        if not rel:
            errors.append(f"missing_relpath:{unit_id}")
            continue
        artifact_path = out_dir / rel
        if not artifact_path.is_file():
            errors.append(f"missing_artifact:{unit_id}")
            continue
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        ids = [str(x) for x in payload.get("promoted_ids") or []]
        by_unit[unit_id] = ids
        promoted.update(ids)

    units_with_promotions = sum(1 for ids in by_unit.values() if ids)
    return {
        "manifest": str(manifest_path),
        "out_dir": str(out_dir),
        "expected_work_units": manifest.get("expected_work_units"),
        "completed_work_units": manifest.get("completed_work_units"),
        "failed_work_units": manifest.get("failed_work_units"),
        "promoted_id_count": len(promoted),
        "units_with_promotions": units_with_promotions,
        "promoted_ids": sorted(promoted),
        "by_unit": by_unit,
        "errors": errors,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate promoted_ids from paid screen manifest")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=_REPO / "runtime" / "reports" / "vbt_full_promoted_ids.json")
    args = parser.parse_args(argv)

    manifest = args.manifest if args.manifest.is_absolute() else _REPO / args.manifest
    result = aggregate(manifest)
    out = args.out if args.out.is_absolute() else _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"promoted_id_count": result["promoted_id_count"], "out": str(out)}))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
