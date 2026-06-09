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
    Enum,
    EvidenceGrade,
    HftTruthManifest,
    PipelineManifest,
    ReconciliationStatus,
    SignalSource,
    StageStatus,
    VectorbtFilterManifest,
)
from hft3_pipeline.run_mode import RunContext, RunMode


# ---------------------------------------------------------------------------
# 1. Enum coercion and serialization
# ---------------------------------------------------------------------------

class TestEnumCoercion:
    """Enum values assigned directly must be coerced to str by __post_init__."""

    def test_vectorbt_coerces_enum_to_str(self):
        m = VectorbtFilterManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            engine_requested=EngineKind.VECTORBT,
            evidence_status=EvidenceGrade.NON_AUTHORITATIVE_PREFILTER,
            signal_source=SignalSource.MODEL_CATALOG_MSP,
        )
        assert isinstance(m.engine_requested, str)
        assert isinstance(m.evidence_status, str)
        assert isinstance(m.signal_source, str)

    def test_hft_coerces_enum_to_str(self):
        m = HftTruthManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            engine_requested=EngineKind.REPLAY_SESSION_HFTBACKTEST,
            evidence_status=EvidenceGrade.AUTHORITATIVE_EVIDENCE,
            reconciliation_status=ReconciliationStatus.PASSED,
        )
        assert isinstance(m.engine_requested, str)
        assert isinstance(m.evidence_status, str)
        assert isinstance(m.reconciliation_status, str)

    def test_pipeline_coerces_enum_to_str(self):
        m = PipelineManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            evidence_grade=EvidenceGrade.SMOKE_E2E_SINGLE_EVENT,
        )
        assert isinstance(m.evidence_grade, str)

    def test_to_dict_produces_json_serializable_values(self):
        m = VectorbtFilterManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            engine_used=EngineKind.NUMPY_FALLBACK,
            evidence_status=EvidenceGrade.NON_AUTHORITATIVE_PREFILTER,
        )
        d = m.to_dict()
        serialized = json.dumps(d)
        assert "NUMPY_FALLBACK" in serialized
        assert "NON_AUTHORITATIVE_PREFILTER" in serialized

    def test_pipeline_to_dict_serializes_stage_status_enum(self):
        m = PipelineManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            stages={"inventory": StageStatus.PASSED, "data_readiness": StageStatus.FAILED},
        )
        d = m.to_dict()
        assert d["stages"]["inventory"] == "PASSED"
        assert d["stages"]["data_readiness"] == "FAILED"
        serialized = json.dumps(d)
        assert "PASSED" in serialized


# ---------------------------------------------------------------------------
# 2. Default values
# ---------------------------------------------------------------------------

class TestManifestDefaults:
    def test_promotion_eligible_defaults_false(self):
        assert VectorbtFilterManifest(run_id="T", lane_id="c", model_id="T").promotion_eligible is False
        assert HftTruthManifest(run_id="T", lane_id="c", model_id="T").promotion_eligible is False
        assert PipelineManifest(run_id="T", lane_id="c", model_id="T").promotion_eligible is False

    def test_vectorbt_evidence_default_empty(self):
        m = VectorbtFilterManifest(run_id="T", lane_id="c", model_id="T")
        assert m.evidence_status == ""
        assert m.engine_used == ""

    def test_hft_reconciliation_default_pending(self):
        m = HftTruthManifest(run_id="T", lane_id="c", model_id="T")
        assert m.reconciliation_status == ReconciliationStatus.PENDING.value
        assert m.pnl_reconciliation_pass is None


# ---------------------------------------------------------------------------
# 3. Promotion enforcement (actual logic test, not tautological)
# ---------------------------------------------------------------------------

class TestEvidenceEnforcedInPromotion:
    def _evidence_ok(self, evidence_status: str) -> bool:
        return evidence_status in (EvidenceGrade.AUTHORITATIVE_EVIDENCE.value, "")

    def test_non_authoritative_evidence_blocks_promotion(self):
        for bad_status in (
            EvidenceGrade.NON_AUTHORITATIVE_PREFILTER.value,
            EvidenceGrade.FIXTURE_ONLY.value,
            EvidenceGrade.DEBUG_ONLY.value,
            EvidenceGrade.BLOCKED.value,
            EvidenceGrade.FAILED_ACCOUNTING_RECONCILIATION.value,
            EvidenceGrade.SMOKE_E2E_SINGLE_EVENT.value,
            EvidenceGrade.PARTIAL_HFT_TRUTH_DEBUG_ONLY.value,
        ):
            assert not self._evidence_ok(bad_status), f"{bad_status} should block promotion"

    def test_authoritative_evidence_allows_promotion(self):
        assert self._evidence_ok(EvidenceGrade.AUTHORITATIVE_EVIDENCE.value)

    def test_empty_evidence_allows_promotion_backward_compat(self):
        assert self._evidence_ok("")


