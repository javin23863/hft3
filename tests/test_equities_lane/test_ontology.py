"""Tests for ontology objects: validation, serialization, citation grounding."""
from __future__ import annotations

import pytest

from equities_lane.src.ontology.session_context import EquitySessionContext
from equities_lane.src.ontology.option_snapshot import (
    OptionContractAtDecision,
    OptionChainSnapshotAtDecision,
)
from equities_lane.src.ontology.payoff import (
    StockOptionPayoffComparison,
    StockOptionRouteDecision,
    ROUTE_STOCK_ONLY,
    ROUTE_NO_TRADE,
    VALID_ROUTES,
)
from equities_lane.src.ontology.feature_vector import StockOptionFeatureVector
from equities_lane.src.ontology.float_metadata import FloatMetadataAtSession
from equities_lane.src.ontology.citations import cite_claim, require_grounding


class TestOptionContractAtDecision:
    def test_valid_contract(self):
        c = OptionContractAtDecision(
            contract_symbol="TEST240315C00050000",
            underlying="TEST",
            strike=50.0,
            right="C",
            expiry="2024-03-15",
            listed_at_ts_ns=1700000000000000000,
            bid=2.0,
            ask=2.5,
            mid=2.25,
            iv=0.35,
            delta=0.55,
            gamma=0.08,
            dte_days=30,
        )
        c.validate()

    def test_invalid_right_rejected(self):
        with pytest.raises(ValueError, match="right must be C or P"):
            OptionContractAtDecision(
                contract_symbol="X", underlying="T", strike=50.0,
                right="A", expiry="2024-03-15",
                listed_at_ts_ns=100, bid=2.0, ask=2.5, mid=2.25,
                iv=0.3, delta=0.5, gamma=0.05, dte_days=30,
            ).validate()

    def test_zero_strike_rejected(self):
        with pytest.raises(ValueError, match="strike must be > 0"):
            OptionContractAtDecision(
                contract_symbol="X", underlying="T", strike=0.0,
                right="C", expiry="2024-03-15",
                listed_at_ts_ns=100, bid=2.0, ask=2.5, mid=2.25,
                iv=0.3, delta=0.5, gamma=0.05, dte_days=30,
            ).validate()

    def test_ask_lt_bid_rejected(self):
        with pytest.raises(ValueError, match="ask.*< bid"):
            OptionContractAtDecision(
                contract_symbol="X", underlying="T", strike=50.0,
                right="C", expiry="2024-03-15",
                listed_at_ts_ns=100, bid=3.0, ask=2.0, mid=2.5,
                iv=0.3, delta=0.5, gamma=0.05, dte_days=30,
            ).validate()

    def test_to_dict(self):
        c = OptionContractAtDecision(
            contract_symbol="X", underlying="T", strike=50.0,
            right="P", expiry="2024-03-15",
            listed_at_ts_ns=100, bid=1.0, ask=2.0, mid=1.5,
            iv=0.3, delta=-0.5, gamma=0.05, dte_days=30,
        )
        d = c.to_dict()
        assert d["right"] == "P"
        assert d["strike"] == 50.0


class TestOptionChainSnapshotAtDecision:
    def test_future_contract_rejected(self):
        c = OptionContractAtDecision(
            contract_symbol="X", underlying="T", strike=50.0,
            right="C", expiry="2024-03-15",
            listed_at_ts_ns=200, bid=1.0, ask=2.0, mid=1.5,
            iv=0.3, delta=0.5, gamma=0.05, dte_days=30,
        )
        with pytest.raises(ValueError, match="future leakage"):
            OptionChainSnapshotAtDecision(
                decision_timestamp_ns=100, spot=50.0,
                iv_atm=0.3, iv_term_atm=0.3, iv_skew_25d=0.02,
                gex_net=0.0, dex_net=0.0, call_wall_strike=60.0,
                put_wall_strike=40.0, pc_ratio_volume=0.5,
                num_quotes=1, coverage=1.0, contracts=(c,),
            ).validate()

    def test_valid_snapshot(self):
        snap = OptionChainSnapshotAtDecision(
            decision_timestamp_ns=300, spot=50.0,
            iv_atm=0.3, iv_term_atm=0.3, iv_skew_25d=0.02,
            gex_net=0.0, dex_net=0.0, call_wall_strike=60.0,
            put_wall_strike=40.0, pc_ratio_volume=0.5,
            num_quotes=0, coverage=1.0,
        )
        snap.validate()


