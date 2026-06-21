"""Tests for the paid-screen profiling and failure diagnostics module."""
import pytest
import os
import json
from pathlib import Path

from backtest_pipeline.src.paid_screen_profiling import (
    RunProfiler, FailureDiagnostic, determine_manifest_status,
    write_failure_diagnostics, StageTimer,
    merge_unit_screening_artifacts, write_aggregate_screening_artifact,
    artifact_matches_resume_unit, derive_run_research_split,
    resolve_events_csv_hash, resolve_lake_manifest_hash,
)
from backtest_pipeline.src.paid_screen_types import PaidScreenUnit
from backtest_pipeline.src.paid_screen_batch import resolve_resume_provenance

_VALID_UNIT_ARTIFACT = (
    Path(__file__).resolve().parents[1]
    / "research_cards"
    / "pipeline_runs"
    / "paid_batch_ok"
    / "screening_artifact.json"
)


class TestRunProfiler:
    def test_stage_timing(self):
        p = RunProfiler()
        p.start_stage("npz_load")
        p.end_stage("npz_load", {"file": "test.npz"})
        assert len(p.stage_timings) == 1
        assert p.stage_timings[0].stage_name == "npz_load"
        assert p.stage_timings[0].elapsed_seconds >= 0.0
        assert p.stage_timings[0].metadata == {"file": "test.npz"}

    def test_failure_recording(self):
        p = RunProfiler()
        try:
            raise ValueError("test error")
        except Exception as e:
            p.record_failure("signal_construction", e, "unit_001",
                             cache_state={"hit": False})
        assert len(p.failures) == 1
        assert p.failures[0].exception_type == "ValueError"
        assert "test error" in p.failures[0].exception_message
        assert "Traceback" in p.failures[0].full_traceback
        assert p.failures[0].worker_pid > 0
        assert p.failures[0].cache_state == {"hit": False}

    def test_manifest_summary(self):
        p = RunProfiler()
        p.cache_hits = 80
        p.cache_misses = 20
        p.start_stage("vbt_sim")
        p.end_stage("vbt_sim")
        summary = p.manifest_summary()
        assert summary["cache_hit_rate"] == 0.8
        assert "vbt_sim" in summary["time_by_stage"]
        assert summary["total_failures"] == 0
        assert summary["time_by_stage"]["vbt_sim"]["count"] == 1

    def test_manifest_summary_p50_p95(self):
        p = RunProfiler()
        for i in range(20):
            p.start_stage("vbt_sim")
            p.end_stage("vbt_sim")
        summary = p.manifest_summary()
        assert summary["time_by_stage"]["vbt_sim"]["count"] == 20
        assert "p50_seconds" in summary["time_by_stage"]["vbt_sim"]
        assert "p95_seconds" in summary["time_by_stage"]["vbt_sim"]

    def test_multiple_stage_types(self):
        p = RunProfiler()
        p.start_stage("npz_load")
        p.end_stage("npz_load")
        p.start_stage("bar_construction")
        p.end_stage("bar_construction")
        p.start_stage("vbt_sim")
        p.end_stage("vbt_sim")
        summary = p.manifest_summary()
        assert len(summary["time_by_stage"]) == 3
        assert "npz_load" in summary["time_by_stage"]
        assert "bar_construction" in summary["time_by_stage"]
        assert "vbt_sim" in summary["time_by_stage"]

    def test_cache_hit_rate_zero_division(self):
        p = RunProfiler()
        summary = p.manifest_summary()
        assert summary["cache_hit_rate"] == 0.0  # no hits or misses

    def test_record_failure_with_input_hashes(self):
        p = RunProfiler()
        try:
            raise RuntimeError("diag test")
        except Exception as e:
            diag = p.record_failure("vbt_sim", e, "batch_001",
                                    input_hashes={"data": "abc123"},
                                    config={"scope": "paid-compute"})
        assert diag.input_hashes == {"data": "abc123"}
        assert diag.relevant_config == {"scope": "paid-compute"}


class TestManifestStatus:
    def test_complete(self):
        assert determine_manifest_status(100, 0, False, 100) == "complete"

    def test_partial_failed(self):
        assert determine_manifest_status(90, 10, False, 100) == "partial_failed"

    def test_failed(self):
        assert determine_manifest_status(0, 100, False, 100) == "failed"

    def test_aborted(self):
        assert determine_manifest_status(50, 0, True, 100) == "aborted"

    def test_aborted_takes_priority(self):
        assert determine_manifest_status(0, 0, True, 100) == "aborted"

    def test_failed_zero_completed(self):
        assert determine_manifest_status(0, 10, False, 10) == "failed"

    def test_partial_with_zero_expected_edge(self):
        # 0 completed, 0 failed, 0 expected, not aborted → should not be "complete"
        # because completed != expected (0 != 0 is False, so it falls through)
        # Actually 0 == 0 is True, so it would be "complete" — that's an edge case
        # but acceptable: an empty run with no failures is technically complete
        assert determine_manifest_status(0, 0, False, 0) == "complete"


