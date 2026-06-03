from __future__ import annotations

import json
import math

import pytest

from trade_manager.monitor import (
    POSITION_STATUS_MISMATCH,
    POSITION_STATUS_OK,
    POSITION_STATUS_UNKNOWN,
    ExpectedPosition,
    PositionMonitorConfig,
    PositionSnapshot,
    capture_position_snapshot,
    reconcile_positions,
)


class _ReadOnlyAdapter:
    def __init__(self) -> None:
        self.positions = {"ES": 2.0}
        self.account_state = {"cash": 1000.0}
        self.position_calls: list[str] = []
        self.account_state_calls = 0
        self.routing_calls: list[str] = []

    def get_position(self, symbol: str) -> float:
        self.position_calls.append(symbol)
        return self.positions[symbol]

    def get_account_state(self) -> dict[str, float]:
        self.account_state_calls += 1
        return self.account_state

    def submit_order(self, order_intent):  # pragma: no cover - regression guard
        self.routing_calls.append("submit_order")
        raise AssertionError("Phase 20 monitor must not submit orders")

    def cancel_order(self, order_id: str):  # pragma: no cover - regression guard
        self.routing_calls.append("cancel_order")
        raise AssertionError("Phase 20 monitor must not cancel orders")

    def replace_order(self, order_id: str, new_order_intent):  # pragma: no cover - regression guard
        self.routing_calls.append("replace_order")
        raise AssertionError("Phase 20 monitor must not replace orders")


def _config() -> PositionMonitorConfig:
    return PositionMonitorConfig(max_position_mismatch_contracts=0.5, stale_position_max_ns=1_000)


def test_phase20_snapshot_serialization_is_json_safe() -> None:
    snapshot = PositionSnapshot(
        timestamp_ns=100,
        source="unit_test",
        positions={"ES": 1.0},
        account_state={"cash": 1000.0},
    )

    payload = snapshot.to_dict()

    assert payload == {
        "timestamp_ns": 100,
        "source": "unit_test",
        "positions": {"ES": 1.0},
        "account_state": {"cash": 1000.0},
    }
    assert json.loads(json.dumps(payload))["positions"]["ES"] == 1.0


def test_phase20_reconciliation_matches_expected_position() -> None:
    snapshot = PositionSnapshot(100, "unit_test", {"ES": 2.0}, {"cash": 1000.0})
    expected = [ExpectedPosition("ES", 2.0, ("intent-1",))]

    result = reconcile_positions(timestamp_ns=150, snapshot=snapshot, expected_positions=expected, config=_config())

    assert result.status == POSITION_STATUS_OK
    assert result.mismatches == []
    assert result.max_abs_mismatch == 0.0


def test_phase20_reconciliation_flags_mismatch_over_threshold() -> None:
    snapshot = PositionSnapshot(100, "unit_test", {"ES": 1.0}, {"cash": 1000.0})
    expected = [ExpectedPosition("ES", 2.0, ("intent-1", "intent-2"))]

    result = reconcile_positions(timestamp_ns=150, snapshot=snapshot, expected_positions=expected, config=_config())

    assert result.status == POSITION_STATUS_MISMATCH
    assert result.max_abs_mismatch == 1.0
    assert result.mismatches == [
        {
            "symbol": "ES",
            "reason": "POSITION_MISMATCH",
            "expected": 2.0,
            "observed": 1.0,
            "abs_mismatch": 1.0,
            "source_order_intent_ids": ["intent-1", "intent-2"],
        }
    ]


def test_phase20_missing_observation_is_unknown_not_ok() -> None:
    snapshot = PositionSnapshot(100, "unit_test", {}, {"cash": 1000.0})
    expected = [ExpectedPosition("ES", 1.0, ("intent-1",))]

    result = reconcile_positions(timestamp_ns=150, snapshot=snapshot, expected_positions=expected, config=_config())

    assert result.status == POSITION_STATUS_UNKNOWN
    assert result.mismatches[0]["reason"] == "POSITION_OBSERVATION_MISSING"


def test_phase20_missing_snapshot_is_unknown_not_ok() -> None:
    expected = [ExpectedPosition("ES", 1.0, ("intent-1",))]

    result = reconcile_positions(timestamp_ns=150, snapshot=None, expected_positions=expected, config=_config())

    assert result.status == POSITION_STATUS_UNKNOWN
    assert result.mismatches == [{"reason": "POSITION_SNAPSHOT_MISSING"}]


def test_phase20_stale_snapshot_is_unknown_not_ok() -> None:
    snapshot = PositionSnapshot(100, "unit_test", {"ES": 2.0}, {"cash": 1000.0})
    expected = [ExpectedPosition("ES", 2.0, ("intent-1",))]

    result = reconcile_positions(timestamp_ns=1_200, snapshot=snapshot, expected_positions=expected, config=_config())

    assert result.status == POSITION_STATUS_UNKNOWN
    assert result.mismatches[0]["reason"] == "POSITION_SNAPSHOT_STALE"


def test_phase20_future_snapshot_is_unknown_not_ok() -> None:
    snapshot = PositionSnapshot(200, "unit_test", {"ES": 2.0}, {"cash": 1000.0})
    expected = [ExpectedPosition("ES", 2.0, ("intent-1",))]

    result = reconcile_positions(timestamp_ns=150, snapshot=snapshot, expected_positions=expected, config=_config())

    assert result.status == POSITION_STATUS_UNKNOWN
    assert result.mismatches[0]["reason"] == "POSITION_SNAPSHOT_FROM_FUTURE"


def test_phase20_duplicate_expected_symbol_is_unknown_not_ok() -> None:
    snapshot = PositionSnapshot(100, "unit_test", {"ES": 1.0}, {"cash": 1000.0})
    expected = [ExpectedPosition("ES", 1.0, ("intent-1",)), ExpectedPosition("ES", 1.0, ("intent-2",))]

    result = reconcile_positions(timestamp_ns=150, snapshot=snapshot, expected_positions=expected, config=_config())

    assert result.status == POSITION_STATUS_UNKNOWN
    assert result.mismatches == [{"symbol": "ES", "reason": "EXPECTED_POSITION_DUPLICATE"}]


def test_phase20_snapshot_uses_read_only_adapter_methods_only() -> None:
    adapter = _ReadOnlyAdapter()

    snapshot = capture_position_snapshot(timestamp_ns=100, source="adapter", symbols=("ES",), adapter=adapter)

    assert snapshot.positions == {"ES": 2.0}
    assert snapshot.account_state == {"cash": 1000.0}
    assert adapter.position_calls == ["ES"]
    assert adapter.account_state_calls == 1
    assert adapter.routing_calls == []


def test_phase20_rejects_invalid_monitor_config() -> None:
    with pytest.raises(ValueError, match="max_position_mismatch_contracts"):
        PositionMonitorConfig(max_position_mismatch_contracts=-1.0, stale_position_max_ns=1_000)
    with pytest.raises(ValueError, match="max_position_mismatch_contracts"):
        PositionMonitorConfig(max_position_mismatch_contracts=math.nan, stale_position_max_ns=1_000)
    with pytest.raises(ValueError, match="stale_position_max_ns"):
        PositionMonitorConfig(max_position_mismatch_contracts=1.0, stale_position_max_ns=-1)


def test_phase20_rejects_non_finite_position_values() -> None:
    with pytest.raises(ValueError, match="positions"):
        PositionSnapshot(100, "unit_test", {"ES": math.inf}, {"cash": 1000.0})
    with pytest.raises(ValueError, match="quantity"):
        ExpectedPosition("ES", math.nan, ("intent-1",))
