from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from execution import safety
from execution.interfaces import AccountState
from hft3.validation.certification_registry import PromotionRecord, save_promotion
from trade_manager import ModelSignal, TradeManager
from trade_manager.risk_layer import (
    TradeManagerRiskConfig,
    TradeManagerRiskContext,
    TradeManagerRiskError,
    TradeManagerRiskLayer,
    load_risk_config,
)


class _FakeAdapter:
    source_adapter = "phase17_fake_adapter"

    def __init__(self, *, connected: bool = True, position: float = 0.0, account: AccountState | None = None) -> None:
        self.connected = connected
        self.position = position
        self.account = account or AccountState()
        self.account_state_calls = 0
        self.position_calls = 0

    def is_connected(self) -> bool:
        return self.connected

    def get_position(self, symbol: str) -> float:
        self.position_calls += 1
        return self.position

    def get_account_state(self) -> AccountState:
        self.account_state_calls += 1
        return self.account

    def submit_order(self, order_intent):  # pragma: no cover - called only on a safety regression
        raise AssertionError("Phase 17 risk evaluation must not submit orders")

    def cancel_order(self, order_id: str):  # pragma: no cover - called only on a safety regression
        raise AssertionError("Phase 17 risk evaluation must not cancel orders")

    def replace_order(self, order_id: str, new_order_intent):  # pragma: no cover - called only on a safety regression
        raise AssertionError("Phase 17 risk evaluation must not replace orders")

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
                "campaign_id": "phase17-campaign",
                "artifacts": {"report": "report.md"},
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    return path


def _promotion(root: Path, **overrides) -> PromotionRecord:
    run_id = str(overrides.get("run_id", "RUN-1"))
    base = dict(
        registry_id="reg-phase17",
        model_id="HYP_5",
        candidate_id="HYP_5__candidate",
        experiment_id="exp-phase17",
        run_id=run_id,
        dataset_id="databento_es_mbo_v1",
        feature_set_id="core_64_v1",
        config_hash="abc123",
        git_commit="a88321c4629ad22cb5f453cc5f691878d7474d95",
        timestamp="2026-06-03T12:00:00Z",
        promotion_status="PROMOTED",
        promotion_reason="phase17 risk layer test",
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
        report_path=f"artifacts/runs/{run_id}/report.md",
        artifact_path=f"artifacts/runs/{run_id}/manifest.json",
    )
    base.update(overrides)
    return PromotionRecord(**base)


def _manager_with_intent(
    root: Path,
    *,
    promotion_overrides: dict | None = None,
    signal_overrides: dict | None = None,
    order_overrides: dict | None = None,
) -> tuple[TradeManager, object]:
    _write_manifest(root)
    save_promotion(_promotion(root, **(promotion_overrides or {})), root)
    manager = TradeManager(root)
    manager.activate_model("HYP_5")
    signal = manager.ingest_signal("HYP_5", _signal(**(signal_overrides or {})))
    order_args = dict(
        strategy_id="phase17-strategy",
        quantity=1.0,
        order_type="LIMIT",
        limit_price=5123.25,
        time_in_force="DAY",
        risk_budget_id="risk-budget-1",
        execution_profile={"venue": "CME", "adapter": "none"},
    )
    order_args.update(order_overrides or {})
    intent = manager.create_order_intent(
        "HYP_5",
        signal,
        **order_args,
    )
    return manager, intent


def _signal(**overrides) -> ModelSignal:
    base = dict(
        signal_id="sig-1",
        registry_id="reg-phase17",
        model_id="HYP_5",
        candidate_id="HYP_5__candidate",
        run_id="RUN-1",
        timestamp_ns=1_700_000_000_000_000_000,
        symbol="ES",
        side="BUY",
        strength=0.75,
        confidence=0.90,
        expected_edge=12.5,
        reason_code="PHASE17_TEST_SIGNAL",
        source_features_reference="features/snap-1.json",
        market_context={"event_context": "NORMAL", "regime_state": "NORMAL"},
        latency_profile={"decision_to_send_us": 80},
        signal_source="pytest_signal_source",
    )
    base.update(overrides)
    return ModelSignal(**base)


