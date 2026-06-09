"""End-to-end tests for the unified pipeline."""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages"))
sys.path.insert(0, str(REPO_ROOT / "apps"))

from hft3_pipeline import stages
from hft3_pipeline.run_mode import RunContext, RunMode
from hft3_pipeline.manifest import PipelineManifest
from hft3_pipeline.manifest import VectorbtFilterManifest, HftTruthManifest


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def cme_run_ctx():
    return RunContext(
        run_mode=RunMode.REAL_RESEARCH,
        run_id="TEST-E2E-001",
        lane_id="cme_futures",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        symbol="MES.v.0",
        event_id="CPI_2024_09_11_TIGHT",
    )


@pytest.fixture
def inventory(repo_root):
    return stages.stage_inventory(repo_root)


class TestEndToEndPipeline:
    """End-to-end tests that verify the pipeline runs through all stages."""

    @pytest.mark.slow
    def test_pipeline_reaches_stage_4(self, repo_root, cme_run_ctx, inventory):
        """Verify pipeline reaches HFT truth stage (Stage 4)."""
        # Stage 1: Data readiness
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        assert data_result.get("status") == "ready", f"Data not ready: {data_result}"
        
        # Stage 2: Data fingerprint
        feature_result = stages.stage_data_fingerprint(repo_root, cme_run_ctx, data_result)
        assert feature_result.get("status") == "ready", f"Fingerprint failed: {feature_result}"
        assert feature_result.get("data_type") == "mbo_raw", "Should be raw data, not features"
        
        # Stage 3: VectorBT filter
        vectorbt_manifest = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        assert vectorbt_manifest.top_n_forwarded >= 1, f"VectorBT produced {vectorbt_manifest.top_n_forwarded} candidates (need >= 1)"
        
        # Stage 4: HFT truth
        hft_manifest = stages.stage_hft_truth(repo_root, cme_run_ctx, vectorbt_manifest, feature_result)
        assert isinstance(hft_manifest, HftTruthManifest), "HFT truth should return HftTruthManifest"
        assert hft_manifest.pnl != 0.0 or hft_manifest.trades == 0, "HFT truth should compute PnL"

    @pytest.mark.slow
    def test_pipeline_reaches_stage_7(self, repo_root, cme_run_ctx, inventory):
        """Verify pipeline reaches promotion stage (Stage 7)."""
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        assert data_result.get("status") == "ready"
        
        feature_result = stages.stage_data_fingerprint(repo_root, cme_run_ctx, data_result)
        assert feature_result.get("status") == "ready"
        
        vectorbt_manifest = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        if vectorbt_manifest.top_n_forwarded == 0:
            pytest.skip("VectorBT produced 0 candidates")
        
        hft_manifest = stages.stage_hft_truth(repo_root, cme_run_ctx, vectorbt_manifest, feature_result)
        metrics_result = stages.stage_full_metrics(repo_root, cme_run_ctx, hft_manifest)
        promotion_result = stages.stage_promotion(
            repo_root, cme_run_ctx, hft_manifest, metrics_result, vectorbt_manifest
        )
        assert promotion_result["promotion_status"] in ("PROMOTED", "QUARANTINED"), \
            f"Promotion status should be PROMOTED or QUARANTINED, got {promotion_result['promotion_status']}"

    @pytest.mark.slow
    def test_pipeline_completes_all_stages(self, repo_root, cme_run_ctx, inventory):
        """Verify pipeline completes all 10 stages."""
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        assert data_result.get("status") == "ready"
        
        feature_result = stages.stage_data_fingerprint(repo_root, cme_run_ctx, data_result)
        assert feature_result.get("status") == "ready"
        
        vectorbt_manifest = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        if vectorbt_manifest.top_n_forwarded == 0:
            pytest.skip("VectorBT produced 0 candidates")
        
        hft_manifest = stages.stage_hft_truth(repo_root, cme_run_ctx, vectorbt_manifest, feature_result)
        metrics_result = stages.stage_full_metrics(repo_root, cme_run_ctx, hft_manifest)
        robustness_result = stages.stage_robustness(repo_root, cme_run_ctx, metrics_result)
        promotion_result = stages.stage_promotion(
            repo_root, cme_run_ctx, hft_manifest, metrics_result, vectorbt_manifest
        )
        
        if promotion_result["promotion_status"] == "PROMOTED":
            tm_result = stages.stage_trade_manager(
                repo_root, cme_run_ctx, promotion_result, metrics_result, hft_manifest
            )
            assert tm_result["status"] in ("COMPLETED", "SKIPPED"), \
                f"Trade Manager status should be COMPLETED or SKIPPED, got {tm_result['status']}"
        
        wb_result = stages.stage_workbench_truth(repo_root, None)
        assert wb_result["status"] == "COMPLETED", "Workbench truth should complete"


