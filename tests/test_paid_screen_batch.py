"""Tests for the paid-screen batch entry point."""
import json
import os
from pathlib import Path

import numpy as np
import pytest
from backtest_pipeline.src.paid_screen_types import (
    PaidScreenUnit, WorkerContext, UnitScreeningResult,
)
from backtest_pipeline.src.paid_screen_batch import (
    screen_paid_batch, resolve_model_from_registry, build_batching_key,
    group_units_by_batch_key, batching_key_for_unit, _build_candidate_model,
    build_structured_parsed_hypothesis, _resolve_signal_implementation_hash,
    _signal_implementation_hash_paths, resolve_batching_hashes, ohlcv_data_cache_key,
    _worker_scratch_artifact_dir, _write_screening_artifact, resolve_resume_provenance,
)
from backtest_pipeline.src.paid_screen_profiling import RunProfiler, artifact_matches_resume_unit, DEFAULT_RESEARCH_SPLIT
from backtest_pipeline.src.vectorbt_adapter import FilterResult, validate_screening_artifact


def make_context(**overrides) -> WorkerContext:
    defaults = dict(
        repo_root=".", git_commit="abc123", screening_scope="pilot",
        vectorbt_engine="numba", vectorbt_version="0.0.0",
        rust_runtime_proof=False, events_csv_hash="eh",
        lake_manifest_hash="lh",
    )
    defaults.update(overrides)
    return WorkerContext(**defaults)


def make_unit(**overrides) -> PaidScreenUnit:
    defaults = dict(
        unit_id="u1", model_id="HYP_5", hyp_id=5,
        symbol="MES.v.0", event_id="CPI_2024_09_11_TIGHT",
        event_type="CPI", thesis="test",
    )
    defaults.update(overrides)
    return PaidScreenUnit(**defaults)


def _batch_cache_key(ctx, unit=None, **unit_overrides) -> str:
    """Reproduce the symbol-aware cache key used by screen_paid_batch."""
    u = make_unit(**unit_overrides) if unit is None else unit
    return ohlcv_data_cache_key(u, ctx)


def _valid_filter_result(unit: PaidScreenUnit | None = None, **overrides) -> FilterResult:
    from backtest_pipeline.src.promotion_gate import RejectedCandidate

    u = unit or make_unit(model_id="SPREAD_BLOWOUT_RECOMPRESSION")
    rejected = RejectedCandidate(
        candidate_id="test_reject_row",
        hypothesis_id=u.model_id,
        reject_reason="vectorbt_unavailable_fail_closed",
        metric_values={
            "symbol": u.symbol,
            "base_candidate_metadata": {"event_id": u.event_id},
            "base_candidate_id": f"{u.model_id}|{u.symbol}|{u.event_id}|{u.hyp_id}",
        },
    )
    defaults = dict(
        backend="vectorbt_unavailable",
        run_id="provenance_test",
        screening_scope="pilot",
        code_commit="abc123",
        parameter_space_id="ps_test",
        parameter_space_hash="ps_hash_test",
        max_trials=1,
        trials_run=0,
        max_total_trials=1,
        max_models=1,
        max_symbols=1,
        max_feature_sets=1,
        rejected=[rejected],
    )
    defaults.update(overrides)
    return FilterResult(**defaults)


