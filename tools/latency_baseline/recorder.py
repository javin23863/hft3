"""Latency baseline sample schema and JSONL recorder.

This module intentionally separates placement trigger speed, native SDK
call-return cost, and broker/exchange acknowledgment latency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import time
from typing import Any


SCHEMA_VERSION = "latency_baseline_sample_v1"

TIMESTAMP_FIELDS = (
    "market_event_received_ts",
    "features_ready_ts",
    "decision_ready_ts",
    "risk_check_ready_ts",
    "order_ready_ts",
    "order_api_call_start_ts",
    "order_send_ts",
    "order_send_return_ts",
    "ack_received_ts",
    "cancel_send_ts",
    "cancel_ack_received_ts",
    "replace_send_ts",
    "replace_ack_received_ts",
)

METRIC_FIELDS = (
    "tick_to_decision_us",
    "decision_to_send_trigger_us",
    "tick_to_send_trigger_us",
    "decision_to_send_us",
    "tick_to_send_us",
    "rithmic_send_call_us",
    "send_to_ack_us",
    "cancel_to_send_us",
    "cancel_to_ack_us",
    "replace_to_send_us",
    "replace_to_ack_us",
)

ORDER_ACTIONS = ("new", "cancel", "replace")
ACTION_REQUIRED_TIMESTAMPS = {
    "new": ("market_event_received_ts", "decision_ready_ts", "order_send_ts"),
    "cancel": ("decision_ready_ts", "cancel_send_ts"),
    "replace": ("decision_ready_ts", "replace_send_ts"),
}
ACTION_SUCCESS_ACK_TIMESTAMPS = {
    "new": "ack_received_ts",
    "cancel": "cancel_ack_received_ts",
    "replace": "replace_ack_received_ts",
}


def monotonic_ns() -> int:
    """Return a high-resolution monotonic timestamp in nanoseconds."""

    return time.perf_counter_ns()


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def dated_jsonl_path(root: Path, run_id: str, timestamp_utc: str | None = None) -> Path:
    stamp = timestamp_utc or utc_now_iso()
    day = stamp[:10]
    return root / "data" / "latency_baselines" / day / f"{run_id}.jsonl"


def _duration_us(start_ns: int | None, end_ns: int | None) -> float | None:
    if start_ns is None or end_ns is None:
        return None
    if end_ns < start_ns:
        return None
    return (end_ns - start_ns) / 1000.0


def _coerce_timestamps(raw: dict[str, Any]) -> dict[str, int | None]:
    timestamps: dict[str, int | None] = {}
    for field in TIMESTAMP_FIELDS:
        value = raw.get(field)
        timestamps[field] = None if value is None else int(value)
    if timestamps["order_api_call_start_ts"] is None and timestamps["order_send_ts"] is not None:
        timestamps["order_api_call_start_ts"] = timestamps["order_send_ts"]
    if timestamps["order_send_return_ts"] is None and timestamps["order_send_ts"] is not None:
        timestamps["order_send_return_ts"] = timestamps["order_send_ts"]
    return timestamps


def _metric_values(timestamps: dict[str, int | None], *, order_action: str) -> dict[str, float | None]:
    values = {
        "tick_to_decision_us": _duration_us(
            timestamps["market_event_received_ts"],
            timestamps["decision_ready_ts"],
        ),
        "decision_to_send_trigger_us": _duration_us(
            timestamps["decision_ready_ts"],
            timestamps["order_api_call_start_ts"],
        ),
        "tick_to_send_trigger_us": _duration_us(
            timestamps["market_event_received_ts"],
            timestamps["order_api_call_start_ts"],
        ),
        "decision_to_send_us": _duration_us(
            timestamps["decision_ready_ts"],
            timestamps["order_send_return_ts"],
        ),
        "tick_to_send_us": _duration_us(
            timestamps["market_event_received_ts"],
            timestamps["order_send_return_ts"],
        ),
        "rithmic_send_call_us": _duration_us(
            timestamps["order_api_call_start_ts"],
            timestamps["order_send_return_ts"],
        ),
        "send_to_ack_us": _duration_us(
            timestamps["order_send_return_ts"],
            timestamps["ack_received_ts"],
        ),
        "cancel_to_send_us": _duration_us(
            timestamps["decision_ready_ts"],
            timestamps["cancel_send_ts"],
        ),
        "cancel_to_ack_us": _duration_us(
            timestamps["cancel_send_ts"],
            timestamps["cancel_ack_received_ts"],
        ),
        "replace_to_send_us": _duration_us(
            timestamps["decision_ready_ts"],
            timestamps["replace_send_ts"],
        ),
        "replace_to_ack_us": _duration_us(
            timestamps["replace_send_ts"],
            timestamps["replace_ack_received_ts"],
        ),
    }
    if order_action == "new":
        values["cancel_to_send_us"] = None
        values["cancel_to_ack_us"] = None
        values["replace_to_send_us"] = None
        values["replace_to_ack_us"] = None
    elif order_action == "cancel":
        values["tick_to_decision_us"] = None
        values["decision_to_send_trigger_us"] = None
        values["tick_to_send_trigger_us"] = None
        values["decision_to_send_us"] = None
        values["tick_to_send_us"] = None
        values["rithmic_send_call_us"] = None
        values["send_to_ack_us"] = None
        values["replace_to_send_us"] = None
        values["replace_to_ack_us"] = None
    elif order_action == "replace":
        values["tick_to_decision_us"] = None
        values["decision_to_send_trigger_us"] = None
        values["tick_to_send_trigger_us"] = None
        values["decision_to_send_us"] = None
        values["tick_to_send_us"] = None
        values["rithmic_send_call_us"] = None
        values["send_to_ack_us"] = None
        values["cancel_to_send_us"] = None
        values["cancel_to_ack_us"] = None
    return values


def build_latency_sample(
    *,
    run_id: str,
    environment: str,
    broker: str,
    venue: str,
    symbol: str,
    strategy_id: str,
    model_id: str = "",
    trade_manager_id: str = "",
    order_action: str = "new",
    side: str = "",
    order_type: str = "",
    quantity: float | int | None = None,
    timestamps: dict[str, Any] | None = None,
    timestamp_utc: str | None = None,
    success: bool = True,
    reject_reason: str = "",
) -> dict[str, Any]:
    """Build one latency baseline record from monotonic timestamp probes."""

    raw_timestamps = _coerce_timestamps(timestamps or {})
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "timestamp_utc": timestamp_utc or utc_now_iso(),
        "environment": environment,
        "broker": broker,
        "venue": venue,
        "symbol": symbol,
        "strategy_id": strategy_id,
        "model_id": model_id,
        "trade_manager_id": trade_manager_id,
        "order_action": order_action,
        "side": side,
        "order_type": order_type,
        "quantity": quantity,
        **_metric_values(raw_timestamps, order_action=order_action),
        "success": bool(success),
        "reject_reason": reject_reason,
        "raw_timestamps": raw_timestamps,
    }
    errors = validate_latency_sample(record)
    if errors:
        raise ValueError("; ".join(errors))
    return record


def validate_latency_sample(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["latency sample must be a JSON object"]
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    for field in (
        "run_id",
        "timestamp_utc",
        "environment",
        "broker",
        "venue",
        "symbol",
        "strategy_id",
        "order_action",
        "raw_timestamps",
    ):
        if record.get(field) in (None, ""):
            errors.append(f"{field} required")
    action = record.get("order_action")
    if action not in ORDER_ACTIONS:
        errors.append(f"order_action must be one of {', '.join(ORDER_ACTIONS)}")
    _check_optional_number(errors, record, "quantity")
    raw = record.get("raw_timestamps")
    if not isinstance(raw, dict):
        errors.append("raw_timestamps must be an object")
    else:
        for field in TIMESTAMP_FIELDS:
            if field not in raw:
                errors.append(f"raw_timestamps.{field} missing")
            elif raw[field] is not None:
                try:
                    value = int(raw[field])
                except (TypeError, ValueError):
                    errors.append(f"raw_timestamps.{field} must be integer nanoseconds")
                    continue
                if value < 0:
                    errors.append(f"raw_timestamps.{field} must be non-negative")
        if action in ACTION_REQUIRED_TIMESTAMPS:
            for field in ACTION_REQUIRED_TIMESTAMPS[str(action)]:
                if raw.get(field) is None:
                    errors.append(f"raw_timestamps.{field} required for {action} action")
        if record.get("success") is True and action in ACTION_SUCCESS_ACK_TIMESTAMPS:
            ack_field = ACTION_SUCCESS_ACK_TIMESTAMPS[str(action)]
            if raw.get(ack_field) is None:
                errors.append(f"raw_timestamps.{ack_field} required when {action} action succeeds")
        _check_order(errors, raw, ("market_event_received_ts", "features_ready_ts", "decision_ready_ts"))
        _check_order(
            errors,
            raw,
            (
                "decision_ready_ts",
                "risk_check_ready_ts",
                "order_ready_ts",
                "order_api_call_start_ts",
                "order_send_ts",
                "order_send_return_ts",
                "ack_received_ts",
            ),
        )
        _check_order(errors, raw, ("decision_ready_ts", "cancel_send_ts", "cancel_ack_received_ts"))
        _check_order(errors, raw, ("decision_ready_ts", "replace_send_ts", "replace_ack_received_ts"))
    for field in METRIC_FIELDS:
        value = record.get(field)
        if value is not None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                errors.append(f"{field} must be numeric")
                continue
            if not math.isfinite(number):
                errors.append(f"{field} must be finite")
            elif number < 0:
                errors.append(f"{field} must be non-negative")
    return errors


def _check_optional_number(errors: list[str], record: dict[str, Any], field: str) -> None:
    value = record.get(field)
    if value is None or value == "":
        return
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"{field} must be numeric")
        return
    if not math.isfinite(number):
        errors.append(f"{field} must be finite")


def _check_order(errors: list[str], raw: dict[str, Any], fields: tuple[str, ...]) -> None:
    present = [(field, raw.get(field)) for field in fields if raw.get(field) is not None]
    for (prev_field, prev_value), (field, value) in zip(present, present[1:]):
        if int(value) < int(prev_value):
            errors.append(f"raw_timestamps.{field} before {prev_field}")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_no}: latency sample must be a JSON object")
        errors = validate_latency_sample(record)
        if errors:
            raise ValueError(f"{path}:{line_no}: invalid latency sample: {'; '.join(errors)}")
        records.append(record)
    return records


@dataclass
class LatencyRecorder:
    repo_root: Path
    run_id: str
    environment: str
    broker: str
    venue: str
    symbol: str
    strategy_id: str
    model_id: str = ""
    trade_manager_id: str = ""
    jsonl_path: Path | None = None
    _explicit_jsonl_path: bool = field(init=False, default=False)
    _sample_written: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)
        self._explicit_jsonl_path = self.jsonl_path is not None
        if self.jsonl_path is None:
            self.jsonl_path = dated_jsonl_path(self.repo_root, self.run_id)

    def sample_path(self) -> Path:
        if self.jsonl_path is None:
            raise RuntimeError("jsonl_path not initialized")
        return self.jsonl_path

    def write_sample(
        self,
        *,
        order_action: str,
        timestamps: dict[str, Any],
        side: str = "",
        order_type: str = "",
        quantity: float | int | None = None,
        success: bool = True,
        reject_reason: str = "",
        timestamp_utc: str | None = None,
        ) -> dict[str, Any]:
        if timestamp_utc and not self._explicit_jsonl_path and not self._sample_written:
            self.jsonl_path = dated_jsonl_path(self.repo_root, self.run_id, timestamp_utc=timestamp_utc)
        record = build_latency_sample(
            run_id=self.run_id,
            timestamp_utc=timestamp_utc,
            environment=self.environment,
            broker=self.broker,
            venue=self.venue,
            symbol=self.symbol,
            strategy_id=self.strategy_id,
            model_id=self.model_id,
            trade_manager_id=self.trade_manager_id,
            order_action=order_action,
            side=side,
            order_type=order_type,
            quantity=quantity,
            timestamps=timestamps,
            success=success,
            reject_reason=reject_reason,
        )
        path = self.sample_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        self._sample_written = True
        return record