class TestStockOptionPayoffComparison:
    def test_fill_prob_bounds(self):
        with pytest.raises(ValueError, match="fill_probability_stock"):
            StockOptionPayoffComparison(
                underlying_symbol="T", session_date="2024-01-15",
                decision_timestamp_ns=100, stock_expected_value=5.0,
                option_expected_value=2.0, stock_plus_option_expected_value=7.0,
                expected_slippage_stock=0.0, expected_slippage_option=0.0,
                spread_cost_stock=0.0, spread_cost_option=0.0,
                fill_probability_stock=1.5, fill_probability_option=0.5,
                latency_assumption_stock_us=0, latency_assumption_option_us=0,
                max_loss_stock=0, max_loss_option=0,
                convexity_exposure=0, gamma_exposure=0, delta_exposure=0,
                theta_decay_window_seconds=0, liquidity_score_stock=0,
                liquidity_score_option=0, borrow_shortability_constraint="long_only",
            ).validate()

    def test_negative_max_loss_rejected(self):
        with pytest.raises(ValueError, match="max_loss_stock"):
            StockOptionPayoffComparison(
                underlying_symbol="T", session_date="2024-01-15",
                decision_timestamp_ns=100, stock_expected_value=5.0,
                option_expected_value=2.0, stock_plus_option_expected_value=7.0,
                expected_slippage_stock=0.0, expected_slippage_option=0.0,
                spread_cost_stock=0.0, spread_cost_option=0.0,
                fill_probability_stock=0.5, fill_probability_option=0.5,
                latency_assumption_stock_us=0, latency_assumption_option_us=0,
                max_loss_stock=-1, max_loss_option=0,
                convexity_exposure=0, gamma_exposure=0, delta_exposure=0,
                theta_decay_window_seconds=0, liquidity_score_stock=0,
                liquidity_score_option=0, borrow_shortability_constraint="long_only",
            ).validate()


