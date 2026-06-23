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
    from backtest_pipeline.src.promotion_gate import RejectedCandidate
    from backtest_pipeline.src.vectorbt_adapter import FilterResult
    from backtest_pipeline.src.vectorbt_adapter import compute_screening_artifact_hash

    dest.parent.mkdir(parents=True, exist_ok=True)
    provenance = resolve_resume_provenance(str(repo_root), unit)
    if _VALID_UNIT_ARTIFACT.is_file():
        payload = json.loads(_VALID_UNIT_ARTIFACT.read_text(encoding="utf-8"))
    else:
        rejected = RejectedCandidate(
            candidate_id="fixture_reject",
            hypothesis_id=unit.model_id,
            reject_reason="vectorbt_unavailable_fail_closed",
            metric_values={
                "symbol": unit.symbol,
                "base_candidate_id": f"{unit.model_id}|{unit.symbol}|{unit.event_id}|{unit.hyp_id or 0}",
                "base_candidate_metadata": {
                    "event_id": unit.event_id,
                    "symbol": unit.symbol,
                    "context_set_id": unit.context_set_id,
                    "allowed_context_set_id": unit.context_set_id,
                    "declared_context_sets": list(unit.declared_context_sets),
                },
            },
        )
        payload = FilterResult(
            backend="vectorbt_unavailable",
            run_id="paid_batch_ok_fixture",
            code_commit=provenance["code_commit"],
            screening_scope="pilot",
            research_clock=unit.research_clock,
            target_event_type_or_null=unit.event_type,
            allowed_context_set_id_or_null=unit.context_set_id,
            declared_context_sets=list(unit.declared_context_sets),
            parameter_space_id="ps_fixture",
            parameter_space_hash="ps_hash_fixture",
            max_trials=1,
            max_total_trials=1,
            max_models=1,
            max_symbols=1,
            max_feature_sets=1,
            rejected=[rejected],
        ).to_dict()
        payload["research_split"] = unit.research_split or "discovery_confirmation"
    payload.update(provenance)
    payload["screening_artifact_hash"] = compute_screening_artifact_hash(payload)
    validate_screening_artifact(payload)
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


def _invoke_main(v2, argv: list[str]) -> int:
    """Call orchestrator main(); normalize ``return`` and ``sys.exit`` to int."""
    try:
        rc = v2.main(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)
    return 0 if rc is None else int(rc)


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