class TestScreenPaidBatch:
    def test_empty_batch_returns_empty(self):
        ctx = make_context()
        results = screen_paid_batch([], ctx)
        assert results == []

    def test_no_data_fails_all_units(self):
        """When NPZ data is not found, all units get ERROR with no_ohlcv_data."""
        ctx = make_context(repo_root="/nonexistent")
        units = [
            make_unit(unit_id="u1", event_id="__TEST_NO_NPZ_UNIT__"),
            make_unit(unit_id="u2", event_id="__TEST_NO_NPZ_UNIT__"),
        ]
        results = screen_paid_batch(units, ctx)
        assert len(results) == 2
        for r in results:
            assert r.status == "ERROR"
            assert "no_ohlcv_data" in r.error

    def test_per_unit_result_returned(self):
        """Each unit gets its own UnitScreeningResult."""
        ctx = make_context()
        # Mock the data loader by pre-populating the cache
        import numpy as np
        data_cache = {_batch_cache_key(ctx): np.array([[1, 2, 3, 4, 5]])}
        units = [make_unit(unit_id="u1"), make_unit(unit_id="u2", model_id="HYP_6")]
        results = screen_paid_batch(units, ctx, data_cache=data_cache, run_screening=False)
        assert len(results) == 2
        assert results[0].unit_id == "u1"
        assert results[1].unit_id == "u2"

    def test_profiler_records_cache_hit(self):
        """When data is in cache, profiler records a cache hit."""
        ctx = make_context()
        import numpy as np
        data_cache = {_batch_cache_key(ctx): np.array([[1, 2, 3, 4, 5]])}
        profiler = RunProfiler()
        units = [make_unit()]
        screen_paid_batch(units, ctx, profiler=profiler, data_cache=data_cache, run_screening=False)
        assert profiler.cache_hits == 1
        assert profiler.cache_misses == 0

    def test_profiler_records_cache_miss(self):
        """When data is not in cache, profiler records a cache miss."""
        ctx = make_context(repo_root="/nonexistent")
        profiler = RunProfiler()
        units = [make_unit(event_id="__TEST_NO_NPZ_UNIT__")]
        screen_paid_batch(units, ctx, profiler=profiler, data_cache={}, run_screening=False)
        assert profiler.cache_misses >= 1

    def test_unit_failure_recorded_in_profiler(self):
        """When a unit fails, the failure is recorded in the profiler."""
        ctx = make_context(repo_root="/nonexistent")
        profiler = RunProfiler()
        units = [make_unit(event_id="__TEST_NO_NPZ_UNIT__")]
        screen_paid_batch(units, ctx, profiler=profiler, run_screening=False)
        assert len(profiler.failures) >= 1

    def test_thesis_not_used_for_resolution(self):
        """The thesis field is NOT used to determine model_id."""
        u1 = make_unit(thesis="alpha strategy")
        u2 = make_unit(thesis="beta strategy")
        # Both have the same model_id, so they should resolve to the same model
        assert u1.model_id == u2.model_id

    def test_model_resolution_uses_model_id(self):
        """resolve_model_from_registry uses model_id, not thesis."""
        model = resolve_model_from_registry("HYP_5", ".")
        assert model is not None
        # The returned dict should have model_id or slug
        assert "model_id" in model or "slug" in model

    def test_build_candidate_model_attaches_feature_recipe_hash(self):
        unit = make_unit()
        model_entry = {"model_id": "HYP_5", "hyp_id": 5}
        parsed = build_structured_parsed_hypothesis(unit, model_entry)
        candidate = _build_candidate_model(unit, model_entry, Path("."), parsed)
        assert candidate.feature_recipe_hash
        assert candidate.feature_recipe
        assert candidate.metadata.get("feature_recipe_hash") == candidate.feature_recipe_hash

    def test_thesis_text_does_not_change_structured_execution(self):
        model_entry = {"model_id": "HYP_5", "hyp_id": 5}
        unit_alpha = make_unit(thesis="alpha prose completely different model words")
        unit_beta = make_unit(thesis="beta other thesis should not matter")
        parsed_alpha = build_structured_parsed_hypothesis(unit_alpha, model_entry)
        parsed_beta = build_structured_parsed_hypothesis(unit_beta, model_entry)
        cand_alpha = _build_candidate_model(unit_alpha, model_entry, Path("."), parsed_alpha)
        cand_beta = _build_candidate_model(unit_beta, model_entry, Path("."), parsed_beta)
        assert parsed_alpha.primary_model_id == parsed_beta.primary_model_id == "HYP_5"
        assert parsed_alpha.feature_list == parsed_beta.feature_list == ["HYP_5"]
        assert cand_alpha.candidate_id == cand_beta.candidate_id
        assert cand_alpha.feature_recipe_hash == cand_beta.feature_recipe_hash
        assert cand_alpha.model_id == cand_beta.model_id == "HYP_5"

    def test_screen_paid_batch_forwards_run_budget_to_matrix(self, monkeypatch):
        import numpy as np
        from types import SimpleNamespace
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache
        from backtest_pipeline.src.vectorbt_adapter import FilterResult

        captured: dict = {}

        def _fake_matrix(**kwargs):
            captured["run_budget"] = kwargs.get("run_budget")
            return FilterResult(backend="vectorbt", run_id="run_test")

        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.run_vectorbt_simulation_matrix",
            _fake_matrix,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.apply_promotion_gates",
            lambda result, **kwargs: result,
        )

        ctx = make_context(run_budget={"max_wall_clock_seconds": 42})
        cache = BoundedLRUCache(max_entries=4, max_memory_mb=64)
        cache_key = _batch_cache_key(ctx)
        cache.put(cache_key, np.array([[1, 2, 3, 4, 5, 1_700_000_000_000.0]]))
        units = [make_unit()]
        screen_paid_batch(units, ctx, data_cache=cache, run_screening=True)

        budget = captured.get("run_budget")
        assert budget is not None
        assert budget.max_wall_clock_seconds == 42

    def test_screen_paid_batch_owns_single_provenance_stamp(self, monkeypatch):
        import numpy as np
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache
        from backtest_pipeline.src.vectorbt_adapter import FilterResult

        calls: list[dict] = []

        def _fake_matrix(**kwargs):
            return FilterResult(backend="vectorbt", run_id="run_test")

        def _fake_stamp(*args, **kwargs):
            calls.append(dict(kwargs))

        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.run_vectorbt_simulation_matrix",
            _fake_matrix,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.apply_promotion_gates",
            lambda result, **kwargs: result,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch._apply_filter_result_provenance_metadata",
            _fake_stamp,
        )

        ctx = make_context(run_budget={})
        cache = BoundedLRUCache(max_entries=4, max_memory_mb=64)
        cache.put(_batch_cache_key(ctx), np.array([[1, 2, 3, 4, 5, 1_700_000_000_000.0]]))

        screen_paid_batch([make_unit()], ctx, data_cache=cache, run_screening=True)

        assert len(calls) == 1
        assert calls[0]["screening_scope"] == ctx.screening_scope

    def test_screen_paid_batch_default_run_budget_has_no_wall_clock_cap(self, monkeypatch):
        from types import SimpleNamespace
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache
        from backtest_pipeline.src.vectorbt_adapter import FilterResult

        captured: dict = {}

        def _fake_matrix(**kwargs):
            captured["run_budget"] = kwargs.get("run_budget")
            return FilterResult(backend="vectorbt", run_id="run_test")

        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.run_vectorbt_simulation_matrix",
            _fake_matrix,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.apply_promotion_gates",
            lambda result, **kwargs: result,
        )

        ctx = make_context(run_budget={})
        cache = BoundedLRUCache(max_entries=4, max_memory_mb=64)
        import numpy as np
        cache_key = _batch_cache_key(ctx)
        cache.put(cache_key, np.array([[1, 2, 3, 4, 5, 1_700_000_000_000.0]]))
        screen_paid_batch([make_unit()], ctx, data_cache=cache, run_screening=True)

        budget = captured.get("run_budget")
        assert budget is not None
        assert budget.max_wall_clock_seconds is None

    def test_worker_scratch_artifacts_avoid_pipeline_runs(self, monkeypatch, tmp_path):
        import numpy as np
        from types import SimpleNamespace
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache
        from backtest_pipeline.src.vectorbt_adapter import FilterResult

        written_paths: list[str] = []

        def fake_write(artifact_path, *args, **kwargs):
            written_paths.append(artifact_path)
            Path(artifact_path).parent.mkdir(parents=True, exist_ok=True)
            Path(artifact_path).write_text("{}", encoding="utf-8")
            return "hash"

        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch._write_screening_artifact",
            fake_write,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.run_vectorbt_simulation_matrix",
            lambda **kwargs: FilterResult(backend="vectorbt", run_id="scratch_test"),
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.apply_promotion_gates",
            lambda result, **kwargs: result,
        )

        ctx = make_context(repo_root=str(tmp_path))
        scratch = tmp_path / "orchestrator_run" / ".worker_scratch"
        cache = BoundedLRUCache(max_entries=4, max_memory_mb=64)
        cache_key = _batch_cache_key(ctx)
        cache.put(cache_key, np.array([[1, 2, 3, 4, 5, 1_700_000_000_000.0]]))
        units = [make_unit(unit_id="u1"), make_unit(unit_id="u2")]

        results = screen_paid_batch(
            units,
            ctx,
            data_cache=cache,
            run_screening=True,
            scratch_root=str(scratch),
        )

        assert len(results) == 2
        assert len(written_paths) == 2
        for artifact_path in written_paths:
            path = Path(artifact_path)
            assert "pipeline_runs" not in path.parts
            assert path.parent.parent == scratch
            assert path.name == "screening_artifact.json"
        for result in results:
            assert result.status == "OK"
            assert Path(result.screening_artifact_path).parent.parent == scratch

    def test_default_scratch_dir_under_runtime_not_pipeline_runs(self):
        repo = "/repo/root"
        scratch_dir = _worker_scratch_artifact_dir(repo, "u99")
        parts = Path(scratch_dir).parts
        assert "pipeline_runs" not in parts
        assert parts[-2:] == ("paid_screen_scratch", "u99")
        assert parts[-3] == "runtime"


