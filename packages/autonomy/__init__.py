"""Autonomy safety rails — the subsystem every autonomous action is subordinate to.

Nothing in the maintenance loop may demote/retest/repromote/arm unless these
rails permit it. Default posture is FULLY DISABLED (two-key, fail-closed): a
missing/corrupt config, a missing env key, an engaged kill, or a tripped
circuit breaker all resolve to "not allowed". The rails can only ever make the
gate chain stricter, never looser.
"""
from .config import (  # noqa: F401
    ACTIONS,
    AutonomyConfig,
    action_enabled,
    can_arm_live,
    kill_engaged,
    load_config,
    master_enabled,
)
from .gates import AUTONOMY_REQUIRED_GATES, GateResult, evaluate_gate_chain  # noqa: F401
