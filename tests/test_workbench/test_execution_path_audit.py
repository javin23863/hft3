from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.src.latency.execution_path_audit import (
    AuditConfig,
    build_span,
    ensure_chi404_latency_authority,
    load_spans,
    main as audit_main,
    run_audit,
    validate_native_baseline_summary,
)


def _timestamps(action: str = "new", offset_ns: int = 0) -> dict[str, int | None]:
    market = 1_000_000 + offset_ns
    decode = market + 1_000
    features = decode + 1_000
    decision = features + 1_000
    arbitration = decision + 1_000
    risk = arbitration + 1_000
    order_ready = risk + 1_000
    send_call = order_ready + 1_000
    order_send = send_call + 1_000
    send_return = order_send + 1_000
    ack = order_send + 10_000
    raw: dict[str, int | None] = {
        "market_event_received_ts": market,
        "decode_ready_ts": decode,
        "features_ready_ts": features,
        "decision_ready_ts": decision,
        "arbitration_ready_ts": arbitration,
        "risk_check_ready_ts": risk,
        "order_ready_ts": order_ready if action == "new" else None,
        "order_send_call_ts": send_call if action == "new" else None,
        "order_send_ts": order_send if action == "new" else None,
        "order_send_return_ts": send_return if action == "new" else None,
        "ack_received_ts": ack if action == "new" else None,
        "cancel_decision_ready_ts": decision if action == "cancel" else None,
        "cancel_send_ts": order_send if action == "cancel" else None,
        "cancel_ack_received_ts": ack if action == "cancel" else None,
        "replace_decision_ready_ts": decision if action == "replace" else None,
        "replace_send_ts": order_send if action == "replace" else None,
        "replace_ack_received_ts": ack if action == "replace" else None,
    }
    return raw


def _span(tmp_path: Path, *, action: str = "new", offset_ns: int = 0) -> dict[str, object]:
    return build_span(
        run_id="captured",
        mode="replay",
        environment="paper",
        broker="rithmic",
        venue="CME",
        exchange="CME",
        symbol="ES",
        strategy_id="latency_probe",
        model_id="latency_probe_model",
        trade_manager_id="latency_probe_tm",
        order_action=action,
        side="buy",
        order_type="limit",
        quantity=1,
        timestamps=_timestamps(action, offset_ns),
        success=True,
        critical_path_language="cpp",
        ffi_boundary_count=0,
        ipc_boundary_count=0,
        allocation_count_before_send=0,
        pre_send_blocking_io_count=0,
        serialization_bytes=96,
        timestamp_utc="2026-06-04T00:00:00Z",
    )


def _write_spans(tmp_path: Path, *spans: dict[str, object]) -> Path:
    path = tmp_path / "captured_spans.jsonl"
    payload = spans or (_span(tmp_path, action="new"), _span(tmp_path, action="cancel", offset_ns=20_000), _span(tmp_path, action="replace", offset_ns=40_000))
    path.write_text("\n".join(json.dumps(span, sort_keys=True) for span in payload) + "\n", encoding="utf-8")
    return path


def _config(tmp_path: Path, *, run_id: str = "audit-replay", mode: str = "replay", spans_jsonl: Path | None = None) -> AuditConfig:
    return AuditConfig(
        repo_root=tmp_path,
        run_id=run_id,
        mode=mode,
        environment="paper",
        broker="rithmic",
        venue="CME",
        exchange="CME",
        symbol="ES",
        strategy_id="latency_probe",
        model_id="latency_probe_model",
        trade_manager_id="latency_probe_tm",
        duration_seconds=1.0,
        samples=9,
        spans_jsonl=spans_jsonl,
    )


