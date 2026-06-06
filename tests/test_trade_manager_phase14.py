from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution import safety
from hft3.validation.certification_registry import PromotionRecord, save_promotion
from trade_manager import TradeManager, TradeManagerActivationError


def _write_manifest(root: Path, run_id: str = "RUN-1") -> Path:
    path = root / "artifacts" / "runs" / run_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "campaign_id": "phase14-campaign",
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
        registry_id="reg-phase14",
        model_id="HYP_5",
        candidate_id="HYP_5__candidate",
        experiment_id="exp-phase14",
        run_id=run_id,
        dataset_id="databento_es_mbo_v1",
        feature_set_id="core_64_v1",
        config_hash="abc123",
        git_commit="a88321c4629ad22cb5f453cc5f691878d7474d95",
        timestamp="2026-06-03T12:00:00Z",
        promotion_status="PROMOTED",
        promotion_reason="phase14 handoff test",
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


def test_phase14_trade_manager_activates_promoted_record_with_manifest(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    save_promotion(_promotion(tmp_path), tmp_path)

    manager = TradeManager(tmp_path)
    active = manager.activate_model("HYP_5")

    assert active.activation_status == "ACTIVE"
    assert active.model_id == "HYP_5"
    assert active.manifest_path == str(manifest_path)
    assert active.allowed_symbols == ("ES",)
    assert active.to_dict()["manifest"]["run_id"] == "RUN-1"
    assert manager.active_models["HYP_5"] == active


def test_phase14_trade_manager_rejects_non_promoted_latest_record(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    save_promotion(_promotion(tmp_path, promotion_status="PROMOTED"), tmp_path)
    save_promotion(_promotion(tmp_path, promotion_status="QUARANTINED"), tmp_path)

    manager = TradeManager(tmp_path)
    with pytest.raises(TradeManagerActivationError) as excinfo:
        manager.activate_model("HYP_5")

    assert excinfo.value.reason == "PROMOTION_STATUS_NOT_PROMOTED"
    assert manager.active_models == {}


def test_phase14_trade_manager_requires_operational_registry_fields(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    save_promotion(_promotion(tmp_path, allowed_symbols=[], latency_profile={}), tmp_path)

    manager = TradeManager(tmp_path)
    with pytest.raises(TradeManagerActivationError) as excinfo:
        manager.activate_model("HYP_5")

    assert excinfo.value.reason == "PROMOTION_RECORD_INCOMPLETE"
    assert excinfo.value.missing_fields == ["allowed_symbols", "latency_profile"]
    assert manager.active_models == {}


def test_phase14_trade_manager_requires_manifest_evidence(tmp_path: Path) -> None:
    save_promotion(_promotion(tmp_path), tmp_path)

    manager = TradeManager(tmp_path)
    with pytest.raises(TradeManagerActivationError) as excinfo:
        manager.activate_model("HYP_5")

    assert excinfo.value.reason == "MANIFEST_NOT_FOUND"
    assert manager.active_models == {}


def test_phase14_trade_manager_promoted_records_use_latest_status(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "RUN-1")
    save_promotion(_promotion(tmp_path, model_id="HYP_5", run_id="RUN-1"), tmp_path)
    save_promotion(
        _promotion(tmp_path, model_id="HYP_1", run_id="RUN-2", promotion_status="REJECTED"),
        tmp_path,
    )
    save_promotion(_promotion(tmp_path, model_id="HYP_7", run_id="RUN-3"), tmp_path)
    save_promotion(
        _promotion(tmp_path, model_id="HYP_7", run_id="RUN-3", promotion_status="QUARANTINED"),
        tmp_path,
    )

    manager = TradeManager(tmp_path)

    assert [record.model_id for record in manager.promoted_records()] == ["HYP_5"]


def test_phase14_trade_manager_does_not_route_live_or_rithmic_orders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "REPLAY")
    safety.reset_counters()
    _write_manifest(tmp_path)
    save_promotion(_promotion(tmp_path), tmp_path)

    TradeManager(tmp_path).activate_model("HYP_5")

    assert safety.counter_snapshot() == {
        "live_broker_call_count": 0,
        "rithmic_order_call_count": 0,
    }
