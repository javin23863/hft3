from __future__ import annotations

import json
from pathlib import Path

from workbench.src.latency.operating_envelope import (
    aggregate_campaign_latency_envelopes,
    build_latency_operating_envelope,
    compact_envelope_fields,
    write_latency_operating_envelope,
)
from workbench.src.latency.viability import LatencyViability
from workbench.src.core.composition import DefensiveStub, ModelComposition
from workbench.src.sim.cpp_latency_profile import CppLatencyProfile, LatencyPercentilesUs


def _profile() -> CppLatencyProfile:
    return CppLatencyProfile(
        cpp_decision_compute=LatencyPercentilesUs(5.0, 8.0, 10.0, "test_cpp"),
        order_send=LatencyPercentilesUs(2.0, 4.0, 5.0, "test_send"),
        gateway_ack=LatencyPercentilesUs(50_000.0, 80_000.0, 100_000.0, "test_ack"),
        feed_delay=LatencyPercentilesUs(10.0, 15.0, 20.0, "test_feed"),
    )


def _blocked_profile() -> CppLatencyProfile:
    return CppLatencyProfile(
        cpp_decision_compute=LatencyPercentilesUs(5.0, 8.0, 10.0, "test_cpp"),
        order_send=LatencyPercentilesUs(0.0, 0.0, 0.0, "blocked_until_paper_order_ack"),
        gateway_ack=LatencyPercentilesUs(0.0, 0.0, 0.0, "blocked_until_paper_order_ack"),
        feed_delay=LatencyPercentilesUs(10.0, 15.0, 20.0, "test_feed"),
        order_ack_blocked=True,
    )


def _viability() -> LatencyViability:
    return LatencyViability(
        breakeven_us=10_000.0,
        breakeven_ms=10.0,
        measured_production_p99_us=100_035.0,
        measured_production_p99_ms=100.035,
        latency_profitability_buffer_us=5_000.0,
        latency_buffer_ms=5.0,
        lane_required="microsecond",
        lane_measured="microsecond",
        lane_pass=True,
        pnl_by_injection_us={0: 100.0, 100: 75.0, 250: 50.0, 1000: -5.0},
        pnl_by_latency={0.0: 100.0},
        per_trade_latency={},
        python_research_runtime_us=999_999.0,
        cpp_hot_path_runtime_us=100_035.0,
        simulated_latency_adjusted_pnl=25.0,
        survives_cpp_execution_delay=True,
        recommendation="VIABLE",
        cpp_latency_profile={},
    )


def _passing_execution_audit() -> dict:
    return {
        "run_id": "lataudit-pass",
        "status": "pass",
        "mode": "synthetic",
        "primary_kpi": "tick_to_send_us",
        "tick_to_send_p50_us": 45.0,
        "tick_to_send_p99_us": 50.0,
        "tick_to_send_p99_9_us": 55.0,
        "failures": [],
        "warnings": [],
    }


def test_operating_envelope_separates_placement_from_ack_latency() -> None:
    envelope = build_latency_operating_envelope(
        run_id="RUN_A",
        model_id="HYP_5",
        event_id="EV_A",
        viability=_viability(),
        cpp_profile=_profile(),
        phase5_timestamp_schema={"complete": True, "monotonic_non_decreasing": True},
        audit_records=[],
        chi404_observed=True,
        execution_path_audit_status=_passing_execution_audit(),
    )

    compact = compact_envelope_fields(envelope)

    assert envelope["status"] == "PASS"
    assert compact["placement_speed_p99_us"] == 35.0
    assert compact["send_to_ack_p99_us"] == 100_000.0
    assert compact["offensive_operating_band"] == "microsecond_loop"
    assert envelope["external_confirmation"]["modeled_as_async_state_confirmation"] is True
    assert envelope["external_confirmation"]["blocks_on_ack"] is False


