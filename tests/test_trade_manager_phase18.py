from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from execution import safety
from execution.interfaces import AccountState
from hft3.validation.certification_registry import PromotionRecord, save_promotion
from trade_manager import ModelSignal, TradeManager
from trade_manager.order_state import (
    ORDER_STATE_VALUES,
    TERMINAL_ORDER_STATES,
    OrderStateTransitionError,
    TradeManagerOrderState,
    TradeManagerOrderTransition,
    transition_from_risk_decision,
    validate_order_state_transition,
)
from trade_manager.risk_layer import TradeManagerRiskConfig, TradeManagerRiskContext, TradeManagerRiskLayer


EXPECTED_PHASE18_STATES = [
    "CREATED",
    "SENT_TO_RISK",
    "RISK_REJECTED",
    "RISK_APPROVED",
    "SENT_TO_EXECUTION",
    "ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCEL_REQUESTED",
    "CANCELLED",
    "REPLACE_REQUESTED",
    "REPLACED",
    "BROKER_REJECTED",
    "EXPIRED",
    "TIMED_OUT",
    "ERROR",
    "KILLED",
]


class _FakeAdapter:
    source_adapter = "phase18_fake_adapter"

    def __init__(self, *, connected: bool = True, position: float = 0.0, account: AccountState | None = None) -> None:
        self.connected = connected
        self.position = position
        self.account = account or AccountState()

    def is_connected(self) -> bool:
        return self.connected

    def get_position(self, symbol: str) -> float:
        return self.position

    def get_account_state(self) -> AccountState:
        return self.account

    def submit_order(self, order_intent):  # pragma: no cover - called only on a safety regression
        raise AssertionError("Phase 18 order state must not submit orders")

    def cancel_order(self, order_id: str):  # pragma: no cover - called only on a safety regression
        raise AssertionError("Phase 18 order state must not cancel orders")

    def replace_order(self, order_id: str, new_order_intent):  # pragma: no cover - called only on a safety regression
        raise AssertionError("Phase 18 order state must not replace orders")

    def get_order_status(self, order_id: str):
        return None

    def drain_order_events(self) -> list:
        return []

    def after_elapse(self, replay_time_ns: int) -> None:
        return None


def _write_manifest(root: Path, run_id: str = "RUN-1") -> Path:
    path = root / "artifacts" / "runs" / run_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "campaign_id": "phase18-campaign",
                "artifacts": {"report": "report.md"},
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_promotion_cards(root: Path) -> tuple[str, str]:
    cards_dir = root / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    model_card_path = cards_dir / "model_card.json"
    validation_card_path = cards_dir / "validation_card.json"
    model_card_path.write_text(
        json.dumps({"model_card": {"validation_card_id": "VALIDATION-1"}}),
        encoding="utf-8",
    )
    validation_card_path.write_text(
        json.dumps({"validation_card": {"validation_id": "VALIDATION-1"}}),
        encoding="utf-8",
    )
    return "cards/model_card.json", "cards/validation_card.json"


def _promotion(root: Path, **overrides) -> PromotionRecord:
    run_id = str(overrides.get("run_id", "RUN-1"))
    model_card_path, validation_card_path = _write_promotion_cards(root)
    base = dict(
        registry_id="reg-phase18",
        model_id="HYP_5",
        candidate_id="HYP_5__candidate",
        experiment_id="exp-phase18",
        run_id=run_id,
        dataset_id="databento_es_mbo_v1",
        feature_set_id="core_64_v1",
        config_hash="abc123",
        git_commit="a88321c4629ad22cb5f453cc5f691878d7474d95",
        timestamp="2026-06-03T12:00:00Z",
        promotion_status="PROMOTED",
        promotion_reason="phase18 order state test",
        passed_gates=["T0", "T1", "T2", "T3", "T4"],
        failed_gates=[],
        quarantined_warnings=[],
        backtest_metrics={"sharpe": 1.2},
        robustness_metrics={"passed": True},
        walk_forward_metrics={"status": "PASS"},
        walk_forward_correlation_metrics={"status": "PASS"},
        latency_profile={"decision_to_send_us": 80},
        execution_assumptions={"fill_model": "queue_position_aware"},
        data_resolution="L3_MBO",
        model_combination={"alpha_ids": ["HYP_5"]},
        alpha_components=["HYP_5"],
        defensive_components=[],
        hybrid_components=[],
        allowed_symbols=["ES"],
        allowed_instruments=["ES"],
        allowed_order_types=["limit"],
        risk_limits_reference="configs/risk/limits.yaml",
        capital_allocation_reference="configs/risk/capital.yaml",
        kill_switch_reference="configs/risk/kill_switch.yaml",
        model_card_path=model_card_path,
        validation_card_path=validation_card_path,
        report_path=f"artifacts/runs/{run_id}/report.md",
        artifact_path=f"artifacts/runs/{run_id}/manifest.json",
    )
    base.update(overrides)
    return PromotionRecord(**base)


