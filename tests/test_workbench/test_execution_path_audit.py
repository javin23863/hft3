from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.src.latency.execution_path_audit import (
    AuditConfig,
    build_span,
    load_spans,
    main as audit_main,
    run_audit,
)


def _config(tmp_path: Path, *, run_id: str = "audit-syn", mode: str = "synthetic") -> AuditConfig:
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
    )


def test_synthetic_audit_writes_required_outputs_and_separates_ack(tmp_path: Path) -> None:
    summary = run_audit(_config(tmp_path))

    assert summary["primary_kpi"] == "tick_to_send_us"
    assert summary["principle"] == "placement_speed_is_tick_to_send_us_ack_latency_is_reported_separately"
    assert summary["metrics"]["tick_to_send_us"]["count"] == 3
    assert summary["metrics"]["send_to_ack_us"]["count"] == 3
    assert summary["metrics"]["tick_to_send_us"]["p99_us"] < summary["metrics"]["send_to_ack_us"]["p99_us"]

    spans_path = Path(summary["spans_path"])
    runtime_path = Path(summary["runtime_env_path"])
    assert spans_path.name == "spans.jsonl"
    assert runtime_path.name == "runtime_env.json"
    assert spans_path.is_file()
    assert runtime_path.is_file()
    assert load_spans(spans_path)
    assert (tmp_path / "reports" / "latency_audit" / "audit-syn_summary.json").is_file()
    assert (tmp_path / "reports" / "latency_audit" / "audit-syn_summary.md").is_file()
    assert (tmp_path / "reports" / "latency_audit" / "current_low_latency_status.json").is_file()


def test_audit_cli_supports_synthetic_and_replay_modes(tmp_path: Path) -> None:
    assert audit_main(["--mode", "synthetic", "--repo-root", str(tmp_path), "--run-id", "syn", "--samples", "3"]) == 0
    assert audit_main(["--mode", "replay", "--repo-root", str(tmp_path), "--run-id", "rep", "--samples", "3"]) == 0

    assert (tmp_path / "reports" / "latency_audit" / "syn_summary.json").is_file()
    replay_summary = json.loads((tmp_path / "reports" / "latency_audit" / "rep_summary.json").read_text(encoding="utf-8"))
    assert replay_summary["mode"] == "replay"
    assert replay_summary["status"] in {"pass", "warn"}


def test_paper_live_mode_fails_loudly_without_execution_boundaries(tmp_path: Path) -> None:
    rc = audit_main(["--mode", "paper-live", "--repo-root", str(tmp_path), "--run-id", "paper-blocked"])

    assert rc == 2
    summary = json.loads((tmp_path / "reports" / "latency_audit" / "paper-blocked_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "blocked"
    assert summary["blocked_reason"] == "PAPER_LIVE_EXECUTION_BOUNDARIES_NOT_WIRED"
    assert summary["sample_count"] == 0


def test_span_validation_rejects_ack_as_placement_or_non_monotonic_order() -> None:
    with pytest.raises(ValueError, match="raw_timestamps.order_send_ts before order_send_call_ts"):
        build_span(
            run_id="bad",
            mode="synthetic",
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
        mode="synthetic",
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
    summary = run_audit(_config(tmp_path, run_id="baseline"))
    assert summary["status"] in {"pass", "warn"}

    from workbench.src.latency.execution_path_audit import collect_runtime_env, write_audit_outputs

    config = _config(tmp_path, run_id="io-fail")
    failed = write_audit_outputs(config, [span], runtime_env=collect_runtime_env(config))
    assert failed["status"] == "fail"
    assert any(gate["gate"] == "no_sync_persistence_before_order_send" for gate in failed["failures"])
