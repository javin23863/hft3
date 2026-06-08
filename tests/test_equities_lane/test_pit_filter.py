"""Tests for point-in-time filtration (pit_filter.py)."""
from __future__ import annotations

import pytest

from equities_lane.src.models import SessionTick
from equities_lane.src.integrity.pit_filter import (
    PITCheckResult,
    check_equity_ticks,
    check_option_quotes,
    check_option_contracts,
    check_float_metadata,
)


def _tick(ts_ns: int) -> SessionTick:
    return SessionTick(ts_ns=ts_ns, bid_px=10.0, bid_sz=100, ask_px=10.1, ask_sz=100)


class TestCheckEquityTicks:
    def test_clean_when_all_ticks_at_or_before_decision(self):
        ticks = [_tick(100), _tick(200), _tick(300)]
        result = check_equity_ticks(ticks, decision_ts_ns=300)
        assert result.is_pit_clean is True
        assert result.contamination_count == 0
        assert result.rejection_reason is None

    def test_rejects_future_tick(self):
        ticks = [_tick(100), _tick(400)]
        result = check_equity_ticks(ticks, decision_ts_ns=300)
        assert result.is_pit_clean is False
        assert result.contamination_count == 1
        assert "equity leakage" in result.rejection_reason
        assert len(result.contaminated_samples) == 1

    def test_multiple_future_ticks_counted(self):
        ticks = [_tick(100), _tick(301), _tick(302), _tick(303)]
        result = check_equity_ticks(ticks, decision_ts_ns=300)
        assert result.contamination_count == 3

    def test_max_samples_limits_samples(self):
        ticks = [_tick(301 + i) for i in range(20)]
        result = check_equity_ticks(ticks, decision_ts_ns=300, max_samples=2)
        assert result.contamination_count == 20
        assert len(result.contaminated_samples) == 2

    def test_empty_ticks_is_clean(self):
        result = check_equity_ticks([], decision_ts_ns=300)
        assert result.is_pit_clean is True

    def test_exactly_at_boundary_is_clean(self):
        ticks = [_tick(300)]
        result = check_equity_ticks(ticks, decision_ts_ns=300)
        assert result.is_pit_clean is True

    def test_one_ns_after_boundary_is_contaminated(self):
        ticks = [_tick(301)]
        result = check_equity_ticks(ticks, decision_ts_ns=300)
        assert result.is_pit_clean is False


class TestCheckOptionQuotes:
    def test_clean_quotes(self):
        quotes = [{"quote_ts_ns": 100, "symbol": "TEST"}, {"quote_ts_ns": 200}]
        result = check_option_quotes(quotes, decision_ts_ns=200)
        assert result.is_pit_clean is True

    def test_rejects_future_quote(self):
        quotes = [{"quote_ts_ns": 201, "symbol": "TEST"}]
        result = check_option_quotes(quotes, decision_ts_ns=200)
        assert result.is_pit_clean is False
        assert "option leakage" in result.rejection_reason

    def test_missing_ts_defaults_to_zero(self):
        quotes = [{"symbol": "TEST"}]
        result = check_option_quotes(quotes, decision_ts_ns=100)
        assert result.is_pit_clean is True

    def test_none_ts_defaults_to_zero(self):
        quotes = [{"quote_ts_ns": None, "symbol": "TEST"}]
        result = check_option_quotes(quotes, decision_ts_ns=100)
        assert result.is_pit_clean is True

    def test_max_samples(self):
        quotes = [{"quote_ts_ns": 201 + i, "symbol": f"T{i}"} for i in range(10)]
        result = check_option_quotes(quotes, decision_ts_ns=200, max_samples=3)
        assert result.contamination_count == 10
        assert len(result.contaminated_samples) == 3


class TestCheckOptionContracts:
    def test_clean_contracts(self):
        contracts = [{"contract_symbol": "A", "listed_at_ts_ns": 100}]
        result = check_option_contracts(contracts, decision_ts_ns=200)
        assert result.is_pit_clean is True

    def test_rejects_retroactive_contract(self):
        contracts = [{"contract_symbol": "A", "listed_at_ts_ns": 201}]
        result = check_option_contracts(contracts, decision_ts_ns=200)
        assert result.is_pit_clean is False
        assert "retroactive" in result.rejection_reason

    def test_missing_listed_defaults_to_zero(self):
        contracts = [{"contract_symbol": "A"}]
        result = check_option_contracts(contracts, decision_ts_ns=200)
        assert result.is_pit_clean is True


class TestCheckFloatMetadata:
    def test_clean_when_as_of_equals_session(self):
        result = check_float_metadata("2024-01-15", "2024-01-15")
        assert result.is_pit_clean is True

    def test_clean_when_as_of_before_session(self):
        result = check_float_metadata("2024-01-10", "2024-01-15")
        assert result.is_pit_clean is True

    def test_rejects_forward_float(self):
        result = check_float_metadata("2024-01-20", "2024-01-15")
        assert result.is_pit_clean is False
        assert "forward float" in result.rejection_reason

    def test_invalid_date_format(self):
        result = check_float_metadata("not-a-date", "2024-01-15")
        assert result.is_pit_clean is False
        assert "parse failure" in result.rejection_reason


class TestPITCheckResultToDict:
    def test_to_dict_keys(self):
        r = PITCheckResult(is_pit_clean=True, contamination_count=0, rejection_reason=None)
        d = r.to_dict()
        assert "is_pit_clean" in d
        assert "contamination_count" in d
        assert "rejection_reason" in d
        assert "contaminated_samples" in d
