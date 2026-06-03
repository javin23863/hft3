from __future__ import annotations

import json
import math

import pytest

from trade_manager.kill_switch import (
    KILL_SWITCH_ACTIONS,
    KILL_SWITCH_TRIGGERS,
    KillSwitchConfig,
    KillSwitchConfigError,
    KillSwitchContext,
    KillSwitchDecision,
    KillSwitchEvent,
    KillSwitchThresholds,
    evaluate_kill_switch,
    load_kill_switch_config,
)
from trade_manager.monitor import POSITION_STATUS_MISMATCH, POSITION_STATUS_UNKNOWN, PositionReconciliationResult


EXPECTED_TRIGGERS = (
    "max_daily_loss_breach",
    "max_drawdown_breach",
    "position_limit_breach",
    "runaway_order_rate",
    "runaway_cancel_rate",
    "stale_market_data",
    "broker_disconnect",
    "execution_adapter_failure",
    "position_mismatch",
    "fill_reconciliation_failure",
    "abnormal_slippage",
    "abnormal_latency",
)

EXPECTED_ACTIONS = (
    "stop_new_orders",
    "cancel_open_orders",
    "flatten_positions_if_configured",
    "disable_affected_model",
    "log_event",
    "create_incident_report",
    "update_observer_state",
)


class _RoutingAdapterGuard:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def submit_order(self, order_intent):  # pragma: no cover - regression guard
        self.calls.append("submit_order")
        raise AssertionError("Phase 21 kill switch must not submit orders")

    def cancel_order(self, order_id: str):  # pragma: no cover - regression guard
        self.calls.append("cancel_order")
        raise AssertionError("Phase 21 kill switch must not cancel orders")

    def replace_order(self, order_id: str, new_order_intent):  # pragma: no cover - regression guard
        self.calls.append("replace_order")
        raise AssertionError("Phase 21 kill switch must not replace orders")

    def flatten_positions(self):  # pragma: no cover - regression guard
        self.calls.append("flatten_positions")
        raise AssertionError("Phase 21 kill switch must not flatten positions")


def _config() -> KillSwitchConfig:
    return KillSwitchConfig(
        thresholds=KillSwitchThresholds(
            max_daily_loss=100.0,
            max_drawdown=50.0,
            max_position_abs=3.0,
            max_order_rate=10.0,
            max_cancel_rate=20.0,
            stale_market_data_max_ns=1_000,
            max_slippage_ticks=4.0,
            max_latency_ns=5_000,
        ),
        trigger_actions={
            "max_daily_loss_breach": ("stop_new_orders", "cancel_open_orders", "flatten_positions_if_configured"),
            "max_drawdown_breach": ("stop_new_orders", "cancel_open_orders"),
            "runaway_order_rate": ("stop_new_orders", "cancel_open_orders"),
            "runaway_cancel_rate": ("stop_new_orders", "log_event"),
            "position_mismatch": ("stop_new_orders", "cancel_open_orders", "flatten_positions_if_configured"),
        },
    )


def _quiet_context(**overrides) -> KillSwitchContext:
    values = {"timestamp_ns": 2_000, "last_market_data_ns": 1_500}
    values.update(overrides)
    return KillSwitchContext(**values)


def test_phase21_exact_trigger_and_action_inventories() -> None:
    assert KILL_SWITCH_TRIGGERS == EXPECTED_TRIGGERS
    assert KILL_SWITCH_ACTIONS == EXPECTED_ACTIONS
    shipped_config = load_kill_switch_config("configs/risk/kill_switch.yaml")
    shipped_actions = {action for actions in shipped_config.trigger_actions.values() for action in actions}
    assert set(shipped_config.trigger_actions) == set(EXPECTED_TRIGGERS)
    assert shipped_actions == set(EXPECTED_ACTIONS)