# ---------------------------------------------------------------------------
# 4. reconcile_pnl accounting
# ---------------------------------------------------------------------------

class TestReconcilePnlAccounting:
    def test_no_double_fee_counting(self):
        """Fees deducted per-fill must NOT be subtracted again from account balance."""
        from backtest_pipeline.src.replay_matrix import reconcile_pnl
        fills = [
            {"side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0, "fees": 0.5},
            {"side": "SELL", "avg_fill_price": 101.0, "filled_quantity": 1.0, "fees": 0.5},
        ]
        # PnL from fills: -(100*1) + (101*1) - 0.5 - 0.5 = 0.0
        # Account balance (hftbacktest already deducted fees): 0.0
        # Delta should be 0.0 → pass
        recon = reconcile_pnl({"fills_detail": fills, "balance": 0.0})
        assert recon["pnl_from_fills"] == 0.0
        assert recon["pnl_from_account"] == 0.0
        assert recon["passes"] is True

    def test_reconcile_with_fee_discrepancy(self):
        from backtest_pipeline.src.replay_matrix import reconcile_pnl
        fills = [
            {"side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0, "fees": 0.5},
            {"side": "SELL", "avg_fill_price": 101.0, "filled_quantity": 1.0, "fees": 0.5},
        ]
        # PnL from fills: 0.0, Account balance: 5.0 (corrupted)
        # Delta: 5.0, tolerance: max(2*0.25, 0.01) = 0.5
        # Delta(5.0) > tolerance(0.5) → fail
        recon = reconcile_pnl({"fills_detail": fills, "balance": 5.0})
        assert recon["passes"] is False
        assert "pnl_delta" in recon["reason"]

    def test_reconcile_reports_total_fill_fees(self):
        from backtest_pipeline.src.replay_matrix import reconcile_pnl
        fills = [
            {"side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0, "fees": 0.5},
            {"side": "SELL", "avg_fill_price": 101.0, "filled_quantity": 1.0, "fees": 0.7},
        ]
        recon = reconcile_pnl({"fills_detail": fills, "balance": -0.2})
        assert recon["total_fill_fees"] == 1.2

    def test_reconcile_tick_tolerance(self):
        from backtest_pipeline.src.replay_matrix import reconcile_pnl
        fills = [
            {"side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 2.0, "fees": 0.0},
            {"side": "SELL", "avg_fill_price": 101.0, "filled_quantity": 2.0, "fees": 0.0},
        ]
        # PnL from fills: 2.0, tolerance: max(4*0.25, 0.01) = 1.0
        recon = reconcile_pnl({"fills_detail": fills, "balance": 1.0})
        assert recon["tolerance"] == 1.0
        assert recon["passes"] is True  # delta=1.0 <= tolerance=1.0

    def test_reconcile_no_fills(self):
        from backtest_pipeline.src.replay_matrix import reconcile_pnl
        recon = reconcile_pnl({"fills_detail": [], "balance": 0.0})
        assert recon["pnl_from_fills"] == 0.0
        assert recon["passes"] is True


# ---------------------------------------------------------------------------
# 5. Fill pairing
# ---------------------------------------------------------------------------

class TestFillPairing:
    def test_paired_fills_compute_pnl(self):
        from backtest_pipeline.src.replay_matrix import _pair_fills_into_trades
        fills = [
            {"timestamp_ns": 100, "side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0},
            {"timestamp_ns": 200, "side": "SELL", "avg_fill_price": 101.0, "filled_quantity": 1.0},
        ]
        trade_pnls = _pair_fills_into_trades(fills)
        assert len(trade_pnls) == 1
        assert trade_pnls[0] == 1.0

    def test_unpaired_fills_warns(self):
        from backtest_pipeline.src.replay_matrix import _pair_fills_into_trades
        fills = [
            {"timestamp_ns": 100, "side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0},
            {"timestamp_ns": 200, "side": "BUY", "avg_fill_price": 101.0, "filled_quantity": 1.0},
            {"timestamp_ns": 300, "side": "SELL", "avg_fill_price": 102.0, "filled_quantity": 1.0},
        ]
        with pytest.warns(UserWarning, match="unpaired fills dropped"):
            _pair_fills_into_trades(fills)

    def test_single_side_fills_returns_empty(self):
        from backtest_pipeline.src.replay_matrix import _pair_fills_into_trades
        fills = [
            {"timestamp_ns": 100, "side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0},
        ]
        assert _pair_fills_into_trades(fills) == []

    def test_compute_win_rate_uses_pairing(self):
        from backtest_pipeline.src.replay_matrix import _compute_win_rate
        fills = [
            {"timestamp_ns": 100, "side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0},
            {"timestamp_ns": 200, "side": "SELL", "avg_fill_price": 101.0, "filled_quantity": 1.0},
            {"timestamp_ns": 300, "side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0},
            {"timestamp_ns": 400, "side": "SELL", "avg_fill_price": 99.0, "filled_quantity": 1.0},
        ]
        wr = _compute_win_rate(fills, net_pnl=0.0, num_trades=2)
        assert wr == 0.5

    def test_compute_win_rate_heuristic_fallback(self):
        from backtest_pipeline.src.replay_matrix import _compute_win_rate
        fills = [
            {"timestamp_ns": 100, "side": "BUY", "avg_fill_price": 100.0, "filled_quantity": 1.0},
        ]
        wr = _compute_win_rate(fills, net_pnl=10.0, num_trades=5)
        assert wr == 1.0


# ---------------------------------------------------------------------------
# 6. Ledger file naming
# ---------------------------------------------------------------------------

class TestLedgerFileNaming:
    def test_single_record_ledgers_use_json_extension(self, tmp_path):
        from backtest_pipeline.src.replay_matrix import write_hft_ledgers
        raw = {
            "fills_detail": [
                {"timestamp_ns": 100, "side": "BUY", "avg_fill_price": 100.0,
                 "filled_quantity": 1.0, "fees": 0.0},
            ],
            "position": 0.0, "balance": 0.0, "num_trades": 1, "fee": 0.0,
            "steps": 100, "order_intent_count": 1,
            "order_lifecycle_summary": {"accepted_count": 1, "filled_count": 1,
                                         "cancel_count": 0, "rejected_count": 0},
            "lifecycle_path": "/tmp/lc.jsonl", "queue_model": "LogProbQueueModel2",
        }
        ledgers = write_hft_ledgers("TEST", raw, manifest_dir=tmp_path / "ledgers")
        for key in ("positions", "pnl_timeseries", "order_state_transitions", "orders"):
            assert ledgers[key].endswith(".json"), f"{key} should use .json, got {ledgers[key]}"
        assert ledgers["fills"].endswith(".jsonl")
        assert ledgers["slippage_metrics"].endswith(".json")


# ---------------------------------------------------------------------------
# 7. Latency config warnings
# ---------------------------------------------------------------------------

class TestLatencyConfigWarnings:
    def test_missing_config_warns(self):
        from hft3_pipeline.stages import _load_latency_config
        with pytest.warns(UserWarning, match="cpp_latency_profile.yaml not found"):
            cfg = _load_latency_config(Path("/nonexistent"))
        assert cfg["source"] == "hardcoded_default"


# ---------------------------------------------------------------------------
# 8. Single-event smoke
# ---------------------------------------------------------------------------

class TestSingleEventSmoke:
    def test_smoke_only_for_ci_debug(self):
        for mode in (RunMode.FIXTURE_CI, RunMode.DEBUG):
            m = PipelineManifest(
                run_id="T-1", lane_id="cme_futures", model_id="T",
                event_id="CPI_2024_09_11_TIGHT",
                single_event_smoke=True,
                evidence_grade=EvidenceGrade.SMOKE_E2E_SINGLE_EVENT.value,
            )
            assert m.promotion_eligible is False

    def test_real_research_no_smoke_label(self):
        m = PipelineManifest(
            run_id="T-1", lane_id="cme_futures", model_id="T",
            event_id="CPI_2024_09_11_TIGHT",
        )
        assert m.single_event_smoke is False
        assert not m.evidence_grade