class TestStockOptionRouteDecision:
    def test_invalid_route_rejected(self):
        payoff = StockOptionPayoffComparison(
            underlying_symbol="T", session_date="2024-01-15",
            decision_timestamp_ns=100, stock_expected_value=5.0,
            option_expected_value=0.0, stock_plus_option_expected_value=5.0,
            expected_slippage_stock=0.0, expected_slippage_option=0.0,
            spread_cost_stock=0.0, spread_cost_option=0.0,
            fill_probability_stock=0.5, fill_probability_option=0.0,
            latency_assumption_stock_us=0, latency_assumption_option_us=0,
            max_loss_stock=0, max_loss_option=0,
            convexity_exposure=0, gamma_exposure=0, delta_exposure=0,
            theta_decay_window_seconds=0, liquidity_score_stock=0,
            liquidity_score_option=0, borrow_shortability_constraint="long_only",
        )
        with pytest.raises(ValueError, match="final_route_decision"):
            StockOptionRouteDecision(
                underlying_symbol="T", session_date="2024-01-15",
                decision_timestamp_ns=100, final_route_decision="INVALID",
                payoff=payoff, ontology_claim_ids=("x",),
            ).validate()

    def test_clean_without_claims_rejected(self):
        payoff = StockOptionPayoffComparison(
            underlying_symbol="T", session_date="2024-01-15",
            decision_timestamp_ns=100, stock_expected_value=5.0,
            option_expected_value=0.0, stock_plus_option_expected_value=5.0,
            expected_slippage_stock=0.0, expected_slippage_option=0.0,
            spread_cost_stock=0.0, spread_cost_option=0.0,
            fill_probability_stock=0.5, fill_probability_option=0.0,
            latency_assumption_stock_us=0, latency_assumption_option_us=0,
            max_loss_stock=0, max_loss_option=0,
            convexity_exposure=0, gamma_exposure=0, delta_exposure=0,
            theta_decay_window_seconds=0, liquidity_score_stock=0,
            liquidity_score_option=0, borrow_shortability_constraint="long_only",
        )
        with pytest.raises(ValueError, match="ontology_claim_id"):
            StockOptionRouteDecision(
                underlying_symbol="T", session_date="2024-01-15",
                decision_timestamp_ns=100, final_route_decision=ROUTE_STOCK_ONLY,
                payoff=payoff, ontology_claim_ids=(), leakage_status="CLEAN",
            ).validate()

    def test_rejected_without_reason_rejected(self):
        payoff = StockOptionPayoffComparison(
            underlying_symbol="T", session_date="2024-01-15",
            decision_timestamp_ns=100, stock_expected_value=0.0,
            option_expected_value=0.0, stock_plus_option_expected_value=0.0,
            expected_slippage_stock=0.0, expected_slippage_option=0.0,
            spread_cost_stock=0.0, spread_cost_option=0.0,
            fill_probability_stock=0.0, fill_probability_option=0.0,
            latency_assumption_stock_us=0, latency_assumption_option_us=0,
            max_loss_stock=0, max_loss_option=0,
            convexity_exposure=0, gamma_exposure=0, delta_exposure=0,
            theta_decay_window_seconds=0, liquidity_score_stock=0,
            liquidity_score_option=0, borrow_shortability_constraint="long_only",
        )
        with pytest.raises(ValueError, match="rejection_reason required"):
            StockOptionRouteDecision(
                underlying_symbol="T", session_date="2024-01-15",
                decision_timestamp_ns=100, final_route_decision=ROUTE_NO_TRADE,
                payoff=payoff, leakage_status="REJECTED",
            ).validate()

    def test_to_dict(self):
        payoff = StockOptionPayoffComparison(
            underlying_symbol="T", session_date="2024-01-15",
            decision_timestamp_ns=100, stock_expected_value=5.0,
            option_expected_value=0.0, stock_plus_option_expected_value=5.0,
            expected_slippage_stock=0.0, expected_slippage_option=0.0,
            spread_cost_stock=0.0, spread_cost_option=0.0,
            fill_probability_stock=0.5, fill_probability_option=0.0,
            latency_assumption_stock_us=0, latency_assumption_option_us=0,
            max_loss_stock=0, max_loss_option=0,
            convexity_exposure=0, gamma_exposure=0, delta_exposure=0,
            theta_decay_window_seconds=0, liquidity_score_stock=0,
            liquidity_score_option=0, borrow_shortability_constraint="long_only",
        )
        dec = StockOptionRouteDecision(
            underlying_symbol="T", session_date="2024-01-15",
            decision_timestamp_ns=100, final_route_decision=ROUTE_NO_TRADE,
            payoff=payoff, leakage_status="REJECTED",
            rejection_reason="test",
        )
        d = dec.to_dict()
        assert d["final_route_decision"] == ROUTE_NO_TRADE
        assert d["leakage_status"] == "REJECTED"


class TestStockOptionFeatureVector:
    def test_not_pit_clean_rejected(self):
        with pytest.raises(ValueError, match="not point-in-time clean"):
            StockOptionFeatureVector(
                underlying_symbol="T", decision_timestamp_ns=100,
                equity_features={"a": 1.0}, option_features={"b": 2.0},
                combined_features={}, equity_data_source="test",
                option_data_source="test", equity_schema_used="mbo",
                option_schema_used="cbbo-1m", is_pit_clean=False,
            ).validate()

    def test_non_numeric_equity_feature_rejected(self):
        with pytest.raises(ValueError, match="not numeric"):
            StockOptionFeatureVector(
                underlying_symbol="T", decision_timestamp_ns=100,
                equity_features={"a": "bad"}, option_features={},
                combined_features={}, equity_data_source="test",
                option_data_source="test", equity_schema_used="mbo",
                option_schema_used="cbbo-1m", is_pit_clean=True,
            ).validate()

    def test_valid_feature_vector(self):
        fv = StockOptionFeatureVector(
            underlying_symbol="T", decision_timestamp_ns=100,
            equity_features={"ofi": 1.5}, option_features={"iv": 0.3},
            combined_features={"ofi_iv": 0.45}, equity_data_source="databento",
            option_data_source="opra", equity_schema_used="mbo",
            option_schema_used="cbbo-1m", is_pit_clean=True,
        )
        fv.validate()