def test_replay_audit_writes_required_outputs_and_separates_ack(tmp_path: Path) -> None:
    summary = run_audit(_config(tmp_path, spans_jsonl=_write_spans(tmp_path)))

    assert summary["primary_kpi"] == "tick_to_send_us"
    assert summary["placement_trigger_kpi"] == "tick_to_send_trigger_us"
    assert summary["principle"] == "placement_trigger_and_sdk_return_are_separate_from_ack_latency"
    assert summary["metrics"]["tick_to_send_trigger_us"]["count"] == 1
    assert summary["metrics"]["tick_to_send_us"]["count"] == 1
    assert summary["metrics"]["rithmic_send_call_us"]["count"] == 1
    assert summary["metrics"]["send_to_ack_us"]["count"] == 1
    assert summary["metrics"]["tick_to_send_us"]["p99_us"] < summary["metrics"]["send_to_ack_us"]["p99_us"]

    spans_path = Path(summary["spans_path"])
    runtime_path = Path(summary["runtime_env_path"])
    assert spans_path.name == "spans.jsonl"
    assert runtime_path.name == "runtime_env.json"
    assert spans_path.is_file()
    assert runtime_path.is_file()
    assert load_spans(spans_path)
    assert (tmp_path / "reports" / "latency_audit" / "audit-replay_summary.json").is_file()
    assert (tmp_path / "reports" / "latency_audit" / "audit-replay_summary.md").is_file()
    assert (tmp_path / "reports" / "latency_audit" / "current_low_latency_status.json").is_file()


def test_audit_cli_requires_replay_spans_jsonl(tmp_path: Path) -> None:
    assert audit_main(["--mode", "replay", "--repo-root", str(tmp_path), "--run-id", "rep"]) == 2

    replay_summary = json.loads((tmp_path / "reports" / "latency_audit" / "rep_summary.json").read_text(encoding="utf-8"))
    assert replay_summary["mode"] == "replay"
    assert replay_summary["status"] == "blocked"
    assert replay_summary["blocked_reason"] == "REPLAY_SPANS_JSONL_REQUIRED"


def test_audit_cli_consumes_captured_replay_spans(tmp_path: Path) -> None:
    spans_jsonl = _write_spans(tmp_path)
    assert audit_main(["--mode", "replay", "--repo-root", str(tmp_path), "--run-id", "rep", "--spans-jsonl", str(spans_jsonl)]) == 0

    replay_summary = json.loads((tmp_path / "reports" / "latency_audit" / "rep_summary.json").read_text(encoding="utf-8"))
    assert replay_summary["mode"] == "replay"
    assert replay_summary["status"] in {"pass", "warn"}


