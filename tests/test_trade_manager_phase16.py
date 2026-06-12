from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from execution import safety
from execution.interfaces import OrderIntent as ExecutionOrderIntent
from hft3.validation.certification_registry import PromotionRecord, save_promotion
from trade_manager import ModelSignal, TradeManager
from trade_manager.order_intent import OrderIntentValidationError, TradeManagerOrderIntent


EXPECTED_PHASE16_FIELDS = [
    "order_intent_id",
    "registry_id",
    "model_id",
    "strategy_id",
    "signal_id",
    "timestamp",
    "symbol",
    "side",
    "quantity",
    "order_type",
    "limit_price",
    "time_in_force",
    "expected_edge",
    "risk_budget_id",
    "reason_code",
    "execution_profile",
    "latency_profile",
    "source_features_reference",
]


def _write_manifest(root: Path, run_id: str = "RUN-1") -> Path:
    path = root / "artifacts" / "runs" / run_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "campaign_id": "phase16-campaign",
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
        registry_id="reg-phase16",
        model_id="HYP_5",
        candidate_id="HYP_5__candidate",
        experiment_id="exp-phase16",
        run_id=run_id,
        dataset_id="databento_es_mbo_v1",
        feature_set_id="core_64_v1",
        config_hash="abc123",
        git_commit="a88321c4629ad22cb5f453cc5f691878d7474d95",
        timestamp="2026-06-03T12:00:00Z",
        promotion_status="PROMOTED",
        promotion_reason="phase16 order intent test",
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


def _manager_with_active_model(root: Path) -> TradeManager:
    _write_manifest(root)
    save_promotion(_promotion(root), root)
    manager = TradeManager(root)
    manager.activate_model("HYP_5")
    return manager


def _signal(**overrides) -> ModelSignal:
    base = dict(
        signal_id="sig-1",
        registry_id="reg-phase16",
        model_id="HYP_5",
        candidate_id="HYP_5__candidate",
        run_id="RUN-1",
        timestamp_ns=1_700_000_000_000_000_000,
        symbol="ES",
        side="BUY",
        strength=0.75,
        confidence=0.90,
        expected_edge=12.5,
        reason_code="PHASE16_TEST_SIGNAL",
        source_features_reference="features/snap-1.json",
        market_context={"event_context": "NORMAL", "regime_state": "NORMAL"},
        latency_profile={"decision_to_send_us": 80},
        signal_source="pytest_signal_source",
    )
    base.update(overrides)
    return ModelSignal(**base)


def _order_args(**overrides) -> dict:
    base = dict(
        strategy_id="phase16-strategy",
        quantity=1.0,
        order_type="LIMIT",
        limit_price=5123.25,
        time_in_force="DAY",
        risk_budget_id="risk-budget-1",
        execution_profile={"venue": "CME", "adapter": "none"},
    )
    base.update(overrides)
    return base


def _ingest_signal(manager: TradeManager, signal: ModelSignal | None = None) -> ModelSignal:
    return manager.ingest_signal("HYP_5", signal or _signal())


def test_phase16_trade_manager_order_intent_schema_has_documented_18_fields() -> None:
    assert [field.name for field in fields(TradeManagerOrderIntent)] == EXPECTED_PHASE16_FIELDS
    assert TradeManagerOrderIntent is not ExecutionOrderIntent
    assert not issubclass(TradeManagerOrderIntent, ExecutionOrderIntent)


def test_phase16_converts_model_signal_to_trade_manager_order_intent(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)
    signal = _ingest_signal(manager)

    intent = manager.create_order_intent("HYP_5", signal, **_order_args())

    assert isinstance(intent, TradeManagerOrderIntent)
    assert not isinstance(intent, ExecutionOrderIntent)
    assert intent.order_intent_id == "sig-1:order-intent"
    assert intent.registry_id == signal.registry_id
    assert intent.model_id == signal.model_id
    assert intent.strategy_id == "phase16-strategy"
    assert intent.signal_id == signal.signal_id
    assert intent.timestamp == signal.timestamp_ns
    assert intent.symbol == signal.symbol
    assert intent.side == signal.side
    assert intent.quantity == 1.0
    assert intent.order_type == "LIMIT"
    assert intent.limit_price == 5123.25
    assert intent.time_in_force == "DAY"
    assert intent.expected_edge == signal.expected_edge
    assert intent.risk_budget_id == "risk-budget-1"
    assert intent.reason_code == signal.reason_code
    assert intent.execution_profile == {"venue": "CME", "adapter": "none"}
    assert intent.latency_profile == signal.latency_profile
    assert intent.source_features_reference == signal.source_features_reference
    assert manager.order_intents["HYP_5"] == [intent]