class TestFloatMetadataAtSession:
    def test_forward_float_rejected(self):
        with pytest.raises(ValueError, match="point-in-time violation"):
            FloatMetadataAtSession(
                underlying_symbol="T", session_date="2024-01-10",
                as_of_date="2024-01-20", float_shares=1000,
                source="test", raw_path="/tmp/test.csv",
            ).validate()

    def test_valid_float(self):
        fm = FloatMetadataAtSession(
            underlying_symbol="T", session_date="2024-01-20",
            as_of_date="2024-01-10", float_shares=1000,
            source="test", raw_path="/tmp/test.csv",
        )
        fm.validate()

    def test_negative_float_rejected(self):
        with pytest.raises(ValueError, match="float_shares must be >= 0"):
            FloatMetadataAtSession(
                underlying_symbol="T", session_date="2024-01-20",
                as_of_date="2024-01-10", float_shares=-1,
                source="test", raw_path="/tmp/test.csv",
            ).validate()


class TestCitations:
    def test_cite_claim_requires_anchor(self):
        with pytest.raises(ValueError, match="no pdf or code_ref"):
            cite_claim(claim_id="x")

    def test_cite_claim_requires_claim_id(self):
        with pytest.raises(ValueError, match="claim_id is required"):
            cite_claim(claim_id="")

    def test_require_grounding_rejects_empty(self):
        with pytest.raises(ValueError, match="zero ontology citations"):
            require_grounding([], context="test")

    def test_require_grounding_rejects_missing_anchor(self):
        with pytest.raises(ValueError, match="no pdf or code_ref"):
            require_grounding([{"claim_id": "x"}], context="test")

    def test_valid_citation_passes(self):
        c = cite_claim(claim_id="x", pdf="test.pdf")
        require_grounding([c], context="test")


class TestEquitySessionContext:
    def test_non_mbo_schema_rejected(self):
        with pytest.raises(ValueError, match="L3-only"):
            EquitySessionContext(
                underlying_symbol="T", session_date="2024-01-15",
                decision_timestamp_ns=100, equity_data_source="test",
                equity_schema_used="mbp",
                equity_npz_path="/tmp/x.npz",
                equity_normalized_path="/tmp/x.ndjson",
                float_metadata_path="/tmp/float.csv",
                catalog_yaml_path="/tmp/cat.yaml",
            ).validate()

    def test_l3_only_false_rejected(self):
        import tempfile, os
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            npz = Path(td) / "x.npz"
            ndjson = Path(td) / "x.ndjson"
            npz.write_text("t", encoding="utf-8")
            ndjson.write_text("t", encoding="utf-8")
            with pytest.raises(ValueError, match="l3_only"):
                EquitySessionContext(
                    underlying_symbol="T", session_date="2024-01-15",
                    decision_timestamp_ns=100, equity_data_source="test",
                    equity_schema_used="mbo",
                    equity_npz_path=str(npz),
                    equity_normalized_path=str(ndjson),
                    float_metadata_path=str(Path(td) / "float.csv"),
                    catalog_yaml_path=str(Path(td) / "cat.yaml"),
                    l3_only=False,
                ).validate()

    def test_empty_symbol_rejected(self):
        with pytest.raises(ValueError, match="underlying_symbol required"):
            EquitySessionContext(
                underlying_symbol="", session_date="2024-01-15",
                decision_timestamp_ns=100, equity_data_source="test",
                equity_schema_used="mbo",
                equity_npz_path="/tmp/x.npz",
                equity_normalized_path="/tmp/x.ndjson",
                float_metadata_path="/tmp/float.csv",
                catalog_yaml_path="/tmp/cat.yaml",
            ).validate()