def _context(intent, adapter: _FakeAdapter | None = None, **overrides) -> TradeManagerRiskContext:
    base = dict(
        adapter=adapter or _FakeAdapter(),
        execution_mode="LIVE",
        system_clock_ns=intent.timestamp + 1_000,
        exchange_clock_ns=intent.timestamp,
        last_market_data_ns=intent.timestamp + 500,
        local_inventory=0.0,
        carried_session_pnl_signed=0.0,
        open_order_count=0,
        bid_price=5123.00,
        ask_price=5123.25,
    )
    base.update(overrides)
    return TradeManagerRiskContext(**base)


def _risk_layer(**overrides) -> TradeManagerRiskLayer:
    values = {"model_eligibility": ("HYP_5",), **overrides}
    config = TradeManagerRiskConfig(**values)
    return TradeManagerRiskLayer(config)


def test_phase17_trade_manager_risk_approves_and_stores_inert_decision(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    adapter = _FakeAdapter()

    decision = manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent, adapter))

    assert decision.allowed is True
    assert decision.reason == "RISK_APPROVED"
    assert decision.action == "NONE"
    assert decision.order_intent_id == intent.order_intent_id
    assert manager.risk_decisions["HYP_5"] == [decision]
    assert adapter.account_state_calls == 1
    assert adapter.position_calls == 1


def test_phase17_loads_documented_risk_limit_config() -> None:
    config = load_risk_config(Path("configs/risk/limits.yaml"))

    assert config.max_order_size == 1
    assert config.symbol_eligibility == ("ES",)
    assert config.model_eligibility == ("HYP_5",)
    assert config.kill_switch_status == "armed"


def test_phase17_risk_rejects_inactive_model(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    manager.active_models["HYP_5"] = replace(manager.active_models["HYP_5"], activation_status="INACTIVE")

    with pytest.raises(TradeManagerRiskError) as excinfo:
        manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))

    assert excinfo.value.reason == "MODEL_NOT_ACTIVE"
    assert manager.risk_decisions == {}