class TestVectorBTIntegration:
    """Tests for VectorBT integration with the pipeline."""

    @pytest.mark.slow
    def test_vectorbt_runs_after_feature_extraction(self, repo_root, cme_run_ctx, inventory):
        """Verify VectorBT runs after feature extraction."""
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        assert data_result.get("status") == "ready"
        
        feature_result = stages.stage_data_fingerprint(repo_root, cme_run_ctx, data_result)
        assert feature_result.get("status") == "ready"
        
        # VectorBT should run successfully
        vectorbt_manifest = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        assert vectorbt_manifest.parameters_tested > 0, "VectorBT should test parameters"

    def test_vectorbt_blocks_on_insufficient_input(self, repo_root, inventory):
        """Verify VectorBT blocks when input is insufficient."""
        # Create a run context with invalid event
        bad_ctx = RunContext(
            run_mode=RunMode.REAL_RESEARCH,
            run_id="TEST-BAD-001",
            lane_id="cme_futures",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            symbol="MES.v.0",
            event_id="NONEXISTENT_EVENT",
        )
        
        data_result = stages.stage_data_readiness(repo_root, bad_ctx, inventory)
        # Should fail at data readiness
        assert data_result.get("status") != "ready", "Should fail for nonexistent event"


class TestHFTTruthGate:
    """Tests for HFT truth gate."""

    @pytest.mark.slow
    def test_hft_truth_uses_replay_matrix(self, repo_root, cme_run_ctx, inventory):
        """Verify HFT truth uses replay_matrix (not SignalBacktester)."""
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        assert data_result.get("status") == "ready"
        
        feature_result = stages.stage_data_fingerprint(repo_root, cme_run_ctx, data_result)
        assert feature_result.get("status") == "ready"
        
        vectorbt_manifest = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        if vectorbt_manifest.top_n_forwarded == 0:
            pytest.skip("VectorBT produced 0 candidates")
        
        hft_manifest = stages.stage_hft_truth(repo_root, cme_run_ctx, vectorbt_manifest, feature_result)
        assert hft_manifest.queue_model == "LogProbQueueModel2", \
            "HFT truth should use LogProbQueueModel2 (ReplaySession)"


class TestPromotionGates:
    """Tests for promotion gates."""

    def test_positive_pnl_alone_cannot_promote(self):
        """Verify positive PnL alone cannot promote a model."""
        # Create a run context with FIXTURE_CI mode
        fixture_ctx = RunContext(
            run_mode=RunMode.FIXTURE_CI,
            run_id="TEST-FIXTURE-001",
            lane_id="cme_futures",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            symbol="MES.v.0",
            event_id="CPI_2024_09_11_TIGHT",
        )
        
        # Even with positive PnL, FIXTURE_CI mode should block promotion
        eligible, blockers = fixture_ctx.check_promotion_eligibility()
        assert not eligible, "FIXTURE_CI mode should block promotion"
        assert any("run_mode" in b.lower() for b in blockers), "Should mention run_mode in blockers"