def test_phase21_loads_config_and_rejects_unknown_fields(tmp_path) -> None:
    path = tmp_path / "kill_switch.yaml"
    path.write_text(
        "thresholds:\n  max_daily_loss: 100\ntrigger_actions:\n  max_daily_loss_breach:\n    - stop_new_orders\n",
        encoding="utf-8",
    )

    config = load_kill_switch_config(path)

    assert config.thresholds.max_daily_loss == 100
    assert config.trigger_actions["max_daily_loss_breach"] == ("stop_new_orders",)
    with pytest.raises(KillSwitchConfigError, match="UNKNOWN_FIELDS"):
        KillSwitchConfig.from_dict({"thresholds": {}, "extra": True})
    with pytest.raises(KillSwitchConfigError, match="UNKNOWN_FIELDS"):
        KillSwitchConfig.from_dict({"thresholds": {"unknown_threshold": 1}})


def test_phase21_no_trigger_active_decision_is_inert() -> None:
    decision = evaluate_kill_switch(_quiet_context(), _config())

    assert decision.active is False
    assert decision.triggers == ()
    assert decision.requested_actions == ()
    assert decision.details == {"decision_only": True, "adapter_created": False, "routed": False}


def test_phase21_daily_loss_and_drawdown_triggers() -> None:
    decision = evaluate_kill_switch(_quiet_context(daily_loss=101.0, current_drawdown=51.0), _config())

    assert [event.trigger for event in decision.triggers] == ["max_daily_loss_breach", "max_drawdown_breach"]
    assert decision.requested_actions == ("stop_new_orders", "cancel_open_orders", "flatten_positions_if_configured")


def test_phase21_order_and_cancel_rate_triggers() -> None:
    decision = evaluate_kill_switch(_quiet_context(order_rate=11.0, cancel_rate=21.0), _config())

    assert [event.trigger for event in decision.triggers] == ["runaway_order_rate", "runaway_cancel_rate"]
    assert decision.requested_actions == ("stop_new_orders", "cancel_open_orders", "log_event")


def test_phase21_maps_phase20_mismatch_and_unknown_to_position_mismatch() -> None:
    mismatch = PositionReconciliationResult(
        timestamp_ns=2_000,
        status=POSITION_STATUS_MISMATCH,
        mismatches=[{"symbol": "ES", "reason": "POSITION_MISMATCH"}],
        max_abs_mismatch=1.0,
    )
    unknown = PositionReconciliationResult(
        timestamp_ns=2_000,
        status=POSITION_STATUS_UNKNOWN,
        mismatches=[{"reason": "POSITION_SNAPSHOT_MISSING"}],
        max_abs_mismatch=0.0,
    )

    mismatch_decision = evaluate_kill_switch(_quiet_context(position_reconciliation=mismatch), _config())
    unknown_decision = evaluate_kill_switch(_quiet_context(position_reconciliation=unknown), _config())

    assert [event.trigger for event in mismatch_decision.triggers] == ["position_mismatch"]
    assert mismatch_decision.triggers[0].details["status"] == "MISMATCH"
    assert [event.trigger for event in unknown_decision.triggers] == ["position_mismatch"]
    assert unknown_decision.triggers[0].details["status"] == "UNKNOWN"
    assert unknown_decision.triggers[0].details["fail_closed"] is True


def test_phase21_requested_actions_only_and_fake_adapter_never_called() -> None:
    adapter = _RoutingAdapterGuard()
    decision = evaluate_kill_switch(_quiet_context(daily_loss=101.0), _config())

    assert decision.requested_actions == ("stop_new_orders", "cancel_open_orders", "flatten_positions_if_configured")
    assert adapter.calls == []