def test_phase17_risk_requires_created_order_intent(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    unknown = replace(intent, order_intent_id="unknown-order-intent")

    with pytest.raises(TradeManagerRiskError) as excinfo:
        manager.evaluate_order_intent_risk("HYP_5", unknown, _risk_layer(), _context(intent))

    assert excinfo.value.reason == "ORDER_INTENT_NOT_CREATED"
    assert manager.risk_decisions == {}


def test_phase17_risk_rejects_tampered_order_intent_envelope(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    tampered = replace(intent, quantity=0.5)

    with pytest.raises(TradeManagerRiskError) as excinfo:
        manager.evaluate_order_intent_risk("HYP_5", tampered, _risk_layer(), _context(intent))

    assert excinfo.value.reason == "ORDER_INTENT_ENVELOPE_MISMATCH"
    assert manager.risk_decisions == {}


@pytest.mark.parametrize(
    ("risk_layer", "expected_reason"),
    [
        (_risk_layer(max_order_size=0.5), "ORDER_SIZE_LIMIT_EXCEEDED"),
        (_risk_layer(max_position_size=0.5), "POSITION_SIZE_LIMIT_EXCEEDED"),
        (_risk_layer(model_eligibility=("HYP_7",)), "MODEL_NOT_ELIGIBLE"),
        (_risk_layer(symbol_eligibility=("NQ",)), "SYMBOL_NOT_ELIGIBLE"),
        (_risk_layer(instrument_eligibility=("NQ",)), "INSTRUMENT_NOT_ELIGIBLE"),
        (_risk_layer(kill_switch_status="halted"), "KILL_SWITCH_NOT_ARMED"),
    ],
)
def test_phase17_risk_static_rejections_run_after_production_safety_allows(
    tmp_path: Path,
    risk_layer: TradeManagerRiskLayer,
    expected_reason: str,
) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    adapter = _FakeAdapter()

    decision = manager.evaluate_order_intent_risk("HYP_5", intent, risk_layer, _context(intent, adapter))

    assert decision.allowed is False
    assert decision.reason == expected_reason
    assert decision.action == "REJECT_ORDER"
    assert adapter.account_state_calls == 1
    assert adapter.position_calls == 1


@pytest.mark.parametrize(
    ("config_overrides", "context_overrides", "expected_reason"),
    [
        ({"max_gross_exposure": 1.5}, {"gross_exposure": 1.0}, "GROSS_EXPOSURE_LIMIT_EXCEEDED"),
        ({"max_net_exposure": 0.5}, {"net_exposure": 0.0}, "NET_EXPOSURE_LIMIT_EXCEEDED"),
        ({"max_drawdown": 50.0}, {"current_drawdown": 51.0}, "DRAWDOWN_LIMIT_EXCEEDED"),
        ({"max_open_orders": 1}, {"open_order_count": 1}, "OPEN_ORDER_LIMIT_EXCEEDED"),
        ({"max_order_rate": 1}, {"order_rate": 1}, "ORDER_RATE_LIMIT_EXCEEDED"),
        ({"max_cancel_rate": 1}, {"cancel_rate": 1}, "CANCEL_RATE_LIMIT_EXCEEDED"),
        ({"duplicate_order_check": True}, {"duplicate_order_intent_ids": "intent"}, "DUPLICATE_ORDER_INTENT"),
        ({"price_band_check": True, "price_band_ticks": 2.0}, {"reference_price": 5124.00}, "PRICE_BAND_EXCEEDED"),
        ({"liquidity_check": True}, {"has_liquidity": False}, "LIQUIDITY_UNAVAILABLE"),
        ({"spread_check": True}, {"bid_price": 5123.50, "ask_price": 5123.00}, "CROSSED_MARKET"),
        ({"spread_check": True, "max_spread_ticks": 1.0}, {"bid_price": 5123.00, "ask_price": 5123.50}, "SPREAD_LIMIT_EXCEEDED"),
    ],
)
def test_phase17_risk_configured_static_limits_are_enforced(
    tmp_path: Path,
    config_overrides: dict,
    context_overrides: dict,
    expected_reason: str,
) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    adapter = _FakeAdapter()
    if context_overrides.get("duplicate_order_intent_ids") == "intent":
        context_overrides = {**context_overrides, "duplicate_order_intent_ids": (intent.order_intent_id,)}

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(**config_overrides),
        _context(intent, adapter, **context_overrides),
    )

    assert decision.allowed is False
    assert decision.reason == expected_reason
    assert decision.action == "REJECT_ORDER"
    assert adapter.account_state_calls == 1
    assert adapter.position_calls == 1


def test_phase17_risk_configures_production_safety_monitors() -> None:
    layer = TradeManagerRiskLayer(
        TradeManagerRiskConfig(
            stale_data_max_ns=11,
            disconnect_grace_ns=13,
            max_clock_drift_ns=17,
            max_position_mismatch_contracts=0.25,
            max_daily_loss=19.0,
        )
    )

    assert layer.orchestrator.stale_data.max_stale_ns == 11
    assert layer.orchestrator.disconnect.disconnect_grace_ns == 13
    assert layer.orchestrator.clock_drift.max_drift_ns == 17
    assert layer.orchestrator.position_mismatch.max_mismatch == 0.25
    assert layer.orchestrator.daily_loss.loss_limit == 19.0


@pytest.mark.parametrize(
    ("risk_layer", "adapter", "context_overrides", "expected_monitor", "expected_action"),
    [
        (
            _risk_layer(max_daily_loss=5.0),
            _FakeAdapter(account=AccountState(unrealized_pnl=-6.0)),
            {},
            "DailyLossLimitFlatten",
            "FLATTEN_AND_HALT",
        ),
        (
            _risk_layer(max_position_mismatch_contracts=0.25),
            _FakeAdapter(position=1.0),
            {},
            "PositionMismatchGuard",
            "REJECT_ORDER",
        ),
    ],
)
def test_phase17_risk_surfaces_configured_production_safety_monitor_rejections(
    tmp_path: Path,
    risk_layer: TradeManagerRiskLayer,
    adapter: _FakeAdapter,
    context_overrides: dict,
    expected_monitor: str,
    expected_action: str,
) -> None:
    manager, intent = _manager_with_intent(tmp_path)

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        risk_layer,
        _context(intent, adapter, **context_overrides),
    )

    assert decision.allowed is False
    assert decision.reason == "PRODUCTION_SAFETY_REJECTED"
    assert decision.monitor_name == expected_monitor
    assert decision.action == expected_action


