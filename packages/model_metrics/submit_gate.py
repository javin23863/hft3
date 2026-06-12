"""Live-engine submit gate — the call site that makes lifecycle state ENFORCING.

The trade-manager risk layer consults this before allowing an order. Untracked
models pass through unchanged (the registry only governs models it tracks), so
this is a no-op for the current system and only bites a model the lifecycle
registry has marked non-tradeable or RED. mtime-cached so the hot path reads the
registry at most once per change.
"""
from __future__ import annotations

import json
from typing import Tuple

from . import decay_detector, lifecycle

_cache: dict = {"mtime": None, "reg": {}}


def _load() -> dict:
    p = lifecycle.registry_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] == mtime:
        return _cache["reg"]
    try:
        with open(p, "r", encoding="utf-8") as fh:
            reg = json.load(fh).get("models", {}) or {}
    except (json.JSONDecodeError, OSError):
        reg = {}
    _cache["mtime"], _cache["reg"] = mtime, reg
    return reg


def model_submit_decision(model_id: str) -> Tuple[bool, float, str]:
    """(allowed, size_factor, reason). Untracked => (True, 1.0). Tracked-bad => (False, 0.0)."""
    rec = _load().get(model_id)
    if rec is None:
        return (True, 1.0, "untracked")
    state = rec.get("current_state", "")
    model_state = (rec.get("last_revalidation") or {}).get("model_state", "GREEN")
    allowed, size = decay_detector.submit_allowed(state, model_state)
    return (allowed, size, f"lifecycle={state} model_state={model_state}")