def test_phase21_invalid_trigger_action_config_fails_closed() -> None:
    with pytest.raises(KillSwitchConfigError, match="TRIGGER_ACTIONS_INVALID"):
        KillSwitchConfig(trigger_actions={"unknown_trigger": ("stop_new_orders",)})
    with pytest.raises(KillSwitchConfigError, match="TRIGGER_ACTIONS_INVALID"):
        KillSwitchConfig(trigger_actions={"max_daily_loss_breach": ("unknown_action",)})
    with pytest.raises(KillSwitchConfigError, match="TRIGGER_ACTIONS_INVALID"):
        KillSwitchConfig(trigger_actions={"max_daily_loss_breach": ()})
    with pytest.raises(KillSwitchConfigError, match="THRESHOLD_NEGATIVE"):
        KillSwitchConfig(thresholds=KillSwitchThresholds(max_daily_loss=-1.0))
    with pytest.raises(KillSwitchConfigError, match="must be finite"):
        KillSwitchConfig(thresholds=KillSwitchThresholds(max_drawdown=math.inf))


def test_phase21_stale_missing_future_duplicate_data_do_not_pass_silently() -> None:
    missing = evaluate_kill_switch(KillSwitchContext(timestamp_ns=2_000), _config())
    stale = evaluate_kill_switch(_quiet_context(last_market_data_ns=500), _config())
    future = evaluate_kill_switch(_quiet_context(last_market_data_ns=3_000), _config())
    duplicate = evaluate_kill_switch(_quiet_context(duplicate_market_data_count=1), _config())

    assert missing.triggers[0].details["reason"] == "MARKET_DATA_TIMESTAMP_MISSING"
    assert stale.triggers[0].trigger == "stale_market_data"
    assert future.triggers[0].details["reason"] == "MARKET_DATA_TIMESTAMP_FROM_FUTURE"
    assert duplicate.triggers[0].details["reason"] == "MARKET_DATA_DUPLICATE"


def test_phase21_rejects_invalid_context_timestamps() -> None:
    with pytest.raises(ValueError, match="timestamp_ns"):
        KillSwitchContext(timestamp_ns=-1)
    with pytest.raises(ValueError, match="last_market_data_ns"):
        KillSwitchContext(timestamp_ns=1, last_market_data_ns=-1)


def test_phase21_rejects_non_boolean_context_flags() -> None:
    with pytest.raises(ValueError, match="broker_connected"):
        KillSwitchContext(timestamp_ns=1, last_market_data_ns=1, broker_connected="False")
    with pytest.raises(ValueError, match="execution_adapter_ok"):
        KillSwitchContext(timestamp_ns=1, last_market_data_ns=1, execution_adapter_ok="False")
    with pytest.raises(ValueError, match="fill_reconciliation_ok"):
        KillSwitchContext(timestamp_ns=1, last_market_data_ns=1, fill_reconciliation_ok="False")
    with pytest.raises(ValueError, match="position_reconciliation"):
        KillSwitchContext(timestamp_ns=1, last_market_data_ns=1, position_reconciliation="UNKNOWN")


def test_phase21_serialization_is_json_safe() -> None:
    event = KillSwitchEvent(100, "max_daily_loss_breach", {"daily_loss": 101.0})
    decision = KillSwitchDecision(
        timestamp_ns=100,
        active=True,
        triggers=(event,),
        requested_actions=("stop_new_orders", "log_event"),
        details={"decision_only": True},
    )

    payload = decision.to_dict()

    assert payload == {
        "timestamp_ns": 100,
        "active": True,
        "triggers": [{"timestamp_ns": 100, "trigger": "max_daily_loss_breach", "details": {"daily_loss": 101.0}}],
        "requested_actions": ["stop_new_orders", "log_event"],
        "details": {"decision_only": True},
    }
    assert json.loads(json.dumps(payload))["requested_actions"] == ["stop_new_orders", "log_event"]
    with pytest.raises(ValueError, match="active"):
        KillSwitchDecision(100, "False", (event,), ("stop_new_orders",), {})
    with pytest.raises(ValueError, match="triggers"):
        KillSwitchDecision(100, True, ({"trigger": "max_daily_loss_breach"},), ("stop_new_orders",), {})
    with pytest.raises(ValueError, match="details"):
        KillSwitchEvent(100, "max_daily_loss_breach", {"daily_loss": math.nan})
    with pytest.raises(ValueError, match="details"):
        KillSwitchDecision(100, True, (event,), ("stop_new_orders",), {"pnl": math.inf})