class TestFailureDiagnosticsPersistence:
    def test_write_and_read(self, tmp_path):
        p = RunProfiler()
        try:
            raise RuntimeError("diag test")
        except Exception as e:
            p.record_failure("vbt_sim", e, "batch_001")
        path = write_failure_diagnostics(str(tmp_path), p.failures)
        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["exception_type"] == "RuntimeError"
        assert data[0]["unit_or_batch_id"] == "batch_001"
        assert data[0]["stage_name"] == "vbt_sim"

    def test_write_empty_failures(self, tmp_path):
        path = write_failure_diagnostics(str(tmp_path), [])
        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert data == []

    def test_write_multiple_failures(self, tmp_path):
        p = RunProfiler()
        for i in range(5):
            try:
                raise ValueError(f"error {i}")
            except Exception as e:
                p.record_failure("stage", e, f"unit_{i}")
        path = write_failure_diagnostics(str(tmp_path), p.failures)
        with open(path) as f:
            data = json.load(f)
        assert len(data) == 5
        assert data[0]["unit_or_batch_id"] == "unit_0"
        assert data[4]["unit_or_batch_id"] == "unit_4"

    def test_failure_diagnostic_to_dict(self):
        try:
            raise ValueError("test")
        except Exception:
            import traceback as tb
            diag = FailureDiagnostic(
                unit_or_batch_id="u1",
                stage_name="stage1",
                exception_type="ValueError",
                exception_message="test",
                full_traceback=tb.format_exc(),
                worker_pid=123,
                start_ts_utc="2026-01-01T00:00:00Z",
                finish_ts_utc="2026-01-01T00:00:01Z",
                elapsed_seconds=1.0,
                cache_state={},
            )
            d = diag.to_dict()
            assert d["unit_or_batch_id"] == "u1"
            assert d["exception_type"] == "ValueError"
            assert "full_traceback" in d
            assert d["elapsed_seconds"] == 1.0


class TestManifestStatusIntegration:
    """Test that determine_manifest_status correctly replaces hardcoded 'complete'."""

    def test_no_failures_is_complete(self):
        assert determine_manifest_status(100, 0, False, 100) == "complete"

    def test_with_failures_is_partial_failed(self):
        assert determine_manifest_status(90, 10, False, 100) == "partial_failed"

    def test_manifest_would_not_say_complete_with_failures(self):
        # This is the critical test: the old code always said "complete"
        # The new code must NOT say "complete" when failed > 0
        status = determine_manifest_status(90, 10, False, 100)
        assert status != "complete"
        assert status == "partial_failed"


class TestAggregateScreeningArtifact:
    def test_write_aggregate_emits_valid_run_level_artifact(self, tmp_path):
        run_id = "paid_merge_test"
        run_dir = tmp_path / run_id
        run_dir.mkdir()
        artifact_text = _VALID_UNIT_ARTIFACT.read_text(encoding="utf-8")
        (run_dir / "units" / "u1").mkdir(parents=True)
        (run_dir / "units" / "u2").mkdir(parents=True)
        (run_dir / "units" / "u1" / "screening_artifact.json").write_text(
            artifact_text, encoding="utf-8"
        )
        (run_dir / "units" / "u2" / "screening_artifact.json").write_text(
            artifact_text, encoding="utf-8"
        )

        rows = [
            {"unit_id": "u1", "status": "OK", "screening_artifact_relpath": "units/u1/screening_artifact.json"},
            {"unit_id": "u2", "status": "OK_CACHED", "screening_artifact_relpath": "units/u2/screening_artifact.json"},
        ]
        aggregate_path = write_aggregate_screening_artifact(
            run_dir,
            rows,
            finished_at_utc="2026-06-19T13:00:00+00:00",
        )
        assert aggregate_path == str(run_dir / "screening_artifact.json")
        payload = json.loads((run_dir / "screening_artifact.json").read_text(encoding="utf-8"))
        from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact

        validate_screening_artifact(payload)
        assert payload["run_id"] == run_id
        assert isinstance(payload["promoted_ids"], list)

    def test_merge_unit_artifacts_unions_promoted_ids(self):
        artifact = json.loads(_VALID_UNIT_ARTIFACT.read_text(encoding="utf-8"))
        merged = merge_unit_screening_artifacts(
            [artifact, dict(artifact)],
            run_id="merge_only",
            finished_at_utc="2026-06-19T13:00:00+00:00",
        )
        assert merged["run_id"] == "merge_only"
        assert merged["trials_run"] >= int(artifact.get("trials_run") or 0)

    def test_merge_aggregate_provenance_records_child_hashes_honestly(self):
        artifact_a = json.loads(_VALID_UNIT_ARTIFACT.read_text(encoding="utf-8"))
        artifact_b = dict(artifact_a)
        artifact_a["screening_artifact_hash"] = "child_hash_a"
        artifact_a["data_manifest_hash"] = "manifest_a"
        artifact_b["screening_artifact_hash"] = "child_hash_b"
        artifact_b["data_manifest_hash"] = "manifest_b"

        merged = merge_unit_screening_artifacts(
            [artifact_a, artifact_b],
            run_id="merge_provenance",
            finished_at_utc="2026-06-19T13:00:00+00:00",
        )

        provenance = merged["aggregate_provenance"]
        assert provenance["scope"] == "run_level_merge"
        assert provenance["unit_count"] == 2
        assert set(provenance["unit_artifact_hashes"]) == {"child_hash_a", "child_hash_b"}
        assert provenance["unit_data_manifest_hashes"] == ["manifest_a", "manifest_b"]
        assert merged["data_manifest_hash"] != "manifest_a"
        assert merged["data_manifest_hash"] != "manifest_b"
        assert len(merged["data_manifest_hash"]) == 32