def test_phase16_flat_signal_does_not_create_order_intent(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)
    signal = _ingest_signal(manager, _signal(side="FLAT", strength=0.0))

    with pytest.raises(OrderIntentValidationError) as excinfo:
        manager.create_order_intent("HYP_5", signal, **_order_args())

    assert excinfo.value.reason == "SIGNAL_SIDE_NOT_ACTIONABLE"
    assert manager.order_intents == {}


def test_phase16_order_intent_validation_rejects_bad_required_fields(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)
    signal = _ingest_signal(manager)

    with pytest.raises(OrderIntentValidationError) as excinfo:
        manager.create_order_intent(
            "HYP_5",
            signal,
            **_order_args(
                strategy_id="",
                quantity=0,
                limit_price=float("nan"),
                time_in_force="",
                risk_budget_id="",
                execution_profile={},
            ),
        )

    assert excinfo.value.reason == "ORDER_INTENT_INVALID"
    assert set(excinfo.value.invalid_fields) == {
        "strategy_id",
        "quantity",
        "limit_price",
        "time_in_force",
        "risk_budget_id",
        "execution_profile",
    }
    assert manager.order_intents == {}


def test_phase16_rejects_order_type_not_allowed_by_registry(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)
    signal = _ingest_signal(manager)

    with pytest.raises(OrderIntentValidationError) as excinfo:
        manager.create_order_intent(
            "HYP_5",
            signal,
            **_order_args(order_type="MARKET", limit_price=None),
        )

    assert excinfo.value.reason == "ORDER_INTENT_INVALID"
    assert excinfo.value.invalid_fields == ["order_type"]
    assert manager.order_intents == {}


def test_phase16_order_intent_payload_has_no_execution_interface_shape(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)
    signal = _ingest_signal(manager)

    payload = manager.create_order_intent("HYP_5", signal, **_order_args()).to_dict()

    assert list(payload) == EXPECTED_PHASE16_FIELDS
    assert set(payload).isdisjoint(
        {
            "intent_id",
            "timestamp_ns",
            "price",
            "latency_budget_ms",
            "max_slippage_ticks",
            "feature_snapshot_id",
            "event_context",
            "regime_state",
            "risk_metadata",
            "reduce_only",
        }
    )


def test_phase16_order_intent_is_idempotent_by_signal_id(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)
    signal = _ingest_signal(manager)

    first = manager.create_order_intent("HYP_5", signal, **_order_args())
    with pytest.raises(OrderIntentValidationError) as excinfo:
        manager.create_order_intent("HYP_5", signal, **_order_args(order_intent_id="different"))

    assert excinfo.value.reason == "ORDER_INTENT_ALREADY_CREATED"
    assert manager.order_intents["HYP_5"] == [first]


def test_phase16_order_intent_requires_ingested_signal(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)

    with pytest.raises(OrderIntentValidationError) as excinfo:
        manager.create_order_intent("HYP_5", _signal(signal_id="not-ingested"), **_order_args())

    assert excinfo.value.reason == "SIGNAL_NOT_INGESTED"
    assert manager.order_intents == {}


def test_phase16_order_intent_rejects_tampered_same_id_signal(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)
    signal = _ingest_signal(manager)
    tampered = _signal(signal_id=signal.signal_id, side="SELL", expected_edge=999.0)

    with pytest.raises(OrderIntentValidationError) as excinfo:
        manager.create_order_intent("HYP_5", tampered, **_order_args())

    assert excinfo.value.reason == "SIGNAL_ENVELOPE_MISMATCH"
    assert manager.order_intents == {}


def test_phase16_order_intent_creation_does_not_call_risk_or_execution_adapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_call(*args, **kwargs):
        raise AssertionError("Phase 16 order-intent creation must not route execution or risk")

    monkeypatch.setenv("EXECUTION_MODE", "REPLAY")
    monkeypatch.setattr("execution.adapter_factory.create_adapter", forbid_call)
    monkeypatch.setattr("execution.adapters.paper_broker.PaperBrokerAdapter.submit_order", forbid_call)
    monkeypatch.setattr("execution.adapters.live_broker.LiveBrokerAdapter.submit_order", forbid_call)
    monkeypatch.setattr("execution.production_safety.ProductionSafetyOrchestrator.pre_trade_check", forbid_call)
    safety.reset_counters()

    manager = _manager_with_active_model(tmp_path)
    signal = _ingest_signal(manager)
    intent = manager.create_order_intent("HYP_5", signal, **_order_args())

    assert isinstance(intent, TradeManagerOrderIntent)
    assert not isinstance(intent, ExecutionOrderIntent)
    assert safety.counter_snapshot() == {
        "crypto_order_call_count": 0,
        "live_broker_call_count": 0,
        "rithmic_order_call_count": 0,
    }
