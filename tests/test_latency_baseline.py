from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.latency_baseline.recorder import (
    LatencyRecorder,
    build_latency_sample,
    load_jsonl,
)
from tools.latency_baseline.run import main as latency_baseline_main
from tools.latency_baseline.summary import build_summary, write_summary_reports


def test_tick_to_send_is_separate_from_send_to_ack() -> None:
    record = build_latency_sample(
        run_id="r1",
        environment="paper",
        broker="rithmic",
        venue="CME",
        symbol="ES",
        strategy_id="latency_probe",
        order_action="new",
        timestamps={
            "market_event_received_ts": 1_000_000,
            "features_ready_ts": 1_010_000,
            "decision_ready_ts": 1_030_000,
            "risk_check_ready_ts": 1_040_000,
            "order_ready_ts": 1_050_000,
            "order_send_ts": 1_060_000,
            "ack_received_ts": 2_060_000,
        },
    )
    assert record["tick_to_decision_us"] == pytest.approx(30.0)
    assert record["decision_to_send_us"] == pytest.approx(30.0)
    assert record["tick_to_send_us"] == pytest.approx(60.0)
    assert record["send_to_ack_us"] == pytest.approx(1000.0)


def test_recorder_persists_jsonl_under_dated_data_root(tmp_path: Path) -> None:
    recorder = LatencyRecorder(
        repo_root=tmp_path,
        run_id="run-a",
        environment="paper",
        broker="rithmic",
        venue="CME",
        symbol="ES",
        strategy_id="latency_probe",
    )
    recorder.write_sample(
        order_action="new",
        timestamps={
            "market_event_received_ts": 1,
            "decision_ready_ts": 11,
            "order_send_ts": 21,
            "ack_received_ts": 31,
        },
        timestamp_utc="2026-06-04T00:00:00Z",
    )
    path = recorder.sample_path()
    assert path == tmp_path / "data" / "latency_baselines" / "2026-06-04" / "run-a.jsonl"
    records = load_jsonl(path)
    assert len(records) == 1
    assert records[0]["run_id"] == "run-a"


def test_summary_views_and_baseline_hard_fail(tmp_path: Path) -> None:
    records = [
        build_latency_sample(
            run_id="base",
            environment="paper",
            broker="rithmic",
            venue="CME",
            symbol="ES",
            strategy_id="latency_probe",
            order_action="new",
            timestamps={
                "market_event_received_ts": 1_000_000,
                "decision_ready_ts": 1_010_000,
                "order_send_ts": 1_020_000,
                "ack_received_ts": 1_520_000,
            },
        )
        for _ in range(3)
    ]
    baseline = build_summary(
        records,
        run_id="base",
        sample_path=tmp_path / "base.jsonl",
        baseline_path=None,
    )
    reports = tmp_path / "reports"
    _, _, current = write_summary_reports(baseline, reports_root=reports, update_current_baseline=True)
    assert current is not None

    slower = [
        build_latency_sample(
            run_id="slow",
            environment="paper",
            broker="rithmic",
            venue="CME",
            symbol="ES",
            strategy_id="latency_probe",
            order_action="new",
            timestamps={
                "market_event_received_ts": 1_000_000,
                "decision_ready_ts": 1_020_000,
                "order_send_ts": 1_040_000,
                "ack_received_ts": 1_540_000,
            },
        )
        for _ in range(3)
    ]
    summary = build_summary(
        slower,
        run_id="slow",
        sample_path=tmp_path / "slow.jsonl",
        baseline_path=current,
    )
    assert "tick_to_send_us" in summary["views"]["offensive"]
    assert "send_to_ack_us" in summary["views"]["round_trip"]
    assert summary["comparison"]["status"] == "fail"
    hard = summary["comparison"]["hard_failures"]
    assert hard and hard[0]["metric"] == "tick_to_send_us"


def test_synthetic_cli_writes_jsonl_and_reports(tmp_path: Path) -> None:
    rc = latency_baseline_main(
        [
            "--mode",
            "synthetic",
            "--repo-root",
            str(tmp_path),
            "--run-id",
            "syn1",
            "--duration",
            "1",
            "--samples",
            "9",
            "--env",
            "paper",
            "--broker",
            "rithmic",
            "--symbol",
            "ES",
            "--exchange",
            "CME",
            "--strategy",
            "latency_probe",
        ]
    )
    assert rc == 0
    samples = list((tmp_path / "data" / "latency_baselines").glob("*/*.jsonl"))
    assert len(samples) == 1
    records = load_jsonl(samples[0])
    assert len(records) == 9
    actions = {record["order_action"] for record in records}
    assert actions == {"new", "cancel", "replace"}
    summary_path = tmp_path / "reports" / "latency_baselines" / "syn1_summary.json"
    report = json.loads(summary_path.read_text(encoding="utf-8"))
    assert report["primary_kpi"] == "tick_to_send_us"
    assert report["metrics"]["tick_to_send_us"]["count"] == 3
    assert report["metrics"]["send_to_ack_us"]["count"] == 3
    assert report["metrics"]["cancel_to_ack_us"]["count"] == 3
    assert (tmp_path / "reports" / "latency_baselines" / "syn1_summary.md").is_file()


def test_invalid_samples_fail_loudly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="order_send_ts before decision_ready_ts"):
        build_latency_sample(
            run_id="bad",
            environment="paper",
            broker="rithmic",
            venue="CME",
            symbol="ES",
            strategy_id="latency_probe",
            order_action="new",
            timestamps={
                "market_event_received_ts": 1_000,
                "decision_ready_ts": 3_000,
                "order_send_ts": 2_000,
                "ack_received_ts": 4_000,
            },
        )

    bad_jsonl = tmp_path / "bad.jsonl"
    bad_jsonl.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="latency sample must be a JSON object"):
        load_jsonl(bad_jsonl)


def test_broker_mode_fails_loudly_until_execution_adapter_is_wired(tmp_path: Path) -> None:
    rc = latency_baseline_main(["--repo-root", str(tmp_path), "--run-id", "blocked"])
    assert rc == 2
    blocker = tmp_path / "reports" / "latency_baselines" / "blocked_broker_blocker.json"
    payload = json.loads(blocker.read_text(encoding="utf-8"))
    assert payload["blocker"] == "BROKER_MODE_REQUIRES_EXECUTION_ADAPTER"
    assert payload["principle"] == "do_not_treat_ack_latency_as_placement_speed"