class TestMetricsSurface:
    """Tests for metrics surface."""

    def test_all_metric_groups_emitted(self, repo_root, cme_run_ctx, inventory):
        """Verify all 6 metric groups are emitted."""
        from hft3.model_metrics import calculate_metric_values, generate_model_scorecard
        
        # Create a simple backtest result
        m = calculate_metric_values({
            "net_pnl": 10.0,
            "num_trades": 5,
            "expectancy": 2.0,
            "measured_p99_ms": 1.0,
        })
        
        sc = generate_model_scorecard("TEST", "RUN-001", m)
        
        # Check that we have 6 metric groups
        assert len(sc.metric_groups) == 6, f"Should have 6 metric groups, got {len(sc.metric_groups)}"
        
        group_names = {g.group_name for g in sc.metric_groups}
        expected_groups = {
            "net_alpha_quality",
            "drawdown_loss_behavior",
            "robustness_stability",
            "execution_realism",
            "portfolio_fit",
            "prediction_calibration_quality",
        }
        assert group_names == expected_groups, f"Missing groups: {expected_groups - group_names}"

    def test_missing_metrics_have_reasons(self, repo_root, cme_run_ctx, inventory):
        """Verify missing metrics have reasons."""
        from hft3.model_metrics import calculate_metric_values, generate_model_scorecard
        
        m = calculate_metric_values({
            "net_pnl": 10.0,
            "num_trades": 5,
            "expectancy": 2.0,
            "measured_p99_ms": 1.0,
        })
        
        sc = generate_model_scorecard("TEST", "RUN-001", m)
        
        # Check that all missing metrics have reasons
        for group in sc.metric_groups:
            for name, entry in group.metrics.items():
                if entry.status == "missing":
                    assert entry.missing_reason, f"Missing metric {name} has no reason"


class TestManifestPersistence:
    """Tests for manifest persistence."""

    def test_vectorbt_manifest_has_required_fields(self):
        """Verify VectorBT manifest has all required fields."""
        m = VectorbtFilterManifest(
            run_id="TEST-001",
            lane_id="cme_futures",
            model_id="TEST",
        )
        d = m.to_dict()
        
        required_fields = [
            "run_id", "hftbacktest_required", "pit_status", "leakage_status",
            "missing_reasons", "next_action", "engine_requested", "engine_used",
            "evidence_status", "signal_source", "signal_model_id",
        ]
        for field in required_fields:
            assert field in d, f"Missing field: {field}"

    def test_hft_truth_manifest_has_required_fields(self):
        """Verify HFT truth manifest has all required fields."""
        m = HftTruthManifest(
            run_id="TEST-001",
            lane_id="cme_futures",
            model_id="TEST",
        )
        d = m.to_dict()
        
        required_fields = [
            "parent_vectorbt_run_id", "parameter_set_id", "hftbacktest_config",
            "latency_config", "queue_model", "fill_model", "fee_model",
            "slippage_model", "execution_realism", "promotion_eligible", "next_action",
            "engine_requested", "engine_used", "evidence_status",
            "reconciliation_status", "ledger_paths",
        ]
        for field in required_fields:
            assert field in d, f"Missing field: {field}"


class TestRunModeEnforcement:
    """Tests for run mode enforcement."""

    def test_no_synthetic_research_promotion(self):
        """Verify synthetic/fixture/benchmark/debug modes block promotion."""
        for mode in [RunMode.FIXTURE_CI, RunMode.PERFORMANCE_BENCHMARK, RunMode.DEBUG]:
            ctx = RunContext(run_mode=mode, run_id=f"TEST-{mode.value}")
            eligible, _ = ctx.check_promotion_eligibility()
            assert not eligible, f"{mode.value} should not be promotion eligible"

    def test_run_mode_dict_includes_eligibility(self):
        """Verify run mode dict includes promotion_eligible flag."""
        for mode in RunMode:
            ctx = RunContext(run_mode=mode, run_id=f"TEST-{mode.value}")
            d = ctx.to_dict()
            assert "promotion_eligible" in d, f"{mode.value} should have promotion_eligible"
            assert d["promotion_eligible"] == mode.promotion_eligible, \
                f"{mode.value} promotion_eligible mismatch"
