"""Execution mode safety counters and guards."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from execution.interfaces import ExecutionAdapter

live_broker_call_count: int = 0
rithmic_order_call_count: int = 0


def reset_counters() -> None:
    global live_broker_call_count, rithmic_order_call_count
    live_broker_call_count = 0
    rithmic_order_call_count = 0


def record_live_broker_call() -> None:
    global live_broker_call_count
    live_broker_call_count += 1


def record_rithmic_order_call() -> None:
    global rithmic_order_call_count
    rithmic_order_call_count += 1


def execution_mode() -> str:
    return os.environ.get("EXECUTION_MODE", "REPLAY").upper()


def assert_replay_safe(adapter: ExecutionAdapter, declared_mode: str | None = None) -> None:
    """Forbid live-capable adapters in a replay session.

    Key the check off the session's declared mode, not ambient env: a research
    replay launched from a shell with EXECUTION_MODE=PAPER/LIVE exported must
    still be checked. Callers that know their mode should pass it explicitly;
    the env fallback exists only for legacy call sites.
    """
    mode = (declared_mode or execution_mode()).upper()
    if mode != "REPLAY":
        return
    name = type(adapter).__name__
    forbidden = ("PaperBrokerAdapter", "LiveBrokerAdapter", "RithmicApiConnector")
    if any(x in name for x in forbidden):
        raise RuntimeError(f"REPLAY mode cannot use adapter {name}")


def assert_paper_safe(adapter: ExecutionAdapter, declared_mode: str | None = None) -> None:
    """Forbid live adapters in a paper session.

    Key the check off the session's declared mode, not ambient env: a factory
    call with mode="PAPER" from an EXECUTION_MODE=REPLAY shell must still be
    checked. Callers that know their mode should pass it explicitly;
    the env fallback exists only for legacy call sites.
    """
    mode = (declared_mode or execution_mode()).upper()
    if mode == "PAPER" and type(adapter).__name__ in ("LiveBrokerAdapter",):
        raise RuntimeError(f"PAPER mode cannot use {type(adapter).__name__}")


def assert_live_config(declared_mode: str | None = None) -> None:
    """Enforce required env vars for live sessions.

    Key the check off the session's declared mode, not ambient env: a factory
    call with mode="LIVE" from an EXECUTION_MODE=REPLAY shell must still be
    checked. Callers that know their mode should pass it explicitly;
    the env fallback exists only for legacy call sites.
    """
    mode = (declared_mode or execution_mode()).upper()
    if mode != "LIVE":
        return
    required = (
        "LIVE_MAX_ORDER_SIZE",
        "LIVE_DAILY_LOSS_LIMIT",
        "LIVE_KILL_SWITCH",
        "LIVE_RISK_ENABLED",
    )
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"LIVE mode missing required config: {missing}")


def counter_snapshot() -> dict[str, int]:
    return {
        "live_broker_call_count": live_broker_call_count,
        "rithmic_order_call_count": rithmic_order_call_count,
    }
