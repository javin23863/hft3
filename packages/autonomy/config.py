"""Two-key master enable for autonomy. Fail-closed by construction.

Both keys are required for ANY autonomous action:
  1. env  HFT3_AUTONOMY_ENABLED=1
  2. file configs/autonomy.yaml  ->  enabled: true  + per-action scope flags

The most dangerous action (arm-to-live) needs a third sub-flag,
``rearm.allow_live: true``. Default construction (no file, no env) yields every
capability False. Any parse/IO error resolves to disabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import breaker, paths

ACTIONS = frozenset({"demote", "retest", "repromote", "rearm"})


@dataclass(frozen=True)
class AutonomyConfig:
    enabled: bool = False
    actions: dict = field(default_factory=dict)        # action -> bool
    rearm_allow_live: bool = False
    scope_models: tuple = ()                            # () = all
    # circuit-breaker tunables
    max_failed_arms_in_window: int = 2
    window_hours: float = 72.0
    max_arm_cycles_per_model: int = 2
    thrash_window_hours: float = 24.0
    max_simultaneous_demotions: int = 2
    correlation_window_minutes: float = 30.0

    def action_scope_ok(self, action: str) -> bool:
        return bool(self.actions.get(action, False))


def env_enabled() -> bool:
    import os

    return os.environ.get("HFT3_AUTONOMY_ENABLED") == "1"


def kill_engaged() -> bool:
    """True (fail-closed) if the autonomy kill is fired OR the breaker is frozen.

    Separate from the trading kill-switch: this stops the LOOP, not the book.
    """
    import os

    if os.environ.get("HFT3_AUTONOMY_KILL", "").lower() == "fired":
        return True
    try:
        return breaker.is_frozen()
    except Exception:
        return True  # unreadable breaker state => treat as engaged


def load_config() -> AutonomyConfig:
    """Load + validate the config file. Any error => disabled defaults."""
    path = paths.config_path()
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as fh:
            raw: Any = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            return AutonomyConfig()
        actions = raw.get("actions") or {}
        rearm = raw.get("rearm") or {}
        bk = raw.get("circuit_breaker") or {}
        return AutonomyConfig(
            enabled=bool(raw.get("enabled", False)),
            actions={str(k): bool(v) for k, v in actions.items()},
            rearm_allow_live=bool(rearm.get("allow_live", False)),
            scope_models=tuple(raw.get("scope_models", []) or ()),
            max_failed_arms_in_window=int(bk.get("max_failed_arms_in_window", 2)),
            window_hours=float(bk.get("window_hours", 72.0)),
            max_arm_cycles_per_model=int(bk.get("max_arm_cycles_per_model", 2)),
            thrash_window_hours=float(bk.get("thrash_window_hours", 24.0)),
            max_simultaneous_demotions=int(bk.get("max_simultaneous_demotions", 2)),
            correlation_window_minutes=float(bk.get("correlation_window_minutes", 30.0)),
        )
    except FileNotFoundError:
        return AutonomyConfig()
    except Exception:
        return AutonomyConfig()  # fail-closed on malformed config


def master_enabled() -> bool:
    """Both keys present AND kill not engaged."""
    return env_enabled() and load_config().enabled and not kill_engaged()


def action_enabled(action: str, *, model_id: str | None = None) -> bool:
    """A specific autonomous action is permitted right now (fail-closed)."""
    if action not in ACTIONS:
        return False
    cfg = load_config()
    if not (env_enabled() and cfg.enabled and not kill_engaged()):
        return False
    if not cfg.action_scope_ok(action):
        return False
    if cfg.scope_models and model_id is not None and model_id not in cfg.scope_models:
        return False
    return True


def can_arm_live(*, model_id: str | None = None) -> bool:
    """The hardest gate: arm a model to LIVE. Requires the rearm sub-flag too."""
    return action_enabled("rearm", model_id=model_id) and load_config().rearm_allow_live
