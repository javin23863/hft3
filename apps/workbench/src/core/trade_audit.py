"""Per-trade audit timestamps — C++-authoritative latency fields."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping

import pandas as pd


PHASE5_TIMESTAMP_FIELDS = (
    "market_data_exchange_ts",
    "market_data_wire_ts",
    "market_data_receive_start_ts",
    "market_data_receive_ts",
    "market_data_decode_start_ts",
    "market_data_decode_end_ts",
    "book_snapshot_start_ts",
    "book_snapshot_end_ts",
    "feature_build_start_ts",
    "feature_build_end_ts",
    "signal_start_ts",
    "signal_end_ts",
    "decision_start_ts",
    "decision_end_ts",
    "risk_check_start_ts",
    "risk_check_end_ts",
    "sizing_start_ts",
    "sizing_end_ts",
    "order_intent_create_ts",
    "order_queue_enter_ts",
    "order_queue_exit_ts",
    "order_send_ts",
    "gateway_send_ts",
    "gateway_ack_ts",
    "exchange_ack_ts",
    "queue_position_ts",
    "fill_model_start_ts",
    "fill_model_end_ts",
    "fill_ts",
    "pnl_mark_start_ts",
    "pnl_mark_end_ts",
    "audit_record_start_ts",
    "audit_record_end_ts",
)


def phase5_latency_chain_ns(injected: Any) -> int:
    return int(
        (injected.feed_delay_us
        + injected.decision_compute_us
        + injected.decision_to_send_us
        + injected.send_to_ack_us)
        * 1000
    )


@dataclass
class TradeAuditRecord:
    model_id: str
    market_data_exchange_ts: int
    market_data_wire_ts: int
    market_data_receive_start_ts: int
    market_data_receive_ts: int
    market_data_decode_start_ts: int
    market_data_decode_end_ts: int
    book_snapshot_start_ts: int
    book_snapshot_end_ts: int
    feature_build_start_ts: int
    feature_build_end_ts: int
    signal_start_ts: int
    signal_end_ts: int
    decision_start_ts: int
    decision_end_ts: int
    risk_check_start_ts: int
    risk_check_end_ts: int
    sizing_start_ts: int
    sizing_end_ts: int
    order_intent_create_ts: int
    order_queue_enter_ts: int
    order_queue_exit_ts: int
    order_send_ts: int
    gateway_send_ts: int
    gateway_ack_ts: int
    exchange_ack_ts: int
    queue_position_ts: int
    fill_model_start_ts: int
    fill_model_end_ts: int
    fill_ts: int
    pnl_mark_start_ts: int
    pnl_mark_end_ts: int
    audit_record_start_ts: int
    audit_record_end_ts: int
    side: str
    exec_price: float
    qty: int
    signal: float
    mid_at_signal: float
    net_pnl_contribution: float = 0.0
    # Measured C++ hot-path components (microseconds) — source of truth for viability
    feed_delay_us: float = 0.0
    decision_compute_us: float = 0.0
    decision_to_send_us: float = 0.0
    send_to_ack_us: float = 0.0
    # Informational only — Python research wall time; NOT used for promotion
    python_research_compute_us: float = 0.0
    latency_injection_us: float = 0.0

    @property
    def tick_to_ack_us(self) -> float:
        return self.feed_delay_us + self.decision_compute_us + self.decision_to_send_us + self.send_to_ack_us

    @property
    def tick_to_fill_us(self) -> float:
        if self.fill_ts <= 0 or self.market_data_exchange_ts <= 0:
            return self.tick_to_ack_us
        return (self.fill_ts - self.market_data_exchange_ts) / 1000.0

    def to_latency_dict(self) -> Dict[str, float]:
        return {
            "feed_delay_us": self.feed_delay_us,
            "decision_compute_us": self.decision_compute_us,
            "decision_to_send_us": self.decision_to_send_us,
            "send_to_ack_us": self.send_to_ack_us,
            "tick_to_ack_us": self.tick_to_ack_us,
            "tick_to_fill_us": self.tick_to_fill_us,
            "python_research_compute_us": self.python_research_compute_us,
        }


def build_audit_timestamps_ns(
    exchange_ts_ns: int,
    injected: Any,
    fill_ts_ns: int | None = None,
) -> Dict[str, int]:
    """Construct the Phase 5 33-timestamp audit chain.

    The measured C++ latency budget anchors the chain. Sub-stages without a
    dedicated measurement are zero-duration markers so the audit stays explicit
    without inventing unsupported latency.
    """
    feed_ns = int(injected.feed_delay_us * 1000)
    decision_ns = int(injected.decision_compute_us * 1000)
    decision_to_send_ns = int(injected.decision_to_send_us * 1000)
    ack_ns = int(injected.send_to_ack_us * 1000)

    wire = exchange_ts_ns
    recv_start = exchange_ts_ns + max(0, feed_ns // 2)
    recv = exchange_ts_ns + feed_ns
    d_start = recv
    d_end = d_start + decision_ns
    queue_exit = d_end + max(0, decision_to_send_ns // 2)
    send = d_end + decision_to_send_ns
    ack = send + ack_ns
    fill = int(fill_ts_ns) if fill_ts_ns is not None else ack

    timestamps = {
        "market_data_exchange_ts": exchange_ts_ns,
        "market_data_wire_ts": wire,
        "market_data_receive_start_ts": recv_start,
        "market_data_receive_ts": recv,
        "market_data_decode_start_ts": recv,
        "market_data_decode_end_ts": recv,
        "book_snapshot_start_ts": recv,
        "book_snapshot_end_ts": recv,
        "feature_build_start_ts": recv,
        "feature_build_end_ts": recv,
        "signal_start_ts": recv,
        "signal_end_ts": recv,
        "decision_start_ts": d_start,
        "decision_end_ts": d_end,
        "risk_check_start_ts": d_end,
        "risk_check_end_ts": d_end,
        "sizing_start_ts": d_end,
        "sizing_end_ts": d_end,
        "order_intent_create_ts": d_end,
        "order_queue_enter_ts": d_end,
        "order_queue_exit_ts": queue_exit,
        "order_send_ts": send,
        "gateway_send_ts": send,
        "gateway_ack_ts": ack,
        "exchange_ack_ts": ack,
        "queue_position_ts": ack,
        "fill_model_start_ts": ack,
        "fill_model_end_ts": ack,
        "fill_ts": fill,
        "pnl_mark_start_ts": fill,
        "pnl_mark_end_ts": fill,
        "audit_record_start_ts": fill,
        "audit_record_end_ts": fill,
    }
    return {field: timestamps[field] for field in PHASE5_TIMESTAMP_FIELDS}


def phase5_timestamp_schema_status(record: Mapping[str, Any]) -> Dict[str, Any]:
    missing = [field for field in PHASE5_TIMESTAMP_FIELDS if field not in record]
    ordered = True
    previous = None
    for field in PHASE5_TIMESTAMP_FIELDS:
        if field not in record:
            ordered = False
            continue
        current = int(record[field])
        if previous is not None and current < previous:
            ordered = False
        previous = current
    return {
        "schema_version": "phase5_33_timestamp_v1",
        "required_count": len(PHASE5_TIMESTAMP_FIELDS),
        "present_count": len(PHASE5_TIMESTAMP_FIELDS) - len(missing),
        "missing_fields": missing,
        "complete": not missing,
        "monotonic_non_decreasing": ordered,
    }


def summarize_phase5_timestamp_schema(
    records: List[TradeAuditRecord],
    *,
    expected_trade_count: int | None = None,
) -> Dict[str, Any]:
    expected = len(records) if expected_trade_count is None else int(expected_trade_count)
    if not records:
        complete = expected == 0
        return {
            "schema_version": "phase5_33_timestamp_v1",
            "required_count": len(PHASE5_TIMESTAMP_FIELDS),
            "expected_trade_count": expected,
            "trade_count": 0,
            "complete": complete,
            "monotonic_non_decreasing": complete,
            "missing_fields": [] if complete else list(PHASE5_TIMESTAMP_FIELDS),
        }
    statuses = [phase5_timestamp_schema_status(asdict(record)) for record in records]
    missing = sorted({field for status in statuses for field in status["missing_fields"]})
    return {
        "schema_version": "phase5_33_timestamp_v1",
        "required_count": len(PHASE5_TIMESTAMP_FIELDS),
        "expected_trade_count": expected,
        "trade_count": len(records),
        "complete": len(records) == expected and all(status["complete"] for status in statuses),
        "monotonic_non_decreasing": all(status["monotonic_non_decreasing"] for status in statuses),
        "missing_fields": missing,
    }


def audit_records_to_dataframe(records: List[TradeAuditRecord]) -> pd.DataFrame:
    rows = []
    for r in records:
        d = asdict(r)
        d.update(r.to_latency_dict())
        rows.append(d)
    return pd.DataFrame(rows)


def summarize_latency_us(records: List[TradeAuditRecord], field: str) -> Dict[str, float]:
    df = audit_records_to_dataframe(records)
    if df.empty or field not in df.columns:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    s = df[field]
    return {
        "p50": float(s.quantile(0.50)),
        "p95": float(s.quantile(0.95)),
        "p99": float(s.quantile(0.99)),
        "max": float(s.max()),
    }