def _signal(**overrides) -> ModelSignal:
    base = dict(
        signal_id="sig-1",
        registry_id="reg-phase18",
        model_id="HYP_5",
        candidate_id="HYP_5__candidate",
        run_id="RUN-1",
        timestamp_ns=1_700_000_000_000_000_000,
        symbol="ES",
        side="BUY",
        strength=0.75,
        confidence=0.90,
        expected_edge=12.5,
        reason_code="PHASE18_TEST_SIGNAL",
        source_features_reference="features/snap-1.json",
        market_context={"event_context": "NORMAL", "regime_state": "NORMAL"},
        latency_profile={"decision_to_send_us": 80},
        signal_source="pytest_signal_source",
    )
    base.update(overrides)
    return ModelSignal(**base)


def _manager_with_intent(root: Path) -> tuple[TradeManager, object]:
    _write_manifest(root)
    save_promotion(_promotion(root), root)
    manager = TradeManager(root)
    manager.activate_model("HYP_5")
    signal = manager.ingest_signal("HYP_5", _signal())
    intent = manager.create_order_intent(
        "HYP_5",
        signal,
        strategy_id="phase18-strategy",
        quantity=1.0,
        order_type="LIMIT",
        limit_price=5123.25,
        time_in_force="DAY",
        risk_budget_id="risk-budget-1",
        execution_profile={"venue": "CME", "adapter": "none"},
    )
    return manager, intent


def _risk_layer(**overrides) -> TradeManagerRiskLayer:
    values = {"model_eligibility": ("HYP_5",), **overrides}
    return TradeManagerRiskLayer(TradeManagerRiskConfig(**values))


def _context(intent, adapter: _FakeAdapter | None = None, **overrides) -> TradeManagerRiskContext:
    base = dict(
        adapter=adapter or _FakeAdapter(),
        execution_mode="LIVE",
        system_clock_ns=intent.timestamp + 1_000,
        exchange_clock_ns=intent.timestamp,
        last_market_data_ns=intent.timestamp + 500,
        bid_price=5123.00,
        ask_price=5123.25,
    )
    base.update(overrides)
    return TradeManagerRiskContext(**base)


def _states(manager: TradeManager, model_id: str = "HYP_5") -> list[TradeManagerOrderState]:
    return [transition.state for transition in manager.order_state_transitions.get(model_id, [])]


def test_phase18_order_state_schema_has_documented_17_states() -> None:
    assert ORDER_STATE_VALUES == tuple(EXPECTED_PHASE18_STATES)
    assert [state.value for state in TradeManagerOrderState] == EXPECTED_PHASE18_STATES
    assert TradeManagerOrderState.RISK_REJECTED in TERMINAL_ORDER_STATES
    assert TradeManagerOrderState.ERROR in TERMINAL_ORDER_STATES


def test_phase18_create_order_intent_records_created_state(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)

    transitions = manager.order_state_transitions["HYP_5"]
    assert _states(manager) == [TradeManagerOrderState.CREATED]
    assert transitions[0].order_intent_id == intent.order_intent_id
    assert transitions[0].reason == "ORDER_INTENT_CREATED"
    assert transitions[0].timestamp_ns > 0


def test_phase18_risk_approved_records_risk_state_without_execution(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    decision = manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))

    assert decision.allowed is True
    assert _states(manager) == [
        TradeManagerOrderState.CREATED,
        TradeManagerOrderState.SENT_TO_RISK,
        TradeManagerOrderState.RISK_APPROVED,
    ]
    assert TradeManagerOrderState.SENT_TO_EXECUTION not in _states(manager)
    assert manager.order_state_transitions["HYP_5"][-1].risk_reason == "RISK_APPROVED"


