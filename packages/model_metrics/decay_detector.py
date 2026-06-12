"""Edge-decay detector + demotion routing + the enforced submit gate.

Reuses ``state_engine.classify_model_state`` (the decay kernel) unchanged: it
compares a fresh observation (a re-validation backtest now; the live stream
later) against the model's FROZEN certified envelope and emits trigger names.
This module maps those trigger names to a demotion ROUTE and a target lifecycle
state, and DEFINES the order-submission policy (``submit_allowed``) the live
engine must consult to make GREEN/YELLOW/RED enforcing.

STATUS: ``submit_allowed`` / ``evaluate`` are the policy + detector functions; a
driver (``run_lifecycle_eval``) and the live-engine call site that consult them
are NOT yet wired. Today nothing auto-demotes or gates orders from this — it is
the policy layer, not yet the enforcement call site.

Routing (keyed off the exact trigger names emitted by state_engine):
  * ONLY regime triggers            -> regime_shift   -> ARCHIVED_PAUSED (auto re-arm)
  * drawdown/feature-domain/etc.    -> edge_gone      -> RETIRED   (provisional; the
                                       orchestrator's re-validation confirms vs reroute)
  * execution drift                 -> param_tweak    -> GAUNTLET  (re-fit params)
  * unexplained alpha-shape drift   -> hypothesis_tweak -> SCREENING (F3 variant)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from model_metrics.state_engine import classify_model_state

from . import lifecycle

# --- Trigger-name -> route classification -----------------------------------
_REGIME = frozenset({"blocked_regime", "unapproved_regime"})

_EXECUTION = frozenset({
    "slippage", "slippage_drift", "fill_rate", "spread",
    "latency_drift", "latency_exceeds_alpha_half_life",
    "placement_trigger_latency_drift", "placement_latency_drift",
    "placement_exceeds_opportunity_window", "placement_trigger_exceeds_opportunity_window",
    "decision_to_trigger_latency_drift", "decision_to_send_latency_drift",
    "rithmic_send_call_latency_drift", "trade_count_high", "trade_count_low",
    "order_reject_rate",
})

# Infra/data faults — NOT a model-fit problem. Re-fitting params won't fix a
# dead feed or a failed exec-path audit. Route to ops review (no research route);
# the model is taken offline but not auto-sent to re-training.
_INFRA = frozenset({
    "async_ack_stale_state", "data_freshness", "low_latency_execution_path_audit",
    "envelope_inactive",
})

# Serious edge-failure signals: thesis may be dead. Provisional — the orchestrator
# re-validates on the model's own regime before actually RETIRING (never auto-retire
# from a single observation).
_EDGE_GONE = frozenset({
    "drawdown_kill_threshold", "drawdown_warning_threshold", "drawdown_velocity",
    "feature_training_domain", "high_confidence_wrong", "book_correlation",
})

_ROUTE_TARGET = {
    lifecycle.ROUTE_REGIME_SHIFT: lifecycle.ARCHIVED_PAUSED,
    lifecycle.ROUTE_PARAM_TWEAK: lifecycle.GAUNTLET,
    lifecycle.ROUTE_HYPOTHESIS_TWEAK: lifecycle.SCREENING,
    lifecycle.ROUTE_EDGE_GONE: lifecycle.RETIRED,
}


def route_demotion(triggers: list[dict]) -> Optional[str]:
    """Map a set of trigger dicts to a demotion route. None if no triggers."""
    names = {t.get("name") for t in triggers if t.get("name")}
    if not names:
        return None
    if names <= _REGIME:  # ONLY regime triggers fired -> edge intact, regime gated
        return lifecycle.ROUTE_REGIME_SHIFT
    if names <= _INFRA:  # ONLY infra/data faults -> ops review, not a research route
        return None
    if names & _EDGE_GONE:
        return lifecycle.ROUTE_EDGE_GONE
    if names & _EXECUTION:
        return lifecycle.ROUTE_PARAM_TWEAK
    return lifecycle.ROUTE_HYPOTHESIS_TWEAK


def target_state_for_route(route: Optional[str]) -> Optional[str]:
    return _ROUTE_TARGET.get(route) if route else None


# --- Enforced submit gate (turns advisory state into policy) ----------------
def enforcement_for_state(state: str) -> dict:
    """Graduated YELLOW->RED enforcement (owner decision)."""
    if state == "GREEN":
        return {"action": "allow", "size_factor": 1.0, "raise_threshold": False}
    if state == "YELLOW":
        return {"action": "size_down", "size_factor": 0.5, "raise_threshold": True}
    return {"action": "flatten_offline", "size_factor": 0.0, "raise_threshold": True}


# Lifecycle states in which a model may emit orders at all.
_SUBMIT_STATES = frozenset({lifecycle.LIVE, lifecycle.DEGRADED})


def submit_allowed(lifecycle_state: str, model_state: str = "GREEN") -> tuple[bool, float]:
    """The hard submit gate the live engine consults before emitting an order.

    Returns (allowed, size_factor). Only LIVE (full size) and DEGRADED (reduced)
    may trade; QUARANTINED/ARCHIVED_PAUSED/RETIRED/etc. are flat. RED model_state
    forces flat even in an otherwise-tradeable lifecycle state. Fail-closed:
    unknown state => not allowed.
    """
    if lifecycle_state not in _SUBMIT_STATES:
        return (False, 0.0)
    enf = enforcement_for_state(model_state)
    return (enf["size_factor"] > 0.0, enf["size_factor"])


@dataclass
class DecayResult:
    model_state: str            # GREEN / YELLOW / RED
    demote: bool
    route: Optional[str]
    target_state: Optional[str]
    enforcement: dict
    triggers: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "model_state": self.model_state,
            "demote": self.demote,
            "route": self.route,
            "target_state": self.target_state,
            "enforcement": self.enforcement,
            "triggers": self.triggers,
            "reason": self.reason,
        }


def evaluate(envelope_snapshot: Any, observation: Any) -> DecayResult:
    """Classify a fresh observation against the frozen certified envelope."""
    decision = classify_model_state(envelope_snapshot, observation)
    triggers = list(decision.triggers)
    if decision.state == "GREEN":
        return DecayResult("GREEN", False, None, None, enforcement_for_state("GREEN"), [], "within envelope")
    route = route_demotion(triggers)
    names = sorted({t.get("name") for t in triggers if t.get("name")})
    return DecayResult(
        model_state=decision.state,
        demote=True,
        route=route,
        target_state=target_state_for_route(route),
        enforcement=enforcement_for_state(decision.state),
        triggers=triggers,
        reason=f"{decision.state}: {', '.join(names)}",
    )