class TestVastLauncherV2Only:
    def test_v2_provenance_flags_always_passed(self):
        text = _VAST_SCRIPT.read_text(encoding="utf-8")
        assert "run_paid_screen.py" in text
        assert "run_vectorbt_paid_screen.py" not in text
        assert "VBT_EXECUTION_MODE" not in text
        assert "--max-batches-before-recycle" in text
        assert "--cache-memory-limit-mb" in text
        assert "--cache-max-entries" in text
        assert "--resume" in text
        assert '--events-csv "$EVENTS_CSV"' in text
        assert '--events-csv-hash "$EVENTS_CSV_HASH"' in text
        assert '--lake-manifest-hash "$LAKE_MANIFEST_HASH"' in text
        assert "Resolving v2 provenance hashes" in text
        assert "Do not substitute manifest.json or units JSONL." in text

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

    def test_fresh_result_row_includes_feature_plane_metadata(self, tmp_path):
        v2 = _load_v2_module()
        artifact = tmp_path / "screening_artifact.json"
        artifact.write_text(
            json.dumps(
                {
                    "research_clock": "context_feature_uplift",
                    "allowed_context_set_id_or_null": "target_plus_cross_asset",
                    "declared_context_sets": ["target_only", "target_plus_cross_asset"],
                    "feature_plane_status": "scheduled_event_only",
                    "feature_usage_manifest_hash": "feature_hash",
                    "context_ablation_status": "not_measured",
                }
            ),
            encoding="utf-8",
        )
        result = UnitScreeningResult(
            unit_id="u42",
            status="OK",
            screening_artifact_path=str(artifact),
        )
        row = v2._result_to_dict(result)
        assert row["research_clock"] == "context_feature_uplift"
        assert row["allowed_context_set_id_or_null"] == "target_plus_cross_asset"
        assert row["declared_context_sets"] == ["target_only", "target_plus_cross_asset"]
        assert row["feature_usage_manifest_hash"] == "feature_hash"

    def test_resume_cached_row_includes_screening_artifact_relpath(self, tmp_path):
        v2 = _load_v2_module()
        unit_dir = tmp_path / "units" / "u9"
        unit_dir.mkdir(parents=True)
        (unit_dir / "screening_artifact.json").write_text(
            json.dumps(
                {
                    "promoted_ids": [],
                    "rejected_ids": [],
                    "research_clock": "context_feature_uplift",
                    "allowed_context_set_id_or_null": "target_plus_cross_asset",
                    "declared_context_sets": ["target_only", "target_plus_cross_asset"],
                    "feature_plane_status": "scheduled_event_only",
                    "feature_usage_manifest_hash": "feature_hash",
                    "context_ablation_status": "not_measured",
                }
            ),
            encoding="utf-8",
        )
        row = v2._resume_cached_unit_result(tmp_path, "u9")
        assert row["screening_artifact_relpath"] == "units/u9/screening_artifact.json"
        assert row["research_clock"] == "context_feature_uplift"
        assert row["allowed_context_set_id_or_null"] == "target_plus_cross_asset"
        assert row["declared_context_sets"] == ["target_only", "target_plus_cross_asset"]
        assert row["feature_usage_manifest_hash"] == "feature_hash"

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

    def test_drain_collects_queued_batch_when_workers_already_dead(self):
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
        for proc in workers:
            proc.join(timeout=5)
        result_queue.put((
            1,
            [UnitScreeningResult(unit_id="u1", status="OK")],
            {"stage_timings": {}},
        ))

        collected, stop_reason = v2._drain_workers(
            workers,
            batch_queue,
            result_queue,
            expected_batches=5,
            timeout_per_batch=10.0,
        )

        assert len(collected) == 1
        assert collected[0][0] == 1
        assert stop_reason == "worker_failed_exitcode_1"


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

    def test_declared_context_sets_match_ignores_order(self):
        from backtest_pipeline.src.paid_screen_profiling import _artifact_unit_context_matches

        unit = _matching_paid_batch_unit(
            context_set_id="target_plus_cross_asset",
            declared_context_sets=("target_only", "target_plus_cross_asset"),
        )
        payload = {
            "research_clock": unit.research_clock,
            "allowed_context_set_id_or_null": unit.context_set_id,
            "declared_context_sets": ["target_plus_cross_asset", "target_only"],
        }
        assert _artifact_unit_context_matches(payload, unit)

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
    def test_main_dry_run_without_hash_sources(self, tmp_path, capsys):
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
        rc = _invoke_main(
            v2,
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
                "--dry-run",
            ],
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "after_resume=1" in out

    def test_main_dry_run_resume_reports_filtered_count(self, tmp_path, capsys):
        v2 = _load_v2_module()
        unit = _matching_paid_batch_unit()
        units_path = tmp_path / "units.jsonl"
        units_path.write_text(
            json.dumps(
                {
                    "unit_id": unit.unit_id,
                    "model_id": unit.model_id,
                    "hyp_id": unit.hyp_id,
                    "symbol": unit.symbol,
                    "event_id": unit.event_id,
                    "event_type": unit.event_type,
                    "research_split": unit.research_split,
                    "research_clock": unit.research_clock,
                    "context_set_id": unit.context_set_id,
                    "declared_context_sets": list(unit.declared_context_sets),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        out_dir = tmp_path / "out"
        _copy_valid_unit_artifact(
            out_dir / "units" / unit.unit_id / "screening_artifact.json",
            repo_root=_REPO,
            unit=unit,
        )

        rc = _invoke_main(
            v2,
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(_REPO),
                "--dry-run",
                "--resume",
                "--events-csv-hash",
                "not_applicable_for_vectorbt_pilot",
                "--lake-manifest-hash",
                "pilot_requires_lake_manifest_before_screen",
                "--vectorbt-scope",
                "pilot",
            ],
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "[resume] skipping 1 units with valid artifacts" in out
        assert "DRY_RUN units=1 after_resume=0 batches=0" in out

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
        rc = _invoke_main(
            v2,
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
            ],
        )
        assert rc == 1

    def test_main_rejects_invalid_context_set_unit_row_cleanly(self, tmp_path, capsys):
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
                    "context_set_id": "not_a_context_set",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        out_dir = repo / "out"
        rc = _invoke_main(
            v2,
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
                "--dry-run",
            ],
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "ERROR: invalid unit row" in captured.err
        assert "context_set_id_invalid:not_a_context_set" in captured.err
        assert "not_a_context_set" in captured.err
        assert "Traceback" not in captured.err

    def test_main_rejects_invalid_research_clock_unit_row_cleanly(self, tmp_path, capsys):
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
                    "research_clock": "not_a_clock",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        out_dir = repo / "out"
        rc = _invoke_main(
            v2,
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
                "--dry-run",
            ],
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "ERROR: invalid unit row" in captured.err
        assert "research_clock_invalid:not_a_clock" in captured.err
        assert "Traceback" not in captured.err

    def test_main_reports_malformed_units_jsonl_cleanly(self, tmp_path, capsys):
        v2 = _load_v2_module()
        repo = tmp_path / "repo"
        repo.mkdir()
        units_path = repo / "units.jsonl"
        units_path.write_text("{not-json}\n", encoding="utf-8")
        out_dir = repo / "out"
        rc = _invoke_main(
            v2,
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
                "--dry-run",
            ],
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "ERROR: unable to load units jsonl" in captured.err
        assert "Traceback" not in captured.err

    def test_main_reports_invalid_utf8_units_jsonl_cleanly(self, tmp_path, capsys):
        v2 = _load_v2_module()
        repo = tmp_path / "repo"
        repo.mkdir()
        units_path = repo / "units.jsonl"
        units_path.write_bytes(b"\xff\xfe\x00\n")
        out_dir = repo / "out"
        rc = _invoke_main(
            v2,
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
                "--dry-run",
            ],
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "ERROR: unable to load units jsonl" in captured.err
        assert "Traceback" not in captured.err

    def test_main_rejects_non_object_unit_row_cleanly(self, tmp_path, capsys):
        v2 = _load_v2_module()
        repo = tmp_path / "repo"
        repo.mkdir()
        units_path = repo / "units.jsonl"
        units_path.write_text(json.dumps(["not", "object"]) + "\n", encoding="utf-8")
        out_dir = repo / "out"
        rc = _invoke_main(
            v2,
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
                "--dry-run",
            ],
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "ERROR: invalid unit row expected JSON object" in captured.err
        assert "Traceback" not in captured.err

    def test_main_rejects_non_iterable_declared_context_sets_cleanly(self, tmp_path, capsys):
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
                    "declared_context_sets": 7,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        out_dir = repo / "out"
        rc = _invoke_main(
            v2,
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
                "--dry-run",
            ],
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "ERROR: invalid unit row" in captured.err
        assert "Traceback" not in captured.err

    def test_main_reports_malformed_ready_gate_cleanly(self, tmp_path, capsys):
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
        gate_path = repo / "gate.json"
        gate_path.write_text("{not-json}\n", encoding="utf-8")
        out_dir = repo / "out"
        rc = _invoke_main(
            v2,
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
                "--workers",
                "2",
                "--ready-gate-file",
                str(gate_path),
            ],
        )
        captured = capsys.readouterr()
        assert rc == 2
        assert "ERROR: ready gate file is unreadable" in captured.err
        assert "Traceback" not in captured.err

    def test_ready_gate_requires_json_object(self, tmp_path):
        v2 = _load_v2_module()
        gate_path = tmp_path / "gate.json"
        gate_path.write_text("[]\n", encoding="utf-8")

        with pytest.raises(ValueError, match="ready gate file must contain a JSON object"):
            v2._load_ready_gate(gate_path)

    def test_ready_gate_requires_literal_true(self, tmp_path):
        v2 = _load_v2_module()
        gate_path = tmp_path / "gate.json"
        gate_path.write_text(
            json.dumps(
                {
                    "errors": [],
                    "ready_for_full_run": "false",
                    "lookahead_pytest_tail": "1 passed",
                }
            ),
            encoding="utf-8",
        )

        assert v2._load_ready_gate(gate_path) is False

    def test_ready_gate_requires_empty_errors_list(self, tmp_path):
        v2 = _load_v2_module()
        gate_path = tmp_path / "gate.json"
        gate_path.write_text(
            json.dumps(
                {
                    "errors": {},
                    "ready_for_full_run": True,
                    "lookahead_pytest_tail": "1 passed",
                }
            ),
            encoding="utf-8",
        )

        assert v2._load_ready_gate(gate_path) is False

    def test_ready_gate_requires_string_pytest_tail(self, tmp_path):
        v2 = _load_v2_module()
        gate_path = tmp_path / "gate.json"
        gate_path.write_text(
            json.dumps(
                {
                    "errors": [],
                    "ready_for_full_run": True,
                    "lookahead_pytest_tail": {"bad": "tail"},
                }
            ),
            encoding="utf-8",
        )

        assert v2._load_ready_gate(gate_path) is False

    def test_ready_gate_hash_path_requires_json_object(self, tmp_path):
        v2 = _load_v2_module()
        gate_path = tmp_path / "gate.json"
        gate_path.write_text("[]\n", encoding="utf-8")

        with pytest.raises(ValueError, match="ready gate file must contain a JSON object"):
            v2._assert_hashes_match_ready_gate(
                gate_path,
                events_csv_hash="events",
                lake_manifest_hash="lake",
            )

    def test_ready_gate_hash_path_requires_pilot_hash_object(self, tmp_path):
        v2 = _load_v2_module()
        gate_path = tmp_path / "gate.json"
        gate_path.write_text(
            json.dumps(
                {
                    "ready_for_full_run": True,
                    "lookahead_pytest_tail": "1 passed",
                    "pilot_hashes": "bad",
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="ready gate pilot_hashes must be a JSON object"):
            v2._assert_hashes_match_ready_gate(
                gate_path,
                events_csv_hash="events",
                lake_manifest_hash="lake",
            )

    def test_ready_gate_hash_path_requires_pilot_hashes(self, tmp_path):
        v2 = _load_v2_module()
        gate_path = tmp_path / "gate.json"
        gate_path.write_text(
            json.dumps(
                {
                    "ready_for_full_run": True,
                    "lookahead_pytest_tail": "1 passed",
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="ready gate pilot_hashes missing"):
            v2._assert_hashes_match_ready_gate(
                gate_path,
                events_csv_hash="events",
                lake_manifest_hash="lake",
            )

    def test_ready_gate_hash_path_requires_events_and_lake_hashes(self, tmp_path):
        v2 = _load_v2_module()
        gate_path = tmp_path / "gate.json"
        gate_path.write_text(
            json.dumps(
                {
                    "ready_for_full_run": True,
                    "lookahead_pytest_tail": "1 passed",
                    "pilot_hashes": {},
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="ready gate pilot_hashes missing events/lake hashes"):
            v2._assert_hashes_match_ready_gate(
                gate_path,
                events_csv_hash="events",
                lake_manifest_hash="lake",
            )

    def test_ready_gate_hash_path_requires_string_hashes(self, tmp_path):
        v2 = _load_v2_module()
        gate_path = tmp_path / "gate.json"
        gate_path.write_text(
            json.dumps(
                {
                    "ready_for_full_run": True,
                    "lookahead_pytest_tail": "1 passed",
                    "pilot_hashes": {
                        "events_csv_hash": 123,
                        "lake_manifest_hash": "lake",
                    },
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="ready gate pilot_hashes events/lake hashes must be strings"):
            v2._assert_hashes_match_ready_gate(
                gate_path,
                events_csv_hash="123",
                lake_manifest_hash="lake",
            )

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
        rc = _invoke_main(
            v2,
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
            ],
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

    def test_redispatch_outstanding_batches_requeues_orphaned_work(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        outstanding = {
            7: (0.0, 0, []),
            8: (50.0, 0, []),
        }
        redispatched = v2._redispatch_outstanding_batches(
            outstanding,
            batch_queue,
            now=100.0,
        )
        assert redispatched == 2
        assert batch_queue.get(timeout=1)[0] in {7, 8}
        assert batch_queue.get(timeout=1)[0] in {7, 8}
        assert outstanding[7][1] == 1
        assert outstanding[8][1] == 1

    def test_redispatch_preserves_original_dispatch_time_for_expire(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        outstanding = {1: (0.0, 1, [])}
        redispatched = v2._redispatch_outstanding_batches(
            outstanding,
            batch_queue,
            now=50.0,
        )
        assert redispatched == 1
        assert outstanding[1][0] == 0.0
        assert outstanding[1][1] == 2

    def test_expire_hung_batches_synthesizes_errors(self):
        v2 = _load_v2_module()
        from backtest_pipeline.src.paid_screen_types import PaidScreenUnit

        unit = PaidScreenUnit(
            unit_id="u1",
            model_id="HYP_5",
            hyp_id=5,
            symbol="MES.v.0",
            event_id="ADP_EMPLOYMENT_2018_05_02_TIGHT",
            event_type="ADP_EMPLOYMENT",
        )
        outstanding = {3: (0.0, 0, [unit])}
        expired = v2._expire_hung_batches(
            outstanding,
            set(),
            now=200.0,
            batch_timeout_seconds=30.0,
        )
        assert len(expired) == 1
        batch_id, results, _summary = expired[0]
        assert batch_id == 3
        assert results[0].status == "ERROR"
        assert results[0].error == "batch_worker_hung_or_lost"

    def test_expire_after_max_redispatch_without_full_timeout(self):
        v2 = _load_v2_module()
        outstanding = {9: (100.0, v2._MAX_BATCH_REDISPATCH, [])}
        expired = v2._expire_hung_batches(
            outstanding,
            set(),
            now=101.0,
            batch_timeout_seconds=1800.0,
        )
        assert len(expired) == 1
        batch_id, results, _summary = expired[0]
        assert batch_id == 9
        assert results[0].error == "batch_worker_hung_or_lost"

    def test_pipelined_all_batches_collected_via_expire_exits_promptly(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        batch_count = 12
        batches = [(idx, []) for idx in range(batch_count)]
        workers = [
            ctx.Process(target=_sleep_forever_worker)
            for _ in range(2)
        ]
        for proc in workers:
            proc.start()

        t0 = time.monotonic()
        collected, stop_reason = v2._pipelined_dispatch_and_drain(
            workers,
            batch_queue,
            result_queue,
            batches=batches,
            expected_batches=batch_count,
            timeout_per_batch=0.3,
            inflight_limit=4,
        )
        elapsed = time.monotonic() - t0

        assert stop_reason is None
        assert len(collected) == batch_count
        assert elapsed < 30.0
        for _batch_id, results, _summary in collected:
            assert results[0].error == "batch_worker_hung_or_lost"

    def test_pipelined_high_worker_count_fast_fail_shuts_down_promptly(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        num_workers = 32
        batch_count = 128
        batches = [(idx, []) for idx in range(batch_count)]
        worker_args = {
            "repo_root": str(_REPO),
            "screening_scope": "paid-compute",
            "events_csv_hash": "eh",
            "lake_manifest_hash": "lh",
            "scratch_root": str(_REPO / "runtime" / "paid_screen_scratch" / "orchestrator_test"),
        }
        workers = [
            ctx.Process(
                target=_echo_fast_fail_worker,
                args=({}, batch_queue, result_queue),
            )
            for _ in range(num_workers)
        ]
        for proc in workers:
            proc.start()

        t0 = time.monotonic()
        collected, stop_reason = v2._pipelined_dispatch_and_drain(
            workers,
            batch_queue,
            result_queue,
            batches=batches,
            expected_batches=batch_count,
            timeout_per_batch=30.0,
            inflight_limit=v2._inflight_batch_limit(num_workers),
            spawn_ctx=ctx,
            spawn_worker_args=worker_args,
            target_worker_count=num_workers,
        )
        elapsed = time.monotonic() - t0

        assert stop_reason is None
        assert len(collected) == batch_count
        assert elapsed < 45.0

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


class TestOrchestratorMainExit:
    def test_orchestrator_main_exits_after_drain(self, tmp_path, monkeypatch):
        """main() must terminate promptly after drain+shutdown (mocked 128 workers)."""
        v2 = _load_v2_module()
        num_workers = 128

        class FakeProcess:
            _next_pid = 10_000

            def __init__(self, *args, **kwargs):
                type(self)._next_pid += 1
                self.pid = type(self)._next_pid
                self.exitcode = 0

            def start(self) -> None:
                return None

            def is_alive(self) -> bool:
                return False

            def join(self, timeout=None) -> None:
                return None

            def terminate(self) -> None:
                return None

            def kill(self) -> None:
                return None

        def fake_spawn(_ctx, _worker_args, _batch_queue, _result_queue):
            return FakeProcess()

        def fake_dispatch(
            workers,
            batch_queue,
            result_queue,
            batches,
            expected_batches,
            **kwargs,
        ):
            on_batch = kwargs.get("on_batch_collected")
            collected = []
            for batch_id, _units in batches:
                results = [
                    UnitScreeningResult(
                        unit_id=f"u{batch_id}",
                        status="ERROR",
                        error="no_ohlcv_data",
                    )
                ]
                summary = {"stage_timings": {}}
                if on_batch is not None:
                    on_batch(batch_id, results, summary)
                collected.append((batch_id, results, summary))
            v2._shutdown_workers(
                workers,
                batch_queue,
                total_timeout_seconds=v2._POST_DRAIN_EXIT_BUDGET_SECONDS,
            )
            v2._close_mp_queues(batch_queue, result_queue)
            return collected, None

        monkeypatch.setattr(v2, "_spawn_paid_screen_worker", fake_spawn)
        monkeypatch.setattr(v2, "_pipelined_dispatch_and_drain", fake_dispatch)

        repo = tmp_path / "repo"
        repo.mkdir()
        events_csv = repo / "events.csv"
        events_csv.write_text("event_id\nE1\n", encoding="utf-8")
        units_path = repo / "units.jsonl"
        unit_rows = [
            {
                "unit_id": f"u{i}",
                "model_id": "HYP_5",
                "hyp_id": 5,
                "symbol": "MES.v.0",
                "event_id": "CPI_2024_09_11_TIGHT",
                "event_type": "CPI",
                "research_split": "discovery_confirmation",
            }
            for i in range(num_workers)
        ]
        units_path.write_text(
            "\n".join(json.dumps(row) for row in unit_rows) + "\n",
            encoding="utf-8",
        )
        out_dir = repo / "out"
        gate_path = repo / "gate.json"
        gate_path.write_text(
            json.dumps({
                "errors": [],
                "ready_for_full_run": True,
                "lookahead_pytest_tail": "1 passed in 0.01s",
                "pilot_hashes": {
                    "events_csv_hash": "events_csv_hash",
                    "lake_manifest_hash": "explicit_lake_hash",
                },
            }),
            encoding="utf-8",
        )

        t0 = time.monotonic()
        rc = _invoke_main(
            v2,
            [
                "--units-jsonl",
                str(units_path),
                "--out",
                str(out_dir),
                "--repo-root",
                str(repo),
                "--events-csv",
                str(events_csv),
                "--events-csv-hash",
                "events_csv_hash",
                "--lake-manifest-hash",
                "explicit_lake_hash",
                "--workers",
                str(num_workers),
                "--ready-gate-file",
                str(gate_path),
                "--batch-timeout-seconds",
                "5",
            ],
        )
        elapsed = time.monotonic() - t0

        assert elapsed < 30.0
        assert rc == 1
        manifest_path = out_dir / "paid_screen_run_manifest.json"
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["status"] in {"complete", "partial", "aborted", "failed"}
        assert manifest["finished_at_utc"] is not None


class TestWorkerShutdownAtScale:
    def test_worker_shutdown_timeout_scales_with_pool_size(self):
        v2 = _load_v2_module()
        assert v2._worker_shutdown_timeout_seconds(1) == pytest.approx(30.25, abs=0.01)
        assert v2._worker_shutdown_timeout_seconds(230) == pytest.approx(87.5, abs=0.01)
        assert v2._worker_shutdown_timeout_seconds(1000) == v2._WORKER_SHUTDOWN_MAX_TIMEOUT_SECONDS

    @pytest.mark.slow
    def test_shutdown_workers_reaps_230_stuck_processes_within_budget(self):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        num_workers = 230
        workers = [
            ctx.Process(target=_sleep_forever_worker)
            for _ in range(num_workers)
        ]
        for proc in workers:
            proc.start()

        t0 = time.monotonic()
        terminated = v2._shutdown_workers(workers, batch_queue)
        elapsed = time.monotonic() - t0

        assert elapsed < v2._POST_DRAIN_EXIT_BUDGET_SECONDS
        assert all(not proc.is_alive() for proc in workers)
        assert len(terminated) >= 1
        v2._close_mp_queues(batch_queue)

    @pytest.mark.slow
    @pytest.mark.parametrize("num_workers", [64, 230])
    def test_pipelined_high_worker_count_exits_after_drain(self, num_workers):
        v2 = _load_v2_module()
        ctx = mp.get_context("spawn")
        batch_queue = ctx.Queue()
        result_queue = ctx.Queue()
        batch_count = min(num_workers * 2, 500)
        batches = [(idx, []) for idx in range(batch_count)]
        worker_args = {
            "repo_root": str(_REPO),
            "screening_scope": "paid-compute",
            "events_csv_hash": "eh",
            "lake_manifest_hash": "lh",
            "scratch_root": str(_REPO / "runtime" / "paid_screen_scratch" / "orchestrator_scale_test"),
        }
        workers = [
            ctx.Process(
                target=_echo_fast_fail_worker,
                args=({}, batch_queue, result_queue),
            )
            for _ in range(num_workers)
        ]
        for proc in workers:
            proc.start()

        t0 = time.monotonic()
        collected, stop_reason = v2._pipelined_dispatch_and_drain(
            workers,
            batch_queue,
            result_queue,
            batches=batches,
            expected_batches=batch_count,
            timeout_per_batch=30.0,
            inflight_limit=v2._inflight_batch_limit(num_workers),
            spawn_ctx=ctx,
            spawn_worker_args=worker_args,
            target_worker_count=num_workers,
        )
        elapsed = time.monotonic() - t0

        assert stop_reason is None
        assert len(collected) == batch_count
        assert elapsed < v2._POST_DRAIN_EXIT_BUDGET_SECONDS
        assert all(not proc.is_alive() for proc in workers)


class TestWorkerScratchPath:
    def test_build_worker_run_budget_omits_zero_wall_clock(self):
        v2 = _load_v2_module()
        assert v2._build_worker_run_budget(0) == {}
        assert v2._build_worker_run_budget(-1) == {}
        assert v2._build_worker_run_budget(42) == {"max_wall_clock_seconds": 42}
        assert v2._build_worker_run_budget(
            42,
            max_trials=256,
            max_total_trials=256,
            max_models=1,
            max_symbols=1,
            max_feature_sets=1,
        ) == {
            "max_trials": 256,
            "max_total_trials": 256,
            "max_models": 1,
            "max_symbols": 1,
            "max_feature_sets": 1,
            "max_wall_clock_seconds": 42,
        }

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