class TestArtifactProvenanceStamping:
    def test_write_screening_artifact_stamps_context_hashes(self, tmp_path):
        ctx = make_context(
            repo_root=str(tmp_path),
            events_csv_hash="ctx_events_hash_abc",
            lake_manifest_hash="ctx_lake_hash_xyz",
        )
        unit = make_unit(model_id="SPREAD_BLOWOUT_RECOMPRESSION")
        ohlcv_hash = "deadbeef" * 4
        filter_result = _valid_filter_result(unit)
        artifact_path = tmp_path / "scratch" / unit.unit_id / "screening_artifact.json"

        model_entry = {"model_id": unit.model_id, "hyp_id": unit.hyp_id}
        parsed = build_structured_parsed_hypothesis(unit, model_entry)
        candidate = _build_candidate_model(unit, model_entry, tmp_path, parsed)

        _write_screening_artifact(
            str(artifact_path),
            filter_result,
            unit,
            model_entry,
            ctx,
            ohlcv_hash,
            RunProfiler(),
            candidate=candidate,
        )

        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        validate_screening_artifact(payload)
        assert payload["data_manifest_hash"] == ohlcv_hash
        assert payload["lake_manifest_hash"] == "ctx_lake_hash_xyz"
        assert payload["events_csv_hash_or_not_applicable"] == "ctx_events_hash_abc"
        provenance = resolve_resume_provenance(str(tmp_path), unit, git_commit=ctx.git_commit)
        assert artifact_matches_resume_unit(
            payload,
            unit,
            events_csv_hash="ctx_events_hash_abc",
            lake_manifest_hash="ctx_lake_hash_xyz",
            research_split=DEFAULT_RESEARCH_SPLIT,
            screening_scope="pilot",
            **provenance,
        )

    def test_write_screening_artifact_validates_json_primitive_payload(
        self, monkeypatch, tmp_path
    ):
        from backtest_pipeline.src.promotion_gate import PromotedCandidate
        from backtest_pipeline.src import vectorbt_adapter
        from backtest_pipeline.src.vectorbt_adapter import ScreeningArtifactError

        ctx = make_context(repo_root=str(tmp_path))
        unit = make_unit(model_id="SPREAD_BLOWOUT_RECOMPRESSION")
        prom = PromotedCandidate(
            candidate_id="nan_metric_row",
            hypothesis_id=unit.model_id,
            strategy_family=unit.model_id,
            asset_class="futures",
            symbol=unit.symbol,
            timeframe="1m",
            param_values={"signal_threshold": 0.15},
            vectorbt_run_id="json_roundtrip_guard",
            vectorbt_results={
                "gate_metric_authority": "official_vectorbt_portfolio_stats",
                "oos_expectancy": 1.0,
                "max_drawdown_pct": -1.0,
                "num_trades": 10,
                "profit_factor": 1.2,
                "sharpe": 0.5,
                "sortino": 0.6,
            },
            pass_reason="vectorbt_simulated",
        )
        filter_result = _valid_filter_result(
            unit,
            backend="vectorbt",
            run_id="json_roundtrip_guard",
            promoted=[prom],
            rejected=[],
            trials_run=1,
            max_total_trials=1,
            vectorbt_available=True,
            vectorbt_version="1.0.0",
        )
        artifact_path = tmp_path / "scratch" / unit.unit_id / "screening_artifact.json"
        model_entry = {"model_id": unit.model_id, "hyp_id": unit.hyp_id}
        parsed = build_structured_parsed_hypothesis(unit, model_entry)
        candidate = _build_candidate_model(unit, model_entry, tmp_path, parsed)
        original_primitive = vectorbt_adapter._json_primitive_screening_payload

        def primitive_with_null_profit_factor(value):
            payload = original_primitive(value)
            if isinstance(payload, dict) and payload.get("run_id") == "json_roundtrip_guard":
                payload["promoted"][0]["profit_factor"] = None
            return payload

        monkeypatch.setattr(
            vectorbt_adapter,
            "_json_primitive_screening_payload",
            primitive_with_null_profit_factor,
        )

        with pytest.raises(
            ScreeningArtifactError,
            match="empty candidate field: profit_factor",
        ):
            _write_screening_artifact(
                str(artifact_path),
                filter_result,
                unit,
                model_entry,
                ctx,
                "deadbeef" * 4,
                RunProfiler(),
                candidate=candidate,
            )
        assert not artifact_path.exists()

    def test_candidate_and_artifact_carry_unit_context_metadata(self, tmp_path):
        ctx = make_context(
            repo_root=str(tmp_path),
            events_csv_hash="ctx_events_hash_abc",
            lake_manifest_hash="ctx_lake_hash_xyz",
        )
        unit = make_unit(
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            research_clock="context_feature_uplift",
            context_set_id="target_plus_cross_asset",
            declared_context_sets=("target_only", "target_plus_cross_asset"),
        )
        ohlcv_hash = "deadbeef" * 4
        filter_result = _valid_filter_result(unit)
        artifact_path = tmp_path / "scratch" / unit.unit_id / "screening_artifact.json"

        model_entry = {"model_id": unit.model_id, "hyp_id": unit.hyp_id}
        parsed = build_structured_parsed_hypothesis(unit, model_entry)
        candidate = _build_candidate_model(unit, model_entry, tmp_path, parsed)

        assert candidate.research_clock == "context_feature_uplift"
        assert candidate.metadata["context_set_id"] == "target_plus_cross_asset"
        assert candidate.metadata["declared_context_sets"] == ["target_only", "target_plus_cross_asset"]

        artifact_hash = _write_screening_artifact(
            str(artifact_path),
            filter_result,
            unit,
            model_entry,
            ctx,
            ohlcv_hash,
            RunProfiler(),
            candidate=candidate,
        )

        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        validate_screening_artifact(payload)
        assert artifact_hash == payload["screening_artifact_hash"]
        assert payload["research_clock"] == "context_feature_uplift"
        assert payload["allowed_context_set_id_or_null"] == "target_plus_cross_asset"
        assert payload["declared_context_sets"] == ["target_only", "target_plus_cross_asset"]
        assert payload["context_ablation_status"] == "not_measured"
        assert artifact_matches_resume_unit(
            payload,
            unit,
            events_csv_hash="ctx_events_hash_abc",
            lake_manifest_hash="ctx_lake_hash_xyz",
            research_split=DEFAULT_RESEARCH_SPLIT,
            screening_scope="pilot",
            **resolve_resume_provenance(str(tmp_path), unit, git_commit=ctx.git_commit),
        )

    def test_resume_rejects_artifact_from_different_context(self, tmp_path):
        ctx = make_context(
            repo_root=str(tmp_path),
            events_csv_hash="ctx_events_hash_abc",
            lake_manifest_hash="ctx_lake_hash_xyz",
        )
        baseline = make_unit(model_id="SPREAD_BLOWOUT_RECOMPRESSION")
        context_unit = make_unit(
            model_id=baseline.model_id,
            hyp_id=baseline.hyp_id,
            symbol=baseline.symbol,
            event_id=baseline.event_id,
            event_type=baseline.event_type,
            research_clock="context_feature_uplift",
            context_set_id="target_plus_cross_asset",
            declared_context_sets=("target_only", "target_plus_cross_asset"),
        )
        artifact_path = tmp_path / "scratch" / baseline.unit_id / "screening_artifact.json"
        model_entry = {"model_id": baseline.model_id, "hyp_id": baseline.hyp_id}
        parsed = build_structured_parsed_hypothesis(baseline, model_entry)
        candidate = _build_candidate_model(baseline, model_entry, tmp_path, parsed)
        _write_screening_artifact(
            str(artifact_path),
            _valid_filter_result(baseline),
            baseline,
            model_entry,
            ctx,
            "deadbeef" * 4,
            RunProfiler(),
            candidate=candidate,
        )

        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact_matches_resume_unit(
            payload,
            baseline,
            events_csv_hash="ctx_events_hash_abc",
            lake_manifest_hash="ctx_lake_hash_xyz",
            research_split=DEFAULT_RESEARCH_SPLIT,
            screening_scope="pilot",
            **resolve_resume_provenance(str(tmp_path), baseline, git_commit=ctx.git_commit),
        )
        assert not artifact_matches_resume_unit(
            payload,
            context_unit,
            events_csv_hash="ctx_events_hash_abc",
            lake_manifest_hash="ctx_lake_hash_xyz",
            research_split=DEFAULT_RESEARCH_SPLIT,
            screening_scope="pilot",
        )

    def test_screen_paid_batch_artifact_resume_accepts_context_hashes(self, monkeypatch, tmp_path):
        import numpy as np
        from types import SimpleNamespace
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache

        ctx = make_context(
            repo_root=str(tmp_path),
            events_csv_hash="batch_events_hash",
            lake_manifest_hash="batch_lake_hash",
        )
        scratch = tmp_path / "runtime" / "paid_screen_scratch" / "run_a"
        cache = BoundedLRUCache(max_entries=4, max_memory_mb=64)
        cache_key = _batch_cache_key(ctx)
        cache.put(cache_key, np.array([[1, 2, 3, 4, 5, 1_700_000_000_000.0]]))
        unit = make_unit(unit_id="u_prov", model_id="SPREAD_BLOWOUT_RECOMPRESSION")

        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.run_vectorbt_simulation_matrix",
            lambda **kwargs: _valid_filter_result(unit, run_id="batch_prov_test"),
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.apply_promotion_gates",
            lambda result, **kwargs: result,
        )

        results = screen_paid_batch(
            [unit],
            ctx,
            data_cache=cache,
            run_screening=True,
            scratch_root=str(scratch),
        )

        assert len(results) == 1
        assert results[0].status == "OK"
        payload = json.loads(Path(results[0].screening_artifact_path).read_text(encoding="utf-8"))
        assert payload["lake_manifest_hash"] == "batch_lake_hash"
        assert payload["events_csv_hash_or_not_applicable"] == "batch_events_hash"
        provenance = resolve_resume_provenance(str(tmp_path), unit, git_commit=ctx.git_commit)
        assert artifact_matches_resume_unit(
            payload,
            unit,
            events_csv_hash="batch_events_hash",
            lake_manifest_hash="batch_lake_hash",
            research_split=DEFAULT_RESEARCH_SPLIT,
            screening_scope="pilot",
            **provenance,
        )


