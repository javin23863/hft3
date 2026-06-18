"""Tests for the Ontology Gate Agent (ontology_gate.py).

Every test exercises a real check — no mock-only assertions. The gate is
deterministic, so tests feed concrete inputs and assert concrete outputs.
"""

from __future__ import annotations

import copy

import pytest

from backtest_pipeline.src.ontology_gate import (
    DRIFT_PATTERN_FEATURES_AS_CLUES,
    DRIFT_PATTERN_HBT_NO_CPP,
    DRIFT_PATTERN_LAKE_AS_USAGE,
    DRIFT_PATTERN_NON_RUST_AS_PAID,
    DRIFT_PATTERN_PARALLEL_AUTHORITY,
    DRIFT_PATTERN_PER_EVENT_AS_UPLIFT,
    DRIFT_PATTERN_VBT_AS_HFT_REALISM,
    FableChecklist,
    GateVerdict,
    check_drift,
    check_invariants,
    check_scope_honesty,
    check_tool_usage,
    gate_decision,
    run_gate,
    trace_citation,
    validate_artifact_schema,
    validate_fable_entry_checklist,
)
from backtest_pipeline.src.feature_plane import (
    FEATURE_PLANE_STATUS_BAR_STUB,
    FEATURE_PLANE_STATUS_FEATURE_COMPLETE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_citation_claim():
    return {
        "paper_id": "cont-kukanov-stoikov-2011-ofi",
        "spec_ref": "VECTORBT_SCREENING_ENGINE_SPEC.md::Screening Artifact Contract",
        "tool_doc_ref": "Portfolio.from_signals::vectorbt==1.0.0",
    }


@pytest.fixture
def unbacked_citation_claim():
    return {
        "paper_id": "nonexistent-paper-2099",
        "spec_ref": "NONEXISTENT_SPEC.md::Imaginary Section",
        "tool_doc_ref": "FakeAPI.not_real::fake==0.0.0",
    }


@pytest.fixture
def valid_artifact():
    """Minimal valid screening artifact for schema validation tests."""
    return {
        "run_id": "test_run_001",
        "created_at_utc": "2026-06-18T00:00:00Z",
        "code_commit": "abc123",
        "screening_backend": "vectorbt",
        "vectorbt_version": "1.0.0",
        "vectorbt_engine": "numba",
        "engine_parity_status": "not_applicable_pilot",
        "rust_engine_required_for_scope": False,
        "rust_engine_available": False,
        "vectorbt_engine_runtime_proof": False,
        "license_review": "not_applicable_pilot",
        "research_clock": "scheduled_event",
        "parameter_space_id": "ps_001",
        "parameter_space_hash": "abc123",
        "max_trials": 32,
        "trials_run": 5,
        "run_budget_id": "rb_001",
        "max_models": 1,
        "max_symbols": 1,
        "max_feature_sets": 1,
        "max_total_trials": 32,
        "max_wall_clock_seconds": 3600,
        "max_peak_memory_mb_or_null": 4096,
        "abort_on_budget_exhaustion": True,
        "screening_scope": "pilot",
        "candidate_ids": ["c1"],
        "candidate_reasons": {"c1": "queued"},
        "promoted_ids": [],
        "promoted_reasons": {},
        "rejected_ids": ["c1"],
        "rejected_reasons": {"c1": "insufficient_trades"},
        "stop_reasons": ["MAX_TRIALS_REACHED"],
        "feature_set_id": "fs_v1",
        "feature_set_hash": "abc123",
        "data_manifest_hash": "abc123",
        "lake_manifest_hash": "abc123",
        "events_csv_hash_or_not_applicable": "not_applicable_for_vectorbt_pilot",
        "split_scheme_id": "walk_forward_v1",
        "no_lookahead_signal_shift_proof": "shifted_1_bar",
        "fees_model_id": "fixed_001",
        "slippage_model_id": "fixed_001",
        "bar_construction_id": "ohlcv_1m",
        "feature_plane_status": FEATURE_PLANE_STATUS_BAR_STUB,
        "feature_usage_manifest_hash": "cebd94b020a870d37df35d23e4b8529a3e1ee7c567f45f8272500de222320b0a",
        "feature_usage_manifest": {
            "primary_fs_v1": {"catalog_eligibility": "eligible", "model_consumption": "not_used"},
            "cross_asset_futures": {"catalog_eligibility": "not_measured", "model_consumption": "not_used"},
            "vix_vvix_sensor": {"catalog_eligibility": "not_measured", "model_consumption": "not_used"},
            "vix_options": {"catalog_eligibility": "not_measured", "model_consumption": "not_used"},
            "cme_options_context": {"catalog_eligibility": "not_measured", "model_consumption": "not_used"},
            "macro_context": {"catalog_eligibility": "not_measured", "model_consumption": "not_used"},
            "continuous_session": {"catalog_eligibility": "not_measured", "model_consumption": "not_used"},
            "latency_state": {"catalog_eligibility": "not_measured", "model_consumption": "not_used"},
        },
        "model_feature_usage_status": "not_used",
        "declared_context_sets": [],
        "target_event_type_or_null": "CPI",
        "allowed_context_set_id_or_null": None,
        "context_feature_coverage_status": "not_measured",
        "context_ablation_status": "not_run",
        "continuous_clock_status": "not_run",
        "cross_asset_alignment_status": "not_run",
        "vix_sensor_status": "not_run",
        "vix_options_status": "not_run",
        "cme_options_context_status": "not_run",
        "latency_feature_status": "not_run",
        "data_scope_skip_manifest_hash": "abc123",
        "full_product_evidence_status": "refused",
        "screening_artifact_hash": "abc123",
        "promoted": [],
        "rejected": [
            {
                "candidate_id": "c1",
                "model_id": "HYP_5",
                "symbol": "MES.v.0",
                "research_clock": "scheduled_event",
                "opportunity_type_or_event_type": "CPI",
                "parameter_values": {"stop_loss": 0.5},
                "parameter_values_hash": "abc123",
                "trials_budget_tier": "pilot",
                "in_sample_metrics": {},
                "out_of_sample_metrics": {},
                "walk_forward_metrics": {},
                "wfc_metrics": {},
                "surface_stability_metrics": {},
                "robustness_gate_scope": "pilot",
                "wfc_status": "not_run",
                "dsr_status": "not_run",
                "pbo_status": "not_run",
                "cscv_status": "not_run",
                "robustness_artifact_staleness": "not_run",
                "trade_count": 0,
                "gross_return": 0.0,
                "total_fees": 0.0,
                "total_slippage": 0.0,
                "net_return": 0.0,
                "net_pnl": 0.0,
                "expectancy_per_trade": 0.0,
                "profit_factor": 0.0,
                "sharpe": 0.0,
                "sortino": 0.0,
                "max_drawdown": 0.0,
                "turnover": 0.0,
                "bootstrap_ci_or_not_run": "not_run",
                "dsr_or_not_run": "not_run",
                "pbo_or_not_run": "not_run",
                "cscv_count_or_not_run": "not_run",
                "fee_stress_or_not_run": "not_run",
                "slippage_stress_or_not_run": "not_run",
                "latency_stress_or_not_run": "not_run",
                "holm_bh_or_not_run": "not_run",
                "null_battery_or_not_run": "not_run",
                "planted_alpha_or_not_run": "not_run",
                "adversarial_or_not_run": "not_run",
                "parameter_perturbation_or_not_run": "not_run",
                "screening_status": "reject",
                "replay_eligibility_status": "not_eligible",
                "rejection_reason_or_null": "insufficient_trades",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Fable entry checklist tests
# ---------------------------------------------------------------------------

class TestFableEntryChecklist:

    def test_all_true_passes(self):
        result = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        assert result.all_true is True

    def test_not_grounded_fails(self):
        result = validate_fable_entry_checklist(
            grounded=False, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        assert result.all_true is False

    def test_vault_not_read_fails(self):
        result = validate_fable_entry_checklist(
            grounded=True, vault_read=False,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        assert result.all_true is False

    def test_no_assumptions_false_fails(self):
        result = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=False, fable_active=True,
        )
        assert result.all_true is False

    def test_fable_active_false_fails(self):
        result = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=False,
        )
        assert result.all_true is False


# ---------------------------------------------------------------------------
# Citation tracer tests
# ---------------------------------------------------------------------------

class TestCitationTracer:

    def test_valid_paper_resolves(self, valid_citation_claim):
        result = trace_citation(
            paper_id=valid_citation_claim["paper_id"],
            spec_ref=valid_citation_claim["spec_ref"],
            tool_doc_ref=valid_citation_claim["tool_doc_ref"],
        )
        assert result.backed is True
        assert result.source_type in ("paper", "spec", "tool_doc", "multi")

    def test_unbacked_citation_rejected(self, unbacked_citation_claim):
        result = trace_citation(
            paper_id=unbacked_citation_claim["paper_id"],
            spec_ref=unbacked_citation_claim["spec_ref"],
            tool_doc_ref=unbacked_citation_claim["tool_doc_ref"],
        )
        assert result.backed is False

    def test_none_citation_unbacked(self):
        result = trace_citation(paper_id=None, spec_ref=None, tool_doc_ref=None)
        assert result.backed is False

    def test_paper_only_resolves(self, valid_citation_claim):
        result = trace_citation(
            paper_id=valid_citation_claim["paper_id"],
            spec_ref=None,
            tool_doc_ref=None,
        )
        assert result.backed is True

    def test_spec_only_resolves(self):
        result = trace_citation(
            paper_id=None,
            spec_ref="VECTORBT_SCREENING_ENGINE_SPEC.md",
            tool_doc_ref=None,
        )
        assert result.backed is True


# ---------------------------------------------------------------------------
# Invariant checker tests
# ---------------------------------------------------------------------------

class TestInvariantChecker:

    def test_docs_area_no_invariants_pass(self):
        """Docs area has no applicable invariants — all should be na/pass."""
        result = check_invariants(area="docs")
        assert result.red_count == 0

    def test_b1_fail_in_features_engine(self):
        result = check_invariants(
            area="features_engine",
            invariant_results={"B1": "fail"},
        )
        assert result.red_count == 1
        assert any("B1" in f for f in result.findings)

    def test_b3_fail_in_backtest_pipeline(self):
        result = check_invariants(
            area="backtest_pipeline",
            invariant_results={"B3": "fail"},
        )
        assert result.red_count == 1

    def test_b7_fail_in_data_system(self):
        result = check_invariants(
            area="data_system",
            invariant_results={"B7": "fail"},
        )
        assert result.red_count == 1

    def test_all_pass_in_backtest_pipeline(self):
        result = check_invariants(
            area="backtest_pipeline",
            invariant_results={"B1": "pass", "B2": "pass", "B3": "pass", "B4": "pass", "B5": "pass"},
        )
        assert result.red_count == 0

    def test_unknown_area_applies_all(self):
        """Unknown area defaults to full B1-B8 per charter."""
        result = check_invariants(area="unknown_area")
        assert result.red_count == 0  # all na, not fail


# ---------------------------------------------------------------------------
# Tool usage checker tests
# ---------------------------------------------------------------------------

class TestToolUsageChecker:

    def test_official_vbt_api_passes(self):
        result = check_tool_usage({
            "tool": "vectorbt",
            "api_name": "Portfolio.from_signals",
            "args": {"close": True, "entries": True, "exits": True},
            "engine": "rust",
            "scope": "paid-compute",
            "version": "1.0.0",
        })
        assert result.api_correct is True
        assert len(result.issues) == 0

    def test_hand_rolled_backtester_rejected(self):
        result = check_tool_usage({
            "tool": "vectorbt",
            "api_name": "my_custom_backtester.run",
            "args": {},
            "engine": "rust",
            "scope": "paid-compute",
        })
        assert result.api_correct is False
        assert any("not_official" in i or "unknown" in i for i in result.issues)

    def test_non_rust_for_paid_compute_rejected(self):
        result = check_tool_usage({
            "tool": "vectorbt",
            "api_name": "Portfolio.from_signals",
            "args": {"close": True, "entries": True, "exits": True},
            "engine": "numba",
            "scope": "paid-compute",
        })
        assert result.api_correct is True  # API is correct, but issues flag non-rust
        assert any("non_rust" in i or "non_rust_engine" in i for i in result.issues)

    def test_hbt_without_source_lock_rejected(self):
        result = check_tool_usage({
            "tool": "hftbacktest",
            "api_name": "hftbacktest.BacktestAsset",
            "hftbacktest_upstream_ref": None,
        })
        assert any("source_lock" in i for i in result.issues)

    def test_hbt_wrong_version_rejected(self):
        result = check_tool_usage({
            "tool": "hftbacktest",
            "api_name": "hftbacktest.BacktestAsset",
            "hftbacktest_upstream_ref": "2.3.0",
        })
        assert any("version_mismatch" in i for i in result.issues)

    def test_pilot_scope_numba_ok(self):
        """Pilot scope is allowed to use numba engine."""
        result = check_tool_usage({
            "tool": "vectorbt",
            "api_name": "Portfolio.from_signals",
            "args": {"close": True, "entries": True, "exits": True},
            "engine": "numba",
            "scope": "pilot",
            "version": "1.0.0",
        })
        assert result.api_correct is True
        assert len(result.issues) == 0

    def test_hbt_production_realism_without_cpp_rejected(self):
        result = check_tool_usage({
            "tool": "hftbacktest",
            "api_name": "hftbacktest.BacktestAsset",
            "hftbacktest_upstream_ref": "2.4.2",
            "claims_production_realism": True,
            "cpp_hot_path_evidence": False,
        })
        assert any("cpp_hot_path" in i or "production_realism" in i for i in result.issues)


# ---------------------------------------------------------------------------
# Artifact validation tests
# ---------------------------------------------------------------------------

class TestArtifactValidation:

    def test_valid_artifact_passes(self, valid_artifact):
        result = validate_artifact_schema(valid_artifact, run_screening_validator=False)
        assert result.valid is True

    def test_missing_field_fails(self, valid_artifact):
        artifact = copy.deepcopy(valid_artifact)
        del artifact["screening_backend"]
        result = validate_artifact_schema(artifact)
        assert result.valid is False
        assert any("screening_backend" in f for f in result.missing_fields)

    def test_wrong_backend_fails(self, valid_artifact):
        artifact = copy.deepcopy(valid_artifact)
        artifact["screening_backend"] = "custom"
        result = validate_artifact_schema(artifact)
        assert result.valid is False

    def test_feature_plane_mislabel_fails(self, valid_artifact):
        """Artifact claiming feature_complete without consumption proof fails."""
        artifact = copy.deepcopy(valid_artifact)
        artifact["feature_plane_status"] = FEATURE_PLANE_STATUS_FEATURE_COMPLETE
        artifact["feature_usage_manifest"] = []
        result = validate_artifact_schema(artifact)
        assert result.valid is False


# ---------------------------------------------------------------------------
# Drift guard tests
# ---------------------------------------------------------------------------

class TestDriftGuard:

    def test_clean_text_passes(self):
        result = check_drift(text="feature_x = compute_ofi(lob_data)")
        assert result.clean is True
        assert len(result.detected_patterns) == 0

    def test_clues_terminology_rejected(self):
        result = check_drift(text="extract feature clues from the data")
        assert DRIFT_PATTERN_FEATURES_AS_CLUES in result.detected_patterns

    def test_per_event_as_context_uplift_rejected(self):
        result = check_drift(text="Per-event standalone profitability demonstrates context uplift.")
        assert DRIFT_PATTERN_PER_EVENT_AS_UPLIFT in result.detected_patterns

    def test_lake_existence_as_usage_rejected(self):
        result = check_drift(text="lake existence proves feature usage")
        assert DRIFT_PATTERN_LAKE_AS_USAGE in result.detected_patterns

    def test_non_rust_as_paid_compute_rejected(self):
        result = check_drift(text="Non-Rust VectorBT results serve as paid-compute evidence.")
        assert DRIFT_PATTERN_NON_RUST_AS_PAID in result.detected_patterns

    def test_vbt_as_execution_realism_rejected(self):
        result = check_drift(text="VectorBT screening proves HFT execution realism.")
        assert DRIFT_PATTERN_VBT_AS_HFT_REALISM in result.detected_patterns

    def test_hbt_without_cpp_as_production_rejected(self):
        result = check_drift(text="Official HftBacktest proves production realism without C++")
        assert DRIFT_PATTERN_HBT_NO_CPP in result.detected_patterns

    def test_parallel_authority_doc_rejected(self):
        result = check_drift(text="This is a new source-of-truth document.")
        assert DRIFT_PATTERN_PARALLEL_AUTHORITY in result.detected_patterns

    def test_structured_drift_flags(self):
        """Drift patterns can be flagged via structured artifact mapping."""
        result = check_drift(artifact={"features_called_clues": True})
        assert DRIFT_PATTERN_FEATURES_AS_CLUES in result.detected_patterns

    def test_explicit_patterns(self):
        """Patterns can be passed directly (for tests/direct flagging)."""
        result = check_drift(patterns=[DRIFT_PATTERN_LAKE_AS_USAGE])
        assert DRIFT_PATTERN_LAKE_AS_USAGE in result.detected_patterns


# ---------------------------------------------------------------------------
# Scope honesty tests
# ---------------------------------------------------------------------------

class TestScopeHonesty:

    def test_clean_passes(self):
        result = check_scope_honesty()
        assert result.honest is True
        assert len(result.issues) == 0

    def test_subset_as_scope_green_rejected(self):
        result = check_scope_honesty(subset_pytest_claimed_as_scope_green=True)
        assert result.honest is False
        assert any("subset" in i for i in result.issues)

    def test_waived_verify_as_done_rejected(self):
        result = check_scope_honesty(waived_verify_claimed_as_done=True)
        assert result.honest is False
        assert any("waived" in i for i in result.issues)

    def test_plan_todo_theater_rejected(self):
        result = check_scope_honesty(plan_todo_theater=True)
        assert result.honest is False
        assert any("todo_theater" in i for i in result.issues)

    def test_scope_green_without_exit_code_rejected(self):
        result = check_scope_honesty(scope_green_without_exit_code=True)
        assert result.honest is False

    def test_missing_verify_tail_rejected(self):
        result = check_scope_honesty(missing_verify_tail=True)
        assert result.honest is False


# ---------------------------------------------------------------------------
# Gate decision tests
# ---------------------------------------------------------------------------

class TestGateDecision:

    def test_all_pass_yields_pass(self):
        fable = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        citation = trace_citation(
            paper_id="cont-kukanov-stoikov-2011-ofi",
            spec_ref="VECTORBT_SCREENING_ENGINE_SPEC.md",
            tool_doc_ref="Portfolio.from_signals::vectorbt==1.0.0",
        )
        invariants = check_invariants(area="docs")
        tool = check_tool_usage({
            "tool": "vectorbt",
            "api_name": "Portfolio.from_signals",
            "args": {"close": True, "entries": True, "exits": True},
            "engine": "rust",
            "scope": "paid-compute",
            "version": "1.0.0",
        })
        drift = check_drift(text="")
        scope = check_scope_honesty()

        verdict = gate_decision(
            fable_checklist=fable,
            citation_results=[citation],
            invariant_result=invariants,
            tool_usage_results=[tool],
            drift_result=drift,
            scope_honesty_result=scope,
        )
        assert verdict.verdict == "PASS"
        assert verdict.red_count == 0

    def test_unbacked_citation_yields_reject(self):
        fable = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        citation = trace_citation(
            paper_id="nonexistent-2099",
            spec_ref="NONEXISTENT.md",
            tool_doc_ref="FakeAPI::fake==0.0.0",
        )
        invariants = check_invariants(area="docs")
        drift = check_drift(text="")
        scope = check_scope_honesty()

        verdict = gate_decision(
            fable_checklist=fable,
            citation_results=[citation],
            invariant_result=invariants,
            drift_result=drift,
            scope_honesty_result=scope,
        )
        assert verdict.verdict == "REJECT"
        assert verdict.red_count > 0

    def test_fable_failure_blocks_gate(self):
        """If Fable checklist fails, gate must reject regardless of other results."""
        fable = validate_fable_entry_checklist(
            grounded=False, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        invariants = check_invariants(area="docs")
        drift = check_drift(text="")
        scope = check_scope_honesty()

        verdict = gate_decision(
            fable_checklist=fable,
            invariant_result=invariants,
            drift_result=drift,
            scope_honesty_result=scope,
        )
        assert verdict.verdict == "REJECT"
        assert any("fable" in r.lower() for r in verdict.reasons)

    def test_invariant_fail_yields_reject(self):
        fable = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        invariants = check_invariants(
            area="backtest_pipeline",
            invariant_results={"B1": "fail"},
        )
        drift = check_drift(text="")
        scope = check_scope_honesty()

        verdict = gate_decision(
            fable_checklist=fable,
            invariant_result=invariants,
            drift_result=drift,
            scope_honesty_result=scope,
        )
        assert verdict.verdict == "REJECT"
        assert any("B1" in r for r in verdict.reasons)

    def test_drift_pattern_yields_reject(self):
        fable = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        drift = check_drift(text="feature clues extracted from data")
        scope = check_scope_honesty()

        verdict = gate_decision(
            fable_checklist=fable,
            drift_result=drift,
            scope_honesty_result=scope,
        )
        assert verdict.verdict == "REJECT"

    def test_scope_honesty_violation_yields_reject(self):
        fable = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        scope = check_scope_honesty(subset_pytest_claimed_as_scope_green=True)

        verdict = gate_decision(
            fable_checklist=fable,
            scope_honesty_result=scope,
        )
        assert verdict.verdict == "REJECT"


# ---------------------------------------------------------------------------
# run_gate integration test
# ---------------------------------------------------------------------------

class TestRunGate:

    def test_run_gate_pass(self):
        fable = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        verdict = run_gate(
            fable_checklist=fable,
            citations=[{
                "paper_id": "cont-kukanov-stoikov-2011-ofi",
                "spec_ref": "VECTORBT_SCREENING_ENGINE_SPEC.md",
                "tool_doc_ref": "Portfolio.from_signals::vectorbt==1.0.0",
            }],
            area="docs",
            call_sites=[{
                "tool": "vectorbt",
                "api_name": "Portfolio.from_signals",
                "args": {"close": True, "entries": True, "exits": True},
                "engine": "rust",
                "scope": "paid-compute",
                "version": "1.0.0",
            }],
        )
        assert isinstance(verdict, GateVerdict)
        assert verdict.verdict == "PASS"

    def test_run_gate_reject_on_unbacked(self):
        fable = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        verdict = run_gate(
            fable_checklist=fable,
            citations=[{
                "paper_id": "nonexistent-2099",
                "spec_ref": "NONEXISTENT.md",
                "tool_doc_ref": "FakeAPI::fake==0.0.0",
            }],
            area="docs",
        )
        assert isinstance(verdict, GateVerdict)
        assert verdict.verdict == "REJECT"

    def test_run_gate_reject_on_fable_failure(self):
        fable = validate_fable_entry_checklist(
            grounded=False, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        verdict = run_gate(fable_checklist=fable, area="docs")
        assert verdict.verdict == "REJECT"

    def test_run_gate_reject_on_drift(self):
        fable = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        verdict = run_gate(
            fable_checklist=fable,
            area="docs",
            drift_text="feature clues in the implementation",
        )
        assert verdict.verdict == "REJECT"

    def test_run_gate_reject_on_scope_honesty(self):
        fable = validate_fable_entry_checklist(
            grounded=True, vault_read=True,
            authority_located=True, no_assumptions=True, fable_active=True,
        )
        verdict = run_gate(
            fable_checklist=fable,
            area="docs",
            subset_pytest_claimed_as_scope_green=True,
        )
        assert verdict.verdict == "REJECT"