def test_phase17_risk_enforces_disconnect_grace_with_supplied_adapter(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    adapter = _FakeAdapter(connected=False)
    risk_layer = _risk_layer(disconnect_grace_ns=1)

    first = manager.evaluate_order_intent_risk("HYP_5", intent, risk_layer, _context(intent, adapter))
    second = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        risk_layer,
        _context(intent, adapter, system_clock_ns=intent.timestamp + 2_000),
    )

    assert first.allowed is True
    assert second.allowed is False
    assert second.reason == "PRODUCTION_SAFETY_REJECTED"
    assert second.monitor_name == "DisconnectMonitor"
    assert second.action == "CANCEL_ALL_AND_HALT"


def test_phase17_risk_enforces_clock_drift_limit(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    adapter = _FakeAdapter()
    risk_layer = _risk_layer(max_clock_drift_ns=10)

    first = manager.evaluate_order_intent_risk("HYP_5", intent, risk_layer, _context(intent, adapter))
    second = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        risk_layer,
        _context(intent, adapter, exchange_clock_ns=intent.timestamp + 2_000),
    )

    assert first.allowed is True
    assert second.allowed is False
    assert second.reason == "PRODUCTION_SAFETY_REJECTED"
    assert second.monitor_name == "ClockDriftMonitor"
    assert second.action == "REJECT_ORDER"


def test_phase17_risk_rejects_stale_signal_before_production_safety(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    adapter = _FakeAdapter()

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(stale_signal_max_ns=100),
        _context(intent, adapter, system_clock_ns=intent.timestamp + 101),
    )

    assert decision.allowed is False
    assert decision.reason == "STALE_SIGNAL"
    assert decision.details == {"signal_age_ns": 101}
    assert adapter.account_state_calls == 1
    assert adapter.position_calls == 1


def test_phase17_risk_surfaces_production_safety_rejection(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    adapter = _FakeAdapter()

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(),
        _context(intent, adapter, last_market_data_ns=0),
    )

    assert decision.allowed is False
    assert decision.reason == "PRODUCTION_SAFETY_REJECTED"
    assert decision.action == "REJECT_ORDER"
    assert decision.monitor_name == "StaleDataMonitor"
    assert decision.details["production_safety"]["halt_reason"] == "No market data received since start"
    assert adapter.account_state_calls == 1
    assert adapter.position_calls == 1


def test_phase17_default_context_enforces_production_safety(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    context = TradeManagerRiskContext(
        adapter=_FakeAdapter(),
        system_clock_ns=intent.timestamp + 1_000,
        exchange_clock_ns=intent.timestamp,
        last_market_data_ns=0,
        bid_price=5123.00,
        ask_price=5123.25,
    )

    decision = manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), context)

    assert decision.allowed is False
    assert decision.reason == "PRODUCTION_SAFETY_REJECTED"
    assert decision.monitor_name == "StaleDataMonitor"


def test_phase17_risk_allows_gross_exposure_reducing_order(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path, signal_overrides={"side": "SELL"})
    adapter = _FakeAdapter(position=3.0)

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(max_gross_exposure=3.0, max_net_exposure=3.0),
        _context(intent, adapter, local_inventory=3.0, gross_exposure=3.0, net_exposure=3.0),
    )

    assert decision.allowed is True
    assert decision.reason == "RISK_APPROVED"


def test_phase17_risk_allows_pure_reducing_order_larger_than_max_order_size(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(
        tmp_path,
        signal_overrides={"side": "SELL"},
        order_overrides={"quantity": 3.0},
    )
    adapter = _FakeAdapter(position=3.0)

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(max_order_size=1.0, max_gross_exposure=3.0, max_net_exposure=3.0),
        _context(intent, adapter, local_inventory=3.0, gross_exposure=3.0, net_exposure=3.0),
    )

    assert decision.allowed is True
    assert decision.reason == "RISK_APPROVED"


def test_phase17_risk_allows_context_instrument_when_it_matches_symbol(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(instrument_eligibility=("ES",)),
        _context(intent, instrument="ES"),
    )

    assert decision.allowed is True
    assert decision.reason == "RISK_APPROVED"