class TestGroupUnitsByBatchKey:
    def test_groups_by_symbol_and_event(self):
        units = [
            make_unit(unit_id="u1", symbol="MES.v.0", event_id="CPI_2024"),
            make_unit(unit_id="u2", symbol="MES.v.0", event_id="CPI_2024"),
            make_unit(unit_id="u3", symbol="ES.v.0", event_id="CPI_2024"),
            make_unit(unit_id="u4", symbol="MES.v.0", event_id="NFP_2024"),
        ]
        ctx = make_context()
        groups = group_units_by_batch_key(units, ctx)
        assert len(groups) == 3
        mes_cpi = [u.unit_id for u in units if u.symbol == "MES.v.0" and u.event_id == "CPI_2024"]
        assert sorted(u.unit_id for grp in groups.values() for u in grp if u.unit_id in mes_cpi) == sorted(mes_cpi)

    def test_different_feature_set_splits_groups(self):
        ctx = make_context()
        u1 = make_unit(unit_id="u1", feature_set_id="fs_a")
        u2 = make_unit(unit_id="u2", feature_set_id="fs_b")
        groups = group_units_by_batch_key([u1, u2], ctx)
        assert len(groups) == 2

    def test_different_context_set_splits_groups(self):
        ctx = make_context()
        u1 = make_unit(unit_id="u1", context_set_id="target_only")
        u2 = make_unit(unit_id="u2", context_set_id="target_plus_cross_asset")
        groups = group_units_by_batch_key([u1, u2], ctx)
        assert len(groups) == 2

    def test_same_full_batching_key_batches_together(self):
        ctx = make_context()
        u1 = make_unit(unit_id="u1")
        u2 = make_unit(unit_id="u2")
        assert batching_key_for_unit(u1, ctx) == batching_key_for_unit(u2, ctx)
        groups = group_units_by_batch_key([u1, u2], ctx)
        assert len(groups) == 1
        assert len(next(iter(groups.values()))) == 2

    def test_empty_units(self):
        groups = group_units_by_batch_key([], make_context())
        assert groups == {}


_REPO = Path(__file__).resolve().parents[1]


class TestSignalImplementationHash:
    def test_signal_hash_paths_include_hypothesis_modules(self):
        paths = _signal_implementation_hash_paths(str(_REPO))
        rel_paths = {p.relative_to(_REPO).as_posix() for p in paths if p.is_file()}
        assert "packages/features_engine/src/hypotheses/registry.py" in rel_paths
        assert "packages/features_engine/src/hypotheses/modules.py" in rel_paths
        assert "packages/features_engine/src/pipeline/market_state_pipeline.py" in rel_paths
        assert "packages/research_pipeline/feature_recipe.py" in rel_paths
        assert "packages/backtest_pipeline/src/fs_v1_screen_path.py" in rel_paths
        assert "packages/backtest_pipeline/src/paid_screen_matrix.py" in rel_paths
        assert "packages/replay/cross_asset_assembly.py" in rel_paths

    def test_resolve_signal_implementation_hash_uses_packages_path(self):
        digest = _resolve_signal_implementation_hash(str(_REPO))
        assert digest != "unknown"
        assert len(digest) == 32

    def test_signal_implementation_hash_changes_when_hypothesis_file_changes(self, tmp_path):
        import shutil

        repo = tmp_path / "repo"
        hyp_src = _REPO / "packages" / "features_engine" / "src" / "hypotheses"
        hyp_dst = repo / "packages" / "features_engine" / "src" / "hypotheses"
        hyp_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(hyp_src, hyp_dst)

        for rel in (
            "packages/features_engine/src/pipeline/market_state_pipeline.py",
            "packages/features_engine/src/model_registry.py",
            "packages/features_engine/config/model_registry.yaml",
            "packages/research_pipeline/feature_recipe.py",
            "packages/backtest_pipeline/src/vectorbt_adapter.py",
        ):
            src = _REPO / rel
            dst = repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        before = _resolve_signal_implementation_hash(str(repo))
        modules_path = hyp_dst / "modules.py"
        modules_path.write_text(modules_path.read_text(encoding="utf-8") + "\n# hash bump\n", encoding="utf-8")
        after = _resolve_signal_implementation_hash(str(repo))

        assert before != "unknown"
        assert after != "unknown"
        assert before != after

    def test_resolve_batching_hashes_never_unknown_in_real_repo(self):
        unit = make_unit()
        ctx = make_context(repo_root=str(_REPO))
        _, _, sig_hash, _ = resolve_batching_hashes(unit, ctx)
        assert sig_hash != "unknown"
        assert len(sig_hash) == 32


