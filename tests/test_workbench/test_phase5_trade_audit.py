from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from data_layer.packet.microstructure_aar_packet import _row_to_audit
from data_layer.packet.microstructure_aar_packet import build_microstructure_aar_packet
from workbench.src.core.trade_audit import (
    PHASE5_TIMESTAMP_FIELDS,
    TradeAuditRecord,
    build_audit_timestamps_ns,
    phase5_latency_chain_ns,
    phase5_timestamp_schema_status,
    summarize_phase5_timestamp_schema,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_phase5_timestamp_schema_has_exactly_33_fields():
    assert len(PHASE5_TIMESTAMP_FIELDS) == 33
    assert len(set(PHASE5_TIMESTAMP_FIELDS)) == 33
    assert all(field.endswith("_ts") for field in PHASE5_TIMESTAMP_FIELDS)


def test_phase5_timestamps_are_complete_and_monotonic():
    injected = SimpleNamespace(
        feed_delay_us=10.0,
        decision_compute_us=20.0,
        decision_to_send_us=30.0,
        send_to_ack_us=40.0,
    )
    fill_ts = 2_000_000
    exchange_ts = fill_ts - phase5_latency_chain_ns(injected)
    ts = build_audit_timestamps_ns(exchange_ts, injected, fill_ts_ns=fill_ts)

    assert list(ts) == list(PHASE5_TIMESTAMP_FIELDS)
    status = phase5_timestamp_schema_status(ts)
    assert status == {
        "schema_version": "phase5_33_timestamp_v1",
        "required_count": 33,
        "present_count": 33,
        "missing_fields": [],
        "complete": True,
        "monotonic_non_decreasing": True,
    }
    assert ts["fill_ts"] == fill_ts
    assert ts["gateway_ack_ts"] == fill_ts


def test_phase5_schema_status_reports_missing_or_out_of_order():
    bad = {field: i for i, field in enumerate(PHASE5_TIMESTAMP_FIELDS)}
    bad.pop("gateway_ack_ts")
    bad["decision_end_ts"] = -1

    status = phase5_timestamp_schema_status(bad)
    assert status["complete"] is False
    assert status["monotonic_non_decreasing"] is False
    assert status["missing_fields"] == ["gateway_ack_ts"]


def test_phase5_schema_summary_reports_trade_count():
    injected = SimpleNamespace(
        feed_delay_us=10.0,
        decision_compute_us=20.0,
        decision_to_send_us=30.0,
        send_to_ack_us=40.0,
    )
    ts = build_audit_timestamps_ns(1_000_000, injected, fill_ts_ns=2_000_000)
    record = TradeAuditRecord(
        model_id="HYP_1",
        **ts,
        side="BUY",
        exec_price=100.0,
        qty=1,
        signal=1.0,
        mid_at_signal=100.0,
    )

    assert summarize_phase5_timestamp_schema([record]) == {
        "schema_version": "phase5_33_timestamp_v1",
        "required_count": 33,
        "expected_trade_count": 1,
        "trade_count": 1,
        "complete": True,
        "monotonic_non_decreasing": True,
        "missing_fields": [],
    }


def test_phase5_schema_summary_rejects_missing_audits_for_trades():
    status = summarize_phase5_timestamp_schema([], expected_trade_count=1)
    assert status["complete"] is False
    assert status["monotonic_non_decreasing"] is False
    assert status["expected_trade_count"] == 1
    assert status["trade_count"] == 0
    assert status["missing_fields"] == list(PHASE5_TIMESTAMP_FIELDS)


def test_aar_row_preserves_all_phase5_timestamp_fields():
    row = pd.Series({field: i for i, field in enumerate(PHASE5_TIMESTAMP_FIELDS)})
    row["feed_delay_us"] = 1.0
    row["decision_compute_us"] = 2.0
    row["decision_to_send_us"] = 3.0
    row["send_to_ack_us"] = 4.0

    audit = _row_to_audit(row)

    assert all(field in audit for field in PHASE5_TIMESTAMP_FIELDS)


def test_aar_rejects_legacy_timestamp_audit_when_phase5_declared(tmp_path):
    artifact = tmp_path / "run"
    artifact.mkdir()
    diagnostics = {
        "event_id": "CPI_2024_09_11_TIGHT",
        "num_trades": 1,
        "phase5_timestamp_schema": {"schema_version": "phase5_33_timestamp_v1"},
    }
    (artifact / "diagnostics.json").write_text(json.dumps(diagnostics), encoding="utf-8")
    (artifact / "manifest.json").write_text('{"data_sufficient": true}', encoding="utf-8")
    (artifact / "config.yaml").write_text("model_id: HYP_1\nevent_id: CPI_2024_09_11_TIGHT\n", encoding="utf-8")
    legacy_row = {
        "market_data_exchange_ts": 1,
        "market_data_receive_ts": 2,
        "decision_start_ts": 2,
        "decision_end_ts": 3,
        "order_send_ts": 4,
        "gateway_ack_ts": 5,
        "fill_ts": 6,
        "feed_delay_us": 1.0,
        "decision_compute_us": 1.0,
        "decision_to_send_us": 1.0,
        "send_to_ack_us": 1.0,
        "tick_to_ack_us": 4.0,
    }
    pd.DataFrame([legacy_row]).to_parquet(artifact / "trades.parquet", index=False)

    _, skips = build_microstructure_aar_packet(artifact, REPO_ROOT)

    assert "AUDIT_INCOMPLETE" in skips
