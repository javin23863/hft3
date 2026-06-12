"""Read-only autonomy status snapshot for the cockpit (ML9)."""
from __future__ import annotations

from . import audit, breaker, config


def snapshot() -> dict:
    cfg = config.load_config()
    return {
        "env_enabled": config.env_enabled(),
        "file_enabled": cfg.enabled,
        "master_enabled": config.master_enabled(),
        "kill_engaged": config.kill_engaged(),
        "actions": cfg.actions,
        "rearm_allow_live": cfg.rearm_allow_live,
        "scope_models": list(cfg.scope_models),
        "can_arm_live": config.can_arm_live(),
        "breaker": breaker.state_snapshot(),
        "audit_chain_ok": audit.verify_chain(),
        "recent_audit": audit.tail(20),
    }