class TestBuildBatchingKey:
    def test_builds_key_with_all_fields(self):
        unit = make_unit()
        ctx = make_context()
        key = build_batching_key(
            unit, ctx,
            data_manifest_hash="dmh",
            feature_set_hash="fsh",
            signal_implementation_hash="sih",
            model_registry_hash="mrh",
        )
        assert key.symbol == "MES.v.0"
        assert key.event_id == "CPI_2024_09_11_TIGHT"
        assert key.data_manifest_hash == "dmh"
        assert key.feature_set_hash == "fsh"
        assert key.signal_implementation_hash == "sih"
        assert key.model_registry_hash == "mrh"
        assert key.lake_manifest_hash == "lh"
        assert key.events_csv_hash == "eh"

    def test_cache_key_methods_work(self):
        unit = make_unit()
        ctx = make_context()
        key = build_batching_key(
            unit, ctx,
            data_manifest_hash="dmh",
            feature_set_hash="fsh",
            signal_implementation_hash="sih",
            model_registry_hash="mrh",
        )
        assert len(key.cache_key()) == 32
        assert len(key.feature_cache_key()) == 32
        assert len(key.signal_cache_key("HYP_5")) == 32
        assert len(key.vbt_result_cache_key("HYP_5", "chunk", "1.0", "rust")) == 32


class TestCacheWiring:
    """Phase 4 Task 4.2 — wiring BoundedLRUCache into screen_paid_batch()."""

    def test_bounded_lru_cache_used_as_data_cache(self):
        """A BoundedLRUCache can be passed as data_cache and returns results."""
        import numpy as np
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache

        ctx = make_context()
        cache = BoundedLRUCache(max_entries=10, max_memory_mb=64)
        # Pre-populate the cache so the data loader is never called.
        cache_key = _batch_cache_key(ctx)
        cache.put(cache_key, np.array([[1, 2, 3, 4, 5]]))
        units = [make_unit(unit_id="u1"), make_unit(unit_id="u2", model_id="HYP_6")]

        results = screen_paid_batch(units, ctx, data_cache=cache, run_screening=False)
        assert len(results) == 2
        assert all(r.status == "SKIPPED" for r in results)

    def test_cache_hit_on_second_batch_same_event_id(self):
        """Second batch with the same event_id hits the cache (no reload)."""
        import numpy as np
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache

        ctx = make_context()
        cache = BoundedLRUCache(max_entries=10, max_memory_mb=64)
        profiler = RunProfiler()

        units = [make_unit(unit_id="u1")]

        # First batch: miss (loads via _default_data_loader which will fail
        # since repo_root=".", so we instead pre-seed via the cache directly
        # to simulate a successful first load, then run a second batch that
        # should hit). We use a repo_root that has no NPZ data so the loader
        # returns None on a genuine miss.
        ctx_miss = make_context(repo_root="/nonexistent")
        # First batch — genuine miss: no data, loader returns None.
        screen_paid_batch(units, ctx_miss, profiler=profiler, data_cache=cache,
                          run_screening=False)
        assert cache.miss_count >= 1
        # After the miss the cache may or may not be populated (loader
        # returned None, so nothing was put). For the hit test we seed the
        # cache manually to simulate a prior successful load.
        cache_key = _batch_cache_key(ctx_miss)
        cache.put(cache_key, np.array([[1, 2, 3, 4, 5]]))

        # Second batch — same event_id, now a hit. Use the same ctx_miss so
        # the cache key matches, and a fresh profiler so only this batch's
        # hit/miss is counted.
        profiler2 = RunProfiler()
        results = screen_paid_batch(units, ctx_miss, profiler=profiler2,
                                     data_cache=cache, run_screening=False)
        assert profiler2.cache_hits == 1
        assert profiler2.cache_misses == 0
        assert cache.hit_count >= 1
        assert all(r.status == "SKIPPED" for r in results)

    def test_cache_miss_on_first_batch(self):
        """First batch with no cached data records a miss."""
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache

        ctx = make_context(repo_root="/nonexistent")
        cache = BoundedLRUCache(max_entries=10, max_memory_mb=64)
        profiler = RunProfiler()
        units = [make_unit(unit_id="u1")]

        screen_paid_batch(units, ctx, profiler=profiler, data_cache=cache, run_screening=False)

        # The loader failed (nonexistent repo), so it's a miss and no data.
        assert profiler.cache_misses >= 1
        assert cache.miss_count >= 1

    def test_profiler_records_correct_hit_miss_counts(self):
        """Profiler reflects the BoundedLRUCache's hit/miss counts exactly."""
        import numpy as np
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache

        ctx = make_context()
        cache = BoundedLRUCache(max_entries=10, max_memory_mb=64)
        cache_key = _batch_cache_key(ctx)
        cache.put(cache_key, np.array([[1, 2, 3, 4, 5]]))

        units = [make_unit(unit_id="u1")]

        # Batch 1 — hit (data pre-seeded in cache).
        p1 = RunProfiler()
        screen_paid_batch(units, ctx, profiler=p1, data_cache=cache, run_screening=False)
        assert p1.cache_hits == 1
        assert p1.cache_misses == 0

        # Batch 2 — hit again (same key, same cache).
        p2 = RunProfiler()
        screen_paid_batch(units, ctx, profiler=p2, data_cache=cache, run_screening=False)
        assert p2.cache_hits == 1
        assert p2.cache_misses == 0

        # The cache's own cumulative counters should now show 2 hits.
        assert cache.hit_count == 2
        assert cache.miss_count == 0

    def test_backward_compat_plain_dict_still_works(self):
        """A plain dict data_cache continues to work (backward compat)."""
        import numpy as np

        ctx = make_context()
        data_cache = {_batch_cache_key(ctx):
                      np.array([[1, 2, 3, 4, 5]])}
        profiler = RunProfiler()
        units = [make_unit(unit_id="u1")]
        results = screen_paid_batch(units, ctx, profiler=profiler,
                                     data_cache=data_cache, run_screening=False)
        assert len(results) == 1
        assert results[0].status == "SKIPPED"
        assert profiler.cache_hits == 1
        assert profiler.cache_misses == 0

    def test_worker_passes_bounded_lru_cache_not_internal_store(self):
        """PaidScreenWorker passes the BoundedLRUCache object, not its
        internal _store OrderedDict, to screen_paid_batch."""
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache
        from backtest_pipeline.src.paid_screen_worker import PaidScreenWorker

        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
        )
        worker.init()
        # The worker's _data_cache must be a BoundedLRUCache instance.
        assert isinstance(worker._data_cache, BoundedLRUCache)
        # Monkey-patch screen_paid_batch to capture the data_cache argument.
        captured = {}
        from backtest_pipeline.src import paid_screen_worker as _wmod
        original = _wmod.screen_paid_batch

        def _capture(**kwargs):
            captured["data_cache"] = kwargs.get("data_cache")
            return []

        try:
            _wmod.screen_paid_batch = _capture
            worker.process_batch([make_unit()])
        finally:
            _wmod.screen_paid_batch = original

        # The worker must pass the BoundedLRUCache object itself, not
        # its internal _store OrderedDict.
        assert "data_cache" in captured
        assert isinstance(captured["data_cache"], BoundedLRUCache)
        assert captured["data_cache"] is worker._data_cache


