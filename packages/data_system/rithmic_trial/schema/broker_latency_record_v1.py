"""broker_latency_record_v1 — monotonic timestamp audit for broker order waterfall."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = "broker_latency_record_v1"


@dataclass
class BrokerLatencyRecordV1:
    schema_version: str = SCHEMA_VERSION
    run_id: str = ""
    order_id: str = ""
    symbol: str = ""
    order_type: str = ""
    session_tag: str = "regular"
    market_state: str = "quiet"
    wall_utc: str = ""

    tick_receive_mono_ns: int | None = None
    feature_done_mono_ns: int | None = None
    decision_start_mono_ns: int | None = None
    decision_end_mono_ns: int | None = None
    order_construct_mono_ns: int | None = None
    risk_check_mono_ns: int | None = None
    rithmic_submit_mono_ns: int | None = None
    rithmic_ack_mono_ns: int | None = None
    exchange_status_mono_ns: int | None = None
    cancel_replace_submit_mono_ns: int | None = None
    cancel_replace_ack_mono_ns: int | None = None
    fill_mono_ns: int | None = None

    exchange_timestamp_ns: int | None = None
    exchange_status_missing: bool = False
    raw_log_line: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not d.get("extra"):
            d.pop("extra", None)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BrokerLatencyRecordV1:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in raw.items() if k in known and k != "extra"}
        extra = {k: v for k, v in raw.items() if k not in known}
        if raw.get("extra"):
            extra.update(raw["extra"])
        rec = cls(**kwargs)
        rec.extra = extra
        return rec


def validate_record(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if raw.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if not raw.get("order_id"):
        errors.append("order_id required")
    if not raw.get("run_id"):
        errors.append("run_id required")
    submit = raw.get("rithmic_submit_mono_ns")
    ack = raw.get("rithmic_ack_mono_ns")
    if submit is not None and ack is not None and int(ack) < int(submit):
        errors.append("rithmic_ack_mono_ns before rithmic_submit_mono_ns")
    return errors


def stage_deltas_us(rec: BrokerLatencyRecordV1) -> dict[str, float | None]:
    """Compute tick→ack waterfall stage deltas in microseconds."""

    def delta(a: int | None, b: int | None) -> float | None:
        if a is None or b is None or b < a:
            return None
        return (b - a) / 1000.0

    return {
        "tick_to_feature_us": delta(rec.tick_receive_mono_ns, rec.feature_done_mono_ns),
        "feature_to_decision_us": delta(rec.feature_done_mono_ns, rec.decision_end_mono_ns),
        "decision_to_construct_us": delta(rec.decision_end_mono_ns, rec.order_construct_mono_ns),
        "construct_to_risk_us": delta(rec.order_construct_mono_ns, rec.risk_check_mono_ns),
        "risk_to_submit_us": delta(rec.risk_check_mono_ns, rec.rithmic_submit_mono_ns),
        "submit_to_ack_us": delta(rec.rithmic_submit_mono_ns, rec.rithmic_ack_mono_ns),
        "ack_to_exchange_status_us": delta(rec.rithmic_ack_mono_ns, rec.exchange_status_mono_ns),
        "ack_to_fill_us": delta(rec.rithmic_ack_mono_ns, rec.fill_mono_ns),
        "tick_to_ack_us": delta(rec.tick_receive_mono_ns, rec.rithmic_ack_mono_ns),
    }
