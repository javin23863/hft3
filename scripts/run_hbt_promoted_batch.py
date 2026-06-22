#!/usr/bin/env python3
"""Batch HftBacktest realism on promoted_ids from VBT manifest with progress meter."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _utc_z(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_relative_path(value: Any, label: str) -> Path:
    text = str(value)
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if posix.is_absolute() or windows.is_absolute() or posix.root or windows.drive or windows.root:
        raise ValueError(f"{label} must be relative: {text}")
    if ".." in posix.parts or ".." in windows.parts:
        raise ValueError(f"{label} must not contain '..': {text}")
    return Path(text)


def _resolve_out_dir(manifest: dict[str, Any], manifest_path: Path) -> Path:
    manifest_run_dir = manifest_path.parent.resolve()
    raw = manifest.get("out_dir")
    if raw in (None, ""):
        return manifest_run_dir
    text = str(raw)
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if ".." in posix.parts or ".." in windows.parts:
        raise ValueError(f"manifest out_dir must not contain '..': {text}")
    candidate_path = Path(text)
    if (windows.drive or windows.root) and not windows.is_absolute():
        raise ValueError(f"manifest out_dir must be relative or absolute: {text}")
    if posix.is_absolute() or windows.is_absolute() or candidate_path.is_absolute():
        candidate = candidate_path.resolve()
    else:
        rel = _safe_relative_path(raw, "manifest out_dir")
        candidate = (manifest_run_dir / rel).resolve()
    if not _is_under(candidate, manifest_run_dir):
        raise ValueError(f"manifest out_dir resolves outside manifest directory: {raw}")
    return candidate


def _resolve_screening_artifact(out_dir: Path, relpath: Any) -> Path:
    rel = _safe_relative_path(relpath, "screening_artifact_relpath")
    art = (out_dir / rel).resolve()
    if not _is_under(art, out_dir.resolve()):
        raise ValueError(f"screening_artifact_relpath resolves outside out_dir: {relpath}")
    return art


def _artifact_for_unit(manifest: dict[str, Any], unit_id: str, out_dir: Path) -> Path | None:
    for row in manifest.get("unit_results") or []:
        if str(row.get("unit_id")) != unit_id:
            continue
        rel = row.get("screening_artifact_relpath")
        if not rel:
            return None
        return _resolve_screening_artifact(out_dir, rel)
    return None


def _units_with_promotions(manifest_path: Path) -> list[tuple[str, Path, list[str]]]:
    manifest = _load_json(manifest_path)
    out_dir = _resolve_out_dir(manifest, manifest_path)
    rows: list[tuple[str, Path, list[str]]] = []
    for row in manifest.get("unit_results") or []:
        if row.get("status") not in {"OK", "OK_CACHED"}:
            continue
        unit_id = str(row.get("unit_id") or "")
        rel = row.get("screening_artifact_relpath")
        if not unit_id or not rel:
            continue
        art = _resolve_screening_artifact(out_dir, rel)
        if not art.is_file():
            continue
        payload = _load_json(art)
        promoted = [str(x) for x in payload.get("promoted_ids") or []]
        if promoted:
            rows.append((unit_id, art, promoted))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run HBT realism batch on promoted VBT units.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--max-units", type=int, default=0, help="0 = all promoted units")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--progress-file",
        type=Path,
        default=_REPO / "runtime" / "reports" / "hbt_promoted_batch_progress.json",
    )
    args = parser.parse_args(argv)

    manifest_path = args.manifest if args.manifest.is_absolute() else _REPO / args.manifest
    if not manifest_path.is_file():
        print(f"error: missing manifest {manifest_path}", file=sys.stderr)
        return 2

    try:
        units = _units_with_promotions(manifest_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.max_units > 0:
        units = units[: args.max_units]
    total = len(units)
    if total == 0:
        print("error: no promoted units in manifest", file=sys.stderr)
        return 2

    promoted_id_count = len({pid for _, _, promoted_ids in units for pid in promoted_ids})
    progress: dict[str, Any] = {
        "started_at_utc": _utc_z(),
        "manifest": str(manifest_path),
        "total_units": total,
        "completed": 0,
        "passed": 0,
        "failed": 0,
        "results": [],
    }

    t0 = time.time()
    for idx, (unit_id, artifact_path, promoted_ids) in enumerate(units, start=1):
        candidate_id = promoted_ids[0]
        run_id = f"hbt_{unit_id}_{candidate_id}"[:180]
        elapsed = time.time() - t0
        rate = idx / elapsed if elapsed > 0 else 0.0
        remaining = total - idx
        eta_s = int(remaining / rate) if rate > 0 else None
        line = (
            f"[{idx}/{total}] unit={unit_id} candidate={candidate_id} "
            f"elapsed={elapsed:.0f}s eta={eta_s}s"
        )
        print(line, flush=True)

        if args.dry_run:
            progress["results"].append({"unit_id": unit_id, "dry_run": True, "candidate_id": candidate_id})
            progress["completed"] = idx
            continue

        cmd = [
            sys.executable,
            str(_REPO / "scripts" / "run_hftbacktest_realism.py"),
            "--screening-artifact",
            str(artifact_path),
            "--candidate-id",
            candidate_id,
            "--run-id",
            run_id,
        ]
        proc = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True)
        ok = proc.returncode == 0
        progress["results"].append(
            {
                "unit_id": unit_id,
                "candidate_id": candidate_id,
                "exit_code": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-500:],
                "stderr_tail": (proc.stderr or "")[-500:],
            }
        )
        progress["completed"] = idx
        progress["passed" if ok else "failed"] += 1
        args.progress_file.parent.mkdir(parents=True, exist_ok=True)
        args.progress_file.write_text(json.dumps(progress, indent=2) + "\n", encoding="utf-8")

    progress["finished_at_utc"] = _utc_z()
    progress["promoted_id_count"] = promoted_id_count
    args.progress_file.write_text(json.dumps(progress, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"progress_file": str(args.progress_file), "total": total}, indent=2))
    return 0 if progress.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