class TestSymbolAwareOhlcvCache:
    def test_same_event_different_symbols_use_distinct_cache_keys(self):
        ctx = make_context()
        mes_unit = make_unit(unit_id="u_mes", symbol="MES.v.0")
        es_unit = make_unit(unit_id="u_es", symbol="ES.v.0")
        assert ohlcv_data_cache_key(mes_unit, ctx) != ohlcv_data_cache_key(es_unit, ctx)

    def test_same_event_different_symbols_do_not_share_ohlcv_cache(self, monkeypatch):
        import numpy as np

        ctx = make_context()
        mes_unit = make_unit(unit_id="u_mes", symbol="MES.v.0")
        es_unit = make_unit(unit_id="u_es", symbol="ES.v.0")
        mes_ohlcv = np.array([[1.0, 2.0, 3.0, 4.0, 5.0]])
        es_ohlcv = np.array([[9.0, 8.0, 7.0, 6.0, 5.0]])
        load_calls: list[tuple[str, str]] = []

        def _fake_loader(event_id, repo_root, symbol=None):
            load_calls.append((event_id, symbol or ""))
            if symbol == "MES.v.0":
                return mes_ohlcv
            if symbol == "ES.v.0":
                return es_ohlcv
            return None

        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch._load_ohlcv_for_unit",
            lambda unit, context: _fake_loader(unit.event_id, context.repo_root, unit.symbol),
        )

        data_cache: dict = {}
        screen_paid_batch([mes_unit], ctx, data_cache=data_cache, run_screening=False)
        screen_paid_batch([es_unit], ctx, data_cache=data_cache, run_screening=False)

        assert load_calls == [
            (mes_unit.event_id, "MES.v.0"),
            (es_unit.event_id, "ES.v.0"),
        ]
        assert data_cache[ohlcv_data_cache_key(mes_unit, ctx)] is mes_ohlcv
        assert data_cache[ohlcv_data_cache_key(es_unit, ctx)] is es_ohlcv


class TestApplyPromotionGatesAfterMatrix:
    def test_gate_failure_moves_promoted_to_rejected(self):
        from backtest_pipeline.src.promotion_gate import PromotedCandidate, PromotionGate
        from backtest_pipeline.src.vectorbt_adapter import FilterResult, apply_promotion_gates

        prom = PromotedCandidate(
            candidate_id="c1",
            hypothesis_id="HYP_5",
            strategy_family="HYP_5",
            asset_class="futures",
            symbol="MES.v.0",
            timeframe="1m",
            param_values={},
            vectorbt_run_id="run1",
            vectorbt_results={
                "gate_metric_authority": "official_vectorbt_portfolio_stats",
                "oos_expectancy": -999.0,
                "wf_consistency": 0.75,
                "max_drawdown_pct": 0.0,
                "num_trades": 100,
            },
            pass_reason="vectorbt_simulated",
        )
        result = FilterResult(
            backend="vectorbt",
            run_id="run1",
            promoted=[prom],
            rejected=[],
        )
        gated = apply_promotion_gates(result, screening_scope="pilot")
        assert gated.promoted == []
        assert len(gated.rejected) == 1
        assert gated.rejected[0].reject_reason == "promotion_gate_failed"


