"""Focused tests for paid-screen v2 orchestrator and vast launcher dispatch."""
from __future__ import annotations

import importlib.util
import json
import multiprocessing as mp
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backtest_pipeline.src.paid_screen_profiling import (
    determine_manifest_status,
    write_aggregate_screening_artifact,
)
from backtest_pipeline.src.paid_screen_types import UnitScreeningResult
from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact


_REPO = Path(__file__).resolve().parents[1]
_VAST_SCRIPT = _REPO / "scripts" / "run_vbt_paid_screen_vast_full.sh"
_SCRIPTS = _REPO / "scripts"
_VALID_UNIT_ARTIFACT = _REPO / "research_cards" / "pipeline_runs" / "paid_batch_ok" / "screening_artifact.json"


def _copy_valid_unit_artifact(dest: Path, *, repo_root: Path, unit: "PaidScreenUnit") -> None:
    from backtest_pipeline.src.paid_screen_batch import resolve_resume_provenance
    from backtest_pipeline.src.vectorbt_adapter import compute_screening_artifact_hash

    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_VALID_UNIT_ARTIFACT.read_text(encoding="utf-8"))
    payload.update(resolve_resume_provenance(str(repo_root), unit))
    payload["screening_artifact_hash"] = compute_screening_artifact_hash(payload)
    dest.write_text(json.dumps(payload), encoding="utf-8")


def _sleep_forever_worker() -> None:
    """Spawn target: stay alive without producing batch results."""
    time.sleep(3600)


def _immediate_worker_exit(_worker_args, _batch_queue, _result_queue) -> None:
    """Spawn target: simulate init/import failure before any batch result."""
    raise SystemExit(1)


def _echo_fast_fail_worker(_worker_args, batch_queue, result_queue) -> None:
    """Spawn target: immediately return ERROR results for each dispatched batch."""
    while True:
        batch = batch_queue.get()
        if batch is None:
            break
        batch_id, units = batch
        results = [
            UnitScreeningResult(
                unit_id=getattr(u, "unit_id", f"u{batch_id}"),
                status="ERROR",
                error="no_ohlcv_data",
            )
            for u in (units or [None])
        ]
        if not results:
            results = [
                UnitScreeningResult(
                    unit_id=f"u{batch_id}",
                    status="ERROR",
                    error="no_ohlcv_data",
                )
            ]
        result_queue.put((batch_id, results, {"stage_timings": {}}))


_DIE_BEFORE_ECHO_BUDGET: mp.sharedctypes.Synchronized | None = None


def _die_budget_then_echo_worker(_worker_args, batch_queue, result_queue) -> None:
    """Spawn target: exit without result for the first N batches, then fast-fail."""
    while True:
        batch = batch_queue.get()
        if batch is None:
            break
        batch_id, _units = batch
        budget = _DIE_BEFORE_ECHO_BUDGET
        if budget is not None:
            with budget.get_lock():
                if budget.value > 0:
                    budget.value -= 1
                    raise SystemExit(137)
        results = [
            UnitScreeningResult(
                unit_id=f"u{batch_id}",
                status="ERROR",
                error="no_ohlcv_data",
            )
        ]
        result_queue.put((batch_id, results, {"stage_timings": {}}))


def _spawn_test_echo_worker(
    ctx: mp.context.BaseContext,
    worker_args: dict,
    batch_queue: mp.Queue,
    result_queue: mp.Queue,
) -> mp.Process:
    proc = ctx.Process(
        target=_die_budget_then_echo_worker,
        args=(worker_args, batch_queue, result_queue),
    )
    proc.start()
    return proc


