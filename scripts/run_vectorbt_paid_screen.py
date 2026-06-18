#!/usr/bin/env python3
"""Parallel VectorBT paid-compute screening over a JSONL unit manifest."""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

_PIPELINE_RESULT_MARKER = "HFT3_PIPELINE_RESULT="


def _parse_pipeline_stdout(stdout: str) -> Optional[Path]:
    for line in stdout.splitlines():
        if line.startswith(_PIPELINE_RESULT_MARKER):
            payload = json.loads(line[len(_PIPELINE_RESULT_MARKER):])
            paths = payload.get("paths") or {}
            rel = paths.get("screening_artifact_path")
            if rel:
                return Path(rel)
            artifact_dir = payload.get("artifact_dir")
            if artifact_dir:
                return Path(artifact_dir) / "screening_artifact.json"
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if '"screening_artifact_path"' not in stripped:
            continue
        try:
            _, raw = stripped.split(":", 1)
            value = raw.strip().rstrip(",").strip().strip('"')
            if value:
                return Path(value)
        except ValueError:
            continue
    return None


def _subprocess_env(repo_root: Path) -> Dict[str, str]:
    env = os.environ.copy()
    roots = [str(repo_root), str(repo_root / "packages"), str(repo_root / "apps")]
    existing = env.get("PYTHONPATH", "").strip()
    if existing:
        roots.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(roots)
    return env


def _load_units(path: Path) -> List[Dict[str, Any]]:
    units: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            units.append(json.loads(line))
    return units