class TestPromotionGateWiringPlantedPass:
    """Step 1: candidate -> VectorBT metrics -> gate -> promoted_ids."""

    @staticmethod
    def _official_stats_promoted(**metric_overrides) -> "PromotedCandidate":
        from backtest_pipeline.src.promotion_gate import PromotedCandidate

        metrics = {
            "gate_metric_authority": "official_vectorbt_portfolio_stats",
            "oos_expectancy": 1.5,
            "wf_consistency": 0.75,
            "max_drawdown_pct": -5.0,
            "num_trades": 50,
        }
        metrics.update(metric_overrides)
        return PromotedCandidate(
            candidate_id="planted_pass",
            hypothesis_id="HYP_5",
            strategy_family="HYP_5",
            asset_class="futures",
            symbol="MES.v.0",
            timeframe="1m",
            param_values={"signal_threshold": 0.15},
            vectorbt_run_id="run_planted",
            vectorbt_results=metrics,
            pass_reason="vectorbt_simulated",
        )

    def test_paid_compute_scope_promotes_official_stats_candidate(self):
        from backtest_pipeline.src.vectorbt_adapter import FilterResult, apply_promotion_gates

        prom = self._official_stats_promoted()
        result = FilterResult(backend="vectorbt", run_id="run_planted", promoted=[prom], rejected=[])
        gated = apply_promotion_gates(result, screening_scope="paid-compute")
        assert len(gated.promoted) == 1
        assert gated.promoted[0].candidate_id == "planted_pass"
        assert gated.promoted[0].pass_reason == "vectorbt_screen_passed_replay_not_eligible"
        assert gated.promoted[0].vectorbt_results["paid_compute_gate_evaluation"]["failures"] == []

    def test_paid_compute_rejects_low_expectancy_with_explicit_reason(self):
        from backtest_pipeline.src.vectorbt_adapter import FilterResult, apply_promotion_gates

        prom = self._official_stats_promoted(oos_expectancy=-1.0)
        result = FilterResult(backend="vectorbt", run_id="run_planted", promoted=[prom], rejected=[])
        gated = apply_promotion_gates(result, screening_scope="paid_compute")
        assert gated.promoted == []
        assert len(gated.rejected) == 1
        failures = gated.rejected[0].metric_values["paid_compute_gate_evaluation"]["failures"]
        assert "oos_expectancy_below_threshold" in failures

    def test_paid_compute_rejects_missing_expectancy_with_explicit_reason(self):
        from backtest_pipeline.src.vectorbt_adapter import FilterResult, apply_promotion_gates

        prom = self._official_stats_promoted()
        del prom.vectorbt_results["oos_expectancy"]
        result = FilterResult(backend="vectorbt", run_id="run_planted", promoted=[prom], rejected=[])
        gated = apply_promotion_gates(result, screening_scope="paid-compute")
        assert gated.promoted == []
        failures = gated.rejected[0].metric_values["paid_compute_gate_evaluation"]["failures"]
        assert "missing_oos_expectancy" in failures

    def test_paid_compute_hydrates_auxiliary_walk_forward_metrics(self):
        from backtest_pipeline.src.vectorbt_adapter import FilterResult, apply_promotion_gates

        prom = self._official_stats_promoted()
        prom.vectorbt_results.pop("oos_expectancy", None)
        prom.vectorbt_results.pop("wf_consistency", None)
        prom.vectorbt_results["auxiliary_numpy_walk_forward"] = {
            "oos_expectancy": 1.25,
            "wf_consistency": 0.9,
        }
        result = FilterResult(backend="vectorbt", run_id="run_planted", promoted=[prom], rejected=[])
        gated = apply_promotion_gates(result, screening_scope="paid_compute")

        assert len(gated.promoted) == 1
        metrics = gated.promoted[0].vectorbt_results
        assert metrics["oos_expectancy"] == 1.25
        assert metrics["wf_consistency"] == 0.9
        assert metrics["paid_compute_gate_evaluation"]["used_fields"] == {
            "oos_expectancy": "auxiliary_numpy_walk_forward",
            "wf_consistency": "auxiliary_numpy_walk_forward",
            "max_drawdown_pct": "Max Drawdown [%]",
            "num_trades": "Total Trades",
        }
        assert "wf_consistency" not in metrics["paid_compute_gate_evaluation"]["skipped_unmeasured_fields"]

    def test_pilot_gate_ignores_auxiliary_walk_forward_metrics(self):
        from backtest_pipeline.src.vectorbt_adapter import FilterResult, apply_promotion_gates

        prom = self._official_stats_promoted(
            expectancy=-1.0,
            auxiliary_numpy_walk_forward={
                "oos_expectancy": 2.0,
                "wf_consistency": 1.0,
            },
        )
        result = FilterResult(backend="vectorbt", run_id="run_planted", promoted=[prom], rejected=[])
        gated = apply_promotion_gates(result, screening_scope="pilot")

        assert gated.promoted == []
        failures = gated.rejected[0].metric_values["pilot_gate_evaluation"]["failures"]
        assert "oos_expectancy_below_threshold" in failures
        assert gated.rejected[0].metric_values["oos_expectancy"] == -1.0
        assert "wf_consistency" not in gated.rejected[0].metric_values

    def test_full_gate_rejects_missing_walk_forward_and_stability(self):
        from backtest_pipeline.src.promotion_gate import PromotionGate
        from backtest_pipeline.src.vectorbt_adapter import FilterResult, apply_promotion_gates

        prom = self._official_stats_promoted()
        prom.vectorbt_results.pop("gate_metric_authority", None)
        prom.vectorbt_results.pop("wf_consistency", None)
        prom.vectorbt_results.pop("oos_expectancy", None)
        result = FilterResult(backend="vectorbt", run_id="run_planted", promoted=[prom], rejected=[])
        gated = apply_promotion_gates(
            result,
            screening_scope="refine",
            gates=PromotionGate(),
        )
        assert gated.promoted == []
        failures = gated.rejected[0].metric_values["promotion_gate_failures"]
        assert "missing_wf_consistency" in failures
        assert "missing_param_stability_score" in failures

    def test_screen_paid_batch_writes_promoted_ids_from_planted_pass(self, monkeypatch, tmp_path):
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache
        from backtest_pipeline.src.vectorbt_adapter import FilterResult, apply_promotion_gates

        prom = self._official_stats_promoted()
        filter_result = FilterResult(
            backend="vectorbt",
            run_id="run_planted",
            promoted=[prom],
            rejected=[],
            screening_scope="paid-compute",
            code_commit="abc123",
            parameter_space_id="ps_test",
            parameter_space_hash="ps_hash_test",
            max_trials=1,
            trials_run=1,
            max_total_trials=1,
            max_models=1,
            max_symbols=1,
            max_feature_sets=1,
            rust_engine_required_for_scope=True,
            rust_engine_available=True,
            vectorbt_engine_runtime_proof=True,
            vectorbt_engine="rust",
            engine_parity_status="rust_runtime_proven",
            vectorbt_version="1.0.0",
            vectorbt_available=True,
        )
        gated = apply_promotion_gates(filter_result, screening_scope="paid-compute")
        assert gated.promoted

        unit = make_unit()
        ctx = make_context(screening_scope="paid-compute", repo_root=str(tmp_path))
        cache = BoundedLRUCache(max_entries=4, max_memory_mb=64)
        ohlcv = np.array([[100.0, 101.0, 102.0, 103.0, 1.0, 1_700_000_000_000.0]] * 40)
        cache.put(_batch_cache_key(ctx, unit), ohlcv)
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.run_vectorbt_simulation_matrix",
            lambda **_kwargs: gated,
        )

        results = screen_paid_batch([unit], ctx, data_cache=cache, run_screening=True)
        assert len(results) == 1
        assert results[0].status == "OK", results[0].error
        assert results[0].promoted_ids == ["planted_pass"]
        artifact = json.loads(Path(results[0].screening_artifact_path).read_text(encoding="utf-8"))
        assert artifact["promoted_ids"] == ["planted_pass"]

    def test_pilot_batch_artifact_stamps_matrix_recipe_handoff(
        self, monkeypatch, tmp_path
    ):
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache
        from backtest_pipeline.src.vectorbt_adapter import FilterResult
        from research_pipeline.types import CandidateModel

        recipe_hash = "recipe_hash_abc"
        unit = make_unit()
        pinned_candidate = CandidateModel(
            candidate_id="candidate_with_recipe_hash",
            model_id=unit.model_id,
            strategy_params={"signal_threshold": 0.15},
            thesis=unit.thesis,
            metadata={
                "strategy_family": unit.model_id,
                "symbol": unit.symbol,
                "feature_recipe_hash": recipe_hash,
            },
        )
        pinned_candidate.feature_recipe_hash = recipe_hash
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch._build_candidate_model",
            lambda *_, **__: pinned_candidate,
        )

        prom = self._official_stats_promoted(
            feature_recipe_hash=recipe_hash,
            base_candidate_metadata={"feature_recipe_hash": recipe_hash},
        )
        filter_result = FilterResult(
            backend="vectorbt",
            run_id="run_matrix_recipe_handoff",
            promoted=[prom],
            rejected=[],
            screening_scope="pilot",
            code_commit="abc123",
            parameter_space_id="ps_test",
            parameter_space_hash="ps_hash_test",
            max_trials=1,
            trials_run=1,
            max_total_trials=1,
            max_models=1,
            max_symbols=1,
            max_feature_sets=1,
            vectorbt_available=True,
            vectorbt_version="1.0.0",
        )

        ctx = make_context(screening_scope="pilot", repo_root=str(tmp_path))
        cache = BoundedLRUCache(max_entries=4, max_memory_mb=64)
        ohlcv = np.array([[100.0, 101.0, 102.0, 103.0, 1.0, 1_700_000_000_000.0]] * 40)
        cache.put(_batch_cache_key(ctx, unit), ohlcv)
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.run_vectorbt_simulation_matrix",
            lambda **_kwargs: filter_result,
        )

        results = screen_paid_batch([unit], ctx, data_cache=cache, run_screening=True)
        assert len(results) == 1
        assert results[0].status == "OK", results[0].error
        artifact = json.loads(
            Path(results[0].screening_artifact_path).read_text(encoding="utf-8")
        )
        validate_screening_artifact(artifact)
        assert artifact["feature_recipe_hash"] == recipe_hash
        assert artifact["hftbacktest_handoff_status"] == "recipe_hash_handoff_ready"
        assert artifact["promoted"][0]["feature_recipe_hash"] == recipe_hash


