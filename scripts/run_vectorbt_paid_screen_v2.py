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

Launch hygiene: run **one** orchestrator per out-dir (``flock`` the manifest
path on Linux, or a single tmux session on Vast). Duplicate launches leave
orphan worker pools and block clean exit even after the manifest is terminal.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import queue
import signal
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
    if payload.get("errors"):
        return False
    if not payload.get("ready_for_full_run"):
        return False
    tail = str(payload.get("lookahead_pytest_tail") or "").strip()
    return bool(tail)


def _assert_hashes_match_ready_gate(
    gate_path: Path,
    *,
    events_csv_hash: str,
    lake_manifest_hash: str,
) -> None:
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    pilot = payload.get("pilot_hashes") or {}
    expected_events = str(pilot.get("events_csv_hash") or "").strip()
    expected_lake = str(pilot.get("lake_manifest_hash") or "").strip()
    if expected_events and events_csv_hash != expected_events:
        raise ValueError(
            f"events_csv_hash {events_csv_hash} != ready gate {expected_events}"
        )
    if expected_lake and lake_manifest_hash != expected_lake:
        raise ValueError(
            f"lake_manifest_hash {lake_manifest_hash} != ready gate {expected_lake}"
        )


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
    row.update(_artifact_feature_plane_metadata(result.screening_artifact_path))
    return row


def _unit_artifact_relpath(unit_id: str) -> str:
    return f"units/{unit_id}/screening_artifact.json"


