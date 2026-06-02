"""Imbalance integration tests (plan Phase 10)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from features_engine.src.features.mbo_features import MBOEvent, OrderBook
from features_engine.src.imbalance.ablation import all_ablation_modes, decide_promotion
from features_engine.src.imbalance.auction import (
    AuctionImbalanceEvent,
    AuctionImbalanceTracker,
    is_auction_window_phase,
)
from features_engine.src.imbalance.book import book_imbalance_ratio, compute_book_imbalance
from features_engine.src.imbalance.classification import (
    DataClass,
    data_class_label,
    resolve_data_class,
)
from features_engine.src.imbalance.engine import ImbalanceEngine
from features_engine.src.imbalance.mbp_book import MBP10Book
from features_engine.src.imbalance.order_flow import (
    OrderFlowImbalanceEngine,
    assert_no_true_ofi_when_insufficient,
)
from features_engine.src.imbalance.normalize import build_envelope, config_hash
from features_engine.src.imbalance.registry import load_imbalance_registry
from features_engine.src.imbalance.quality import run_quality_checks
from options_lane.src.imbalance_eligibility import EligibilityConfig, OptionQuote, option_imbalance_eligible

REPO = Path(__file__).resolve().parents[2]


def test_imbalance_inventory_exists():
    inv_path = REPO / "runtime" / "data_audits" / "hft3_imbalance_inventory.json"
    if not inv_path.is_file():
        import scripts.build_imbalance_inventory as bi

        bi.write_inventory(REPO)
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    assert inv["dataset_count"] > 0
    for row in inv["datasets"]:
        for p in row.get("config_paths", []):
            full = REPO / p
            if row.get("recommended_action") == "quarantine" and row.get("known_gaps"):
                continue
            assert full.is_file(), f"missing config {p}"


def test_book_imbalance_from_mbp10():
    book = MBP10Book()
    book.apply_level("B", 100.0, 50)
    book.apply_level("A", 100.25, 30)
    snap = compute_book_imbalance(book)
    assert snap.book_imbalance_l1 == pytest.approx((50 - 30) / 80.0)
    assert snap.microprice == pytest.approx((100.0 * 30 + 100.25 * 50) / 80.0)


def test_book_imbalance_from_mbo():
    ob = OrderBook()
    ob.apply_event(MBOEvent(timestamp_ns=1, action="ADD", side="B", price=100.0, size=40, order_id=1))
    ob.apply_event(MBOEvent(timestamp_ns=2, action="ADD", side="A", price=100.25, size=20, order_id=2))
    snap = compute_book_imbalance(ob)
    assert snap.book_imbalance_l1 == pytest.approx(20 / 60.0)


def test_mbp10_not_labeled_l3():
    label = data_class_label(DataClass.MBP_10)
    assert "Level 3" not in label or "not" in label.lower()
    assert "MBP-10" in label or "aggregated" in label


def test_order_flow_true_vs_proxy_labeling():
    true_eng = OrderFlowImbalanceEngine(DataClass.MBO)
    proxy_eng = OrderFlowImbalanceEngine(DataClass.MBP_10)
    assert true_eng.feature_family == "order_flow_imbalance"
    assert proxy_eng.feature_family == "order_flow_imbalance_proxy"


def test_no_ofi_when_source_insufficient():
    eng = OrderFlowImbalanceEngine(DataClass.TRADES)
    snap = eng.on_bbo(1000, 100.0, 10, 100.25, 10)
    assert math.isnan(snap.ofi_l1)
    with pytest.raises(ValueError):
        assert_no_true_ofi_when_insufficient(DataClass.MBP_1, 1.0)


def test_auction_imbalance_separate_family():
    eng = ImbalanceEngine(DataClass.MBO)
    snap = eng.on_auction_event(
        {
            "auction_type": "close",
            "imbalance_side": "buy",
            "paired_quantity": 1000,
            "total_imbalance_quantity": 500,
            "indicative_price": 101.0,
            "reference_price": 100.0,
            "ts_ns": 1,
        },
        window_phase="close",
    )
    assert snap.auction is not None
    assert snap.book is None


def test_auction_imbalance_window_alignment():
    tracker = AuctionImbalanceTracker()
    ev = AuctionImbalanceEvent.from_record(
        {"auction_type": "open", "imbalance_side": "sell", "paired_quantity": 1, "total_imbalance_quantity": 2, "ts_ns": 1}
    )
    with pytest.raises(ValueError):
        tracker.update(ev, window_phase="continuous")
    assert is_auction_window_phase("open")


def test_no_silent_schema_downgrade():
    res = resolve_data_class("mbo", available_schema="mbp-1", asset_class="equities", symbols=["GME"])
    assert res.was_downgraded
    assert res.downgrade_reason


def test_options_liquidity_eligibility():
    ok, _ = option_imbalance_eligible(
        OptionQuote(bid_px=1.0, ask_px=1.5, bid_sz=10, ask_sz=10),
        EligibilityConfig(max_spread_bps=100),
    )
    assert not ok
    ok2, _ = option_imbalance_eligible(OptionQuote(10.0, 10.05, 50, 50))
    assert ok2


def test_options_contract_lineage():
    env = build_envelope(
        asset_class="options",
        source="databento",
        venue="OPRA.PILLAR",
        instrument_id="SPXW240315C05500000",
        data_schema="mbp-1",
        data_class="MBP_1",
        feature_family="book_imbalance",
        feature_source="test",
        timestamp_event_ns=1,
        underlying_symbol="SPX",
        strike=5500.0,
        option_type="C",
        expiry="2024-03-15",
    )
    assert env.strike == 5500.0
    assert env.option_type == "C"


def test_futures_roll_metadata():
    env = build_envelope(
        asset_class="futures",
        source="databento",
        venue="GLBX.MDP3",
        instrument_id="MES.v.0",
        data_schema="mbo",
        data_class="MBO",
        feature_family="book_imbalance",
        feature_source="test",
        timestamp_event_ns=1,
        futures_contract_month="2024-06",
        roll_metadata={"front": "MESM4", "roll_date": "2024-06-10"},
    )
    assert env.roll_metadata["front"] == "MESM4"


def test_imbalance_feature_lineage():
    reg = load_imbalance_registry(REPO)
    assert reg["config_hash"]
    assert any(f.feature_name == "book_imbalance_l1" for f in reg["features"])


def test_imbalance_no_lookahead():
    report = run_quality_checks(
        timestamps_ns=[100, 200],
        spreads=[0.25, 0.25],
        book_states=["ok", "ok"],
        feature_timestamps_ns=[100, 200],
        has_future_leak=False,
    )
    assert report.passed
    bad = run_quality_checks(
        timestamps_ns=[100, 200],
        spreads=[0.25, 0.25],
        book_states=["ok", "ok"],
        feature_timestamps_ns=[100, 300],
    )
    assert not bad.passed


def test_imbalance_ablation_required():
    reg = load_imbalance_registry(REPO)
    assert reg["promotion"].get("require_ablation") is True
    assert len(all_ablation_modes()) == 8


def test_imbalance_latency_budget():
    reg = load_imbalance_registry(REPO)
    for f in reg["features"]:
        assert f.latency_estimate_ns >= 0


def test_reject_complexity_without_contribution():
    assert decide_promotion(-1.0, latency_ok=True) == "reject"
    assert decide_promotion(0.0) == "quarantine"


def test_imbalance_signal_from_vector_uses_catalog_slots():
    import numpy as np

    from features_engine.src.features.feature_index import FeatureIndex
    from features_engine.src.imbalance.ablation import all_ablation_modes
    from features_engine.src.imbalance.apply import imbalance_signal_from_vector
    from features_engine.src.hypotheses.modules import MarketState

    vec = np.zeros(64)
    vec[FeatureIndex.BOOK_IMBALANCE_L1] = 1.0
    book_mode = next(m for m in all_ablation_modes() if m.mode_id == "book_only")
    state = MarketState(
        primary_features={},
        cross_asset_features={},
        regime_state="NORMAL",
        event_context="NORMAL",
        volatility_state="NORMAL",
        liquidity_state="NORMAL",
        latency_ms=1.0,
        current_inventory=0,
        feature_vector=vec,
    )
    assert imbalance_signal_from_vector(state, book_mode) > 0.0
    baseline = next(m for m in all_ablation_modes() if m.mode_id == "baseline")
    assert imbalance_signal_from_vector(state, baseline) == 0.0


def test_ablation_mask_zeros_catalog_slots_only():
    import numpy as np

    from features_engine.src.features.feature_index import FeatureIndex
    from features_engine.src.imbalance.ablation import all_ablation_modes
    from features_engine.src.imbalance.apply import mask_imbalance_catalog_slots

    vec = np.zeros(64)
    vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] = 1.0
    vec[FeatureIndex.BOOK_IMBALANCE_L1] = 0.5
    vec[FeatureIndex.BOOK_SLOPE] = 0.3
    baseline = next(m for m in all_ablation_modes() if m.mode_id == "baseline")
    mask_imbalance_catalog_slots(vec, baseline)
    assert vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] == 0.0
    assert vec[FeatureIndex.BOOK_IMBALANCE_L1] == 0.0
    assert vec[FeatureIndex.BOOK_SLOPE] == pytest.approx(0.3)


def test_quality_report_fails_closed_without_snapshots():
    from workbench.src.imbalance.quality_report import build_quality_report_from_snapshots

    report = build_quality_report_from_snapshots([])
    assert report["passed"] is False


def test_shared_book_no_double_apply():
    from features_engine.src.features.mbo_features import MBOEvent, OrderBook
    from features_engine.src.imbalance.engine import ImbalanceEngine
    from features_engine.src.imbalance.classification import DataClass

    book = OrderBook()
    eng = ImbalanceEngine(DataClass.MBO, shared_book=book)
    ev = MBOEvent(timestamp_ns=1, action="ADD", side="B", price=100.0, size=10, order_id=1)
    book.apply_event(ev)
    snap = eng.on_mbo_after_book(ev)
    assert snap.book is not None
    b1, _ = book.top_k_depth(1)
    assert b1 == 10


def test_ablation_wrapper_changes_signal():
    from features_engine.src.hypotheses.modules import BaseHypothesis, MarketState
    from features_engine.src.imbalance.ablation import all_ablation_modes
    from features_engine.src.imbalance.apply import wrap_hypothesis_for_ablation

    class _ConstHyp(BaseHypothesis):
        def __init__(self):
            super().__init__(1, "const")

        def evaluate(self, state: MarketState) -> float:
            return 0.1

    import numpy as np

    from features_engine.src.features.feature_index import FeatureIndex

    vec = np.zeros(64)
    vec[FeatureIndex.BOOK_IMBALANCE_L1] = 1.0
    vec[FeatureIndex.BOOK_IMBALANCE_L10] = 1.0
    vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] = 0.5
    state = MarketState(
        primary_features={},
        cross_asset_features={},
        regime_state="NORMAL",
        event_context="NORMAL",
        volatility_state="NORMAL",
        liquidity_state="NORMAL",
        latency_ms=1.0,
        current_inventory=0,
        feature_vector=vec,
        imbalance_snapshot={
            "book": {"book_imbalance_l1": 1.0, "book_imbalance_l10": 1.0},
            "order_flow": {"ofi_l1": 1.0, "signed_trade_pressure": 0.5},
        },
    )
    base = _ConstHyp()
    baseline_mode = next(m for m in all_ablation_modes() if m.mode_id == "baseline")
    all_mode = next(m for m in all_ablation_modes() if m.mode_id == "all_three")
    assert wrap_hypothesis_for_ablation(base, baseline_mode).evaluate(state) == 0.1
    boosted = wrap_hypothesis_for_ablation(base, all_mode).evaluate(state)
    assert boosted > 0.1


def test_mbp10_replay_fixture():
    from features_engine.src.imbalance.mbp_replay import replay_mbp10_file

    path = REPO / "tests" / "fixtures" / "imbalance_mbp10_sample.ndjson"
    snap = replay_mbp10_file(path)
    assert snap["book"]["book_imbalance_l1"] == pytest.approx((60 - 25) / 85.0, rel=1e-3)


def test_best_ablation_verdict():
    from features_engine.src.imbalance.ablation import AblationRunResult, best_ablation_verdict

    results = [
        AblationRunResult("baseline", 0.0, 0.0, 0.0, "quarantine"),
        AblationRunResult("book_only", 0.0, 1.0, 1.0, "promote"),
        AblationRunResult("all_three", 0.0, 0.5, 0.5, "promote"),
    ]
    verdict, best = best_ablation_verdict(results)
    assert verdict == "promote"
    assert best == "book_only"


def test_book_imbalance_zero_depth():
    assert math.isnan(book_imbalance_ratio(0, 0))