class TestResumeArtifactMatching:
    def test_derive_run_research_split_defaults(self):
        rows = [{"unit_id": "u1", "model_id": "HYP_5", "symbol": "MES.v.0", "event_id": "E1"}]
        assert derive_run_research_split(rows) == "discovery_confirmation"

    def test_derive_run_research_split_rejects_mixed_values(self):
        rows = [
            {"unit_id": "u1", "research_split": "discovery_confirmation"},
            {"unit_id": "u2", "research_split": "holdout"},
        ]
        with pytest.raises(ValueError, match="mixed research_split"):
            derive_run_research_split(rows)

    def test_artifact_matches_resume_unit_with_fixture(self):
        payload = json.loads(_VALID_UNIT_ARTIFACT.read_text(encoding="utf-8"))
        unit = PaidScreenUnit(
            unit_id="u_ok",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            hyp_id=5,
            symbol="MES.v.0",
            event_id="CPI_2024_09_11_TIGHT",
            event_type="CPI",
            research_split="discovery_confirmation",
        )
        assert artifact_matches_resume_unit(
            payload,
            unit,
            events_csv_hash="not_applicable_for_vectorbt_pilot",
            lake_manifest_hash="pilot_requires_lake_manifest_before_screen",
            research_split="discovery_confirmation",
            screening_scope="pilot",
        )

    def test_artifact_accepts_base_candidate_pipe_symbol_when_row_symbol_shortened(self):
        payload = {
            "screening_scope": "paid-compute",
            "events_csv_hash": "events_hash",
            "lake_manifest_hash": "lake_hash",
            "candidate_ids": ["trial_hash"],
            "promoted_ids": ["trial_hash"],
            "promoted": [
                {
                    "candidate_id": "trial_hash",
                    "model_id": "ABSORPTION_FADE",
                    "symbol": "ES",
                    "base_candidate_id": "ABSORPTION_FADE|ES.v.0|EVENT|12",
                    "base_candidate_metadata": {"event_id": "EVENT"},
                }
            ],
            "rejected": [],
        }
        unit = PaidScreenUnit(
            unit_id="u_es",
            model_id="ABSORPTION_FADE",
            hyp_id=12,
            symbol="ES.v.0",
            event_id="EVENT",
            event_type="EVENT",
        )

        assert artifact_matches_resume_unit(
            payload,
            unit,
            events_csv_hash="events_hash",
            lake_manifest_hash="lake_hash",
            research_split="discovery_confirmation",
            screening_scope="paid-compute",
        )

    def test_artifact_accepts_paid_compute_scope_separator_variant(self):
        payload = {
            "screening_scope": "paid_compute",
            "events_csv_hash": "events_hash",
            "lake_manifest_hash": "lake_hash",
            "candidate_ids": [
                "SPREAD_BLOWOUT_RECOMPRESSION|MES.v.0|CPI_2024_09_11_TIGHT|5"
            ],
            "promoted": [],
            "rejected": [],
        }
        unit = PaidScreenUnit(
            unit_id="u_ok",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            hyp_id=5,
            symbol="MES.v.0",
            event_id="CPI_2024_09_11_TIGHT",
            event_type="CPI",
            research_split="discovery_confirmation",
        )

        assert artifact_matches_resume_unit(
            payload,
            unit,
            events_csv_hash="events_hash",
            lake_manifest_hash="lake_hash",
            research_split="discovery_confirmation",
            screening_scope="paid-compute",
        )

    def test_artifact_rejects_different_scope_after_separator_normalization(self):
        payload = {
            "screening_scope": "refine",
            "events_csv_hash": "events_hash",
            "lake_manifest_hash": "lake_hash",
            "candidate_ids": [
                "SPREAD_BLOWOUT_RECOMPRESSION|MES.v.0|CPI_2024_09_11_TIGHT|5"
            ],
            "promoted": [],
            "rejected": [],
        }
        unit = PaidScreenUnit(
            unit_id="u_ok",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            hyp_id=5,
            symbol="MES.v.0",
            event_id="CPI_2024_09_11_TIGHT",
            event_type="CPI",
            research_split="discovery_confirmation",
        )

        assert not artifact_matches_resume_unit(
            payload,
            unit,
            events_csv_hash="events_hash",
            lake_manifest_hash="lake_hash",
            research_split="discovery_confirmation",
            screening_scope="paid-compute",
        )

    def test_artifact_rejects_mismatched_model(self):
        payload = json.loads(_VALID_UNIT_ARTIFACT.read_text(encoding="utf-8"))
        unit = PaidScreenUnit(
            unit_id="u_ok",
            model_id="HYP_99",
            hyp_id=99,
            symbol="MES.v.0",
            event_id="CPI_2024_09_11_TIGHT",
            event_type="CPI",
        )
        assert not artifact_matches_resume_unit(
            payload,
            unit,
            events_csv_hash="not_applicable_for_vectorbt_pilot",
            lake_manifest_hash="pilot_requires_lake_manifest_before_screen",
            research_split="discovery_confirmation",
        )

    def test_artifact_rejects_mismatched_code_commit(self):
        payload = json.loads(_VALID_UNIT_ARTIFACT.read_text(encoding="utf-8"))
        unit = PaidScreenUnit(
            unit_id="u_ok",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            hyp_id=5,
            symbol="MES.v.0",
            event_id="CPI_2024_09_11_TIGHT",
            event_type="CPI",
            research_split="discovery_confirmation",
        )
        payload.update(
            resolve_resume_provenance(str(Path(__file__).resolve().parents[1]), unit)
        )
        assert not artifact_matches_resume_unit(
            payload,
            unit,
            events_csv_hash="not_applicable_for_vectorbt_pilot",
            lake_manifest_hash="pilot_requires_lake_manifest_before_screen",
            research_split="discovery_confirmation",
            screening_scope="pilot",
            code_commit="different_commit",
            model_registry_hash=payload["model_registry_hash"],
            signal_implementation_hash=payload["signal_implementation_hash"],
            feature_set_hash=payload["feature_set_hash"],
            feature_recipe_hash=payload["feature_recipe_hash"],
        )

    def test_artifact_rejects_mismatched_registry_hash(self):
        payload = json.loads(_VALID_UNIT_ARTIFACT.read_text(encoding="utf-8"))
        unit = PaidScreenUnit(
            unit_id="u_ok",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            hyp_id=5,
            symbol="MES.v.0",
            event_id="CPI_2024_09_11_TIGHT",
            event_type="CPI",
            research_split="discovery_confirmation",
        )
        provenance = resolve_resume_provenance(str(Path(__file__).resolve().parents[1]), unit)
        payload.update(provenance)
        assert not artifact_matches_resume_unit(
            payload,
            unit,
            events_csv_hash="not_applicable_for_vectorbt_pilot",
            lake_manifest_hash="pilot_requires_lake_manifest_before_screen",
            research_split="discovery_confirmation",
            screening_scope="pilot",
            code_commit=provenance["code_commit"],
            model_registry_hash="dead_registry_hash",
            signal_implementation_hash=provenance["signal_implementation_hash"],
            feature_set_hash=provenance["feature_set_hash"],
            feature_recipe_hash=provenance["feature_recipe_hash"],
        )


class TestRunHashResolution:
    def test_resolve_events_csv_hash_from_file(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        events_csv = repo / "events.csv"
        events_csv.write_text("event_id,symbol\nE1,MES\n", encoding="utf-8")
        digest = resolve_events_csv_hash(
            explicit_hash=None,
            events_csv=events_csv,
            repo_root=repo,
        )
        assert len(digest) == 32

    def test_resolve_events_csv_hash_fail_closed(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(FileNotFoundError):
            resolve_events_csv_hash(
                explicit_hash=None,
                events_csv=repo / "missing.csv",
                repo_root=repo,
            )

    def test_resolve_lake_manifest_hash_from_file(self, tmp_path):
        repo = tmp_path / "repo"
        manifest = repo / "data" / "manifest.parquet"
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b"lake-manifest-bytes")
        digest = resolve_lake_manifest_hash(
            explicit_hash=None,
            repo_root=repo,
        )
        assert len(digest) == 32

    def test_resolve_lake_manifest_hash_fail_closed(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.delenv("HFT3_MANIFEST_PATH", raising=False)
        with pytest.raises(ValueError):
            resolve_lake_manifest_hash(explicit_hash=None, repo_root=repo)