def _artifact_feature_plane_metadata(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    artifact_path = Path(path)
    if not artifact_path.is_file():
        return {}
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    keys = (
        "research_clock",
        "allowed_context_set_id_or_null",
        "declared_context_sets",
        "feature_plane_status",
        "feature_usage_manifest_hash",
        "model_feature_usage_status",
        "context_ablation_status",
        "continuous_clock_status",
        "cross_asset_alignment_status",
        "vix_sensor_status",
        "vix_options_status",
        "cme_options_context_status",
        "latency_feature_status",
        "data_scope_skip_manifest_hash",
    )
    return {key: payload[key] for key in keys if key in payload}


def _worker_scratch_root(repo_root: Path, out_dir: Path) -> Path:
    """Return runtime scratch directory for worker artifacts (not under pipeline_runs)."""
    return repo_root / "runtime" / "paid_screen_scratch" / out_dir.name


def _build_worker_run_budget(
    max_wall_clock_seconds: int,
    *,
    max_trials: int | None = None,
    max_total_trials: int | None = None,
    max_models: int | None = None,
    max_symbols: int | None = None,
    max_feature_sets: int | None = None,
) -> dict[str, int]:
    """Build worker run_budget; omit unset optional caps."""
    budget: dict[str, int] = {}
    if max_trials is not None:
        budget["max_trials"] = int(max_trials)
    if max_total_trials is not None:
        budget["max_total_trials"] = int(max_total_trials)
    if max_models is not None:
        budget["max_models"] = int(max_models)
    if max_symbols is not None:
        budget["max_symbols"] = int(max_symbols)
    if max_feature_sets is not None:
        budget["max_feature_sets"] = int(max_feature_sets)
    if int(max_wall_clock_seconds) > 0:
        budget["max_wall_clock_seconds"] = int(max_wall_clock_seconds)
    return budget


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
    if not source_artifact_path:
        return None

    dest_dir = out_dir / "units" / unit_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "screening_artifact.json"

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
    row = {
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
    row.update(_artifact_feature_plane_metadata(str(artifact_path)))
    return row


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


def _print_dry_run_plan(
    *,
    args: argparse.Namespace,
    out_dir: Path,
    units_raw_count: int,
    units: list[PaidScreenUnit],
    grouping_ctx: WorkerContext,
) -> None:
    groups = group_units_by_batch_key(units, grouping_ctx)
    after_resume = "not_checked" if args.resume else str(len(units))
    print(
        f"DRY_RUN units={units_raw_count} "
        f"after_resume={after_resume} "
        f"batches={len(groups)} "
        f"workers={args.workers} "
        f"scope={args.vectorbt_scope} "
        f"out={out_dir}"
    )
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
        if profiler_summaries:
            manifest["performance_counters"] = profiler_summaries[-1]
            thread_limits = profiler_summaries[-1].get("native_thread_limits")
            if thread_limits:
                manifest["native_thread_limits"] = thread_limits
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_DRAIN_POLL_INTERVAL_SECONDS = 0.5
_INFLIGHT_BATCH_FACTOR = 2
_MIN_INFLIGHT_BATCHES = 8
_MANIFEST_FLUSH_INTERVAL_BATCHES = 5
_MANIFEST_FLUSH_INTERVAL_SECONDS = 15.0
_MAX_BATCH_REDISPATCH = 3
_STALE_BATCH_MIN_SECONDS = 30.0
_STALE_BATCH_MAX_SECONDS = 120.0
_WORKER_SHUTDOWN_MIN_TIMEOUT_SECONDS = 30.0
_WORKER_SHUTDOWN_MAX_TIMEOUT_SECONDS = 120.0
_WORKER_SHUTDOWN_PER_WORKER_SECONDS = 0.25
_WORKER_SHUTDOWN_COOP_SECONDS = 10.0
_POST_DRAIN_EXIT_BUDGET_SECONDS = 120.0


def _worker_shutdown_timeout_seconds(num_workers: int) -> float:
    """Scale pool teardown budget so large worker counts still finish promptly."""
    scaled = _WORKER_SHUTDOWN_MIN_TIMEOUT_SECONDS + (
        max(0, num_workers) * _WORKER_SHUTDOWN_PER_WORKER_SECONDS
    )
    return min(
        _WORKER_SHUTDOWN_MAX_TIMEOUT_SECONDS,
        _POST_DRAIN_EXIT_BUDGET_SECONDS,
        scaled,
    )


def _drain_goal_reached(
    collected_count: int,
    expected_batches: int,
    inflight: int,
    outstanding: Dict[Any, Any],
) -> bool:
    """True when all batches are collected and no work remains in flight."""
    return (
        collected_count >= expected_batches
        and inflight == 0
        and not outstanding
    )


def _force_kill_process(proc: mp.Process) -> None:
    """Hard-stop a worker process (cross-platform)."""
    try:
        if hasattr(proc, "kill"):
            proc.kill()
        elif proc.pid is not None:
            os.kill(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError, ValueError):
        pass
    except Exception:
        pass
    try:
        proc.join(timeout=0.5)
    except Exception:
        pass


def _close_mp_queues(*queues: "mp.Queue") -> None:
    """Release queue feeder threads so the orchestrator can exit cleanly."""
    for q in queues:
        try:
            q.close()
        except Exception:
            pass
        try:
            q.cancel_join_thread()
        except Exception:
            pass


def _inflight_batch_limit(num_workers: int) -> int:
    """Cap outstanding batches so dispatch cannot outrun drain."""
    return max(_MIN_INFLIGHT_BATCHES, num_workers * _INFLIGHT_BATCH_FACTOR)


def _alive_worker_count(workers: List[mp.Process]) -> int:
    alive = 0
    for proc in workers:
        proc.join(timeout=0)
        if proc.is_alive():
            alive += 1
    return alive


def _shutdown_workers(
    workers: List[mp.Process],
    batch_queue: "mp.Queue",
    *,
    total_timeout_seconds: float | None = None,
) -> set[int]:
    """Signal workers to exit and join with a bounded total wall clock.

    Returns PIDs that were ``terminate()``/SIGKILL'd so callers can exclude them
    from post-shutdown exit-code checks.
    """
    terminated_pids: set[int] = set()
    if not workers:
        return terminated_pids

    if total_timeout_seconds is None:
        total_timeout_seconds = _worker_shutdown_timeout_seconds(len(workers))

    shutdown_started = time.monotonic()
    shutdown_deadline = shutdown_started + total_timeout_seconds

    for _ in workers:
        try:
            batch_queue.put_nowait(None)
        except queue.Full:
            try:
                batch_queue.put(None, timeout=0.5)
            except Exception:
                pass
        except Exception:
            pass

    coop_deadline = shutdown_started + min(
        _WORKER_SHUTDOWN_COOP_SECONDS,
        total_timeout_seconds * 0.15,
    )
    while time.monotonic() < coop_deadline:
        if not any(proc.is_alive() for proc in workers):
            break
        for proc in workers:
            if proc.is_alive():
                proc.join(timeout=0.05)

    for proc in workers:
        if not proc.is_alive():
            continue
        if proc.pid is not None:
            terminated_pids.add(proc.pid)
        try:
            proc.terminate()
        except Exception:
            pass

    while time.monotonic() < shutdown_deadline:
        alive = [proc for proc in workers if proc.is_alive()]
        if not alive:
            break
        for proc in alive:
            proc.join(timeout=0.1)

    kill_deadline = shutdown_started + total_timeout_seconds + 5.0
    while time.monotonic() < kill_deadline:
        alive = [proc for proc in workers if proc.is_alive()]
        if not alive:
            break
        for proc in alive:
            if proc.pid is not None:
                terminated_pids.add(proc.pid)
            _force_kill_process(proc)

    return terminated_pids


def _effective_inflight_limit(inflight_limit: int, alive_workers: int) -> int:
    """Scale dispatch backpressure to workers that are actually alive."""
    if alive_workers <= 0:
        return 0
    alive_cap = max(_MIN_INFLIGHT_BATCHES, alive_workers * _INFLIGHT_BATCH_FACTOR)
    return min(inflight_limit, alive_cap)


def _stale_batch_redispatch_seconds(timeout_per_batch: float) -> float:
    return min(
        max(_STALE_BATCH_MIN_SECONDS, timeout_per_batch * 0.25),
        _STALE_BATCH_MAX_SECONDS,
    )


def _spawn_paid_screen_worker(
    ctx: mp.context.BaseContext,
    worker_args: Dict[str, Any],
    batch_queue: "mp.Queue",
    result_queue: "mp.Queue",
) -> mp.Process:
    proc = ctx.Process(
        target=worker_process_main,
        args=(worker_args, batch_queue, result_queue),
    )
    proc.start()
    return proc


def _maintain_worker_pool(
    workers: List[mp.Process],
    *,
    ctx: mp.context.BaseContext,
    worker_args: Dict[str, Any],
    batch_queue: "mp.Queue",
    result_queue: "mp.Queue",
    target_worker_count: int,
) -> int:
    """Replace dead workers so the pool stays near ``target_worker_count``."""
    respawned = 0
    for idx, proc in enumerate(workers):
        if proc.is_alive():
            continue
        proc.join(timeout=0)
        workers[idx] = _spawn_paid_screen_worker(
            ctx, worker_args, batch_queue, result_queue
        )
        respawned += 1
    while len(workers) < target_worker_count:
        workers.append(
            _spawn_paid_screen_worker(ctx, worker_args, batch_queue, result_queue)
        )
        respawned += 1
    return respawned


def _redispatch_outstanding_batches(
    outstanding: Dict[Any, Tuple[float, int, List[PaidScreenUnit]]],
    batch_queue: "mp.Queue",
    *,
    now: float,
) -> int:
    """Re-queue outstanding batches after worker respawn (lost in-flight work)."""
    redispatched = 0
    for batch_id, (dispatched_at, redispatch_count, batch_units) in list(
        outstanding.items()
    ):
        if redispatch_count >= _MAX_BATCH_REDISPATCH:
            continue
        batch_queue.put((batch_id, batch_units))
        # Preserve original dispatch time so expire cannot be reset indefinitely.
        outstanding[batch_id] = (dispatched_at, redispatch_count + 1, batch_units)
        redispatched += 1
    return redispatched


def _expire_hung_batches(
    outstanding: Dict[Any, Tuple[float, int, List[PaidScreenUnit]]],
    collected_batch_ids: set[Any],
    *,
    now: float,
    batch_timeout_seconds: float,
) -> List[Tuple[Any, List[UnitScreeningResult], Dict[str, Any]]]:
    """Synthesize ERROR results for batches that exceed the worker batch timeout."""
    expired: List[Tuple[Any, List[UnitScreeningResult], Dict[str, Any]]] = []
    for batch_id, (dispatched_at, redispatch_count, batch_units) in list(
        outstanding.items()
    ):
        if batch_id in collected_batch_ids:
            continue
        if redispatch_count < _MAX_BATCH_REDISPATCH:
            if now - dispatched_at < batch_timeout_seconds:
                continue
        if batch_units:
            results = [
                UnitScreeningResult(
                    unit_id=unit.unit_id,
                    status="ERROR",
                    error="batch_worker_hung_or_lost",
                )
                for unit in batch_units
            ]
        else:
            results = [
                UnitScreeningResult(
                    unit_id=f"batch_{batch_id}",
                    status="ERROR",
                    error="batch_worker_hung_or_lost",
                )
            ]
        expired.append((batch_id, results, {"stage_timings": {}}))
    return expired


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
        try:
            batch_id, results, profiler_summary = result_queue.get(
                timeout=min(remaining, _DRAIN_POLL_INTERVAL_SECONDS)
            )
        except queue.Empty:
            if expected_batches > 0 and workers and _workers_all_dead(workers):
                stop_reason = _worker_exit_stop_reason(workers)
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

    terminated_pids = _shutdown_workers(workers, batch_queue)

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

    _close_mp_queues(batch_queue, result_queue)

    return collected, stop_reason


def _pipelined_dispatch_and_drain(
    workers: List[mp.Process],
    batch_queue: "mp.Queue",
    result_queue: "mp.Queue",
    batches: List[Tuple[Any, List[PaidScreenUnit]]],
    expected_batches: int,
    timeout_per_batch: float,
    *,
    run_deadline: float | None = None,
    on_batch_collected: Callable[
        [Any, List[UnitScreeningResult], Dict[str, Any]], None
    ] | None = None,
    inflight_limit: int | None = None,
    spawn_ctx: mp.context.BaseContext | None = None,
    spawn_worker_args: Dict[str, Any] | None = None,
    target_worker_count: int | None = None,
    external_stop: Callable[[], str | None] | None = None,
) -> Tuple[List[Tuple[Any, List[UnitScreeningResult], Dict[str, Any]]], str | None]:
    """Dispatch batches with backpressure while draining worker results.

    Unlike enqueue-all-then-drain, this keeps at most ``inflight_limit`` batches
    outstanding so fast-failing workers cannot flood ``result_queue`` while the
    orchestrator is blocked on manifest I/O in ``on_batch_collected``.

    When ``spawn_ctx`` and ``spawn_worker_args`` are provided, dead workers are
    respawned and stale outstanding batches are re-queued so worker OOM exits
    cannot strand inflight work.
    """
    if inflight_limit is None:
        inflight_limit = _inflight_batch_limit(max(1, len(workers)))

    recover_worker_deaths = (
        spawn_ctx is not None
        and spawn_worker_args is not None
        and target_worker_count is not None
        and target_worker_count > 0
    )
    if target_worker_count is None:
        target_worker_count = len(workers)

    collected: List[Tuple[Any, List[UnitScreeningResult], Dict[str, Any]]] = []
    collected_batch_ids: set[Any] = set()
    stop_reason: str | None = None
    worker_failure_reason: str | None = None
    batch_iter = iter(batches)
    inflight = 0
    outstanding: Dict[Any, Tuple[float, int, List[PaidScreenUnit]]] = {}
    last_progress_log_mono = time.monotonic()
    last_collect_mono = time.monotonic()

    per_batch_budget = timeout_per_batch * max(expected_batches, 1)
    now = time.monotonic()
    per_batch_deadline = now + per_batch_budget
    if run_deadline is not None:
        wall_clock_limited = run_deadline <= per_batch_deadline
        deadline = min(per_batch_deadline, run_deadline)
    else:
        wall_clock_limited = False
        deadline = per_batch_deadline

    def _effective_limit() -> int:
        if recover_worker_deaths:
            return _effective_inflight_limit(
                inflight_limit,
                _alive_worker_count(workers),
            )
        return inflight_limit

    def _try_dispatch_one() -> bool:
        nonlocal inflight
        try:
            batch_id, batch_units = next(batch_iter)
        except StopIteration:
            return False
        batch_queue.put((batch_id, batch_units))
        outstanding[batch_id] = (time.monotonic(), 0, batch_units)
        inflight += 1
        return True

    def _record_batch_collected(
        batch_id: Any,
        results: List[UnitScreeningResult],
        profiler_summary: Dict[str, Any],
        *,
        alive: int,
        source: str,
    ) -> None:
        nonlocal inflight, last_collect_mono, last_progress_log_mono
        if batch_id in collected_batch_ids:
            return
        outstanding.pop(batch_id, None)
        collected.append((batch_id, results, profiler_summary))
        collected_batch_ids.add(batch_id)
        inflight = max(0, inflight - 1)
        last_collect_mono = time.monotonic()
        ok_units = sum(1 for r in results if r.status == "OK")
        failed_units = sum(1 for r in results if r.status == "ERROR")
        print(
            f"[drain] batch={batch_id} units={len(results)} "
            f"ok={ok_units} failed={failed_units} "
            f"collected={len(collected)}/{expected_batches} inflight={inflight} "
            f"alive={alive}/{target_worker_count if recover_worker_deaths else len(workers)} "
            f"src={source}",
            flush=True,
        )
        if on_batch_collected is not None:
            on_batch_collected(batch_id, results, profiler_summary)
        last_progress_log_mono = last_collect_mono

    def _maybe_recover_workers(now_mono: float) -> None:
        nonlocal last_progress_log_mono
        if not recover_worker_deaths:
            return
        respawned = _maintain_worker_pool(
            workers,
            ctx=spawn_ctx,
            worker_args=spawn_worker_args,
            batch_queue=batch_queue,
            result_queue=result_queue,
            target_worker_count=target_worker_count,
        )
        redispatched = 0
        if respawned > 0:
            redispatched = _redispatch_outstanding_batches(
                outstanding,
                batch_queue,
                now=now_mono,
            )
        if respawned or redispatched:
            alive = _alive_worker_count(workers)
            print(
                f"[pool] respawned={respawned} redispatched={redispatched} "
                f"alive={alive}/{target_worker_count} inflight={inflight}",
                flush=True,
            )
            last_progress_log_mono = now_mono

    def _should_stop() -> bool:
        nonlocal stop_reason
        if stop_reason is not None:
            return True
        if external_stop is not None:
            ext = external_stop()
            if ext:
                stop_reason = ext
                return True
        return False

    while inflight < _effective_limit() and not _should_stop():
        if not _try_dispatch_one():
            break

    if expected_batches > 0 and workers and _workers_all_dead(workers):
        if recover_worker_deaths and outstanding:
            _maybe_recover_workers(time.monotonic())
        elif not outstanding:
            stop_reason = _worker_exit_stop_reason(workers)

    while stop_reason is None and len(collected) < expected_batches:
        now_mono = time.monotonic()
        if external_stop is not None:
            ext = external_stop()
            if ext:
                stop_reason = ext
                break
        remaining = deadline - now_mono
        if remaining <= 0:
            stop_reason = (
                "max_wall_clock_seconds_exceeded"
                if wall_clock_limited
                else "batch_drain_timeout_or_collection_shortfall"
            )
            break

        _maybe_recover_workers(now_mono)

        if not recover_worker_deaths:
            polled_failure = _failed_worker_stop_reason(workers)
            if polled_failure is not None:
                worker_failure_reason = polled_failure

        alive = _alive_worker_count(workers)
        if alive == 0 and not outstanding:
            stop_reason = _worker_exit_stop_reason(workers)
            break

        for batch_id, results, profiler_summary in _expire_hung_batches(
            outstanding,
            collected_batch_ids,
            now=now_mono,
            batch_timeout_seconds=timeout_per_batch,
        ):
            _record_batch_collected(
                batch_id,
                results,
                profiler_summary,
                alive=alive,
                source="expire",
            )
            while inflight < _effective_limit() and not _should_stop():
                if not _try_dispatch_one():
                    break

        if _drain_goal_reached(len(collected), expected_batches, inflight, outstanding):
            break

        try:
            batch_id, results, profiler_summary = result_queue.get(
                timeout=min(remaining, _DRAIN_POLL_INTERVAL_SECONDS)
            )
        except queue.Empty:
            if _drain_goal_reached(
                len(collected), expected_batches, inflight, outstanding
            ):
                break
            if alive == 0 and not outstanding:
                stop_reason = _worker_exit_stop_reason(workers)
            elif (
                recover_worker_deaths
                and now_mono - last_progress_log_mono >= _MANIFEST_FLUSH_INTERVAL_SECONDS
            ):
                print(
                    f"[pool] waiting alive={alive}/{target_worker_count} "
                    f"inflight={inflight} outstanding={len(outstanding)} "
                    f"collected={len(collected)}/{expected_batches} "
                    f"since_collect={now_mono - last_collect_mono:.1f}s",
                    flush=True,
                )
                last_progress_log_mono = now_mono
            continue

        _record_batch_collected(
            batch_id,
            results,
            profiler_summary,
            alive=alive,
            source="worker",
        )

        while inflight < _effective_limit() and not _should_stop():
            if not _try_dispatch_one():
                break

        if _drain_goal_reached(len(collected), expected_batches, inflight, outstanding):
            break

    while True:
        try:
            result_queue.get_nowait()
        except queue.Empty:
            break

    terminated_pids = _shutdown_workers(
        workers,
        batch_queue,
        total_timeout_seconds=_POST_DRAIN_EXIT_BUDGET_SECONDS,
    )

    if not recover_worker_deaths and worker_failure_reason is None:
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

    if (
        not recover_worker_deaths
        and worker_failure_reason is not None
    ):
        stop_reason = worker_failure_reason

    _close_mp_queues(batch_queue, result_queue)

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
    parser.add_argument(
        "--max-trials",
        "--vectorbt-max-trials",
        dest="max_trials",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--max-total-trials",
        "--vectorbt-max-total-trials",
        dest="max_total_trials",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--max-models",
        "--vectorbt-max-models",
        dest="max_models",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--max-symbols",
        "--vectorbt-max-symbols",
        dest="max_symbols",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--max-feature-sets",
        "--vectorbt-max-feature-sets",
        dest="max_feature_sets",
        type=int,
        default=None,
    )
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
    parser.add_argument(
        "--abort-on-failed-units",
        action="store_true",
        help="Stop dispatch after first batch with ERROR units (declaration abort_on_failed_units)",
    )

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
        except ValueError as exc:
            print(f"ERROR: invalid unit row {exc}: {row}", file=sys.stderr)
            return 1

    if args.dry_run:
        try:
            derive_run_research_split(units_raw)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        dry_run_ctx = _grouping_context(
            repo_root,
            args.events_csv_hash or "dry_run_events_csv_hash_not_resolved",
            args.lake_manifest_hash or "dry_run_lake_manifest_hash_not_resolved",
            git_commit=resolve_git_commit(str(repo_root)),
        )
        _print_dry_run_plan(
            args=args,
            out_dir=out_dir,
            units_raw_count=len(units_raw),
            units=units,
            grouping_ctx=dry_run_ctx,
        )
        return 0

    # Hashes and research split must be resolved before resume (BLUEPRINT §8).
    try:
        events_csv_hash, lake_manifest_hash = _resolve_run_hashes(args, repo_root)
        if args.ready_gate_file:
            gate_path = (
                args.ready_gate_file
                if args.ready_gate_file.is_absolute()
                else repo_root / args.ready_gate_file
            )
            _assert_hashes_match_ready_gate(
                gate_path,
                events_csv_hash=events_csv_hash,
                lake_manifest_hash=lake_manifest_hash,
            )
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

    bootstrap_started = datetime.now(timezone.utc)
    if not args.dry_run:
        _write_run_manifest(
            manifest_path,
            status="bootstrapping",
            started=bootstrap_started,
            finished=None,
            out_dir=out_dir,
            units_path=units_path,
            args=args,
            units_raw_count=len(units_raw),
            completed=0,
            failed=0,
            skipped=len(skipped_unit_ids),
            unit_result_dicts=[],
            resume_cached_results=[],
            skipped_unit_ids=skipped_unit_ids,
            events_csv_hash=events_csv_hash,
            lake_manifest_hash=lake_manifest_hash,
            research_split=research_split,
            expected_batches=0,
            collected_batches=0,
            aborted=False,
            stop_reason=None,
        )
        print(f"[bootstrap] manifest written ({len(units)} units pending grouping)",
              flush=True)

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

    started = bootstrap_started
    run_started_mono = time.monotonic()
    run_deadline: float | None = None
    if int(args.max_wall_clock_seconds) > 0:
        run_deadline = run_started_mono + float(args.max_wall_clock_seconds)

    expected_batches = len(batches)
    partial_results: List[UnitScreeningResult] = []
    partial_result_dicts: List[Dict[str, Any]] = []
    run_state: Dict[str, Any] = {
        "collected_batches": 0,
        "stop_reason": None,
        "last_manifest_flush_mono": run_started_mono,
    }

    def _should_flush_running_manifest() -> bool:
        collected_batches = int(run_state["collected_batches"])
        if collected_batches <= 0:
            return False
        if collected_batches % _MANIFEST_FLUSH_INTERVAL_BATCHES == 0:
            return True
        return (
            time.monotonic() - float(run_state["last_manifest_flush_mono"])
            >= _MANIFEST_FLUSH_INTERVAL_SECONDS
        )

    def _flush_running_manifest(*, finished: datetime | None = None, force: bool = False) -> None:
        if not force and finished is None and not _should_flush_running_manifest():
            return
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
        run_state["last_manifest_flush_mono"] = time.monotonic()

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
        if args.abort_on_failed_units and any(r.status == "ERROR" for r in batch_results):
            run_state["stop_reason"] = "abort_on_failed_units"
            failed = [r for r in batch_results if r.status == "ERROR"]
            print(
                f"[abort] abort_on_failed_units: batch={_batch_id} "
                f"failed={len(failed)} sample={failed[0].error if failed else ''}",
                flush=True,
            )
        _flush_running_manifest()

    def _external_stop() -> str | None:
        reason = run_state.get("stop_reason")
        return str(reason) if reason else None

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
        "run_budget": _build_worker_run_budget(
            args.max_wall_clock_seconds,
            max_trials=args.max_trials,
            max_total_trials=args.max_total_trials,
            max_models=args.max_models,
            max_symbols=args.max_symbols,
            max_feature_sets=args.max_feature_sets,
        ),
        "max_batches_before_recycle": args.max_batches_before_recycle,
        "cache_memory_limit_mb": int(args.cache_memory_limit_mb),
        "cache_max_entries": int(args.cache_max_entries),
        "scratch_root": str(_worker_scratch_root(repo_root, out_dir)),
        "native_threads": 1,
    }
    for env_key in ("HFT3_NPZ_ROOT", "HFT3_MANIFEST_PATH"):
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            worker_args[env_key] = env_val

    ctx = mp.get_context("spawn")
    batch_queue: "mp.Queue" = ctx.Queue()
    result_queue: "mp.Queue" = ctx.Queue()

    num_workers = max(1, min(args.workers, len(batches)))
    workers: List[mp.Process] = []
    for _ in range(num_workers):
        workers.append(
            _spawn_paid_screen_worker(ctx, worker_args, batch_queue, result_queue)
        )

    # Pipelined dispatch + drain (backpressure keeps result_queue bounded)
    collected, drain_stop_reason = _pipelined_dispatch_and_drain(
        workers, batch_queue, result_queue,
        batches=batches,
        expected_batches=len(batches),
        timeout_per_batch=float(args.batch_timeout_seconds),
        run_deadline=run_deadline,
        on_batch_collected=_on_batch_collected,
        inflight_limit=_inflight_batch_limit(num_workers),
        spawn_ctx=ctx,
        spawn_worker_args=worker_args,
        target_worker_count=num_workers,
        external_stop=_external_stop,
    )
    run_state["stop_reason"] = drain_stop_reason or run_state.get("stop_reason")
    _flush_running_manifest(force=True)

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
    exit_code = 1 if failed or drain_stop_reason is not None else 0
    print(f"EXIT={exit_code}", flush=True)
    sys.exit(exit_code)


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
