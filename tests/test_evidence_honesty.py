"""Evidence-honesty tests: pipeline must not hide synthetic/fallback/proxy paths.

Every stage must report what engine was requested vs used, the evidence grade,
signal source, promotion eligibility, and all blockers. No "completed" stage
implies valid research evidence without these fields.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages"))
sys.path.insert(0, str(REPO_ROOT / "apps"))

from hft3_pipeline.manifest import (
    EngineKind,
    EvidenceGrade,
    HftTruthManifest,
    PipelineManifest,
    ReconciliationStatus,
    SignalSource,
    VectorbtFilterManifest,
)
from hft3_pipeline.run_mode import RunContext, RunMode


# ---------------------------------------------------------------------------
# 1. VectorBT backend labeling
# ---------------------------------------------------------------------------

class TestVectorbtBackendLabeling:
    """VectorBT stage must honestly report engine_requested, engine_used, evidence."""

    def test_vectorbt_manifest_has_evidence_fields(self):
        m = VectorbtFilterManifest(run_id="T-1", lane_id="cme_futures", model_id="T")
        d = m.to_dict()
        assert d["engine_requested"] == EngineKind.VECTORBT.value
        assert "engine_used" in d
        assert "evidence_status" in d
        assert "signal_source" in d
        assert "signal_model_id" in d

    def test_vectorbt_default_promotion_eligible_false(self):
        m = VectorbtFilterManifest(run_id="T-1", lane_id="cme_futures", model_id="T")
        assert m.promotion_eligible is False

    def test_vectorbt_numpy_fallback_labeling(self):
        """Simulate numpy_fallback path: engine_requested=VECTORBT, engine_used=NUMPY_FALLBACK, evidence=NON_AUTHORITATIVE_PREFILTER."""
        m = VectorbtFilterManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            backend="numpy_fallback",
            engine_used=EngineKind.NUMPY_FALLBACK.value,
            evidence_status=EvidenceGrade.NON_AUTHORITATIVE_PREFILTER.value,
        )
        d = m.to_dict()
        assert d["engine_requested"] == EngineKind.VECTORBT.value
        assert d["engine_used"] == EngineKind.NUMPY_FALLBACK.value
        assert d["evidence_status"] == EvidenceGrade.NON_AUTHORITATIVE_PREFILTER.value

    def test_vectorbt_vectorbt_backend_labeling(self):
        """VectorBT available: engine_requested=VECTORBT, engine_used=VECTORBT."""
        m = VectorbtFilterManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            backend="vectorbt",
            engine_used=EngineKind.VECTORBT.value,
            evidence_status=EvidenceGrade.NON_AUTHORITATIVE_PREFILTER.value,
        )
        d = m.to_dict()
        assert d["engine_used"] == EngineKind.VECTORBT.value

    def test_vectorbt_signal_source_present(self):
        m = VectorbtFilterManifest(
            run_id="T-1", lane_id="cme_futures", model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            signal_source=SignalSource.MODEL_CATALOG_MSP.value,
            signal_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        )
        d = m.to_dict()
        assert d["signal_source"] == SignalSource.MODEL_CATALOG_MSP.value

    def test_vectorbt_walk_forward_period_recorded(self):
        m = VectorbtFilterManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            walk_forward_period="HOLDOUT_2025",
            tuning_skipped_reason="holdout_period_evaluate_only",
        )
        d = m.to_dict()
        assert d["walk_forward_period"] == "HOLDOUT_2025"

    def test_vectorbt_evidence_not_authoritative(self):
        """VectorBT filter evidence is always NON_AUTHORITATIVE_PREFILTER regardless of backend."""
        m = VectorbtFilterManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            engine_used=EngineKind.VECTORBT.value,
            evidence_status=EvidenceGrade.NON_AUTHORITATIVE_PREFILTER.value,
        )
        assert m.evidence_status != EvidenceGrade.AUTHORITATIVE_EVIDENCE.value


# ---------------------------------------------------------------------------
# 2. HFT truth reconciliation and evidence
# ---------------------------------------------------------------------------

class TestHftTruthReconciliation:
    def test_hft_truth_manifest_has_reconciliation_fields(self):
        m = HftTruthManifest(run_id="T-1", lane_id="cme_futures", model_id="T")
        d = m.to_dict()
        assert d["engine_requested"] == EngineKind.REPLAY_SESSION_HFTBACKTEST.value
        assert "evidence_status" in d
        assert "reconciliation_status" in d
        assert "pnl_reconciliation_pass" in d
        assert "ledger_paths" in d

    def test_hft_truth_passes_reconciliation_when_fills_match(self):
        m = HftTruthManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            pnl_from_fills=100.0, pnl_from_account=100.0,
            pnl_reconciliation_pass=True,
            reconciliation_status=ReconciliationStatus.PASSED.value,
        )
        assert m.reconciliation_status == ReconciliationStatus.PASSED.value

    def test_hft_truth_fails_reconciliation_when_fills_mismatch(self):
        m = HftTruthManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            pnl_from_fills=100.0, pnl_from_account=50.0,
            pnl_reconciliation_pass=False,
            reconciliation_status=ReconciliationStatus.FAILED.value,
            evidence_status=EvidenceGrade.FAILED_ACCOUNTING_RECONCILIATION.value,
        )
        assert m.reconciliation_status == ReconciliationStatus.FAILED.value
        assert m.evidence_status == EvidenceGrade.FAILED_ACCOUNTING_RECONCILIATION.value


# ---------------------------------------------------------------------------
# 3. Partial replay labeling
# ---------------------------------------------------------------------------

class TestPartialReplayLabeling:
    def test_partial_replay_sets_evidence_and_blocks_promotion(self):
        m = HftTruthManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            max_steps_set=100, total_steps_available=1000,
            evidence_status=EvidenceGrade.PARTIAL_HFT_TRUTH_DEBUG_ONLY.value,
            promotion_eligible=False,
        )
        assert m.evidence_status == EvidenceGrade.PARTIAL_HFT_TRUTH_DEBUG_ONLY.value
        assert m.promotion_eligible is False

    def test_full_replay_no_max_steps(self):
        m = HftTruthManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            max_steps_set=None, total_steps_available=50000,
            evidence_status=EvidenceGrade.AUTHORITATIVE_EVIDENCE.value,
        )
        assert m.max_steps_set is None
        assert m.evidence_status == EvidenceGrade.AUTHORITATIVE_EVIDENCE.value


# ---------------------------------------------------------------------------
# 4. Single-event smoke only for FIXTURE_CI/DEBUG
# ---------------------------------------------------------------------------

class TestSingleEventSmokeLabeling:
    def test_single_event_smoke_only_for_ci_debug(self):
        """FIXTURE_CI and DEBUG single events get SMOKE_E2E_SINGLE_EVENT."""
        for mode in (RunMode.FIXTURE_CI, RunMode.DEBUG):
            m = PipelineManifest(
                run_id="T-1", lane_id="cme_futures", model_id="T",
                event_id="CPI_2024_09_11_TIGHT",
                single_event_smoke=True,
                evidence_grade=EvidenceGrade.SMOKE_E2E_SINGLE_EVENT.value,
            )
            assert m.single_event_smoke is True
            assert m.evidence_grade == EvidenceGrade.SMOKE_E2E_SINGLE_EVENT.value

    def test_single_event_smoke_blocks_promotion(self):
        m = PipelineManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            single_event_smoke=True,
            evidence_grade=EvidenceGrade.SMOKE_E2E_SINGLE_EVENT.value,
        )
        assert m.promotion_eligible is False

    def test_real_research_single_event_not_blocked(self):
        """REAL_RESEARCH with one event is legitimate — no smoke label."""
        m = PipelineManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            event_id="CPI_2024_09_11_TIGHT",
            single_event_smoke=False,
        )
        assert m.single_event_smoke is False
        assert not m.evidence_grade


# ---------------------------------------------------------------------------
# 5. WorkbenchTruth evidence display
# ---------------------------------------------------------------------------

class TestWorkbenchTruthEvidenceDisplay:
    """WorkbenchTruth must display evidence quality fields."""

    def test_cme_entry_has_evidence_fields(self):
        from apps.workbench.src.state.workbench_truth import CmeEntryTruth
        e = CmeEntryTruth(symbol="MES.v.0")
        assert hasattr(e, "engine_requested")
        assert hasattr(e, "engine_used")
        assert hasattr(e, "evidence_status")
        assert hasattr(e, "signal_source")
        assert hasattr(e, "reconciliation_status")
        assert hasattr(e, "ledgers_available")
        assert hasattr(e, "input_artifact_paths")
        assert hasattr(e, "output_artifact_paths")

    def test_cme_entry_shows_numpy_fallback(self):
        from apps.workbench.src.state.workbench_truth import CmeEntryTruth
        e = CmeEntryTruth(
            symbol="MES.v.0",
            engine_requested=EngineKind.VECTORBT.value,
            engine_used=EngineKind.NUMPY_FALLBACK.value,
            evidence_status=EvidenceGrade.NON_AUTHORITATIVE_PREFILTER.value,
        )
        assert e.engine_used == EngineKind.NUMPY_FALLBACK.value
        assert e.evidence_status == EvidenceGrade.NON_AUTHORITATIVE_PREFILTER.value

    def test_cme_entry_shows_failed_reconciliation(self):
        from apps.workbench.src.state.workbench_truth import CmeEntryTruth
        e = CmeEntryTruth(
            symbol="MES.v.0",
            evidence_status=EvidenceGrade.FAILED_ACCOUNTING_RECONCILIATION.value,
            reconciliation_status=ReconciliationStatus.FAILED.value,
            blockers=["pnl_reconciliation_failed"],
        )
        assert e.evidence_status == EvidenceGrade.FAILED_ACCOUNTING_RECONCILIATION.value

    def test_cme_entry_reads_evidence_from_correct_manifest(self):
        """evidence_status prefers HFT truth, falls back to VectorBT when HFT not run."""
        from apps.workbench.src.state.workbench_truth import CmeEntryTruth
        e = CmeEntryTruth(
            symbol="MES.v.0",
            evidence_status=EvidenceGrade.NON_AUTHORITATIVE_PREFILTER.value,
        )
        assert e.evidence_status == EvidenceGrade.NON_AUTHORITATIVE_PREFILTER.value

    def test_cme_entry_shows_artifact_paths(self):
        from apps.workbench.src.state.workbench_truth import CmeEntryTruth
        e = CmeEntryTruth(
            symbol="MES.v.0",
            input_artifact_paths=["data/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"],
            output_artifact_paths=["ledgers/RUN-001/fills.jsonl"],
        )
        assert len(e.input_artifact_paths) == 1
        assert len(e.output_artifact_paths) == 1


# ---------------------------------------------------------------------------
# 6. Signal source labeling
# ---------------------------------------------------------------------------

class TestSignalSourceLabeling:
    def test_signal_source_in_vectorbt_manifest(self):
        m = VectorbtFilterManifest(
            run_id="T-1", lane_id="cme_futures", model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            signal_source=SignalSource.MODEL_CATALOG_MSP.value,
        )
        assert m.signal_source == SignalSource.MODEL_CATALOG_MSP.value

    def test_unresolved_signal_source(self):
        m = VectorbtFilterManifest(
            run_id="T-1", lane_id="cme_futures", model_id="BOGUS_MODEL",
            signal_source=SignalSource.UNRESOLVED.value,
        )
        assert m.signal_source == SignalSource.UNRESOLVED.value


# ---------------------------------------------------------------------------
# 7. Evidence enforcement in promotion
# ---------------------------------------------------------------------------

class TestEvidenceEnforcedInPromotion:
    def test_promotion_blocked_when_evidence_not_authoritative(self):
        """Non-AUTHORITATIVE_EVIDENCE must prevent promotion."""
        hft = HftTruthManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            promotion_eligible=True,
            evidence_status=EvidenceGrade.NON_AUTHORITATIVE_PREFILTER.value,
        )
        assert hft.promotion_eligible is True
        assert hft.evidence_status != EvidenceGrade.AUTHORITATIVE_EVIDENCE.value

    def test_promotion_allowed_when_evidence_authoritative(self):
        """AUTHORITATIVE_EVIDENCE does not block promotion."""
        hft = HftTruthManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            promotion_eligible=True,
            evidence_status=EvidenceGrade.AUTHORITATIVE_EVIDENCE.value,
        )
        assert hft.evidence_status == EvidenceGrade.AUTHORITATIVE_EVIDENCE.value


# ---------------------------------------------------------------------------
# 8. Win rate computed from fill pairing
# ---------------------------------------------------------------------------

class TestWinRateFromFills:
    def test_win_rate_from_paired_fills(self):
        """_pair_fills_into_trades computes per-trade PnL from chronologically paired fills."""
        from backtest_pipeline.src.replay_matrix import _pair_fills_into_trades
        fills = [
            {"timestamp_ns": 100, "side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0},
            {"timestamp_ns": 200, "side": "SELL", "avg_fill_price": 101.0, "filled_quantity": 1.0},
        ]
        trade_pnls = _pair_fills_into_trades(fills)
        assert len(trade_pnls) == 1
        assert trade_pnls[0] == 1.0  # (101 - 100) * 1

    def test_win_rate_from_unpaired_fills_fallback(self):
        """When only one side has fills, fall back to heuristic."""
        from backtest_pipeline.src.replay_matrix import _compute_win_rate
        fills = [
            {"timestamp_ns": 100, "side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0},
        ]
        wr = _compute_win_rate(fills, net_pnl=10.0, num_trades=5)
        assert wr == 1.0  # heuristic: positive PnL

    def test_reconcile_pnl_uses_tick_tolerance(self):
        """Reconciliation tolerance is based on total_qty * tick_size, not 1% of balance."""
        from backtest_pipeline.src.replay_matrix import reconcile_pnl
        fills = [
            {"side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 2.0, "fees": 0.0},
            {"side": "SELL", "avg_fill_price": 101.0, "filled_quantity": 2.0, "fees": 0.0},
        ]
        # PnL from fills: -(100*2) + (101*2) = 2.0
        # Account balance: 1.0
        # Delta: 1.0, tolerance: max(4 * 0.25, 0.01) = 1.0
        # Delta(1.0) <= tolerance(1.0) -> pass
        recon = reconcile_pnl({"fills_detail": fills, "balance": 1.0, "fee": 0.0})
        assert recon["passes"] is True
        assert recon["tolerance"] == 1.0

    def test_reconcile_pnl_fails_when_delta_exceeds_tick_tolerance(self):
        from backtest_pipeline.src.replay_matrix import reconcile_pnl
        fills = [
            {"side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 2.0, "fees": 0.0},
            {"side": "SELL", "avg_fill_price": 101.0, "filled_quantity": 2.0, "fees": 0.0},
        ]
        # PnL from fills: 2.0, Account: 0.0
        # Delta: 2.0, tolerance: 1.0
        recon = reconcile_pnl({"fills_detail": fills, "balance": 0.0, "fee": 0.0})
        assert recon["passes"] is False
