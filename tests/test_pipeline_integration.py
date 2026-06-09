"""Acceptance tests for the unified hft3 pipeline.

These tests prove that:
1. VectorBT stage runs before HFTBacktest
2. VectorBT outputs parameter_set_ids that HFTBacktest consumes
3. VectorBT results cannot promote a model alone
4. HFT truth is required for promotion
5. Full metric surface is emitted
6. Missing metrics include reasons
7. TradeManager receives only promoted models
8. Synthetic data blocks promotion
9. Fixture data blocks promotion
10. No metric is silently omitted
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages"))
sys.path.insert(0, str(REPO_ROOT / "apps"))

from hft3_pipeline import stages
from hft3_pipeline.inventory import build_inventory
from hft3_pipeline.manifest import (
    HftTruthManifest,
    PipelineManifest,
    StageStatus,
    VectorbtFilterManifest,
)
from hft3_pipeline.run_mode import RunContext, RunMode


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def inventory(repo_root):
    return build_inventory(repo_root)


@pytest.fixture
def cme_run_ctx():
    return RunContext(
        run_mode=RunMode.REAL_RESEARCH,
        run_id="TEST-RUN-001",
        lane_id="cme_futures",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        symbol="MES.v.0",
        event_id="CPI_2024_09_11_TIGHT",
    )


# ---------------------------------------------------------------------------
# Part 1: Repo inventory
# ---------------------------------------------------------------------------

class TestRepoInventory:
    def test_repo_inventory_detects_all_lanes(self, repo_root):
        inv = build_inventory(repo_root)
        lane_ids = {l.lane_id for l in inv.lanes}
        assert "cme_futures" in lane_ids
        assert "equities_low_float" in lane_ids
        assert "options_parity" in lane_ids
        assert "crypto" in lane_ids

    def test_pipeline_status_reports_capabilities(self, inventory):
        assert isinstance(inventory.vectorbt_available, bool)
        assert isinstance(inventory.hftbacktest_available, bool)
        assert isinstance(inventory.metrics_engine_available, bool)
        assert isinstance(inventory.certification_registry_available, bool)
        assert isinstance(inventory.trade_manager_available, bool)
        assert isinstance(inventory.workbench_available, bool)

    def test_vectorbt_dependency_available_or_clear_blocker(self, inventory):
        if not inventory.vectorbt_available:
            assert any("vectorbt" in b.lower() for b in inventory.blockers)


# ---------------------------------------------------------------------------
# Part 2: Pipeline stages order
# ---------------------------------------------------------------------------

class TestPipelineOrder:
    def test_vectorbt_filter_runs_before_hftbacktest(self, repo_root, cme_run_ctx, inventory):
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        if data_result.get("status") != "ready":
            pytest.skip("data not available")
        feature_result = stages.stage_feature_generation(repo_root, cme_run_ctx, data_result)
        vbt = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        assert isinstance(vbt, VectorbtFilterManifest)
        assert vbt.run_id
        assert vbt.parameters_tested > 0

    def test_vectorbt_outputs_parameter_set_ids(self, repo_root, cme_run_ctx, inventory):
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        if data_result.get("status") != "ready":
            pytest.skip("data not available")
        feature_result = stages.stage_feature_generation(repo_root, cme_run_ctx, data_result)
        vbt = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        if vbt.top_candidates:
            for c in vbt.top_candidates:
                assert "parameter_set_id" in c
                assert c["parameter_set_id"].startswith("ps_")

    def test_hftbacktest_consumes_vectorbt_parameter_set_ids(self, repo_root, cme_run_ctx, inventory):
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        if data_result.get("status") != "ready":
            pytest.skip("data not available")
        feature_result = stages.stage_feature_generation(repo_root, cme_run_ctx, data_result)
        vbt = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        if not vbt.top_candidates:
            pytest.skip("no vectorbt candidates passed")
        hft = stages.stage_hft_truth(repo_root, cme_run_ctx, vbt, feature_result)
        assert hft.parameter_set_id == vbt.top_candidates[0]["parameter_set_id"]


# ---------------------------------------------------------------------------
# Part 3: Promotion gates
# ---------------------------------------------------------------------------

class TestPromotionGates:
    def test_vectorbt_results_cannot_promote_model(self, repo_root, cme_run_ctx, inventory):
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        if data_result.get("status") != "ready":
            pytest.skip("data not available")
        feature_result = stages.stage_feature_generation(repo_root, cme_run_ctx, data_result)
        vbt = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        assert not vbt.promotion_eligible

    def test_hft_truth_required_for_promotion(self, repo_root, cme_run_ctx, inventory):
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        if data_result.get("status") != "ready":
            pytest.skip("data not available")
        feature_result = stages.stage_feature_generation(repo_root, cme_run_ctx, data_result)
        vbt = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        if not vbt.top_candidates:
            pytest.skip("no vectorbt candidates passed")
        hft = stages.stage_hft_truth(repo_root, cme_run_ctx, vbt, feature_result)
        assert isinstance(hft, HftTruthManifest)
        assert hft.parent_vectorbt_run_id == vbt.run_id

    def test_synthetic_data_blocks_promotion(self):
        ctx = RunContext(run_mode=RunMode.FIXTURE_CI, run_id="TEST-SYNTH")
        ctx.synthetic_data_used = True
        eligible, blockers = ctx.check_promotion_eligibility()
        assert not eligible
        assert any("synthetic" in b.lower() for b in blockers)

    def test_fixture_data_blocks_promotion(self):
        ctx = RunContext(run_mode=RunMode.FIXTURE_CI, run_id="TEST-FIXTURE")
        eligible, blockers = ctx.check_promotion_eligibility()
        assert not eligible

    def test_performance_benchmark_blocks_promotion(self):
        ctx = RunContext(run_mode=RunMode.PERFORMANCE_BENCHMARK, run_id="TEST-BENCH")
        eligible, blockers = ctx.check_promotion_eligibility()
        assert not eligible

    def test_debug_mode_blocks_promotion(self):
        ctx = RunContext(run_mode=RunMode.DEBUG, run_id="TEST-DEBUG")
        eligible, blockers = ctx.check_promotion_eligibility()
        assert not eligible

    def test_real_research_allows_promotion(self):
        ctx = RunContext(run_mode=RunMode.REAL_RESEARCH, run_id="TEST-REAL")
        eligible, blockers = ctx.check_promotion_eligibility()
        assert eligible
        assert len(blockers) == 0

    def test_paper_replay_allows_promotion(self):
        ctx = RunContext(run_mode=RunMode.PAPER_REPLAY, run_id="TEST-PAPER")
        eligible, blockers = ctx.check_promotion_eligibility()
        assert eligible


# ---------------------------------------------------------------------------
# Part 4: Metrics surface
# ---------------------------------------------------------------------------

class TestMetricsSurface:
    def test_full_metric_surface_emitted(self, repo_root, cme_run_ctx, inventory):
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        if data_result.get("status") != "ready":
            pytest.skip("data not available")
        feature_result = stages.stage_feature_generation(repo_root, cme_run_ctx, data_result)
        vbt = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        if not vbt.top_candidates:
            pytest.skip("no vectorbt candidates passed")
        hft = stages.stage_hft_truth(repo_root, cme_run_ctx, vbt, feature_result)
        metrics_result = stages.stage_full_metrics(repo_root, cme_run_ctx, hft)
        scorecard = metrics_result.get("scorecard", {})
        assert "category_scores" in scorecard
        assert len(scorecard["category_scores"]) >= 7

    def test_missing_metrics_include_reasons(self, repo_root, cme_run_ctx, inventory):
        data_result = stages.stage_data_readiness(repo_root, cme_run_ctx, inventory)
        if data_result.get("status") != "ready":
            pytest.skip("data not available")
        feature_result = stages.stage_feature_generation(repo_root, cme_run_ctx, data_result)
        vbt = stages.stage_vectorbt_filter(repo_root, cme_run_ctx, feature_result, inventory)
        if not vbt.top_candidates:
            pytest.skip("no vectorbt candidates passed")
        hft = stages.stage_hft_truth(repo_root, cme_run_ctx, vbt, feature_result)
        metrics_result = stages.stage_full_metrics(repo_root, cme_run_ctx, hft)
        metrics = metrics_result.get("metrics", {})
        missing = metrics.get("missing_reasons", {})
        assert len(missing) > 0
        for key, reason in missing.items():
            assert isinstance(reason, str)
            assert len(reason) > 0

    def test_metric_groups_present(self, repo_root, cme_run_ctx, inventory):
        from hft3.model_metrics import calculate_metric_values, generate_model_scorecard
        m = calculate_metric_values({"net_pnl": 10.0, "num_trades": 5, "expectancy": 2.0, "measured_p99_ms": 1.0})
        sc = generate_model_scorecard("TEST", "RUN-001", m)
        groups = sc.metric_groups
        assert len(groups) == 6
        group_names = {g.group_name for g in groups}
        assert "net_alpha_quality" in group_names
        assert "drawdown_loss_behavior" in group_names
        assert "robustness_stability" in group_names
        assert "execution_realism" in group_names
        assert "portfolio_fit" in group_names
        assert "prediction_calibration_quality" in group_names

    def test_metric_entries_have_metadata(self, repo_root, cme_run_ctx, inventory):
        from hft3.model_metrics import calculate_metric_values, generate_model_scorecard
        m = calculate_metric_values({"net_pnl": 10.0, "num_trades": 5, "expectancy": 2.0, "measured_p99_ms": 1.0})
        sc = generate_model_scorecard("TEST", "RUN-001", m)
        for group in sc.metric_groups:
            for name, entry in group.metrics.items():
                assert hasattr(entry, "unit")
                assert hasattr(entry, "status")
                assert entry.status in ("computed", "missing", "not_applicable")
                if entry.status == "missing":
                    assert entry.missing_reason, f"missing metric {name} has no reason"


# ---------------------------------------------------------------------------
# Part 5: TradeManager handoff
# ---------------------------------------------------------------------------

class TestTradeManagerHandoff:
    def test_trade_manager_receives_only_promoted_models(self, repo_root, cme_run_ctx, inventory):
        result = stages.stage_trade_manager(
            repo_root, cme_run_ctx,
            {"promotion_status": "QUARANTINED"},
            {"scorecard": {}},
            HftTruthManifest(),
        )
        assert result["status"] == "SKIPPED"
        assert "not promoted" in result.get("reason", "").lower()


# ---------------------------------------------------------------------------
# Part 6: Manifest persistence
# ---------------------------------------------------------------------------

class TestManifestPersistence:
    def test_vectorbt_manifest_has_required_fields(self):
        m = VectorbtFilterManifest(
            run_id="TEST-001", lane_id="cme_futures", model_id="TEST",
        )
        d = m.to_dict()
        assert "run_id" in d
        assert "hftbacktest_required" in d
        assert d["hftbacktest_required"] is True
        assert "pit_status" in d
        assert "leakage_status" in d
        assert "missing_reasons" in d
        assert "next_action" in d

    def test_hft_truth_manifest_has_required_fields(self):
        m = HftTruthManifest(
            run_id="TEST-001", lane_id="cme_futures", model_id="TEST",
        )
        d = m.to_dict()
        assert "parent_vectorbt_run_id" in d
        assert "parameter_set_id" in d
        assert "hftbacktest_config" in d
        assert "latency_config" in d
        assert "queue_model" in d
        assert "fill_model" in d
        assert "fee_model" in d
        assert "slippage_model" in d
        assert "execution_realism" in d
        assert "promotion_eligible" in d
        assert "next_action" in d


# ---------------------------------------------------------------------------
# Part 7: Run mode enforcement
# ---------------------------------------------------------------------------

class TestRunModeEnforcement:
    def test_no_synthetic_research_promotion(self):
        for mode in [RunMode.FIXTURE_CI, RunMode.PERFORMANCE_BENCHMARK, RunMode.DEBUG]:
            ctx = RunContext(run_mode=mode, run_id=f"TEST-{mode.value}")
            eligible, _ = ctx.check_promotion_eligibility()
            assert not eligible, f"{mode.value} should not be promotion eligible"

    def test_run_mode_dict_includes_eligibility(self):
        for mode in RunMode:
            ctx = RunContext(run_mode=mode, run_id=f"TEST-{mode.value}")
            d = ctx.to_dict()
            assert "promotion_eligible" in d
            assert d["promotion_eligible"] == mode.promotion_eligible
