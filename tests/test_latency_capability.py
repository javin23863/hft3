from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PACKAGES = _REPO / "packages"
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))

from tools.latency_baseline.recorder import build_latency_sample
from tools.latency_baseline.run import main as latency_baseline_main
from tools.latency_baseline.summary import build_summary, write_summary_reports
from trade_manager.latency_capability import (
    CapabilityAssumptions,
    LatencyCapabilityError,
    LocalOrderState,
    ModelInteractionMode,
    PendingExposureConfig,
    build_capability_report,
    classify_ack_lag,
    classify_internal_speed,
    local_state_after_send,
    state_after_external_message,
)


def _summary(tmp_path: Path, *, tick_to_send_us: int = 80, send_to_ack_us: int = 250_000) -> dict:
    record = build_latency_sample(
        run_id="cap1",
        environment="paper",
        broker="rithmic",
        venue="CME",
        symbol="ESM6",
        strategy_id="latency_probe",
        order_action="new",
        timestamps={
            "market_event_received_ts": 1_000_000,
            "decision_ready_ts": 1_010_000,
            "order_send_ts": 1_000_000 + tick_to_send_us * 1000,
            "ack_received_ts": 1_000_000 + tick_to_send_us * 1000 + send_to_ack_us * 1000,
        },
    )
    return build_summary([record], run_id="cap1", sample_path=tmp_path / "cap1.jsonl")


def test_latency_capability_separates_internal_speed_from_ack_lag(tmp_path: Path) -> None:
    summary = _summary(tmp_path, tick_to_send_us=80, send_to_ack_us=250_000)
    report = build_capability_report(
        summary,
        mode=ModelInteractionMode.CONCURRENT_OFFENSIVE_DEFENSIVE,
        assumptions=CapabilityAssumptions(
            opportunity_decay_us=100,
            competitor_tick_to_send_us=120,
            arbitration_latency_us=15,
            pending_exposure=PendingExposureConfig(stale_pending_timeout_us=100_000),
        ),
    )

    assert report["offensive_capability"]["operating_band"] == "microsecond_loop"
    assert report["evidence_status"] == "observed"
    assert report["blocking_reasons"] == []
    assert report["offensive_capability"]["opportunity_window_compatible"] is True
    assert report["offensive_capability"]["competitor_relation"] == "faster_than_assumed_competitor"
    assert report["external_confirmation_behavior"]["acknowledgment_lag_classification"] == "hundreds_of_milliseconds_or_slower_ack"
    assert report["risk_controls"]["stale_state_risk"] == "high"
    assert report["blocking_behavior"]["blocks_on_ack"] is False
    assert report["hybrid_configuration_capability"]["total_decision_to_action_latency_us"] == pytest.approx(85.0)


def test_pending_state_model_is_nonblocking_and_async() -> None:
    assert local_state_after_send("new") == LocalOrderState.PENDING_NEW
    assert local_state_after_send("cancel") == LocalOrderState.PENDING_CANCEL
    assert local_state_after_send("replace") == LocalOrderState.PENDING_REPLACE
    assert state_after_external_message(LocalOrderState.PENDING_NEW, "ack") == LocalOrderState.ACKED
    assert state_after_external_message(LocalOrderState.PENDING_CANCEL, "cancel_reject") == LocalOrderState.CANCEL_REJECTED
    assert state_after_external_message(LocalOrderState.PENDING_REPLACE, "replaced") == LocalOrderState.REPLACED
    with pytest.raises(LatencyCapabilityError, match="INVALID_EXTERNAL_STATE_TRANSITION"):
        state_after_external_message(LocalOrderState.PENDING_CANCEL, "fill")


def test_operating_bands_do_not_classify_ack_as_placement_speed() -> None:
    assert classify_internal_speed(99.9) == "microsecond_loop"
    assert classify_internal_speed(999.9) == "sub_millisecond_loop"
    assert classify_internal_speed(1_000.0) == "millisecond_loop"
    assert classify_ack_lag(244_000.0) == "hundreds_of_milliseconds_or_slower_ack"


