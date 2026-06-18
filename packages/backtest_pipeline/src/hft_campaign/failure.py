"""Failure taxonomy and diagnostic payloads."""

from __future__ import annotations

import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


FAILURE_KINDS = frozenset(
    {
        "input_validation_failure",
        "engine_construction_failure",
        "data_load_failure",
        "book_not_live",
        "strategy_failure",
        "order_submission_failure",
        "replay_engine_error",
        "artifact_validation_failure",
        "worker_crash",
        "resource_exhaustion",
        "operator_abort",
    }
)


@dataclass
class FailureDiagnostic:
    campaign_id: str
    scenario_id: str
    candidate_id: str
    model_id: str
    symbol: str
    event_id: str
    worker_pid: int
    stage_name: str
    failure_kind: str
    exception_type: str
    exception_message: str
    traceback_text: str
    input_hashes: dict[str, str]
    effective_replay_configuration: dict[str, Any]
    hftbacktest_package_version: str
    code_commit: str
    start_timestamp_utc: str
    finish_timestamp_utc: str
    elapsed_s: float
    memory_usage_mb: float | None = None
    cache_state: dict[str, Any] | None = None
    last_replay_timestamp_ns: int | None = None
    last_wake_reason: int | None = None
    feed_events_processed: int = 0
    order_responses_processed: int = 0
    submitted_orders: int = 0
    open_orders: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_failure_diagnostic(
    *,
    campaign_id: str,
    scenario_id: str,
    candidate_id: str,
    model_id: str,
    symbol: str,
    event_id: str,
    worker_pid: int,
    stage_name: str,
    failure_kind: str,
    exc: BaseException | None = None,
    input_hashes: dict[str, str] | None = None,
    effective_replay_configuration: dict[str, Any] | None = None,
    hftbacktest_package_version: str = "",
    code_commit: str = "",
    start_timestamp_utc: str = "",
    elapsed_s: float = 0.0,
    **runtime_fields: Any,
) -> dict[str, Any]:
    if failure_kind not in FAILURE_KINDS:
        failure_kind = "replay_engine_error"
    finish = datetime.now(timezone.utc).isoformat()
    diagnostic = FailureDiagnostic(
        campaign_id=campaign_id,
        scenario_id=scenario_id,
        candidate_id=candidate_id,
        model_id=model_id,
        symbol=symbol,
        event_id=event_id,
        worker_pid=worker_pid,
        stage_name=stage_name,
        failure_kind=failure_kind,
        exception_type=type(exc).__name__ if exc else "",
        exception_message=str(exc) if exc else "",
        traceback_text=traceback.format_exc() if exc else "",
        input_hashes=input_hashes or {},
        effective_replay_configuration=effective_replay_configuration or {},
        hftbacktest_package_version=hftbacktest_package_version,
        code_commit=code_commit,
        start_timestamp_utc=start_timestamp_utc or finish,
        finish_timestamp_utc=finish,
        elapsed_s=elapsed_s,
        memory_usage_mb=runtime_fields.get("memory_usage_mb"),
        cache_state=runtime_fields.get("cache_state"),
        last_replay_timestamp_ns=runtime_fields.get("last_replay_timestamp_ns"),
        last_wake_reason=runtime_fields.get("last_wake_reason"),
        feed_events_processed=int(runtime_fields.get("feed_events_processed", 0)),
        order_responses_processed=int(runtime_fields.get("order_responses_processed", 0)),
        submitted_orders=int(runtime_fields.get("submitted_orders", 0)),
        open_orders=int(runtime_fields.get("open_orders", 0)),
    )
    return diagnostic.to_dict()