def test_phase18_risk_rejected_records_rejection_details(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(max_order_size=0.5),
        _context(intent),
    )

    final = manager.order_state_transitions["HYP_5"][-1]
    assert transition_from_risk_decision(decision) == TradeManagerOrderState.RISK_REJECTED
    assert _states(manager) == [
        TradeManagerOrderState.CREATED,
        TradeManagerOrderState.SENT_TO_RISK,
        TradeManagerOrderState.RISK_REJECTED,
    ]
    assert final.risk_allowed is False
    assert final.risk_reason == "ORDER_SIZE_LIMIT_EXCEEDED"
    assert final.details["reason"] == "ORDER_SIZE_LIMIT_EXCEEDED"


def test_phase18_re_risk_can_downgrade_prior_approval_before_execution(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    risk_layer = _risk_layer(max_clock_drift_ns=10)

    manager.evaluate_order_intent_risk("HYP_5", intent, risk_layer, _context(intent))
    second = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        risk_layer,
        _context(intent, exchange_clock_ns=intent.timestamp + 2_000),
    )

    assert second.allowed is False
    assert _states(manager) == [
        TradeManagerOrderState.CREATED,
        TradeManagerOrderState.SENT_TO_RISK,
        TradeManagerOrderState.RISK_APPROVED,
        TradeManagerOrderState.SENT_TO_RISK,
        TradeManagerOrderState.RISK_REJECTED,
    ]


def test_phase18_re_risk_same_approval_records_new_audit_sequence(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)

    first = manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))
    second = manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))

    assert first.allowed is True
    assert second.allowed is True
    assert _states(manager) == [
        TradeManagerOrderState.CREATED,
        TradeManagerOrderState.SENT_TO_RISK,
        TradeManagerOrderState.RISK_APPROVED,
        TradeManagerOrderState.SENT_TO_RISK,
        TradeManagerOrderState.RISK_APPROVED,
    ]


def test_phase18_re_risk_after_terminal_rejection_returns_rejection_and_records_error(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(max_order_size=0.5), _context(intent))

    decision = manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(max_order_size=0.5), _context(intent))

    assert decision.allowed is False
    assert manager.risk_decisions["HYP_5"][-1] == decision
    assert _states(manager) == [
        TradeManagerOrderState.CREATED,
        TradeManagerOrderState.SENT_TO_RISK,
        TradeManagerOrderState.RISK_REJECTED,
        TradeManagerOrderState.ERROR,
    ]
    assert manager.order_state_transitions["HYP_5"][-1].details == {
        "requested_state": "SENT_TO_RISK",
        "previous_state": "RISK_REJECTED",
    }


def test_phase18_terminal_rejection_cannot_be_re_risked_to_approved(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(max_order_size=0.5), _context(intent))

    with pytest.raises(OrderStateTransitionError) as excinfo:
        manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))

    assert excinfo.value.reason == "TERMINAL_STATE"
    assert _states(manager)[-1] == TradeManagerOrderState.ERROR
    assert manager.risk_decisions["HYP_5"][-1].allowed is False


def test_phase18_terminal_error_cannot_be_re_risked_to_approved(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    with pytest.raises(OrderStateTransitionError):
        manager.transition_order_state("HYP_5", intent, TradeManagerOrderState.FILLED, reason="invalid")

    with pytest.raises(OrderStateTransitionError) as excinfo:
        manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))

    assert excinfo.value.reason == "TERMINAL_STATE"
    assert _states(manager)[-1] == TradeManagerOrderState.ERROR