def test_operating_envelope_blocks_when_execution_path_audit_is_missing() -> None:
    envelope = build_latency_operating_envelope(
        run_id="RUN_MISSING_AUDIT",
        model_id="HYP_5",
        event_id="EV_A",
        viability=_viability(),
        cpp_profile=_profile(),
        phase5_timestamp_schema={"complete": True, "monotonic_non_decreasing": True},
        audit_records=[],
        chi404_observed=True,
    )

    assert envelope["status"] == "FAIL"
    assert envelope["execution_path_audit"]["status"] == "missing"
    assert any(gate["gate"] == "low_latency_execution_path_audit" for gate in envelope["promotion_blockers"])


def test_operating_envelope_blocks_without_chi404_authority() -> None:
    envelope = build_latency_operating_envelope(
        run_id="RUN_A",
        model_id="HYP_5",
        event_id="EV_A",
        viability=_viability(),
        cpp_profile=_profile(),
        phase5_timestamp_schema={"complete": True, "monotonic_non_decreasing": True},
        audit_records=[],
        chi404_observed=False,
        execution_path_audit_status=_passing_execution_audit(),
    )

    assert envelope["status"] == "FAIL"
    assert any(gate["gate"] == "operating_envelope_generated" for gate in envelope["promotion_blockers"])


def test_operating_envelope_blocks_when_order_ack_is_not_measured() -> None:
    envelope = build_latency_operating_envelope(
        run_id="RUN_BLOCKED_ACK",
        model_id="HYP_5",
        event_id="EV_A",
        viability=_viability(),
        cpp_profile=_blocked_profile(),
        phase5_timestamp_schema={"complete": True, "monotonic_non_decreasing": True},
        audit_records=[],
        chi404_observed=True,
        execution_path_audit_status=_passing_execution_audit(),
    )

    assert envelope["status"] == "FAIL"
    assert envelope["source_authority_detail"]["order_ack_blocked"] is True
    assert any(gate["gate"] == "async_ack_state_risk" for gate in envelope["promotion_blockers"])


def test_operating_envelope_blocks_when_execution_path_audit_failed() -> None:
    envelope = build_latency_operating_envelope(
        run_id="RUN_AUDIT_FAIL",
        model_id="HYP_5",
        event_id="EV_A",
        viability=_viability(),
        cpp_profile=_profile(),
        phase5_timestamp_schema={"complete": True, "monotonic_non_decreasing": True},
        audit_records=[],
        chi404_observed=True,
        execution_path_audit_status={
            "run_id": "lataudit-bad",
            "status": "fail",
            "primary_kpi": "tick_to_send_us",
            "failures": [{"gate": "no_sync_persistence_before_order_send", "reason": "blocking I/O before send was observed"}],
        },
    )

    compact = compact_envelope_fields(envelope)

    assert envelope["status"] == "FAIL"
    assert compact["execution_path_audit_status"] == "fail"
    assert compact["execution_path_audit_run_id"] == "lataudit-bad"
    assert any(gate["gate"] == "low_latency_execution_path_audit" for gate in envelope["promotion_blockers"])


def test_operating_envelope_writer_emits_json_and_markdown(tmp_path: Path) -> None:
    envelope = build_latency_operating_envelope(
        run_id="RUN_WRITE",
        model_id="HYP_5",
        event_id="EV_A",
        viability=_viability(),
        cpp_profile=_profile(),
        phase5_timestamp_schema={"complete": True, "monotonic_non_decreasing": True},
        audit_records=[],
        chi404_observed=True,
        execution_path_audit_status=_passing_execution_audit(),
    )

    json_path, md_path = write_latency_operating_envelope(tmp_path, envelope)

    assert json.loads(json_path.read_text(encoding="utf-8"))["run_id"] == "RUN_WRITE"
    assert "Latency Operating Envelope" in md_path.read_text(encoding="utf-8")


