"""Execution runtime safety counters and guards."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from execution.interfaces import ExecutionAdapter

broker_call_count: int = 0
rithmic_order_call_count: int = 0


def reset_counters() -> None:
    global broker_call_count, rithmic_order_call_count
    broker_call_count = 0
    rithmic_order_call_count = 0


def record_broker_call() -> None:
    global broker_call_count
    broker_call_count += 1


def record_rithmic_order_call() -> None:
    global rithmic_order_call_count
    rithmic_order_call_count += 1


def execution_mode() -> str:
    return os.environ.get("EXECUTION_MODE", "REPLAY").upper()


def assert_replay_safe(adapter: ExecutionAdapter) -> None:
    mode = execution_mode()
    if mode != "REPLAY":
        return
    name = type(adapter).__name__
    forbidden = ("BrokerAdapter", "RithmicApiConnector")
    if any(x in name for x in forbidden):
        raise RuntimeError(f"REPLAY mode cannot use adapter {name}")


def assert_external_config() -> None:
    if execution_mode() != "EXTERNAL":
        return
    required = (
        "EXTERNAL_MAX_ORDER_SIZE",
        "EXTERNAL_DAILY_LOSS_LIMIT",
        "EXTERNAL_KILL_SWITCH",
        "EXTERNAL_RISK_ENABLED",
    )
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"EXTERNAL mode missing required config: {missing}")


def counter_snapshot() -> dict[str, int]:
    return {
        "broker_call_count": broker_call_count,
        "rithmic_order_call_count": rithmic_order_call_count,
    }
