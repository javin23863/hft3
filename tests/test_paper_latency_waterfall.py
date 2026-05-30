"""Tests for paper_latency_record_v1 schema and waterfall."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_system.rithmic_trial.reports.waterfall import build_waterfall_report
from data_system.rithmic_trial.schema.paper_latency_record_v1 import (
    PaperLatencyRecordV1,
    stage_deltas_us,
    validate_record,
)


def test_stage_deltas_submit_to_ack() -> None:
    rec = PaperLatencyRecordV1(
        order_id="1",
        run_id="r1",
        tick_receive_mono_ns=1_000_000,
        rithmic_submit_mono_ns=2_000_000,
        rithmic_ack_mono_ns=5_000_000,
    )
    deltas = stage_deltas_us(rec)
    assert deltas["submit_to_ack_us"] == pytest.approx(3000.0)
    assert deltas["tick_to_ack_us"] == pytest.approx(4000.0)


def test_validate_record_rejects_inverted_ack() -> None:
    raw = {
        "schema_version": "paper_latency_record_v1",
        "order_id": "x",
        "run_id": "r",
        "rithmic_submit_mono_ns": 5000,
        "rithmic_ack_mono_ns": 1000,
    }
    errs = validate_record(raw)
    assert any("rithmic_ack" in e for e in errs)


def test_waterfall_report_percentiles() -> None:
    records = []
    for i in range(100):
        submit = 1_000_000 + i * 1000
        ack = submit + 2000 + i
        records.append(
            PaperLatencyRecordV1(
                run_id="r",
                order_id=str(i),
                rithmic_submit_mono_ns=submit,
                rithmic_ack_mono_ns=ack,
            )
        )
    report = build_waterfall_report(records)
    assert report["paired_submit_ack_count"] == 100
    sa = report["submit_to_ack_us"]
    assert sa["count"] == 100
    assert sa["p99_us"] is not None


def test_promote_rejects_sweep_synthetic_order_ids(tmp_path: Path) -> None:
    from data_system.rithmic_trial.latency.promote_reports import promote_from_records

    records = tmp_path / "records.ndjson"
    records.write_text(
        json.dumps(
            {
                "schema_version": "paper_latency_record_v1",
                "run_id": "r1",
                "order_id": "SWEEP-batch-0",
                "symbol": "ES",
                "rithmic_submit_mono_ns": 1000,
                "rithmic_ack_mono_ns": 2000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no promotable records"):
        promote_from_records(records, tmp_path / "reports")


def test_promote_rejects_mkt_synthetic_order_ids(tmp_path: Path) -> None:
    from data_system.rithmic_trial.latency.promote_reports import promote_from_records

    records = tmp_path / "records.ndjson"
    records.write_text(
        json.dumps(
            {
                "schema_version": "paper_latency_record_v1",
                "run_id": "r1",
                "order_id": "MKT-batch-0",
                "symbol": "MES",
                "rithmic_submit_mono_ns": 1000,
                "rithmic_ack_mono_ns": 2000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no promotable records"):
        promote_from_records(records, tmp_path / "reports")


def test_promote_min_paired_gate(tmp_path: Path) -> None:
    from data_system.rithmic_trial.latency.promote_reports import promote_from_records

    records = tmp_path / "records.ndjson"
    lines = []
    for i in range(3):
        submit = 1_000_000 + i * 10_000
        ack = submit + 2_000
        lines.append(
            json.dumps(
                {
                    "schema_version": "paper_latency_record_v1",
                    "run_id": "r1",
                    "order_id": str(i),
                    "symbol": "MES",
                    "rithmic_submit_mono_ns": submit,
                    "rithmic_ack_mono_ns": ack,
                }
            )
        )
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="min_paired"):
        promote_from_records(records, tmp_path / "reports", min_paired=1000)


def test_promote_reports_from_synthetic_records(tmp_path: Path) -> None:
    from data_system.rithmic_trial.latency.promote_reports import promote_from_records

    records = tmp_path / "records.ndjson"
    lines = []
    for i in range(5):
        submit = 1_000_000 + i * 10_000
        ack = submit + 2_000
        lines.append(
            json.dumps(
                {
                    "schema_version": "paper_latency_record_v1",
                    "run_id": "r1",
                    "order_id": str(i),
                    "symbol": "ES",
                    "order_type": "limit",
                    "rithmic_submit_mono_ns": submit,
                    "rithmic_ack_mono_ns": ack,
                }
            )
        )
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reports = tmp_path / "reports"
    paths = promote_from_records(records, reports)
    profile = json.loads((reports / "latency_profile.json").read_text())
    assert profile["order_submit_to_ack_us"]["count"] == 5


def test_bridge_monotonic_stamp_on_csv(tmp_path: Path) -> None:
    from data_system.rithmic_trial.config import TrialConfig
    from data_system.rithmic_trial.connector.rtrader_bridge import RTraderBridgeConnector

    watch = tmp_path / "watch"
    watch.mkdir()
    csv_path = watch / "orders.csv"
    csv_path.write_text(
        "event_type,symbol,order_id,status\norder_submit,ES,O1,\n",
        encoding="utf-8",
    )
    cfg = TrialConfig(
        enabled=True,
        connector="rtrader_bridge",
        symbol="ES",
        exchange="CME",
        contract="ES",
        capture_environment="paper_or_trial",
        source="rithmic_trial",
        schema_version="normalized_v1",
        repo_root=tmp_path,
        raw_root=tmp_path / "raw",
        normalized_root=tmp_path / "norm",
        replay_root=tmp_path / "replay",
        reports_root=tmp_path / "reports",
        rtrader={"watch_dirs": [str(watch)], "platform": "linux"},
        rithmic={},
    )
    conn = RTraderBridgeConnector(cfg)
    conn.watch_dirs = [watch]
    conn._ingest_file(csv_path)
    assert conn._pending
    ev = conn._pending[0]
    assert "local_monotonic_receive_ns" in ev
