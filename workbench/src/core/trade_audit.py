"""Per-trade audit timestamps — C++-authoritative latency fields."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

import pandas as pd


@dataclass
class TradeAuditRecord:
    model_id: str
    market_data_exchange_ts: int
    market_data_receive_ts: int
    decision_start_ts: int
    decision_end_ts: int
    order_send_ts: int
    gateway_ack_ts: int
    fill_ts: int
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
) -> Dict[str, int]:
    """Construct absolute timestamps from exchange time + C++ latency budget."""
    recv = exchange_ts_ns + int(injected.feed_delay_us * 1000)
    d_start = recv
    d_end = d_start + int(injected.decision_compute_us * 1000)
    send = d_end + int(injected.decision_to_send_us * 1000)
    ack = send + int(injected.send_to_ack_us * 1000)
    return {
        "market_data_exchange_ts": exchange_ts_ns,
        "market_data_receive_ts": recv,
        "decision_start_ts": d_start,
        "decision_end_ts": d_end,
        "order_send_ts": send,
        "gateway_ack_ts": ack,
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