class TestDefaultDataLoaderNpzRoot:
    def test_npz_candidates_prefers_hft3_npz_root_over_repo_stubs(
        self, tmp_path, monkeypatch
    ):
        """Repo-relative stub NPZ must not win when HFT3_NPZ_ROOT points elsewhere."""
        from backtest_pipeline.src.vectorbt_adapter import _npz_candidates_for_event
        from data_system.src.event_data_resolver import npz_search_dirs

        repo = tmp_path / "repo"
        stub_dir = repo / "data" / "npz"
        stub_dir.mkdir(parents=True)
        external = tmp_path / "external_lake"
        external.mkdir()

        event_id = "CPI_2024_09_11_TIGHT"
        symbol = "MES.v.0"
        stub = stub_dir / f"{symbol}_{event_id}_mbo.npz"
        stub.write_bytes(b"stub")
        real = external / f"{symbol}_{event_id}_mbo.npz"
        real.write_bytes(b"real")

        monkeypatch.setenv("HFT3_NPZ_ROOT", str(external))

        candidates = _npz_candidates_for_event(
            npz_search_dirs(repo),
            event_id,
            symbol,
        )
        assert candidates == [real]


class TestV2FeatureFamilyConsumption:
    def test_screen_paid_batch_stamps_primary_fs_v1_and_cross_asset(
        self, monkeypatch, tmp_path,
    ):
        """v2 screen_paid_batch must emit fs_v1 + cross_asset family rows when stores exist."""
        import numpy as np
        from backtest_pipeline.src.fs_v1_screen_path import FS_V1_BAR_CONSTRUCTION_ID
        from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache
        from data_system.src.feature_store import store_path
        from research_pipeline.src.stage_a_screen import REGIME_LABELS_ORDERED

        def _make_store(dest: Path) -> None:
            from data_system.src.feature_store import feature_index_hash

            dest.parent.mkdir(parents=True, exist_ok=True)
            n_rows = 40
            ts_start = 1_700_000_000_000_000_000
            tick_ns = 1_000_000
            ts = np.array([ts_start + i * tick_ns for i in range(n_rows)], dtype=np.int64)
            X = np.zeros((n_rows, 64), dtype=np.float64)
            X[:, 40] = 5000.0
            X[:, 0] = 0.6
            np.savez_compressed(
                str(dest),
                ts=ts,
                X=X,
                best_bid=np.full(n_rows, 4999.75),
                best_ask=np.full(n_rows, 5000.00),
                bbo_valid=np.ones(n_rows, dtype=np.bool_),
                regime_state_vocab=np.array(list(REGIME_LABELS_ORDERED)),
                regime_state_id=np.zeros(n_rows, dtype=np.int32),
                event_ctx_vocab=np.array(["NORMAL"]),
                event_ctx_id=np.zeros(n_rows, dtype=np.int32),
                vol_state_vocab=np.array(["NORMAL"]),
                vol_state_id=np.zeros(n_rows, dtype=np.int32),
                liq_state_vocab=np.array(["NORMAL"]),
                liq_state_id=np.zeros(n_rows, dtype=np.int32),
                tick_size=np.float64(0.25),
                feature_index_hash=np.array(feature_index_hash()),
            )

        repo = tmp_path / "repo"
        (repo / "packages").mkdir(parents=True)
        features_root = repo / "data" / "features"
        event_id = "CPI_2024_09_11_TIGHT"
        mes_sym = "MES.v.0"
        es_sym = "ES.v.0"
        _make_store(store_path(features_root, mes_sym, event_id))
        _make_store(store_path(features_root, es_sym, event_id))
        monkeypatch.setenv("HFT3_FEATURE_ROOT", str(features_root))

        ctx = make_context(repo_root=str(repo), screening_scope="pilot")
        unit = make_unit(
            unit_id="u_fs_v1",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            event_id=event_id,
            symbol=mes_sym,
        )
        scratch = repo / "runtime" / "paid_screen_scratch" / "v2_family_test"

        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.run_vectorbt_simulation_matrix",
            lambda **kwargs: _valid_filter_result(unit, run_id="v2_family_test"),
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.apply_promotion_gates",
            lambda result, **kwargs: result,
        )

        results = screen_paid_batch(
            [unit],
            ctx,
            data_cache=BoundedLRUCache(max_entries=4, max_memory_mb=64),
            run_screening=True,
            scratch_root=str(scratch),
        )

        assert len(results) == 1
        assert results[0].status == "OK"
        payload = json.loads(
            Path(results[0].screening_artifact_path).read_text(encoding="utf-8")
        )
        validate_screening_artifact(payload)
        assert payload["bar_construction_id"] == FS_V1_BAR_CONSTRUCTION_ID
        manifest = payload["feature_usage_manifest"]
        primary = manifest["primary_fs_v1"]
        assert "fs_v1_row_loop" in primary["why_not_used_or_sidelined"]
        cross = manifest["cross_asset_futures"]
        assert "leader_legs_aligned" in cross["why_not_used_or_sidelined"]
        assert payload["cross_asset_alignment_status"] == cross["model_consumption"]