def test_all_model_interaction_modes_are_validated(tmp_path: Path) -> None:
    summary = _summary(tmp_path)
    for mode in ModelInteractionMode:
        report = build_capability_report(summary, mode=mode)
        assert report["model_interaction_mode"] == mode.value
    with pytest.raises(LatencyCapabilityError, match="UNKNOWN_MODEL_INTERACTION_MODE"):
        build_capability_report(summary, mode="invented_mode")


def test_missing_evidence_and_bad_pending_limits_block_capability(tmp_path: Path) -> None:
    summary = {
        "schema_version": "latency_baseline_summary_v1",
        "run_id": "missing",
        "metrics": {
            "send_to_ack_us": {"p99_9_us": 250_000.0},
        },
    }
    report = build_capability_report(
        summary,
        assumptions=CapabilityAssumptions(
            pending_exposure=PendingExposureConfig(
                max_pending_orders=0,
                max_pending_quantity=0,
                duplicate_order_protection=False,
            )
        ),
    )
    assert report["evidence_status"] == "blocked"
    assert "TICK_TO_SEND_MISSING" in report["blocking_reasons"]
    assert "MAX_PENDING_ORDERS_LT_ONE" in report["blocking_reasons"]
    assert "DUPLICATE_ORDER_PROTECTION_DISABLED" in report["blocking_reasons"]
    assert report["risk_controls"]["status"] == "blocked"


def test_latency_summary_writer_emits_capability_report(tmp_path: Path) -> None:
    summary = _summary(tmp_path, tick_to_send_us=900, send_to_ack_us=2_000)
    summary["capability_inputs"] = {
        "model_interaction_mode": "defensive_pre_action_only",
        "opportunity_decay_us": 500,
        "defensive_activation_latency_us": 25,
        "pending_exposure": {"max_pending_orders": 2, "max_pending_quantity": 2},
    }
    json_path, _, _ = write_summary_reports(summary, reports_root=tmp_path / "reports" / "latency_baselines")
    written_summary = json.loads(json_path.read_text(encoding="utf-8"))
    cap_path = Path(written_summary["capability_report"]["json_path"])
    capability = json.loads(cap_path.read_text(encoding="utf-8"))
    assert capability["model_interaction_mode"] == "defensive_pre_action_only"
    assert capability["offensive_capability"]["operating_band"] == "sub_millisecond_loop"
    assert capability["offensive_capability"]["opportunity_window_compatible"] is False
    assert capability["hybrid_configuration_capability"]["arbitration_sequencing_latency_us"] == 25.0


def test_latency_baseline_cli_writes_capability_report_with_speed_assumptions(tmp_path: Path) -> None:
    rc = latency_baseline_main(
        [
            "--mode",
            "synthetic",
            "--repo-root",
            str(tmp_path),
            "--run-id",
            "cap-cli",
            "--duration",
            "1",
            "--samples",
            "9",
            "--interaction-mode",
            "hybrid_configuration",
            "--opportunity-decay-us",
            "50",
            "--competitor-tick-to-send-us",
            "40",
            "--hybrid-coordination-latency-us",
            "12",
            "--arbitration-latency-us",
            "5",
            "--max-pending-orders",
            "3",
        ]
    )
    assert rc == 0
    summary = json.loads((tmp_path / "reports" / "latency_baselines" / "cap-cli_summary.json").read_text())
    cap_path = Path(summary["capability_report"]["json_path"])
    capability = json.loads(cap_path.read_text(encoding="utf-8"))
    assert capability["model_interaction_mode"] == "hybrid_configuration"
    assert capability["assumptions"]["pending_exposure"]["max_pending_orders"] == 3
    assert capability["offensive_capability"]["competitor_relation"] == "slower_than_assumed_competitor"
    assert capability["blocking_behavior"]["blocks_on_ack"] is False