def test_paper_live_mode_fails_loudly_without_execution_boundaries(tmp_path: Path) -> None:
    rc = audit_main(["--mode", "paper-live", "--repo-root", str(tmp_path), "--run-id", "paper-blocked"])

    assert rc == 2
    summary = json.loads((tmp_path / "reports" / "latency_audit" / "paper-blocked_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "blocked"
    assert summary["blocked_reason"] == "PAPER_LIVE_REPLACED_BY_NATIVE_CPP_PROBE"
    assert summary["sample_count"] == 0


def _native_summary(*, count: int = 1000, probe: str = "rithmic_latency_probe") -> dict[str, object]:
    metric = {
        "count": count,
        "p50_us": 1000.0,
        "p95_us": 1500.0,
        "p99_us": 2000.0,
        "p99_9_us": 2500.0,
    }
    return {
        "schema_version": "latency_baseline_summary_v1",
        "run_id": "native-prod",
        "sample_count": count,
        "sample_path": "data/latency_baselines/2026-06-06/native-prod.jsonl",
        "operating_profile": {"host": "CHI404"},
        "broker_mode": {"status": "observed", "broker": "rithmic", "environment": "paper"},
        "broker_artifacts": {
            "hot_path_language": "c++",
            "wrapper": "none",
            "probe": probe,
            "records_written": count,
            "ack_count": count,
        },
        "metrics": {
            "tick_to_send_trigger_us": dict(metric),
            "tick_to_send_us": dict(metric),
            "send_to_ack_us": dict(metric),
        },
    }


def test_native_cpp_baseline_requires_production_submit_ack_count() -> None:
    assert validate_native_baseline_summary(_native_summary(count=1000))["accepted"] is True

    verdict = validate_native_baseline_summary(_native_summary(count=999))

    assert verdict["accepted"] is False
    assert "send_to_ack_us.count 999 < 1000" in verdict["reject_reasons"]


def test_native_cpp_baseline_requires_chi404_source_host() -> None:
    missing_host = _native_summary(count=1000)
    missing_host.pop("operating_profile")

    verdict = validate_native_baseline_summary(missing_host)

    assert verdict["accepted"] is False
    assert "operating_profile.host must be CHI404" in verdict["reject_reasons"]

    wrong_host = _native_summary(count=1000)
    wrong_host["operating_profile"] = {"host": "DEV-WORKSTATION"}

    verdict = validate_native_baseline_summary(wrong_host)

    assert verdict["accepted"] is False
    assert "operating_profile.host must be CHI404" in verdict["reject_reasons"]


def test_native_cpp_baseline_promotion_writes_workbench_authority_artifacts(tmp_path: Path) -> None:
    reports = tmp_path / "reports" / "latency_baselines"
    reports.mkdir(parents=True)
    summary_path = reports / "native-prod_summary.json"
    summary_path.write_text(json.dumps(_native_summary(count=1000), indent=2), encoding="utf-8")

    current = ensure_chi404_latency_authority(tmp_path)

    runtime_summary = json.loads(
        (tmp_path / "runtime" / "latency_reports" / "latency_summary.json").read_text(encoding="utf-8")
    )
    assert current is not None
    assert current["status"] == "PASS"
    assert current["submit_to_ack_sample_count"] == 1000
    assert runtime_summary["order_ack_measured"] is True
    assert runtime_summary["order_ack_p99_ms"] == 2.0
    assert runtime_summary["native_cpp_order_ack"]["probe"] == "rithmic_latency_probe"


def test_span_validation_rejects_ack_as_placement_or_non_monotonic_order() -> None:
    with pytest.raises(ValueError, match="raw_timestamps.order_send_ts before order_send_call_ts"):
        build_span(
            run_id="bad",
            mode="replay",
            environment="paper",
            broker="rithmic",
            venue="CME",
            exchange="CME",
            symbol="ES",
            strategy_id="latency_probe",
            model_id="m",
            trade_manager_id="tm",
            order_action="new",
            timestamps={
                "market_event_received_ts": 1_000_000,
                "decode_ready_ts": 1_001_000,
                "features_ready_ts": 1_002_000,
                "decision_ready_ts": 1_003_000,
                "arbitration_ready_ts": 1_004_000,
                "risk_check_ready_ts": 1_005_000,
                "order_ready_ts": 1_006_000,
                "order_send_call_ts": 1_008_000,
                "order_send_ts": 1_007_000,
                "order_send_return_ts": 1_009_000,
                "ack_received_ts": 1_010_000,
            },
        )


def test_pre_send_blocking_io_is_a_hard_failure(tmp_path: Path) -> None:
    span = build_span(
        run_id="io-fail",
        mode="replay",
        environment="paper",
        broker="rithmic",
        venue="CME",
        exchange="CME",
        symbol="ES",
        strategy_id="latency_probe",
        model_id="m",
        trade_manager_id="tm",
        order_action="new",
        timestamps={
            "market_event_received_ts": 1_000_000,
            "decode_ready_ts": 1_001_000,
            "features_ready_ts": 1_002_000,
            "decision_ready_ts": 1_003_000,
            "arbitration_ready_ts": 1_004_000,
            "risk_check_ready_ts": 1_005_000,
            "order_ready_ts": 1_006_000,
            "order_send_call_ts": 1_007_000,
            "order_send_ts": 1_008_000,
            "order_send_return_ts": 1_009_000,
            "ack_received_ts": 1_010_000,
        },
        pre_send_blocking_io_count=1,
    )
    summary = run_audit(_config(tmp_path, run_id="baseline", spans_jsonl=_write_spans(tmp_path)))
    assert summary["status"] in {"pass", "warn"}

    from workbench.src.latency.execution_path_audit import collect_runtime_env, write_audit_outputs

    config = _config(tmp_path, run_id="io-fail")
    failed = write_audit_outputs(config, [span], runtime_env=collect_runtime_env(config))
    assert failed["status"] == "fail"
    assert any(gate["gate"] == "no_sync_persistence_before_order_send" for gate in failed["failures"])
