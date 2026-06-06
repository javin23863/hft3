from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution import safety
from execution.interfaces import AccountState
from hft3.validation.certification_registry import PromotionRecord, save_promotion
from trade_manager import ModelSignal, TradeManager
from trade_manager.execution_boundary import (
    TradeManagerExecutionBoundaryError,
    TradeManagerExecutionConfig,
    load_execution_config,
)
from trade_manager.order_state import TradeManagerOrderState
from trade_manager.risk_layer import TradeManagerRiskConfig, TradeManagerRiskContext, TradeManagerRiskLayer


class _FakeAdapter:
    source_adapter = "phase19_fake_adapter"

    def get_position(self, symbol: str) -> float:
        return 0.0

    def get_account_state(self) -> AccountState:
        return AccountState()

    def submit_order(self, order_intent):  # pragma: no cover - called only on a routing regression
        raise AssertionError("Phase 19 execution boundary must not submit orders")

    def cancel_order(self, order_id: str):  # pragma: no cover - called only on a routing regression
        raise AssertionError("Phase 19 execution boundary must not cancel orders")

    def replace_order(self, order_id: str, new_order_intent):  # pragma: no cover - called only on a routing regression
        raise AssertionError("Phase 19 execution boundary must not replace orders")

    def get_order_status(self, order_id: str):
        return None

    def drain_order_events(self) -> list:
        return []

    def after_elapse(self, replay_time_ns: int) -> None:
        return None


def _write_manifest(root: Path, run_id: str = "RUN-1") -> Path:
    path = root / "artifacts" / "runs" / run_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": run_id, "campaign_id": "phase19-campaign"}), encoding="utf-8")
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
        registry_id="reg-phase19",
        model_id="HYP_5",
        candidate_id="HYP_5__candidate",
        experiment_id="exp-phase19",
        run_id=run_id,
        dataset_id="databento_es_mbo_v1",
        feature_set_id="core_64_v1",
        config_hash="abc123",
        git_commit="a88321c4629ad22cb5f453cc5f691878d7474d95",
        timestamp="2026-06-03T12:00:00Z",
        promotion_status="PROMOTED",
        promotion_reason="phase19 execution boundary test",
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
        registry_id="reg-phase19",
        model_id="HYP_5",
        candidate_id="HYP_5__candidate",
        run_id="RUN-1",
        timestamp_ns=1_700_000_000_000_000_000,
        symbol="ES",
        side="BUY",
        strength=0.75,
        confidence=0.90,
        expected_edge=12.5,
        reason_code="PHASE19_TEST_SIGNAL",
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
        strategy_id="phase19-strategy",
        quantity=1.0,
        order_type="LIMIT",
        limit_price=5123.25,
        time_in_force="DAY",
        risk_budget_id="risk-budget-1",
        execution_profile={"venue": "CME", "adapter": "none"},
    )
    return manager, intent


def _risk_layer() -> TradeManagerRiskLayer:
    return TradeManagerRiskLayer(TradeManagerRiskConfig(model_eligibility=("HYP_5",)))


def _context(intent) -> TradeManagerRiskContext:
    return TradeManagerRiskContext(
        adapter=_FakeAdapter(),
        execution_mode="LIVE",
        system_clock_ns=intent.timestamp + 1_000,
        exchange_clock_ns=intent.timestamp,
        last_market_data_ns=intent.timestamp + 500,
        bid_price=5123.00,
        ask_price=5123.25,
    )


def test_phase19_loads_execution_config_without_creating_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid_call(*args, **kwargs):
        raise AssertionError("Phase 19 config load must not create adapters")

    monkeypatch.setattr("execution.adapter_factory.create_adapter", forbid_call)

    config = load_execution_config(Path("configs/execution/adapter.yaml"))

    assert config.mode == "REPLAY"
    assert config.adapter == "hftbacktest_simulated_exchange"
    assert config.route_enabled is False


@pytest.mark.parametrize(
    ("raw", "invalid_field"),
    [
        ({"mode": "PAPER", "adapter": "live_broker"}, "adapter"),
        ({"mode": "LIVE", "adapter": "live_broker", "live_broker": ""}, "live_broker"),
        ({"mode": "REPLAY", "adapter": "hftbacktest_simulated_exchange", "route_enabled": True}, "route_enabled"),
        ({"mode": "SIM", "adapter": "hftbacktest_simulated_exchange"}, "mode"),
    ],
)
def test_phase19_execution_config_rejects_invalid_or_route_enabled(raw: dict, invalid_field: str) -> None:
    with pytest.raises(TradeManagerExecutionBoundaryError) as excinfo:
        TradeManagerExecutionConfig.from_dict(raw)

    assert invalid_field in excinfo.value.invalid_fields


def test_phase19_live_rithmic_is_metadata_only_when_route_disabled() -> None:
    config = TradeManagerExecutionConfig.from_dict(
        {"mode": "LIVE", "adapter": "live_broker", "live_broker": "rithmic", "route_enabled": False}
    )

    assert config.mode == "LIVE"
    assert config.live_broker == "rithmic"
    assert config.route_enabled is False


def test_phase19_direct_execution_config_construction_rejects_route_enabled() -> None:
    with pytest.raises(TradeManagerExecutionBoundaryError) as excinfo:
        TradeManagerExecutionConfig(route_enabled=True)

    assert "route_enabled" in excinfo.value.invalid_fields