def test_phase18_transition_after_error_appends_another_error_and_raises(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    with pytest.raises(OrderStateTransitionError):
        manager.transition_order_state("HYP_5", intent, TradeManagerOrderState.FILLED, reason="invalid")

    with pytest.raises(OrderStateTransitionError) as excinfo:
        manager.transition_order_state("HYP_5", intent, TradeManagerOrderState.SENT_TO_RISK, reason="still invalid")

    assert excinfo.value.reason == "TERMINAL_STATE"
    assert _states(manager) == [
        TradeManagerOrderState.CREATED,
        TradeManagerOrderState.ERROR,
        TradeManagerOrderState.ERROR,
    ]


@pytest.mark.parametrize(
    ("timestamp_ns", "expected_reason"),
    [
        (0, "INVALID_TRANSITION_TIMESTAMP"),
        (-1, "INVALID_TRANSITION_TIMESTAMP"),
    ],
)
def test_phase18_transition_rejects_invalid_timestamps(
    tmp_path: Path,
    timestamp_ns: int,
    expected_reason: str,
) -> None:
    manager, intent = _manager_with_intent(tmp_path)

    with pytest.raises(OrderStateTransitionError) as excinfo:
        manager.transition_order_state(
            "HYP_5",
            intent,
            TradeManagerOrderState.SENT_TO_RISK,
            reason="bad timestamp",
            timestamp_ns=timestamp_ns,
        )

    assert excinfo.value.reason == expected_reason
    assert _states(manager) == [TradeManagerOrderState.CREATED, TradeManagerOrderState.ERROR]


def test_phase18_transition_rejects_backdated_timestamp(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    created = manager.order_state_transitions["HYP_5"][-1]

    with pytest.raises(OrderStateTransitionError) as excinfo:
        manager.transition_order_state(
            "HYP_5",
            intent,
            TradeManagerOrderState.SENT_TO_RISK,
            reason="backdated",
            timestamp_ns=created.timestamp_ns - 1,
        )

    assert excinfo.value.reason == "NON_MONOTONIC_TRANSITION_TIMESTAMP"
    assert manager.order_state_transitions["HYP_5"][-1].details["previous_timestamp_ns"] == created.timestamp_ns


def test_phase18_auto_timestamp_after_future_override_remains_monotonic(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    created = manager.order_state_transitions["HYP_5"][-1]
    future_timestamp = created.timestamp_ns + 1_000_000

    sent = manager.transition_order_state(
        "HYP_5",
        intent,
        TradeManagerOrderState.SENT_TO_RISK,
        reason="future timestamp accepted",
        timestamp_ns=future_timestamp,
    )
    approved = manager.transition_order_state(
        "HYP_5",
        intent,
        TradeManagerOrderState.RISK_APPROVED,
        reason="auto timestamp must not go backward",
    )

    assert sent.timestamp_ns == future_timestamp
    assert approved.timestamp_ns >= sent.timestamp_ns


def test_phase18_equal_timestamp_override_is_allowed_as_non_decreasing(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    created = manager.order_state_transitions["HYP_5"][-1]

    sent = manager.transition_order_state(
        "HYP_5",
        intent,
        TradeManagerOrderState.SENT_TO_RISK,
        reason="equal timestamp accepted",
        timestamp_ns=created.timestamp_ns,
    )

    assert sent.timestamp_ns == created.timestamp_ns


def test_phase18_sent_to_execution_state_is_inert_vocabulary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_call(*args, **kwargs):
        raise AssertionError("Phase 18 SENT_TO_EXECUTION state must not route orders")

    monkeypatch.setattr("execution.adapter_factory.create_adapter", forbid_call)
    monkeypatch.setattr("execution.adapters.paper_broker.PaperBrokerAdapter.submit_order", forbid_call)
    monkeypatch.setattr("execution.adapters.live_broker.LiveBrokerAdapter.submit_order", forbid_call)
    safety.reset_counters()
    manager, intent = _manager_with_intent(tmp_path)
    manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent, _FakeAdapter()))

    transition = manager.transition_order_state(
        "HYP_5",
        intent,
        TradeManagerOrderState.SENT_TO_EXECUTION,
        reason="future phase handoff marker",
    )

    assert transition.state == TradeManagerOrderState.SENT_TO_EXECUTION
    assert safety.counter_snapshot() == {
        "live_broker_call_count": 0,
        "rithmic_order_call_count": 0,
    }


def test_phase18_transitions_are_timestamped_monotonically(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))

    timestamps = [transition.timestamp_ns for transition in manager.order_state_transitions["HYP_5"]]
    assert timestamps == sorted(timestamps)
    assert all(timestamp > 0 for timestamp in timestamps)


def test_phase18_invalid_transition_records_error_event(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)

    with pytest.raises(OrderStateTransitionError) as excinfo:
        manager.transition_order_state(
            "HYP_5",
            intent,
            TradeManagerOrderState.FILLED,
            reason="adapter event is future phase",
        )

    assert excinfo.value.reason == "INVALID_STATE_TRANSITION"
    assert _states(manager) == [TradeManagerOrderState.CREATED, TradeManagerOrderState.ERROR]
    error = manager.order_state_transitions["HYP_5"][-1]
    assert error.details == {"requested_state": "FILLED", "previous_state": "CREATED"}


def test_phase18_terminal_state_rejects_follow_up_transition(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(max_order_size=0.5), _context(intent))

    with pytest.raises(OrderStateTransitionError) as excinfo:
        manager.transition_order_state(
            "HYP_5",
            intent,
            TradeManagerOrderState.SENT_TO_EXECUTION,
            reason="future execution phase",
        )

    assert excinfo.value.reason == "TERMINAL_STATE"
    assert _states(manager)[-2:] == [TradeManagerOrderState.RISK_REJECTED, TradeManagerOrderState.ERROR]


def test_phase18_unknown_or_tampered_intent_cannot_transition(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    unknown = replace(intent, order_intent_id="unknown-intent")
    tampered = replace(intent, quantity=0.5)

    with pytest.raises(OrderStateTransitionError) as unknown_exc:
        manager.transition_order_state("HYP_5", unknown, TradeManagerOrderState.SENT_TO_RISK, reason="bad")
    with pytest.raises(OrderStateTransitionError) as tampered_exc:
        manager.transition_order_state("HYP_5", tampered, TradeManagerOrderState.SENT_TO_RISK, reason="bad")

    assert unknown_exc.value.reason == "ORDER_INTENT_NOT_CREATED"
    assert tampered_exc.value.reason == "ORDER_INTENT_ENVELOPE_MISMATCH"
    assert _states(manager) == [TradeManagerOrderState.CREATED]


def test_phase18_validate_order_state_transition_is_inert_and_explicit() -> None:
    validate_order_state_transition(None, TradeManagerOrderState.CREATED)
    validate_order_state_transition(TradeManagerOrderState.CREATED, TradeManagerOrderState.SENT_TO_RISK)
    validate_order_state_transition(TradeManagerOrderState.SENT_TO_RISK, TradeManagerOrderState.RISK_APPROVED)

    with pytest.raises(OrderStateTransitionError):
        validate_order_state_transition(TradeManagerOrderState.CREATED, TradeManagerOrderState.FILLED)


def test_phase18_order_state_does_not_create_adapters_or_route_orders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_call(*args, **kwargs):
        raise AssertionError("Phase 18 order state must not create adapters or route orders")

    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    monkeypatch.setattr("execution.adapter_factory.create_adapter", forbid_call)
    monkeypatch.setattr("execution.adapters.paper_broker.PaperBrokerAdapter.submit_order", forbid_call)
    monkeypatch.setattr("execution.adapters.paper_broker.PaperBrokerAdapter.cancel_order", forbid_call)
    monkeypatch.setattr("execution.adapters.paper_broker.PaperBrokerAdapter.replace_order", forbid_call)
    monkeypatch.setattr("execution.adapters.live_broker.LiveBrokerAdapter.submit_order", forbid_call)
    monkeypatch.setattr("execution.adapters.live_broker.LiveBrokerAdapter.cancel_order", forbid_call)
    monkeypatch.setattr("execution.adapters.live_broker.LiveBrokerAdapter.replace_order", forbid_call)
    safety.reset_counters()

    manager, intent = _manager_with_intent(tmp_path)
    manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent, _FakeAdapter()))

    assert _states(manager)[-1] == TradeManagerOrderState.RISK_APPROVED
    assert safety.counter_snapshot() == {
        "live_broker_call_count": 0,
        "rithmic_order_call_count": 0,
    }


def test_phase18_transition_payload_is_audit_ready(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))

    payload = manager.order_state_transitions["HYP_5"][-1].to_dict()

    assert isinstance(manager.order_state_transitions["HYP_5"][-1], TradeManagerOrderTransition)
    assert payload["order_intent_id"] == intent.order_intent_id
    assert payload["previous_state"] == "SENT_TO_RISK"
    assert payload["state"] == "RISK_APPROVED"
    assert payload["risk_allowed"] is True
    assert payload["details"]["reason"] == "RISK_APPROVED"