def test_phase17_risk_rejects_context_instrument_symbol_mismatch_before_adapter_calls(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path, promotion_overrides={"allowed_instruments": ["NQ"]})
    adapter = _FakeAdapter()

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(instrument_eligibility=("NQ",)),
        _context(intent, adapter, instrument="NQ"),
    )

    assert decision.allowed is False
    assert decision.reason == "INSTRUMENT_SYMBOL_MISMATCH"
    assert adapter.account_state_calls == 0
    assert adapter.position_calls == 0


def test_phase17_risk_requires_symbol_allowed_by_active_model_instruments(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path, promotion_overrides={"allowed_instruments": ["NQ"]})
    adapter = _FakeAdapter()

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(symbol_eligibility=("ES",), instrument_eligibility=("ES",)),
        _context(intent, adapter),
    )

    assert decision.allowed is False
    assert decision.reason == "INSTRUMENT_NOT_ALLOWED_BY_ACTIVE_MODEL"
    assert adapter.account_state_calls == 0
    assert adapter.position_calls == 0


def test_phase17_production_safety_daily_loss_overrides_static_reject(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    adapter = _FakeAdapter(account=AccountState(unrealized_pnl=-6.0))

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(max_daily_loss=5.0, max_order_size=0.5),
        _context(intent, adapter),
    )

    assert decision.allowed is False
    assert decision.reason == "PRODUCTION_SAFETY_REJECTED"
    assert decision.monitor_name == "DailyLossLimitFlatten"
    assert decision.action == "FLATTEN_AND_HALT"


def test_phase17_production_safety_disconnect_overrides_static_reject(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    adapter = _FakeAdapter(connected=False)
    risk_layer = _risk_layer(disconnect_grace_ns=1, max_order_size=0.5)

    manager.evaluate_order_intent_risk("HYP_5", intent, risk_layer, _context(intent, adapter))
    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        risk_layer,
        _context(intent, adapter, system_clock_ns=intent.timestamp + 2_000),
    )

    assert decision.allowed is False
    assert decision.reason == "PRODUCTION_SAFETY_REJECTED"
    assert decision.monitor_name == "DisconnectMonitor"
    assert decision.action == "CANCEL_ALL_AND_HALT"


def test_phase17_explicit_zero_gross_exposure_does_not_fall_back_to_local_inventory(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path, signal_overrides={"side": "SELL"})
    adapter = _FakeAdapter(position=3.0)

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(max_gross_exposure=0.0, max_net_exposure=10.0),
        _context(intent, adapter, local_inventory=3.0, gross_exposure=0.0, net_exposure=3.0),
    )

    assert decision.allowed is True
    assert decision.reason == "RISK_APPROVED"


def test_phase17_explicit_zero_net_exposure_does_not_fall_back_to_local_inventory(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    adapter = _FakeAdapter(position=3.0)

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        _risk_layer(max_position_size=10.0, max_gross_exposure=10.0, max_net_exposure=1.0),
        _context(intent, adapter, local_inventory=3.0, gross_exposure=0.0, net_exposure=0.0),
    )

    assert decision.allowed is True
    assert decision.reason == "RISK_APPROVED"


def test_phase17_risk_reapplies_monitor_config_after_orchestrator_reset(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    risk_layer = _risk_layer(stale_data_max_ns=1)
    risk_layer.orchestrator.reset_session()

    decision = manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        risk_layer,
        _context(intent, last_market_data_ns=intent.timestamp + 998),
    )

    assert risk_layer.orchestrator.stale_data.max_stale_ns == 1
    assert decision.allowed is False
    assert decision.reason == "PRODUCTION_SAFETY_REJECTED"
    assert decision.monitor_name == "StaleDataMonitor"


def test_phase17_risk_evaluation_does_not_create_adapters_or_route_orders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_call(*args, **kwargs):
        raise AssertionError("Phase 17 risk evaluation must not create adapters or route orders")

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
    decision = manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent, _FakeAdapter()))

    assert decision.allowed is True
    assert safety.counter_snapshot() == {
        "crypto_order_call_count": 0,
        "live_broker_call_count": 0,
        "rithmic_order_call_count": 0,
    }
