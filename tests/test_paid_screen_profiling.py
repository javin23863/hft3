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
from backtest_pipeline.src.promotion_gate import PromotedCandidate, RejectedCandidate
from backtest_pipeline.src.vectorbt_adapter import (
    FilterResult,
    compute_screening_artifact_hash,
    validate_screening_artifact,
)


def _tiny_screening_artifact(
    *,
    run_id: str,
    promoted_ids: list[str] | None = None,
    rejected_ids: list[str] | None = None,
    model_id: str = "SPREAD_BLOWOUT_RECOMPRESSION",
    symbol: str = "ES.v.0",
    event_id: str = "CPI_2024_09_11_TIGHT",
    event_type: str = "CPI",
    events_csv_hash: str = "not_applicable_for_vectorbt_pilot",
    lake_manifest_hash: str = "pilot_requires_lake_manifest_before_screen",
) -> dict:
    """Build a small valid artifact through production screening serialization."""
    promoted_ids = promoted_ids or []
    rejected_ids = rejected_ids or []
    base_metadata = {
        "model_id": model_id,
        "symbol": symbol,
        "event_id": event_id,
        "event_type": event_type,
    }
    result = FilterResult(
        promoted=[
            PromotedCandidate(
                candidate_id=candidate_id,
                hypothesis_id=model_id,
                strategy_family=model_id,
                asset_class="CME_FUTURES",
                symbol=symbol,
                timeframe="event_window",
                param_values={"signal_threshold": 0.15, "holding_period_bars": 15},
                vectorbt_run_id=run_id,
                vectorbt_results={
                    "base_candidate_metadata": base_metadata,
                    "opportunity_type_or_event_type": event_type,
                    "oos_expectancy": 1.0,
                    "wf_consistency": 1.0,
                    "max_drawdown_pct": -1.0,
                    "turnover_mean_pct": 1.0,
                    "num_trades": 12,
                    "param_stability_score": 1.0,
                    "slippage_sensitivity": 0.0,
                    "net_return": 0.01,
                    "net_pnl": 10.0,
                    "profit_factor": 1.5,
                    "sharpe": 1.0,
                    "sortino": 1.0,
                },
                pass_reason="vectorbt_screen_passed_replay_not_eligible",
            )
            for candidate_id in promoted_ids
        ],
        rejected=[
            RejectedCandidate(
                candidate_id=candidate_id,
                hypothesis_id=model_id,
                reject_reason="promotion_gate_failed",
                metric_values={
                    "symbol": symbol,
                    "base_candidate_metadata": base_metadata,
                    "opportunity_type_or_event_type": event_type,
                    "parameter_values": {"signal_threshold": 0.15, "holding_period_bars": 15},
                },
            )
            for candidate_id in rejected_ids
        ],
        run_id=run_id,
        total_candidates=len(promoted_ids) + len(rejected_ids),
        code_commit="test_commit",
        vectorbt_available=True,
        backend="python",
        vectorbt_version="test",
        vectorbt_engine="numba",
        engine_parity_status="pilot_python_engine_allowed",
        rust_engine_required_for_scope=False,
        rust_engine_available=False,
        vectorbt_engine_runtime_proof=False,
        license_review="unit_test",
        parameter_space_id="unit_test_parameter_space",
        parameter_space_hash="unit_test_parameter_space_hash",
        max_trials=max(1, len(promoted_ids) + len(rejected_ids)),
        trials_run=len(promoted_ids) + len(rejected_ids),
        run_budget_id="unit_test_budget",
        max_models=1,
        max_symbols=1,
        max_feature_sets=1,
        max_total_trials=max(1, len(promoted_ids) + len(rejected_ids)),
        abort_on_budget_exhaustion=True,
        screening_scope="pilot",
        feature_set_id="fs_v1",
        feature_set_hash="unit_test_feature_set_hash",
        data_manifest_hash="unit_test_data_manifest_hash",
        lake_manifest_hash=lake_manifest_hash,
        events_csv_hash_or_not_applicable=events_csv_hash,
        fees_model_id="unit_test_fees",
        slippage_model_id="unit_test_slippage",
        bar_construction_id="fs_v1_row_loop_from_feature_store",
        target_event_type_or_null=event_type,
    )
    artifact = result.to_dict()
    validate_screening_artifact(artifact)
    return artifact


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
        artifact_u1 = _tiny_screening_artifact(run_id="u1", rejected_ids=["u1_rejected"])
        artifact_u2 = _tiny_screening_artifact(run_id="u2", promoted_ids=["u2_promoted"])
        (run_dir / "units" / "u1").mkdir(parents=True)
        (run_dir / "units" / "u2").mkdir(parents=True)
        (run_dir / "units" / "u1" / "screening_artifact.json").write_text(
            json.dumps(artifact_u1), encoding="utf-8"
        )
        (run_dir / "units" / "u2" / "screening_artifact.json").write_text(
            json.dumps(artifact_u2), encoding="utf-8"
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

        validate_screening_artifact(payload)
        assert payload["run_id"] == run_id
        assert isinstance(payload["promoted_ids"], list)

    def test_merge_unit_artifacts_unions_promoted_ids(self):
        artifact = _tiny_screening_artifact(
            run_id="unit_fixture",
            promoted_ids=["shared_promoted"],
            rejected_ids=["shared_rejected"],
        )
        merged = merge_unit_screening_artifacts(
            [artifact, dict(artifact)],
            run_id="merge_only",
            finished_at_utc="2026-06-19T13:00:00+00:00",
        )
        assert merged["run_id"] == "merge_only"
        assert merged["trials_run"] >= int(artifact.get("trials_run") or 0)
        assert merged["promoted_ids"] == ["shared_promoted"]

    def test_merge_derives_candidate_ids_from_emitted_rows(self):
        rejected_only = _tiny_screening_artifact(
            run_id="u_rejected",
            rejected_ids=["first_unit_rejected"],
        )
        promoted_later = _tiny_screening_artifact(
            run_id="u_promoted",
            promoted_ids=["later_unit_promoted"],
            rejected_ids=["later_unit_rejected"],
        )

        merged = merge_unit_screening_artifacts(
            [rejected_only, promoted_later],
            run_id="merge_order_regression",
            finished_at_utc="2026-06-19T13:00:00+00:00",
        )
        emitted_row_ids = [
            str(row["candidate_id"])
            for row in merged["promoted"] + merged["rejected"]
        ]

        assert merged["promoted_ids"] == ["later_unit_promoted"]
        assert merged["rejected_ids"] == ["first_unit_rejected", "later_unit_rejected"]
        assert merged["candidate_ids"] == emitted_row_ids
        merged["screening_artifact_hash"] = compute_screening_artifact_hash(merged)
        validate_screening_artifact(merged)

    def test_merge_aggregate_provenance_records_child_hashes_honestly(self):
        artifact_a = _tiny_screening_artifact(run_id="a", rejected_ids=["a_rejected"])
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
        payload = _tiny_screening_artifact(
            run_id="u_ok",
            rejected_ids=["u_ok_rejected"],
            symbol="MES.v.0",
        )
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

    def test_artifact_rejects_mismatched_model(self):
        payload = _tiny_screening_artifact(
            run_id="u_ok",
            rejected_ids=["u_ok_rejected"],
            symbol="MES.v.0",
        )
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
        payload = _tiny_screening_artifact(
            run_id="u_ok",
            rejected_ids=["u_ok_rejected"],
            symbol="MES.v.0",
        )
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
        payload = _tiny_screening_artifact(
            run_id="u_ok",
            rejected_ids=["u_ok_rejected"],
            symbol="MES.v.0",
        )
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