def _load_v2_module():
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    spec = importlib.util.spec_from_file_location(
        "run_vectorbt_paid_screen_v2",
        _REPO / "scripts" / "run_vectorbt_paid_screen_v2.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _declaration_run_id(decl_path: Path) -> str:
    """Mirror run_vbt_paid_screen_vast_full.sh declaration run-id extraction."""
    payload = json.loads(decl_path.read_text(encoding="utf-8"))
    for key in ("run_id", "vbt_full_run_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_vast_launch_hashes(
    repo_root: Path,
    events_csv: Path,
    decl_file: Path,
    *,
    manifest_path: str | None = None,
) -> tuple[str, str]:
    """Mirror run_vbt_paid_screen_vast_full.sh v2 hash resolution (fail-closed)."""
    import hashlib
    import os

    def file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:32]

    events_path = events_csv if events_csv.is_absolute() else repo_root / events_csv
    if not events_path.is_file():
        raise FileNotFoundError(f"events CSV unavailable for hash: {events_path}")
    events_hash = file_sha256(events_path)

    lake_hash: str | None = None
    manifest_env = (manifest_path if manifest_path is not None else os.environ.get("HFT3_MANIFEST_PATH", "")).strip()
    if manifest_env:
        manifest = Path(manifest_env)
        if not manifest.is_absolute():
            manifest = repo_root / manifest
        if manifest.is_file():
            lake_hash = file_sha256(manifest)
    if lake_hash is None and decl_file.is_file():
        decl_hash = str(json.loads(decl_file.read_text(encoding="utf-8")).get("lake_manifest_hash") or "").strip()
        if decl_hash:
            lake_hash = decl_hash
    if not lake_hash:
        raise ValueError("lake manifest hash unavailable for v2 launch")
    return events_hash, lake_hash


class TestVastLauncherV1Dispatch:
    def test_v2_only_flags_guarded_from_v1(self):
        text = _VAST_SCRIPT.read_text(encoding="utf-8")
        paid_v2_start = text.index("  PAID_ARGS+=(\n    --max-batches-before-recycle")
        v2_paid_block = text[paid_v2_start:]
        assert "--max-batches-before-recycle" in v2_paid_block
        assert "--cache-memory-limit-mb" in v2_paid_block
        assert "--cache-max-entries" in v2_paid_block
        assert "--resume" in v2_paid_block
        assert '--events-csv "$EVENTS_CSV"' in v2_paid_block
        assert '--events-csv-hash "$EVENTS_CSV_HASH"' in v2_paid_block
        assert '--lake-manifest-hash "$LAKE_MANIFEST_HASH"' in v2_paid_block
        pre_paid_v2 = text[:paid_v2_start]
        assert "--max-batches-before-recycle" not in pre_paid_v2
        assert "--cache-max-entries" not in pre_paid_v2
        assert '--events-csv-hash "$EVENTS_CSV_HASH"' not in pre_paid_v2
        assert '--lake-manifest-hash "$LAKE_MANIFEST_HASH"' not in pre_paid_v2

    def test_v1_rollback_does_not_resolve_or_pass_hash_flags(self):
        text = _VAST_SCRIPT.read_text(encoding="utf-8")
        v1_guard = text.index('if [[ "$EXECUTION_MODE" == "v1" ]]; then')
        hash_resolve = text.index("Resolving v2 provenance hashes")
        assert v1_guard < hash_resolve
        v1_script_line = text[v1_guard:hash_resolve]
        assert "--events-csv-hash" not in v1_script_line
        assert "--lake-manifest-hash" not in v1_script_line
        assert 'if [[ "$EXECUTION_MODE" != "v1" ]]; then' in text
        assert "Do not substitute units JSONL" in text