def _load_ready_gate(path: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return bool(payload.get("ready_for_full_run"))


def _run_unit_worker(args: Tuple[Dict[str, Any], str, str, str, int, bool]) -> Dict[str, Any]:
    unit, repo_root_str, out_dir_str, scope, max_wall_clock, no_llm = args
    repo_root = Path(repo_root_str)
    out_dir = Path(out_dir_str)
    unit_id = str(unit["unit_id"])
    unit_dir = out_dir / "units" / unit_id
    unit_dir.mkdir(parents=True, exist_ok=True)
    dest = unit_dir / "screening_artifact.json"

    if dest.is_file():
        try:
            from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact

            payload = json.loads(dest.read_text(encoding="utf-8"))
            validate_screening_artifact(payload)
            return {
                "unit_id": unit_id,
                "status": "OK_CACHED",
                "screening_artifact_relpath": f"units/{unit_id}/screening_artifact.json",
                "elapsed_seconds": 0.0,
            }
        except Exception:
            pass

    cmd = [
        sys.executable,
        str(repo_root / "scripts" / "run_pipeline.py"),
        "--thesis",
        str(unit["thesis"]),
        "--event-id",
        str(unit["event_id"]),
        "--vectorbt",
        "--vectorbt-scope",
        scope,
        "--no-llm",
        "--repo-root",
        str(repo_root),
        "--orchestrator-result",
    ]
    if max_wall_clock > 0:
        cmd.extend(["--vectorbt-max-wall-clock-seconds", str(max_wall_clock)])

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=_subprocess_env(repo_root),
            timeout=max(max_wall_clock + 60, 600) if max_wall_clock > 0 else 3600,
        )
    except subprocess.TimeoutExpired:
        return {
            "unit_id": unit_id,
            "status": "ERROR",
            "error": "subprocess_timeout",
            "elapsed_seconds": time.time() - t0,
        }

    elapsed = time.time() - t0
    screening_path: Optional[Path] = None
    stdout = (proc.stdout or "").strip()
    if stdout:
        screening_path = _parse_pipeline_stdout(stdout)
        if screening_path is None:
            try:
                payload = json.loads(stdout.split("\n")[-1])
                artifact_dir = payload.get("artifact_dir")
                paths = payload.get("paths") or {}
                rel = paths.get("screening_artifact_path")
                if rel:
                    screening_path = Path(rel)
                elif artifact_dir:
                    screening_path = Path(artifact_dir) / "screening_artifact.json"
            except json.JSONDecodeError:
                pass

    if screening_path is None or not screening_path.is_file():
        return {
            "unit_id": unit_id,
            "status": "ERROR",
            "error": f"no_screening_artifact:exit={proc.returncode}",
            "stderr_tail": (proc.stderr or "")[-400:],
            "elapsed_seconds": elapsed,
        }

    dest.write_text(screening_path.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact

        validate_screening_artifact(json.loads(dest.read_text(encoding="utf-8")))
    except Exception as exc:
        return {
            "unit_id": unit_id,
            "status": "ERROR",
            "error": f"validate_failed:{exc}",
            "elapsed_seconds": elapsed,
        }

    # exit 2 from run_pipeline without --hftbacktest-realism is expected
    return {
        "unit_id": unit_id,
        "status": "OK",
        "screening_artifact_relpath": f"units/{unit_id}/screening_artifact.json",
        "pipeline_exit_code": proc.returncode,
        "elapsed_seconds": elapsed,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="VectorBT paid-compute parallel screen")
    parser.add_argument("--units-jsonl", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="Run directory")
    parser.add_argument("--vectorbt-scope", default="paid-compute")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-wall-clock-seconds", type=int, default=0)
    parser.add_argument("--ready-gate-file", type=Path, default=None)
    parser.add_argument("--owner-waiver", default=None, help="Reason to skip ready gate")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-llm", action="store_true", default=True)
    parser.add_argument("--repo-root", type=Path, default=_REPO)
    args = parser.parse_args(argv)

    repo_root = args.repo_root if args.repo_root.is_absolute() else _REPO / args.repo_root
    units_path = args.units_jsonl if args.units_jsonl.is_absolute() else repo_root / args.units_jsonl
    out_dir = args.out if args.out.is_absolute() else repo_root / args.out
    units = _load_units(units_path)
    if not units:
        print("ERROR: empty units jsonl", file=sys.stderr)
        return 1

    # Per Codex review finding 9: require the ready gate for any non-dry-run
    # multi-worker run (workers > 1), not only workers > 16.  The dry-run
    # exemption is preserved.
    if args.workers > 1 and not args.dry_run:
        if args.owner_waiver:
            print(f"WARN: owner waiver for ready gate: {args.owner_waiver}", file=sys.stderr)
        elif not args.ready_gate_file:
            print(
                "ERROR: --workers > 1 requires --ready-gate-file from "
                "validate_paid_screen_ready_gate.py (or --owner-waiver)",
                file=sys.stderr,
            )
            return 2
        elif not _load_ready_gate(
            args.ready_gate_file if args.ready_gate_file.is_absolute()
            else repo_root / args.ready_gate_file
        ):
            print("ERROR: ready gate file reports ready_for_full_run=false", file=sys.stderr)
            return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "paid_screen_run_manifest.json"

    if args.dry_run:
        print(f"DRY_RUN units={len(units)} workers={args.workers} out={out_dir}")
        for unit in units[:20]:
            print(json.dumps(unit))
        if len(units) > 20:
            print(f"... and {len(units) - 20} more")
        return 0

    started = datetime.now(timezone.utc)
    worker_args = [
        (
            unit,
            str(repo_root),
            str(out_dir),
            args.vectorbt_scope,
            int(args.max_wall_clock_seconds),
            bool(args.no_llm),
        )
        for unit in units
    ]

    unit_results: List[Dict[str, Any]] = []
    if args.workers > 1:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=args.workers) as pool:
            for result in pool.imap_unordered(_run_unit_worker, worker_args):
                unit_results.append(result)
                print(
                    f"[unit] {result.get('unit_id')} -> {result.get('status')} "
                    f"({result.get('elapsed_seconds', 0):.1f}s)",
                    flush=True,
                )
    else:
        for wa in worker_args:
            result = _run_unit_worker(wa)
            unit_results.append(result)
            print(
                f"[unit] {result.get('unit_id')} -> {result.get('status')}",
                flush=True,
            )

    completed = sum(1 for r in unit_results if r.get("status") in {"OK", "OK_CACHED"})
    failed = sum(1 for r in unit_results if r.get("status") == "ERROR")
    skipped = len(units) - completed - failed
    elapsed_hours = max((datetime.now(timezone.utc) - started).total_seconds() / 3600.0, 1e-9)
    units_per_hour = completed / elapsed_hours

    manifest: Dict[str, Any] = {
        "status": "complete",
        "started_at_utc": started.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "units_jsonl": str(units_path),
        "vectorbt_scope": args.vectorbt_scope,
        "workers": args.workers,
        "expected_work_units": len(units),
        "completed_work_units": completed,
        "failed_work_units": failed,
        "skipped_work_units": skipped,
        "units_per_hour": round(units_per_hour, 4),
        "unit_results": unit_results,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Manifest: {manifest_path}")
    print(
        f"completed={completed} failed={failed} skipped={skipped} "
        f"units_per_hour={units_per_hour:.2f}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