@pytest.mark.parametrize(
    ("kwargs", "invalid_field"),
    [
        ({"mode": "SIM"}, "mode"),
        ({"adapter": "paper_broker"}, "adapter"),
        ({"live_broker": "rithmic"}, "live_broker"),
        ({"heartbeat_interval_sec": 0}, "heartbeat_interval_sec"),
        ({"heartbeat_interval_sec": True}, "heartbeat_interval_sec"),
        ({"route_enabled": "false"}, "route_enabled"),
    ],
)
def test_phase19_direct_execution_config_construction_rejects_invalid_values(
    kwargs: dict,
    invalid_field: str,
) -> None:
    with pytest.raises(TradeManagerExecutionBoundaryError) as excinfo:
        TradeManagerExecutionConfig(**kwargs)

    assert invalid_field in excinfo.value.invalid_fields


def test_phase19_execution_config_rejects_unknown_fields() -> None:
    with pytest.raises(TradeManagerExecutionBoundaryError) as excinfo:
        TradeManagerExecutionConfig.from_dict(
            {"mode": "REPLAY", "adapter": "hftbacktest_simulated_exchange", "submit": True}
        )

    assert "submit" in excinfo.value.invalid_fields


def test_phase19_execution_config_from_dict_rejects_non_object() -> None:
    with pytest.raises(TradeManagerExecutionBoundaryError) as excinfo:
        TradeManagerExecutionConfig.from_dict([])  # type: ignore[arg-type]

    assert excinfo.value.reason == "EXECUTION_CONFIG_NOT_OBJECT"


def test_phase19_load_execution_config_rejects_non_object_yaml(tmp_path: Path) -> None:
    for content in ("", "false", "0", "[]"):
        path = tmp_path / "adapter.yaml"
        path.write_text(content, encoding="utf-8")

        with pytest.raises(TradeManagerExecutionBoundaryError) as excinfo:
            load_execution_config(path)

        assert excinfo.value.reason == "EXECUTION_CONFIG_NOT_OBJECT"


def test_phase19_prepares_audit_boundary_without_sent_to_execution(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))
    transitions_before = list(manager.order_state_transitions["HYP_5"])

    boundary = manager.prepare_execution_boundary(
        "HYP_5", intent, load_execution_config(Path("configs/execution/adapter.yaml"))
    )

    assert boundary.risk_allowed is True
    assert boundary.order_state == "RISK_APPROVED"
    assert boundary.can_route is False
    assert boundary.route_enabled is False
    assert boundary.route_block_reason == "PHASE19_INERT_BOUNDARY"
    assert boundary.adapter_created is False
    assert boundary.adapter_instance is None
    assert TradeManagerOrderState.SENT_TO_EXECUTION not in [
        transition.state for transition in manager.order_state_transitions["HYP_5"]
    ]
    assert manager.order_state_transitions["HYP_5"] == transitions_before


def test_phase19_live_metadata_boundary_remains_non_routable_on_dev_workstation(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))
    config = TradeManagerExecutionConfig(
        mode="LIVE",
        adapter="live_broker",
        live_broker="rithmic",
        route_enabled=False,
        host_role="dev_workstation",
    )

    boundary = manager.prepare_execution_boundary("HYP_5", intent, config)

    assert boundary.config.mode == "LIVE"
    assert boundary.config.live_broker == "rithmic"
    assert boundary.config.host_role == "dev_workstation"
    assert boundary.can_route is False
    assert boundary.route_enabled is False
    assert boundary.adapter_created is False


def test_phase19_missing_or_rejected_risk_is_not_execution_eligible(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    missing = manager.prepare_execution_boundary(
        "HYP_5", intent, load_execution_config(Path("configs/execution/adapter.yaml"))
    )
    manager.evaluate_order_intent_risk(
        "HYP_5",
        intent,
        TradeManagerRiskLayer(TradeManagerRiskConfig(max_order_size=0.5)),
        _context(intent),
    )
    rejected = manager.prepare_execution_boundary(
        "HYP_5", intent, load_execution_config(Path("configs/execution/adapter.yaml"))
    )

    assert missing.route_block_reason == "ORDER_NOT_EXECUTION_ELIGIBLE"
    assert rejected.risk_allowed is False
    assert rejected.route_block_reason == "ORDER_NOT_EXECUTION_ELIGIBLE"


def test_phase19_boundary_payload_contains_no_adapter_instance(tmp_path: Path) -> None:
    manager, intent = _manager_with_intent(tmp_path)
    manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))

    payload = manager.prepare_execution_boundary(
        "HYP_5", intent, load_execution_config(Path("configs/execution/adapter.yaml"))
    ).to_dict()

    assert payload["adapter_created"] is False
    assert payload["adapter_instance"] is None
    assert payload["can_route"] is False
    assert payload["config"]["route_enabled"] is False


def test_phase19_trade_manager_does_not_expose_order_routing_methods() -> None:
    manager = TradeManager()

    assert not hasattr(manager, "submit_order")
    assert not hasattr(manager, "cancel_order")
    assert not hasattr(manager, "replace_order")


def test_phase19_execution_boundary_does_not_route_or_increment_counters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_call(*args, **kwargs):
        raise AssertionError("Phase 19 execution boundary must not route orders")

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
    manager.evaluate_order_intent_risk("HYP_5", intent, _risk_layer(), _context(intent))
    manager.prepare_execution_boundary("HYP_5", intent, load_execution_config(Path("configs/execution/adapter.yaml")))

    assert safety.counter_snapshot() == {
        "live_broker_call_count": 0,
        "rithmic_order_call_count": 0,
    }