class TestVastLauncherHashWiring:
    def test_events_hash_derived_from_events_csv(self, tmp_path):
        repo = tmp_path / "repo"
        events = repo / "packages" / "data_system" / "config" / "events.csv"
        events.parent.mkdir(parents=True)
        events.write_text("event_id\nE1\n", encoding="utf-8")
        decl = repo / "decl.json"
        decl.write_text(json.dumps({"lake_manifest_hash": "decl_lake_hash"}), encoding="utf-8")
        events_hash, lake_hash = _resolve_vast_launch_hashes(
            repo,
            Path("packages/data_system/config/events.csv"),
            decl,
            manifest_path="",
        )
        assert len(events_hash) == 32
        assert lake_hash == "decl_lake_hash"

    def test_lake_hash_from_manifest_path_over_declaration(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        events = repo / "events.csv"
        events.write_text("event_id\nE1\n", encoding="utf-8")
        manifest = repo / "data" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b"lake-manifest-bytes")
        decl = repo / "decl.json"
        decl.write_text(json.dumps({"lake_manifest_hash": "decl_only_hash"}), encoding="utf-8")
        events_hash, lake_hash = _resolve_vast_launch_hashes(
            repo,
            events,
            decl,
            manifest_path=str(manifest),
        )
        import hashlib

        assert len(events_hash) == 32
        assert lake_hash == hashlib.sha256(manifest.read_bytes()).hexdigest()[:32]
        assert lake_hash != "decl_only_hash"

    def test_fail_closed_without_lake_hash_source(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        events = repo / "events.csv"
        events.write_text("event_id\nE1\n", encoding="utf-8")
        decl = repo / "decl.json"
        decl.write_text(json.dumps({"expected_work_units": 1}), encoding="utf-8")
        with pytest.raises(ValueError, match="lake manifest hash unavailable"):
            _resolve_vast_launch_hashes(repo, events, decl, manifest_path="")

    def test_fail_closed_when_events_csv_missing(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        decl = repo / "decl.json"
        decl.write_text(json.dumps({"lake_manifest_hash": "lh"}), encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="events CSV unavailable"):
            _resolve_vast_launch_hashes(
                repo,
                Path("missing/events.csv"),
                decl,
                manifest_path="",
            )


class TestVastLauncherRunIdDerivation:
    def test_script_derives_run_id_from_declaration_before_timestamp_fallback(self):
        text = _VAST_SCRIPT.read_text(encoding="utf-8")
        assert '"run_id"' in text
        assert '"vbt_full_run_id"' in text
        decl_ok = text.index("Declaration OK:")
        run_id_guard = text.index('if [[ -z "${VBT_FULL_RUN_ID:-}" && -f "$DECL_FILE" ]]; then')
        timestamp_fallback = text.index('export VBT_FULL_RUN_ID="${VBT_FULL_RUN_ID:-paid_full_$(date -u')
        assert decl_ok < run_id_guard < timestamp_fallback

    def test_run_id_from_declaration_run_id_key(self, tmp_path):
        decl = tmp_path / "vbt_full_run_declaration.json"
        decl.write_text(
            json.dumps({"expected_work_units": 1, "run_id": "paid_full_declared"}),
            encoding="utf-8",
        )
        assert _declaration_run_id(decl) == "paid_full_declared"

    def test_run_id_prefers_run_id_over_vbt_full_run_id(self, tmp_path):
        decl = tmp_path / "vbt_full_run_declaration.json"
        decl.write_text(
            json.dumps(
                {
                    "expected_work_units": 1,
                    "run_id": "primary_run",
                    "vbt_full_run_id": "secondary_run",
                }
            ),
            encoding="utf-8",
        )
        assert _declaration_run_id(decl) == "primary_run"

    def test_run_id_from_vbt_full_run_id_when_run_id_absent(self, tmp_path):
        decl = tmp_path / "vbt_full_run_declaration.json"
        decl.write_text(
            json.dumps({"expected_work_units": 1, "vbt_full_run_id": "legacy_decl_run"}),
            encoding="utf-8",
        )
        assert _declaration_run_id(decl) == "legacy_decl_run"

    def test_run_id_empty_when_declaration_has_no_id_keys(self, tmp_path):
        decl = tmp_path / "vbt_full_run_declaration.json"
        decl.write_text(
            json.dumps({"expected_work_units": 1, "workers_requested": 4}),
            encoding="utf-8",
        )
        assert _declaration_run_id(decl) == ""


class TestV2ManifestRelpaths:
    def test_fresh_result_row_includes_screening_artifact_relpath(self):
        v2 = _load_v2_module()
        result = UnitScreeningResult(unit_id="u42", status="OK")
        row = v2._result_to_dict(result)
        assert row["screening_artifact_relpath"] == "units/u42/screening_artifact.json"

    def test_resume_cached_row_includes_screening_artifact_relpath(self, tmp_path):
        v2 = _load_v2_module()
        unit_dir = tmp_path / "units" / "u9"
        unit_dir.mkdir(parents=True)
        (unit_dir / "screening_artifact.json").write_text(
            json.dumps({"promoted_ids": [], "rejected_ids": []}),
            encoding="utf-8",
        )
        row = v2._resume_cached_unit_result(tmp_path, "u9")
        assert row["screening_artifact_relpath"] == "units/u9/screening_artifact.json"

    def test_aggregate_promoted_ids_accepts_v2_manifest_rows(self, tmp_path):
        run_id = "paid_v2_aggregate"
        run_dir = tmp_path / "research_cards" / "pipeline_runs" / run_id
        run_dir.mkdir(parents=True)
        _copy_valid_unit_artifact(
            run_dir / "units" / "u1" / "screening_artifact.json",
            repo_root=_REPO,
            unit=_matching_paid_batch_unit(unit_id="u1"),
        )

        unit_results = [
            {
                "unit_id": "u1",
                "status": "OK",
                "screening_artifact_relpath": "units/u1/screening_artifact.json",
            }
        ]
        aggregate_path = write_aggregate_screening_artifact(
            run_dir,
            unit_results,
            finished_at_utc="2026-06-19T13:00:00+00:00",
        )
        assert aggregate_path is not None
        payload = json.loads(Path(aggregate_path).read_text(encoding="utf-8"))
        validate_screening_artifact(payload)
        assert payload["run_id"] == run_id

        manifest = {
            "out_dir": str(run_dir),
            "expected_work_units": 1,
            "completed_work_units": 1,
            "failed_work_units": 0,
            "skipped_work_units": 0,
            "unit_results": unit_results,
        }
        manifest_path = run_dir / "paid_screen_run_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        out = tmp_path / "promoted.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(_SCRIPTS / "aggregate_vbt_promoted_ids.py"),
                "--manifest",
                str(manifest_path),
                "--out",
                str(out),
            ],
            cwd=_REPO,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        promoted = json.loads(out.read_text(encoding="utf-8"))
        assert promoted["errors"] == []


class TestV2ResumeManifestAccounting:
    def test_resume_cached_units_count_as_completed(self, tmp_path):
        v2 = _load_v2_module()
        unit_dir = tmp_path / "units" / "u1"
        unit_dir.mkdir(parents=True)
        artifact = {
            "screening_artifact_hash": "abc123",
            "promoted_ids": ["p1"],
            "rejected_ids": [],
        }
        (unit_dir / "screening_artifact.json").write_text(
            json.dumps(artifact), encoding="utf-8"
        )
        cached = v2._resume_cached_unit_result(tmp_path, "u1")
        assert cached["status"] == "OK_CACHED"
        completed, failed, skipped = v2._count_work_units([])
        completed += 1
        status = determine_manifest_status(completed, failed, False, 1)
        assert status == "complete"
        assert failed == 0

    def test_mixed_resume_and_fresh_run_is_complete_when_no_failures(self):
        v2 = _load_v2_module()
        fresh = UnitScreeningResult(unit_id="u2", status="OK")
        completed, failed, skipped = v2._count_work_units([fresh])
        completed += 1  # one resume-cached unit
        status = determine_manifest_status(completed, failed, False, 2)
        assert status == "complete"
        assert failed == 0


class TestDrainWorkersWallClockBudget:
    def test_drain_respects_run_wall_clock_deadline(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        run_deadline = time.monotonic() + 0.4

        t0 = time.monotonic()
        collected, stop_reason = v2._drain_workers(
            [],
            batch_queue,
            result_queue,
            expected_batches=10,
            timeout_per_batch=1000.0,
            run_deadline=run_deadline,
        )
        elapsed = time.monotonic() - t0

        assert collected == []
        assert elapsed < 2.0

    def test_drain_collects_results_before_run_deadline(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        result_queue.put((1, [], {"stage_timings": {}}))
        run_deadline = time.monotonic() + 5.0

        collected, _stop_reason = v2._drain_workers(
            [],
            batch_queue,
            result_queue,
            expected_batches=1,
            timeout_per_batch=1000.0,
            run_deadline=run_deadline,
        )

        assert len(collected) == 1
        assert collected[0][0] == 1

    def test_wall_clock_drain_shortfall_sets_aborted_and_stop_reason(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        run_deadline = time.monotonic() + 0.05
        workers = [
            ctx.Process(target=_sleep_forever_worker)
            for _ in range(1)
        ]
        for proc in workers:
            proc.start()

        collected, stop_reason = v2._drain_workers(
            workers,
            batch_queue,
            result_queue,
            expected_batches=3,
            timeout_per_batch=1000.0,
            run_deadline=run_deadline,
        )

        expected_batches = 3
        collected_batches = len(collected)
        aborted = collected_batches < expected_batches

        assert collected == []
        assert aborted is True
        assert stop_reason == "max_wall_clock_seconds_exceeded"
        status = determine_manifest_status(0, 0, aborted, 10)
        assert status == "aborted"
        for proc in workers:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)


class TestDrainWorkersEarlyWorkerExit:
    def test_drain_aborts_when_all_workers_dead_before_expected_batches(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        workers = [
            ctx.Process(
                target=_immediate_worker_exit,
                args=({}, batch_queue, result_queue),
            )
            for _ in range(2)
        ]
        for proc in workers:
            proc.start()

        t0 = time.monotonic()
        collected, stop_reason = v2._drain_workers(
            workers,
            batch_queue,
            result_queue,
            expected_batches=5,
            timeout_per_batch=1000.0,
        )
        elapsed = time.monotonic() - t0

        expected_batches = 5
        collected_batches = len(collected)
        aborted = collected_batches < expected_batches

        assert collected == []
        assert aborted is True
        assert stop_reason == "worker_failed_exitcode_1"
        assert elapsed < 5.0
        status = determine_manifest_status(0, 0, aborted, 10)
        assert status == "aborted"


class TestDrainWorkersPartialWorkerFailure:
    def test_drain_fails_when_one_worker_crashes_but_batches_complete(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        result_queue.put((
            1,
            [UnitScreeningResult(unit_id="u1", status="OK")],
            {"stage_timings": {}},
        ))
        result_queue.put((
            2,
            [UnitScreeningResult(unit_id="u2", status="OK")],
            {"stage_timings": {}},
        ))
        workers = [
            ctx.Process(
                target=_immediate_worker_exit,
                args=({}, batch_queue, result_queue),
            ),
            ctx.Process(target=_sleep_forever_worker),
        ]
        for proc in workers:
            proc.start()

        collected, stop_reason = v2._drain_workers(
            workers,
            batch_queue,
            result_queue,
            expected_batches=2,
            timeout_per_batch=10.0,
        )

        expected_batches = 2
        collected_batches = len(collected)
        aborted = collected_batches < expected_batches or stop_reason is not None

        assert collected_batches == expected_batches
        assert stop_reason == "worker_failed_exitcode_1"
        assert aborted is True
        status = determine_manifest_status(collected_batches, 0, aborted, 2)
        assert status == "aborted"
        for proc in workers:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)


class TestDrainWorkersProgress:
    def test_drain_prints_batch_progress(self, capsys):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        result_queue.put((
            7,
            [UnitScreeningResult(unit_id="u1", status="OK")],
            {"stage_timings": {}},
        ))

        collected, stop_reason = v2._drain_workers(
            [],
            batch_queue,
            result_queue,
            expected_batches=1,
            timeout_per_batch=10.0,
        )

        assert len(collected) == 1
        assert stop_reason is None
        out = capsys.readouterr().out
        assert "batch=7" in out
        assert "collected=1/1" in out
        assert "ok=1" in out


def _matching_paid_batch_unit(**overrides) -> "PaidScreenUnit":
    from backtest_pipeline.src.paid_screen_types import PaidScreenUnit

    defaults = dict(
        unit_id="u_ok",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        hyp_id=5,
        symbol="MES.v.0",
        event_id="CPI_2024_09_11_TIGHT",
        event_type="CPI",
        research_split="discovery_confirmation",
        thesis="test",
    )
    defaults.update(overrides)
    return PaidScreenUnit(**defaults)


class TestResumeArtifactContextValidation:
    def test_valid_matching_artifact_accepted(self, tmp_path):
        v2 = _load_v2_module()
        unit = _matching_paid_batch_unit()
        repo_root = _REPO
        _copy_valid_unit_artifact(
            tmp_path / "units" / unit.unit_id / "screening_artifact.json",
            repo_root=repo_root,
            unit=unit,
        )
        assert v2._has_valid_artifact(
            tmp_path,
            unit,
            events_csv_hash="not_applicable_for_vectorbt_pilot",
            lake_manifest_hash="pilot_requires_lake_manifest_before_screen",
            research_split="discovery_confirmation",
            screening_scope="pilot",
            repo_root=repo_root,
            git_commit=v2.resolve_git_commit(str(repo_root)),
        )

    def test_wrong_symbol_rejected(self, tmp_path):
        v2 = _load_v2_module()
        unit = _matching_paid_batch_unit(symbol="ES.v.0")
        repo_root = _REPO
        _copy_valid_unit_artifact(
            tmp_path / "units" / unit.unit_id / "screening_artifact.json",
            repo_root=repo_root,
            unit=_matching_paid_batch_unit(),
        )
        assert not v2._has_valid_artifact(
            tmp_path,
            unit,
            events_csv_hash="not_applicable_for_vectorbt_pilot",
            lake_manifest_hash="pilot_requires_lake_manifest_before_screen",
            research_split="discovery_confirmation",
            screening_scope="pilot",
            repo_root=repo_root,
            git_commit=v2.resolve_git_commit(str(repo_root)),
        )

    def test_wrong_events_hash_rejected(self, tmp_path):
        v2 = _load_v2_module()
        unit = _matching_paid_batch_unit()
        repo_root = _REPO
        _copy_valid_unit_artifact(
            tmp_path / "units" / unit.unit_id / "screening_artifact.json",
            repo_root=repo_root,
            unit=unit,
        )
        assert not v2._has_valid_artifact(
            tmp_path,
            unit,
            events_csv_hash="different_events_hash",
            lake_manifest_hash="pilot_requires_lake_manifest_before_screen",
            research_split="discovery_confirmation",
            screening_scope="pilot",
            repo_root=repo_root,
            git_commit=v2.resolve_git_commit(str(repo_root)),
        )

    def test_wrong_research_split_rejected(self, tmp_path):
        v2 = _load_v2_module()
        unit = _matching_paid_batch_unit(research_split="holdout")
        repo_root = _REPO
        _copy_valid_unit_artifact(
            tmp_path / "units" / unit.unit_id / "screening_artifact.json",
            repo_root=repo_root,
            unit=_matching_paid_batch_unit(),
        )
        assert not v2._has_valid_artifact(
            tmp_path,
            unit,
            events_csv_hash="not_applicable_for_vectorbt_pilot",
            lake_manifest_hash="pilot_requires_lake_manifest_before_screen",
            research_split="discovery_confirmation",
            screening_scope="pilot",
            repo_root=repo_root,
            git_commit=v2.resolve_git_commit(str(repo_root)),
        )


class TestV2RunHashResolution:
    def test_main_fails_closed_without_hash_sources(self, tmp_path):
        v2 = _load_v2_module()
        repo = tmp_path / "repo"
        repo.mkdir()
        units_path = repo / "units.jsonl"
        units_path.write_text(
            json.dumps(
                {
                    "unit_id": "u1",
                    "model_id": "HYP_5",
                    "hyp_id": 5,
                    "symbol": "MES.v.0",
                    "event_id": "CPI_2024_09_11_TIGHT",
                    "event_type": "CPI",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        out_dir = repo / "out"
        rc = v2.main(
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
                "--dry-run",
            ]
        )
        assert rc == 1

    def test_resolve_run_hashes_from_explicit_and_files(self, tmp_path):
        v2 = _load_v2_module()
        repo = tmp_path / "repo"
        events_csv = repo / "events.csv"
        events_csv.parent.mkdir(parents=True)
        events_csv.write_text("event_id\nE1\n", encoding="utf-8")
        manifest = repo / "data" / "manifest.parquet"
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b"manifest")
        args = __import__("argparse").Namespace(
            events_csv_hash=None,
            lake_manifest_hash="explicit_lake_hash",
            events_csv=events_csv,
        )
        events_hash, lake_hash = v2._resolve_run_hashes(args, repo)
        assert len(events_hash) == 32
        assert lake_hash == "explicit_lake_hash"

    def test_main_rejects_mixed_research_split_before_run(self, tmp_path, capsys):
        v2 = _load_v2_module()
        repo = tmp_path / "repo"
        repo.mkdir()
        units_path = repo / "units.jsonl"
        units_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "unit_id": "u1",
                            "model_id": "HYP_5",
                            "hyp_id": 5,
                            "symbol": "MES.v.0",
                            "event_id": "CPI_2024_09_11_TIGHT",
                            "event_type": "CPI",
                            "research_split": "discovery_confirmation",
                        }
                    ),
                    json.dumps(
                        {
                            "unit_id": "u2",
                            "model_id": "HYP_6",
                            "hyp_id": 6,
                            "symbol": "MES.v.0",
                            "event_id": "CPI_2024_09_11_TIGHT",
                            "event_type": "CPI",
                            "research_split": "holdout",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        events_csv = repo / "events.csv"
        events_csv.write_text("event_id\nE1\n", encoding="utf-8")
        out_dir = repo / "out"
        rc = v2.main(
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
                "--events-csv",
                str(events_csv),
                "--lake-manifest-hash",
                "explicit_lake_hash",
                "--dry-run",
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "mixed research_split" in err
        assert not (out_dir / "paid_screen_run_manifest.json").is_file()


class TestRunningManifestWrites:
    def test_write_run_manifest_running_status(self, tmp_path):
        v2 = _load_v2_module()
        manifest_path = tmp_path / "paid_screen_run_manifest.json"
        started = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        args = __import__("argparse").Namespace(
            vectorbt_scope="paid-compute",
            workers=4,
            resume=False,
        )
        v2._write_run_manifest(
            manifest_path,
            status="running",
            started=started,
            finished=None,
            out_dir=tmp_path,
            units_path=tmp_path / "u.jsonl",
            args=args,
            units_raw_count=10,
            completed=2,
            failed=0,
            skipped=0,
            unit_result_dicts=[],
            resume_cached_results=[],
            skipped_unit_ids=[],
            events_csv_hash="eh",
            lake_manifest_hash="lh",
            research_split="discovery_confirmation",
            expected_batches=5,
            collected_batches=1,
            aborted=False,
            stop_reason=None,
        )
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert payload["status"] == "running"
        assert payload["collected_batches"] == 1
        assert payload["expected_batches"] == 5
        assert payload["completed_work_units"] == 2
        assert payload["finished_at_utc"] is None

    def test_drain_callback_increments_collected_batches(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        seen: list[int] = []

        def on_batch(_batch_id, results, _summary):
            seen.append(len(results))

        result_queue.put((
            1,
            [UnitScreeningResult(unit_id="u1", status="OK")],
            {"stage_timings": {}},
        ))
        result_queue.put((
            2,
            [UnitScreeningResult(unit_id="u2", status="ERROR")],
            {"stage_timings": {}},
        ))

        collected, _stop_reason = v2._drain_workers(
            [],
            batch_queue,
            result_queue,
            expected_batches=2,
            timeout_per_batch=10.0,
            on_batch_collected=on_batch,
        )

        assert len(collected) == 2
        assert seen == [1, 1]


class TestPipelinedDispatchAndDrain:
    def test_inflight_batch_limit_scales_with_workers(self):
        v2 = _load_v2_module()
        assert v2._inflight_batch_limit(1) == v2._MIN_INFLIGHT_BATCHES
        assert v2._inflight_batch_limit(230) == 460

    def test_effective_inflight_limit_tracks_alive_workers(self):
        v2 = _load_v2_module()
        configured = v2._inflight_batch_limit(230)
        assert v2._effective_inflight_limit(configured, 230) == 460
        assert v2._effective_inflight_limit(configured, 2) == 8
        assert v2._effective_inflight_limit(configured, 0) == 0

    def test_redispatch_stale_batches_requeues_orphaned_work(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        outstanding = {
            7: (0.0, 0, []),
            8: (50.0, 0, []),
        }
        redispatched = v2._redispatch_stale_batches(
            outstanding,
            batch_queue,
            stale_after_seconds=30.0,
            now=100.0,
        )
        assert redispatched == 2
        assert batch_queue.get(timeout=1)[0] in {7, 8}
        assert batch_queue.get(timeout=1)[0] in {7, 8}
        assert outstanding[7][1] == 1
        assert outstanding[8][1] == 1

    def test_pipelined_dispatch_collects_all_batches_under_backpressure(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        batches = [(idx, []) for idx in range(25)]
        inflight_limit = 4
        workers = [
            ctx.Process(
                target=_echo_fast_fail_worker,
                args=({}, batch_queue, result_queue),
            )
            for _ in range(2)
        ]
        for proc in workers:
            proc.start()

        callback_batches: list[int] = []

        def on_batch(batch_id, results, _summary):
            callback_batches.append(int(batch_id))

        collected, stop_reason = v2._pipelined_dispatch_and_drain(
            workers,
            batch_queue,
            result_queue,
            batches=batches,
            expected_batches=len(batches),
            timeout_per_batch=30.0,
            on_batch_collected=on_batch,
            inflight_limit=inflight_limit,
        )

        assert stop_reason is None
        assert len(collected) == len(batches)
        assert sorted(callback_batches) == list(range(25))

    def test_pipelined_high_worker_fast_fail_collects_all_batches(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        num_workers = 16
        batch_count = 64
        batches = [(idx, []) for idx in range(batch_count)]
        workers = [
            ctx.Process(
                target=_echo_fast_fail_worker,
                args=({}, batch_queue, result_queue),
            )
            for _ in range(num_workers)
        ]
        for proc in workers:
            proc.start()

        collected, stop_reason = v2._pipelined_dispatch_and_drain(
            workers,
            batch_queue,
            result_queue,
            batches=batches,
            expected_batches=batch_count,
            timeout_per_batch=30.0,
            inflight_limit=v2._inflight_batch_limit(num_workers),
        )

        assert stop_reason is None
        assert len(collected) == batch_count

    def test_pipelined_recovers_batches_when_workers_die_without_result(
        self, monkeypatch,
    ):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        batches = [(idx, []) for idx in range(6)]
        worker_args = {"repo_root": str(_REPO)}

        global _DIE_BEFORE_ECHO_BUDGET
        _DIE_BEFORE_ECHO_BUDGET = ctx.Value("i", 2)

        monkeypatch.setattr(v2, "_spawn_paid_screen_worker", _spawn_test_echo_worker)
        monkeypatch.setattr(v2, "_STALE_BATCH_MIN_SECONDS", 0.05)
        monkeypatch.setattr(v2, "_STALE_BATCH_MAX_SECONDS", 0.2)

        workers = [
            _spawn_test_echo_worker(ctx, worker_args, batch_queue, result_queue)
            for _ in range(2)
        ]

        collected, stop_reason = v2._pipelined_dispatch_and_drain(
            workers,
            batch_queue,
            result_queue,
            batches=batches,
            expected_batches=len(batches),
            timeout_per_batch=10.0,
            inflight_limit=4,
            spawn_ctx=ctx,
            spawn_worker_args=worker_args,
            target_worker_count=2,
        )

        assert stop_reason is None
        assert len(collected) == len(batches)
        _DIE_BEFORE_ECHO_BUDGET = None

    def test_manifest_flush_throttled_during_callback_flood(self, tmp_path, monkeypatch):
        v2 = _load_v2_module()
        flush_calls: list[int] = []

        def counting_flush(*, finished=None, force=False):
            flush_calls.append(int(run_state["collected_batches"]))

        run_state = {"collected_batches": 0, "last_manifest_flush_mono": time.monotonic()}
        monkeypatch.setattr(v2, "_MANIFEST_FLUSH_INTERVAL_BATCHES", 5)
        monkeypatch.setattr(v2, "_MANIFEST_FLUSH_INTERVAL_SECONDS", 9999.0)

        def should_flush():
            collected_batches = int(run_state["collected_batches"])
            if collected_batches <= 0:
                return False
            if collected_batches % v2._MANIFEST_FLUSH_INTERVAL_BATCHES == 0:
                return True
            return (
                time.monotonic() - float(run_state["last_manifest_flush_mono"])
                >= v2._MANIFEST_FLUSH_INTERVAL_SECONDS
            )

        for batch_idx in range(12):
            run_state["collected_batches"] += 1
            if should_flush():
                counting_flush()

        assert flush_calls == [5, 10]


class TestWorkerScratchPath:
    def test_build_worker_run_budget_omits_zero_wall_clock(self):
        v2 = _load_v2_module()
        assert v2._build_worker_run_budget(0) == {}
        assert v2._build_worker_run_budget(-1) == {}
        assert v2._build_worker_run_budget(42) == {"max_wall_clock_seconds": 42}

    def test_worker_scratch_root_under_runtime_not_out_dir(self, tmp_path):
        v2 = _load_v2_module()
        repo_root = tmp_path / "repo"
        out_dir = repo_root / "research_cards" / "pipeline_runs" / "pipeline_test_run"
        scratch = v2._worker_scratch_root(repo_root, out_dir)
        assert scratch == repo_root / "runtime" / "paid_screen_scratch" / "pipeline_test_run"
        assert ".worker_scratch" not in scratch.parts
        assert "pipeline_runs" not in scratch.parts

    def test_paid_screen_worker_receives_runtime_scratch_root(self, tmp_path):
        v2 = _load_v2_module()
        from backtest_pipeline.src.paid_screen_worker import PaidScreenWorker

        repo_root = tmp_path / "repo"
        out_dir = repo_root / "research_cards" / "pipeline_runs" / "pipeline_worker_run"
        scratch_root = str(v2._worker_scratch_root(repo_root, out_dir))
        worker = PaidScreenWorker(
            repo_root=str(repo_root),
            screening_scope="paid-compute",
            events_csv_hash="eh",
            lake_manifest_hash="lh",
            scratch_root=scratch_root,
        )
        assert worker.scratch_root == scratch_root
        assert Path(worker.scratch_root).parts[-2:] == ("paid_screen_scratch", "pipeline_worker_run")
        assert ".worker_scratch" not in worker.scratch_root
        assert "pipeline_runs" not in Path(worker.scratch_root).parts
