from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution import safety
from execution.interfaces import OrderIntent
from hft3.validation.certification_registry import PromotionRecord, save_promotion
from trade_manager import ModelSignal, StaticSignalSource, TradeManager, TradeManagerSignalError


def _write_manifest(root: Path, run_id: str = "RUN-1") -> Path:
    path = root / "artifacts" / "runs" / run_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "campaign_id": "phase15-campaign",
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
        registry_id="reg-phase15",
        model_id="HYP_5",
        candidate_id="HYP_5__candidate",
        experiment_id="exp-phase15",
        run_id=run_id,
        dataset_id="databento_es_mbo_v1",
        feature_set_id="core_64_v1",
        config_hash="abc123",
        git_commit="a88321c4629ad22cb5f453cc5f691878d7474d95",
        timestamp="2026-06-03T12:00:00Z",
        promotion_status="PROMOTED",
        promotion_reason="phase15 signal ingress test",
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


def _manager_with_active_model(root: Path) -> TradeManager:
    _write_manifest(root)
    save_promotion(_promotion(root), root)
    manager = TradeManager(root)
    manager.activate_model("HYP_5")
    return manager


def _signal(**overrides) -> ModelSignal:
    base = dict(
        signal_id="sig-1",
        registry_id="reg-phase15",
        model_id="HYP_5",
        candidate_id="HYP_5__candidate",
        run_id="RUN-1",
        timestamp_ns=1_700_000_000_000_000_000,
        symbol="ES",
        side="BUY",
        strength=0.75,
        confidence=0.90,
        expected_edge=12.5,
        reason_code="PHASE15_TEST_SIGNAL",
        source_features_reference="features/snap-1.json",
        market_context={"event_context": "NORMAL", "regime_state": "NORMAL"},
        latency_profile={"decision_to_send_us": 80},
        signal_source="pytest_signal_source",
    )
    base.update(overrides)
    return ModelSignal(**base)


def test_phase15_binds_active_model_to_signal_source(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)
    source = StaticSignalSource(strength=0.8, confidence=0.9, expected_edge=10.0)

    manager.bind_signal_source("HYP_5", source)
    accepted = manager.evaluate_signal(
        "HYP_5",
        symbol="ES",
        timestamp_ns=1_700_000_000_000_000_000,
        context="NORMAL",
    )

    assert accepted.model_id == "HYP_5"
    assert accepted.registry_id == "reg-phase15"
    assert accepted.candidate_id == "HYP_5__candidate"
    assert accepted.run_id == "RUN-1"
    assert accepted.symbol == "ES"
    assert accepted.strength == 0.8
    assert manager.signals["HYP_5"] == [accepted]


def test_phase15_signal_ingress_rejects_inactive_model(tmp_path: Path) -> None:
    manager = TradeManager(tmp_path)

    with pytest.raises(TradeManagerSignalError) as excinfo:
        manager.ingest_signal("HYP_5", _signal())

    assert excinfo.value.reason == "MODEL_NOT_ACTIVE"
    assert manager.signals == {}


def test_phase15_signal_identity_must_match_active_model(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)

    with pytest.raises(TradeManagerSignalError) as excinfo:
        manager.ingest_signal("HYP_5", _signal(model_id="HYP_7", run_id="RUN-2"))

    assert excinfo.value.reason == "SIGNAL_INVALID"
    assert excinfo.value.invalid_fields == ["model_id", "run_id"]
    assert manager.signals == {}


def test_phase15_signal_symbol_must_be_allowed_by_registry(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)

    with pytest.raises(TradeManagerSignalError) as excinfo:
        manager.ingest_signal("HYP_5", _signal(symbol="NQ"))

    assert excinfo.value.reason == "SIGNAL_INVALID"
    assert excinfo.value.invalid_fields == ["symbol"]
    assert manager.signals == {}


def test_phase15_signal_validation_rejects_bad_fields(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)

    with pytest.raises(TradeManagerSignalError) as excinfo:
        manager.ingest_signal(
            "HYP_5",
            _signal(
                signal_id="",
                side="LONG",
                timestamp_ns=0,
                strength=float("nan"),
                confidence=1.5,
                expected_edge=float("inf"),
                latency_profile={},
            ),
        )

    assert excinfo.value.reason == "SIGNAL_INVALID"
    assert excinfo.value.invalid_fields == [
        "signal_id",
        "side",
        "timestamp_ns",
        "strength",
        "confidence",
        "expected_edge",
        "latency_profile",
    ]
    assert manager.signals == {}


def test_phase15_signal_validation_rejects_non_finite_timestamp(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)

    with pytest.raises(TradeManagerSignalError) as excinfo:
        manager.ingest_signal("HYP_5", _signal(timestamp_ns=float("nan")))

    assert excinfo.value.reason == "SIGNAL_INVALID"
    assert excinfo.value.invalid_fields == ["timestamp_ns"]
    assert manager.signals == {}


def test_phase15_signal_ingress_is_idempotent_by_signal_id(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)
    signal = _signal()

    manager.ingest_signal("HYP_5", signal)
    with pytest.raises(TradeManagerSignalError) as excinfo:
        manager.ingest_signal("HYP_5", signal)

    assert excinfo.value.reason == "SIGNAL_ALREADY_INGESTED"
    assert manager.signals["HYP_5"] == [signal]


def test_phase15_signal_envelope_has_no_order_intent_fields(tmp_path: Path) -> None:
    manager = _manager_with_active_model(tmp_path)
    signal = manager.ingest_signal("HYP_5", _signal(side="FLAT", strength=0.0))
    payload = signal.to_dict()

    assert not isinstance(signal, OrderIntent)
    assert signal.side == "FLAT"
    assert signal.reason_code == "PHASE15_TEST_SIGNAL"
    assert signal.source_features_reference == "features/snap-1.json"
    assert set(payload).isdisjoint(
        {
            "intent_id",
            "order_intent_id",
            "order_type",
            "price",
            "quantity",
            "time_in_force",
            "latency_budget_ms",
        }
    )


def test_phase15_signal_ingress_does_not_route_broker_or_rithmic_orders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_call(*args, **kwargs):
        raise AssertionError("Phase 15 signal ingress must not route execution")

    monkeypatch.setenv("EXECUTION_MODE", "REPLAY")
    monkeypatch.setattr("execution.adapter_factory.create_adapter", forbid_call)
    monkeypatch.setattr("execution.adapters.broker.BrokerAdapter.submit_order", forbid_call)
    safety.reset_counters()

    manager = _manager_with_active_model(tmp_path)
    manager.bind_signal_source("HYP_5", StaticSignalSource(strength=1.0, confidence=1.0))
    signal = manager.evaluate_signal(
        "HYP_5",
        symbol="ES",
        timestamp_ns=1_700_000_000_000_000_000,
    )

    assert not isinstance(signal, OrderIntent)
    assert safety.counter_snapshot() == {
        "broker_call_count": 0,
        "rithmic_order_call_count": 0,
    }
