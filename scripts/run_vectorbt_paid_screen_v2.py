#!/usr/bin/env python3
"""VectorBT paid-compute screening v2 — long-lived worker orchestrator.

Phase 3 Task 3.2: replaces the v1 per-unit subprocess spawning with long-lived
worker processes (``PaidScreenWorker`` / ``worker_process_main``). Workers are
spawned once via the multiprocessing *spawn* context, kept alive for the entire
run, and fed batches of compatible units through a queue. The orchestrator
collects per-unit results, writes per-unit screening artifacts, persists the
run manifest (using ``determine_manifest_status`` for correct status), and
supports ``--resume`` (skip units whose artifact already validates).

Compatibility flags from v1 (``--vectorbt-scope``, ``--workers``,
``--max-wall-clock-seconds``, ``--ready-gate-file``, ``--owner-waiver``,
``--dry-run``, ``--no-llm``, ``--repo-root``) are preserved. New v2 flags:
``--max-batches-before-recycle``, ``--cache-memory-limit-mb``,
``--cache-max-entries``, ``--events-csv-hash``, ``--lake-manifest-hash``.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import queue
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.paid_screen_types import (
    PaidScreenUnit,
    UnitScreeningResult,
    WorkerContext,
)
from backtest_pipeline.src.paid_screen_batch import (
    group_units_by_batch_key,
    resolve_git_commit,
    resolve_resume_provenance,
)
from backtest_pipeline.src.paid_screen_profiling import (
    artifact_matches_resume_unit,
    derive_run_research_split,
    determine_manifest_status,
    resolve_events_csv_hash,
    resolve_lake_manifest_hash,
    write_aggregate_screening_artifact,
    write_failure_diagnostics,
    FailureDiagnostic,
)
from backtest_pipeline.src.paid_screen_worker import worker_process_main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_units(path: Path) -> List[Dict[str, Any]]:
    """Load JSONL unit rows (same row format as v1 + the structured fields)."""
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


def _result_to_dict(result: UnitScreeningResult) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "unit_id": result.unit_id,
        "status": result.status,
        "screening_artifact_path": result.screening_artifact_path,
        "screening_artifact_relpath": _unit_artifact_relpath(result.unit_id),
        "screening_artifact_hash": result.screening_artifact_hash,
        "error": result.error,
        "elapsed_seconds": round(result.elapsed_seconds, 4),
        "promoted_ids": result.promoted_ids,
        "rejected_ids": result.rejected_ids,
    }
    return row


def _unit_artifact_relpath(unit_id: str) -> str:
    return f"units/{unit_id}/screening_artifact.json"


def _worker_scratch_root(repo_root: Path, out_dir: Path) -> Path:
    """Return runtime scratch directory for worker artifacts (not under pipeline_runs)."""
    return repo_root / "runtime" / "paid_screen_scratch" / out_dir.name


def _build_worker_run_budget(max_wall_clock_seconds: int) -> dict[str, int]:
    """Build worker run_budget; omit wall-clock cap when unset (<= 0)."""
    if int(max_wall_clock_seconds) <= 0:
        return {}
    return {"max_wall_clock_seconds": int(max_wall_clock_seconds)}


def _has_valid_artifact(
    out_dir: Path,
    unit: PaidScreenUnit,
    *,
    events_csv_hash: str,
    lake_manifest_hash: str,
    research_split: str,
    screening_scope: str,
    repo_root: Path,
    git_commit: str,
) -> bool:
    """Return True if an on-disk artifact validates and matches the current unit/run."""
    dest = out_dir / "units" / unit.unit_id / "screening_artifact.json"
    if not dest.is_file():
        return False
    try:
        from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact

        payload = json.loads(dest.read_text(encoding="utf-8"))
        validate_screening_artifact(payload)
        provenance = resolve_resume_provenance(
            str(repo_root), unit, git_commit=git_commit
        )
        return artifact_matches_resume_unit(
            payload,
            unit,
            events_csv_hash=events_csv_hash,
            lake_manifest_hash=lake_manifest_hash,
            research_split=research_split,
            screening_scope=screening_scope,
            **provenance,
        )
    except Exception:
        return False


def _persist_unit_artifact(
    out_dir: Path, unit_id: str, source_artifact_path: Optional[str]
) -> Optional[Path]:
    """Copy the screening artifact produced by the worker into the run tree."""
    dest_dir = out_dir / "units" / unit_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "screening_artifact.json"

    if not source_artifact_path:
        return None

    src = Path(source_artifact_path)
    if src.is_file():
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return dest
    return None


def _resume_cached_unit_result(out_dir: Path, unit_id: str) -> Dict[str, Any]:
    """Manifest row for a resume-skipped unit with a valid on-disk artifact."""
    artifact_path = out_dir / _unit_artifact_relpath(unit_id)
    artifact_hash = None
    promoted_ids: List[str] = []
    rejected_ids: List[str] = []
    if artifact_path.is_file():
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            artifact_hash = payload.get("screening_artifact_hash")
            promoted_ids = list(payload.get("promoted_ids") or [])
            rejected_ids = list(payload.get("rejected_ids") or [])
        except Exception:
            pass
    return {
        "unit_id": unit_id,
        "status": "OK_CACHED",
        "screening_artifact_path": str(artifact_path),
        "screening_artifact_relpath": _unit_artifact_relpath(unit_id),
        "screening_artifact_hash": artifact_hash,
        "error": None,
        "elapsed_seconds": 0.0,
        "promoted_ids": promoted_ids,
        "rejected_ids": rejected_ids,
    }


def _count_work_units(all_results: List[UnitScreeningResult]) -> Tuple[int, int, int]:
    """Return (completed, failed, skipped) for manifest accounting."""
    completed = sum(1 for r in all_results if r.status in {"OK", "OK_CACHED"})
    failed = sum(1 for r in all_results if r.status == "ERROR")
    skipped = sum(1 for r in all_results if r.status == "SKIPPED")
    return completed, failed, skipped


def _resolve_run_hashes(
    args: argparse.Namespace,
    repo_root: Path,
) -> Tuple[str, str]:
    """Resolve real events/lake content hashes; fail closed when unavailable."""
    events_csv = None
    if getattr(args, "events_csv", None):
        events_csv = args.events_csv if args.events_csv.is_absolute() else repo_root / args.events_csv
    events_csv_hash = resolve_events_csv_hash(
        explicit_hash=args.events_csv_hash,
        events_csv=events_csv,
        repo_root=repo_root,
    )
    lake_manifest_hash = resolve_lake_manifest_hash(
        explicit_hash=args.lake_manifest_hash,
        repo_root=repo_root,
    )
    return events_csv_hash, lake_manifest_hash


def _write_run_manifest(
    manifest_path: Path,
    *,
    status: str,
    started: datetime,
    finished: datetime | None,
    out_dir: Path,
    units_path: Path,
    args: argparse.Namespace,
    units_raw_count: int,
    completed: int,
    failed: int,
    skipped: int,
    unit_result_dicts: List[Dict[str, Any]],
    resume_cached_results: List[Dict[str, Any]],
    skipped_unit_ids: List[str],
    events_csv_hash: str,
    lake_manifest_hash: str,
    research_split: str,
    expected_batches: int,
    collected_batches: int,
    aborted: bool,
    stop_reason: str | None,
    failure_diagnostics_path: str | None = None,
    profiler_summaries: List[Dict[str, Any]] | None = None,
    units_per_hour: float = 0.0,
) -> None:
    """Persist the run manifest (initial running snapshot or terminal state)."""
    elapsed_hours = max(
        ((finished or datetime.now(timezone.utc)) - started).total_seconds() / 3600.0,
        1e-9,
    )
    manifest: Dict[str, Any] = {
        "status": status,
        "started_at_utc": started.isoformat(),
        "finished_at_utc": finished.isoformat() if finished else None,
        "out_dir": str(out_dir),
        "units_jsonl": str(units_path),
        "vectorbt_scope": args.vectorbt_scope,
        "workers": args.workers,
        "expected_work_units": units_raw_count,
        "completed_work_units": completed,
        "failed_work_units": failed,
        "skipped_work_units": skipped,
        "units_per_hour": round(units_per_hour, 4),
        "unit_results": unit_result_dicts + resume_cached_results,
        "failure_diagnostics_path": failure_diagnostics_path,
        "resume": args.resume,
        "skipped_unit_ids": skipped_unit_ids,
        "orchestrator_version": "v2",
        "events_csv_hash": events_csv_hash,
        "lake_manifest_hash": lake_manifest_hash,
        "research_split": research_split,
        "expected_batches": expected_batches,
        "collected_batches": collected_batches,
        "aborted": aborted,
        "stop_reason": stop_reason,
    }
    if profiler_summaries is not None:
        manifest["worker_profiler_summaries"] = profiler_summaries
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_DRAIN_POLL_INTERVAL_SECONDS = 0.5


def _workers_all_dead(workers: List[mp.Process]) -> bool:
    if not workers:
        return False
    return not any(proc.is_alive() for proc in workers)


def _failed_worker_stop_reason(
    workers: List[mp.Process],
    *,
    exclude_pids: frozenset[int] | None = None,
) -> str | None:
    """Return a stop reason when any worker has a non-zero exit code."""
    if not workers:
        return None
    failed_codes: List[int] = []
    for proc in workers:
        if exclude_pids and proc.pid in exclude_pids:
            continue
        proc.join(timeout=0)
        if proc.exitcode is not None and proc.exitcode != 0:
            failed_codes.append(int(proc.exitcode))
    if not failed_codes:
        return None
    if len(set(failed_codes)) == 1:
        return f"worker_failed_exitcode_{failed_codes[0]}"
    return "worker_process_failed"


def _worker_exit_stop_reason(workers: List[mp.Process]) -> str:
    if not workers:
        return "no_workers_for_expected_batches"
    fail_reason = _failed_worker_stop_reason(workers)
    if fail_reason is not None:
        return fail_reason
    return "all_workers_exited_before_expected_batches"


def _drain_workers(
    workers: List[mp.Process],
    batch_queue: "mp.Queue",
    result_queue: "mp.Queue",
    expected_batches: int,
    timeout_per_batch: float,
    *,
    run_deadline: float | None = None,
    on_batch_collected: Callable[
        [Any, List[UnitScreeningResult], Dict[str, Any]], None
    ] | None = None,
) -> Tuple[List[Tuple[Any, List[UnitScreeningResult], Dict[str, Any]]], str | None]:
    """Collect ``expected_batches`` results from the result queue.

    Returns ``(collected, stop_reason)`` where ``stop_reason`` is set when the
    drain stops before all expected batches were collected.
    """
    collected: List[Tuple[Any, List[UnitScreeningResult], Dict[str, Any]]] = []
    stop_reason: str | None = None
    worker_failure_reason: str | None = None
    per_batch_budget = timeout_per_batch * max(expected_batches, 1)
    now = time.monotonic()
    per_batch_deadline = now + per_batch_budget
    if run_deadline is not None:
        wall_clock_limited = run_deadline <= per_batch_deadline
        deadline = min(per_batch_deadline, run_deadline)
    else:
        wall_clock_limited = False
        deadline = per_batch_deadline

    if expected_batches > 0 and workers and _workers_all_dead(workers):
        stop_reason = _worker_exit_stop_reason(workers)

    while stop_reason is None and len(collected) < expected_batches:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stop_reason = (
                "max_wall_clock_seconds_exceeded"
                if wall_clock_limited
                else "batch_drain_timeout_or_collection_shortfall"
            )
            break
        polled_failure = _failed_worker_stop_reason(workers)
        if polled_failure is not None:
            worker_failure_reason = polled_failure
        if _workers_all_dead(workers):
            stop_reason = _worker_exit_stop_reason(workers)
            break
        try:
            batch_id, results, profiler_summary = result_queue.get(
                timeout=min(remaining, _DRAIN_POLL_INTERVAL_SECONDS)
            )
        except queue.Empty:
            continue
        collected.append((batch_id, results, profiler_summary))
        ok_units = sum(1 for r in results if r.status == "OK")
        failed_units = sum(1 for r in results if r.status == "ERROR")
        print(
            f"[drain] batch={batch_id} units={len(results)} "
            f"ok={ok_units} failed={failed_units} "
            f"collected={len(collected)}/{expected_batches}",
            flush=True,
        )
        if on_batch_collected is not None:
            on_batch_collected(batch_id, results, profiler_summary)

    # Signal workers to shut down
    for _ in workers:
        try:
            batch_queue.put(None)
        except Exception:
            pass

    terminated_pids: set[int] = set()
    for proc in workers:
        proc.join(timeout=30)
        if proc.is_alive():
            if proc.pid is not None:
                terminated_pids.add(proc.pid)
            proc.terminate()
            proc.join(timeout=5)

    if worker_failure_reason is None:
        post_join_failure = _failed_worker_stop_reason(
            workers,
            exclude_pids=frozenset(terminated_pids),
        )
        if post_join_failure is not None:
            worker_failure_reason = post_join_failure

    if len(collected) < expected_batches and stop_reason is None:
        stop_reason = (
            "max_wall_clock_seconds_exceeded"
            if wall_clock_limited
            else "batch_drain_timeout_or_collection_shortfall"
        )

    if worker_failure_reason is not None:
        stop_reason = worker_failure_reason

    return collected, stop_reason


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="VectorBT paid-compute parallel screen v2 (long-lived workers)"
    )
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

    # v2-specific
    parser.add_argument("--resume", action="store_true",
                        help="Skip units with existing valid artifacts")
    parser.add_argument("--max-batches-before-recycle", type=int, default=100)
    parser.add_argument("--cache-memory-limit-mb", type=int, default=4096)
    parser.add_argument("--cache-max-entries", type=int, default=1000)
    parser.add_argument("--events-csv-hash", default=None,
                        help="Explicit events CSV hash (required if --events-csv missing/unreadable)")
    parser.add_argument("--events-csv", type=Path, default=None,
                        help="Path to events CSV for hash (default: DATA_SYSTEM_EVENTS_CSV or packages/data_system/config/events.csv)")
    parser.add_argument("--lake-manifest-hash", default=None,
                        help="Explicit lake manifest hash (required if HFT3_MANIFEST_PATH unset/missing)")
    parser.add_argument("--batch-timeout-seconds", type=float, default=1800.0,
                        help="Per-batch wall-clock timeout when draining results")

    args = parser.parse_args(argv)

    repo_root = args.repo_root if args.repo_root.is_absolute() else _REPO / args.repo_root
    units_path = args.units_jsonl if args.units_jsonl.is_absolute() else repo_root / args.units_jsonl
    out_dir = args.out if args.out.is_absolute() else repo_root / args.out

    units_raw = _load_units(units_path)
    if not units_raw:
        print("ERROR: empty units jsonl", file=sys.stderr)
        return 1

    # Ready gate enforcement (same semantics as v1)
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
        else:
            gate_path = (args.ready_gate_file if args.ready_gate_file.is_absolute()
                         else repo_root / args.ready_gate_file)
            if not _load_ready_gate(gate_path):
                print("ERROR: ready gate file reports ready_for_full_run=false", file=sys.stderr)
                return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "paid_screen_run_manifest.json"

    # Parse rows into typed units
    units: List[PaidScreenUnit] = []
    for row in units_raw:
        try:
            units.append(PaidScreenUnit.from_jsonl_row(row))
        except KeyError as exc:
            print(f"ERROR: malformed unit row missing field {exc}: {row}", file=sys.stderr)
            return 1

    # Hashes and research split must be resolved before resume (BLUEPRINT §8).
    try:
        events_csv_hash, lake_manifest_hash = _resolve_run_hashes(args, repo_root)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    git_commit = resolve_git_commit(str(repo_root))
    try:
        research_split = derive_run_research_split(units_raw)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    grouping_ctx = _grouping_context(
        repo_root, events_csv_hash, lake_manifest_hash, git_commit=git_commit
    )

    # Resume: filter out units whose artifact validates and matches run context
    skipped_unit_ids: List[str] = []
    if args.resume:
        kept: List[PaidScreenUnit] = []
        for unit in units:
            if _has_valid_artifact(
                out_dir,
                unit,
                events_csv_hash=events_csv_hash,
                lake_manifest_hash=lake_manifest_hash,
                research_split=research_split,
                screening_scope=args.vectorbt_scope,
                repo_root=repo_root,
                git_commit=git_commit,
            ):
                skipped_unit_ids.append(unit.unit_id)
            else:
                kept.append(unit)
        units = kept
        if skipped_unit_ids:
            print(f"[resume] skipping {len(skipped_unit_ids)} units with valid artifacts",
                  flush=True)

    # Dry run: print plan and exit
    if args.dry_run:
        groups = group_units_by_batch_key(units, grouping_ctx)
        print(f"DRY_RUN units={len(units_raw)} "
              f"after_resume={len(units)} "
              f"batches={len(groups)} "
              f"workers={args.workers} "
              f"scope={args.vectorbt_scope} "
              f"out={out_dir}")
        for unit in units[:20]:
            print(json.dumps({
                "unit_id": unit.unit_id,
                "model_id": unit.model_id,
                "symbol": unit.symbol,
                "event_id": unit.event_id,
                "event_type": unit.event_type,
            }))
        if len(units) > 20:
            print(f"... and {len(units) - 20} more")
        return 0

    # Group units into compatible batches (full BatchingKey)
    groups = group_units_by_batch_key(units, grouping_ctx)
    batches: List[Tuple[int, List[PaidScreenUnit]]] = [
        (idx, batch_units) for idx, batch_units in enumerate(groups.values())
    ]

    resume_cached_results = [
        _resume_cached_unit_result(out_dir, uid) for uid in skipped_unit_ids
    ]

    if not batches:
        # All units skipped by resume — terminal OK when artifacts validate.
        started = datetime.now(timezone.utc)
        finished = datetime.now(timezone.utc)
        completed = len(resume_cached_results)
        _write_run_manifest(
            manifest_path,
            status=determine_manifest_status(completed, 0, False, len(units_raw)),
            started=started,
            finished=finished,
            out_dir=out_dir,
            units_path=units_path,
            args=args,
            units_raw_count=len(units_raw),
            completed=completed,
            failed=0,
            skipped=0,
            unit_result_dicts=[],
            resume_cached_results=resume_cached_results,
            skipped_unit_ids=skipped_unit_ids,
            events_csv_hash=events_csv_hash,
            lake_manifest_hash=lake_manifest_hash,
            research_split=research_split,
            expected_batches=0,
            collected_batches=0,
            aborted=False,
            stop_reason=None,
        )
        aggregate_path = write_aggregate_screening_artifact(
            out_dir,
            resume_cached_results,
            finished_at_utc=finished.isoformat(),
        )
        if aggregate_path:
            print(f"Aggregate screening artifact: {aggregate_path}")
        print(f"Manifest: {manifest_path}")
        print(f"completed={completed} failed=0 skipped=0 units_per_hour=0.00")
        return 0

    started = datetime.now(timezone.utc)
    run_started_mono = time.monotonic()
    run_deadline: float | None = None
    if int(args.max_wall_clock_seconds) > 0:
        run_deadline = run_started_mono + float(args.max_wall_clock_seconds)

    expected_batches = len(batches)
    partial_results: List[UnitScreeningResult] = []
    partial_result_dicts: List[Dict[str, Any]] = []
    run_state: Dict[str, Any] = {"collected_batches": 0, "stop_reason": None}

    def _flush_running_manifest(*, finished: datetime | None = None) -> None:
        completed, failed, skipped = _count_work_units(partial_results)
        completed += len(resume_cached_results)
        collected_batches = int(run_state["collected_batches"])
        aborted = finished is not None and collected_batches < expected_batches
        elapsed_hours = max(
            ((finished or datetime.now(timezone.utc)) - started).total_seconds() / 3600.0,
            1e-9,
        )
        units_per_hour = completed / elapsed_hours
        _write_run_manifest(
            manifest_path,
            status="running" if finished is None else determine_manifest_status(
                completed,
                failed,
                aborted,
                len(units_raw),
            ),
            started=started,
            finished=finished,
            out_dir=out_dir,
            units_path=units_path,
            args=args,
            units_raw_count=len(units_raw),
            completed=completed,
            failed=failed,
            skipped=skipped,
            unit_result_dicts=partial_result_dicts.copy(),
            resume_cached_results=resume_cached_results,
            skipped_unit_ids=skipped_unit_ids,
            events_csv_hash=events_csv_hash,
            lake_manifest_hash=lake_manifest_hash,
            research_split=research_split,
            expected_batches=expected_batches,
            collected_batches=collected_batches,
            aborted=aborted,
            stop_reason=run_state.get("stop_reason"),
            units_per_hour=units_per_hour,
        )

    def _on_batch_collected(
        _batch_id: Any,
        batch_results: List[UnitScreeningResult],
        _profiler_summary: Dict[str, Any],
    ) -> None:
        for result in batch_results:
            _persist_unit_artifact(out_dir, result.unit_id, result.screening_artifact_path)
            partial_results.append(result)
            partial_result_dicts.append(_result_to_dict(result))
        run_state["collected_batches"] = int(run_state["collected_batches"]) + 1
        _flush_running_manifest()

    _write_run_manifest(
        manifest_path,
        status="running",
        started=started,
        finished=None,
        out_dir=out_dir,
        units_path=units_path,
        args=args,
        units_raw_count=len(units_raw),
        completed=len(resume_cached_results),
        failed=0,
        skipped=0,
        unit_result_dicts=[],
        resume_cached_results=resume_cached_results,
        skipped_unit_ids=skipped_unit_ids,
        events_csv_hash=events_csv_hash,
        lake_manifest_hash=lake_manifest_hash,
        research_split=research_split,
        expected_batches=expected_batches,
        collected_batches=0,
        aborted=False,
        stop_reason=None,
    )

    worker_args = {
        "repo_root": str(repo_root),
        "screening_scope": args.vectorbt_scope,
        "events_csv_hash": events_csv_hash,
        "lake_manifest_hash": lake_manifest_hash,
        "run_budget": _build_worker_run_budget(args.max_wall_clock_seconds),
        "max_batches_before_recycle": args.max_batches_before_recycle,
        "cache_memory_limit_mb": int(args.cache_memory_limit_mb),
        "cache_max_entries": int(args.cache_max_entries),
        "scratch_root": str(_worker_scratch_root(repo_root, out_dir)),
    }

    ctx = mp.get_context("spawn")
    batch_queue: "mp.Queue" = ctx.Queue()
    result_queue: "mp.Queue" = ctx.Queue()

    num_workers = max(1, min(args.workers, len(batches)))
    workers: List[mp.Process] = []
    for _ in range(num_workers):
        proc = ctx.Process(
            target=worker_process_main,
            args=(worker_args, batch_queue, result_queue),
        )
        proc.start()
        workers.append(proc)

    # Enqueue batches
    for batch_id, batch_units in batches:
        batch_queue.put((batch_id, batch_units))

    # Collect results (manifest updated after each batch via callback)
    collected, drain_stop_reason = _drain_workers(
        workers, batch_queue, result_queue,
        expected_batches=len(batches),
        timeout_per_batch=float(args.batch_timeout_seconds),
        run_deadline=run_deadline,
        on_batch_collected=_on_batch_collected,
    )
    run_state["stop_reason"] = drain_stop_reason

    results_by_batch: Dict[int, List[UnitScreeningResult]] = {}
    profiler_summaries: List[Dict[str, Any]] = []
    for batch_id, batch_results, profiler_summary in collected:
        results_by_batch[batch_id] = batch_results
        profiler_summaries.append(profiler_summary)

    all_results: List[UnitScreeningResult] = list(partial_results)
    present_unit_ids = {r.unit_id for r in all_results}
    failures: List[FailureDiagnostic] = []
    for batch_id, batch_units in batches:
        batch_results = results_by_batch.get(batch_id, [])
        if batch_results:
            for result in batch_results:
                if result.status == "ERROR" and result.error:
                    failures.append(FailureDiagnostic(
                        unit_or_batch_id=result.unit_id,
                        stage_name="screening",
                        exception_type="ScreeningError",
                        exception_message=result.error or "",
                        full_traceback="",
                        worker_pid=0,
                        start_ts_utc=started.isoformat(),
                        finish_ts_utc=datetime.now(timezone.utc).isoformat(),
                        elapsed_seconds=result.elapsed_seconds,
                        cache_state={},
                    ))
            continue
        for unit in batch_units:
            if unit.unit_id in present_unit_ids:
                continue
            err_result = UnitScreeningResult(
                unit_id=unit.unit_id,
                status="ERROR",
                error="batch_no_result_or_timeout",
            )
            all_results.append(err_result)
            partial_result_dicts.append(_result_to_dict(err_result))
            failures.append(FailureDiagnostic(
                unit_or_batch_id=unit.unit_id,
                stage_name="batch_drain",
                exception_type="RuntimeError",
                exception_message="batch_no_result_or_timeout",
                full_traceback="",
                worker_pid=0,
                start_ts_utc=started.isoformat(),
                finish_ts_utc=datetime.now(timezone.utc).isoformat(),
                elapsed_seconds=0.0,
                cache_state={},
            ))

    finished = datetime.now(timezone.utc)
    failure_diagnostics_path = None
    if failures:
        failure_diagnostics_path = write_failure_diagnostics(
            str(out_dir), failures
        )

    completed, failed, skipped = _count_work_units(all_results)
    completed += len(resume_cached_results)
    collected_batches = len(collected)
    aborted = collected_batches < expected_batches or drain_stop_reason is not None
    elapsed_hours = max((finished - started).total_seconds() / 3600.0, 1e-9)
    units_per_hour = completed / elapsed_hours

    _write_run_manifest(
        manifest_path,
        status=determine_manifest_status(completed, failed, aborted, len(units_raw)),
        started=started,
        finished=finished,
        out_dir=out_dir,
        units_path=units_path,
        args=args,
        units_raw_count=len(units_raw),
        completed=completed,
        failed=failed,
        skipped=skipped,
        unit_result_dicts=partial_result_dicts,
        resume_cached_results=resume_cached_results,
        skipped_unit_ids=skipped_unit_ids,
        events_csv_hash=events_csv_hash,
        lake_manifest_hash=lake_manifest_hash,
        research_split=research_split,
        expected_batches=expected_batches,
        collected_batches=collected_batches,
        aborted=aborted,
        stop_reason=drain_stop_reason if aborted else None,
        failure_diagnostics_path=failure_diagnostics_path,
        profiler_summaries=profiler_summaries,
        units_per_hour=units_per_hour,
    )
    unit_result_dicts = partial_result_dicts + resume_cached_results
    aggregate_path = write_aggregate_screening_artifact(
        out_dir,
        unit_result_dicts,
        finished_at_utc=finished.isoformat(),
    )
    if aggregate_path:
        print(f"Aggregate screening artifact: {aggregate_path}")
    print(f"Manifest: {manifest_path}")
    print(
        f"completed={completed} failed={failed} skipped={skipped} "
        f"units_per_hour={units_per_hour:.2f}"
    )
    return 1 if failed or drain_stop_reason is not None else 0


def _grouping_context(
    repo_root: Path,
    events_csv_hash: str,
    lake_manifest_hash: str,
    *,
    git_commit: str,
) -> WorkerContext:
    return WorkerContext(
        repo_root=str(repo_root),
        git_commit=git_commit,
        screening_scope="pilot",
        vectorbt_engine="numba",
        vectorbt_version="0.0.0",
        rust_runtime_proof=False,
        events_csv_hash=events_csv_hash,
        lake_manifest_hash=lake_manifest_hash,
    )


if __name__ == "__main__":
    raise SystemExit(main())