def test_competitor_speed_sensitivity_applies_latency_penalty() -> None:
    slow_profile = CppLatencyProfile(
        cpp_decision_compute=LatencyPercentilesUs(100.0, 150.0, 200.0, "test_cpp"),
        order_send=LatencyPercentilesUs(100.0, 150.0, 200.0, "test_send"),
        gateway_ack=LatencyPercentilesUs(1_000.0, 1_500.0, 2_000.0, "test_ack"),
        feed_delay=LatencyPercentilesUs(100.0, 150.0, 200.0, "test_feed"),
    )
    envelope = build_latency_operating_envelope(
        run_id="RUN_COMPETITOR",
        model_id="HYP_5",
        event_id="EV_A",
        viability=_viability(),
        cpp_profile=slow_profile,
        phase5_timestamp_schema={"complete": True, "monotonic_non_decreasing": True},
        audit_records=[],
        chi404_observed=True,
        execution_path_audit_status=_passing_execution_audit(),
    )

    faster = envelope["competitor_speed_sensitivity"]["scenarios"]["faster"]
    penalties = [row["latency_penalty_us"] for row in faster["window_results"]]
    pnls = [row["latency_adjusted_pnl"] for row in faster["window_results"]]
    assert max(penalties) > 0
    assert len(set(pnls)) > 1
    assert envelope["defensive"]["placement"]["cancel_to_send_us"]["observed"] is False


def test_defensive_composition_blocks_when_cancel_replace_timing_is_missing() -> None:
    envelope = build_latency_operating_envelope(
        run_id="RUN_DEFENSIVE",
        model_id="HYP_5",
        event_id="EV_A",
        viability=_viability(),
        cpp_profile=_profile(),
        phase5_timestamp_schema={"complete": True, "monotonic_non_decreasing": True},
        audit_records=[],
        chi404_observed=True,
        execution_path_audit_status=_passing_execution_audit(),
        composition=ModelComposition(
            primary_model_id="HYP_5",
            defensive_stubs=[DefensiveStub("VPIN_TOXICITY", "continuous", 50.0)],
        ),
        composition_trace={"steps": [], "phase_budgets_us": {"continuous": 50.0}},
    )

    assert envelope["defensive"]["timing_required"] is True
    assert envelope["status"] == "FAIL"
    assert any(gate["gate"] == "composition_latency_feasibility" for gate in envelope["promotion_blockers"])


def test_campaign_latency_envelope_aggregates_event_blockers() -> None:
    passing = build_latency_operating_envelope(
        run_id="RUN_PASS",
        model_id="HYP_5",
        event_id="EV_A",
        viability=_viability(),
        cpp_profile=_profile(),
        phase5_timestamp_schema={"complete": True, "monotonic_non_decreasing": True},
        audit_records=[],
        chi404_observed=True,
        execution_path_audit_status=_passing_execution_audit(),
    )
    failing = build_latency_operating_envelope(
        run_id="RUN_FAIL",
        model_id="HYP_5",
        event_id="EV_B",
        viability=_viability(),
        cpp_profile=_profile(),
        phase5_timestamp_schema={"complete": True, "monotonic_non_decreasing": True},
        audit_records=[],
        chi404_observed=False,
        execution_path_audit_status=_passing_execution_audit(),
    )

    campaign = aggregate_campaign_latency_envelopes(
        campaign_id="CAMP_A",
        model_id="HYP_5",
        symbol="ES",
        period_results=[{"name": "Discovery", "events_run": 2, "event_results": []}],
        event_envelopes=[passing, failing],
    )

    assert campaign["status"] == "FAIL"
    assert campaign["events_observed"] == 2
    assert any(gate["gate"] == "operating_envelope_generated" for gate in campaign["promotion_blockers"])


def test_campaign_latency_envelope_blocks_missing_event_envelopes() -> None:
    passing = build_latency_operating_envelope(
        run_id="RUN_PASS",
        model_id="HYP_5",
        event_id="EV_A",
        viability=_viability(),
        cpp_profile=_profile(),
        phase5_timestamp_schema={"complete": True, "monotonic_non_decreasing": True},
        audit_records=[],
        chi404_observed=True,
        execution_path_audit_status=_passing_execution_audit(),
    )

    campaign = aggregate_campaign_latency_envelopes(
        campaign_id="CAMP_A",
        model_id="HYP_5",
        symbol="ES",
        period_results=[
            {
                "name": "Discovery",
                "events_run": 2,
                "event_results": [{"event_id": "EV_A"}, {"event_id": "EV_B"}],
            }
        ],
        event_envelopes=[passing],
    )

    assert campaign["status"] == "FAIL"
    assert any(gate["status"] == "MISSING_EVENT_ENVELOPE" for gate in campaign["promotion_blockers"])
