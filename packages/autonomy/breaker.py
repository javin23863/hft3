"""Circuit breaker — auto-freezes autonomy on repeated failure. Human-only clear.

Trip conditions (any => freeze, fail-closed): N failed/killed arms in a window,
demote<->rearm thrash per model, correlated mass-demotion. A frozen breaker
blocks every autonomous action and can ONLY be cleared by a human
(``clear(operator=...)``, surfaced via the cockpit control plane). An
unreadable/corrupt state file is treated as frozen.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import paths

_DEFAULT = {"frozen": False, "freeze_reason": "", "frozen_at": "", "arm_outcomes": [], "demotions": []}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        t = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        d = datetime.fromisoformat(t)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _read() -> dict:
    try:
        with open(paths.state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {**_DEFAULT, "frozen": True, "freeze_reason": "corrupt state"}
        return {**_DEFAULT, **data}
    except FileNotFoundError:
        return dict(_DEFAULT)
    except (json.JSONDecodeError, OSError):
        # Fail-closed: cannot read => behave as frozen.
        return {**_DEFAULT, "frozen": True, "freeze_reason": "unreadable state"}


def _write(state: dict) -> None:
    p = paths.state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(p)


def is_frozen() -> bool:
    return bool(_read().get("frozen", False))


def state_snapshot() -> dict:
    """Read-only view for the cockpit."""
    return _read()


def trip(reason: str, *, ts: Optional[str] = None) -> None:
    state = _read()
    state["frozen"] = True
    state["freeze_reason"] = reason
    state["frozen_at"] = ts or _now().isoformat()
    _write(state)


def clear(*, operator: str, ts: Optional[str] = None) -> None:
    """Human-only unfreeze. Records who cleared it."""
    state = _read()
    state["frozen"] = False
    state["freeze_reason"] = ""
    state["cleared_by"] = operator
    state["cleared_at"] = ts or _now().isoformat()
    _write(state)


def record_arm_outcome(model_id: str, outcome: str, *, ts: Optional[str] = None) -> None:
    """outcome in {pass, fail, killed}."""
    state = _read()
    state.setdefault("arm_outcomes", []).append(
        {"model_id": model_id, "outcome": outcome, "ts": ts or _now().isoformat()}
    )
    _write(state)


def record_demotion(model_id: str, *, ts: Optional[str] = None) -> None:
    state = _read()
    state.setdefault("demotions", []).append({"model_id": model_id, "ts": ts or _now().isoformat()})
    _write(state)


def _within(ts: Optional[str], now: datetime, hours: float) -> bool:
    d = _parse(ts)
    return d is not None and (now - d).total_seconds() <= hours * 3600.0


def evaluate_and_maybe_trip(cfg: Any, *, now: Optional[datetime] = None) -> Optional[str]:
    """Check trip conditions against the rolling history. Trips + returns reason, else None."""
    now = now or _now()
    state = _read()
    if state.get("frozen"):
        return state.get("freeze_reason") or "frozen"

    outcomes = state.get("arm_outcomes", [])
    failed = [o for o in outcomes
              if o.get("outcome") in {"fail", "killed"} and _within(o.get("ts"), now, cfg.window_hours)]
    if len(failed) >= cfg.max_failed_arms_in_window:
        reason = f"{len(failed)} failed/killed arms within {cfg.window_hours}h"
        trip(reason, ts=now.isoformat())
        return reason

    # thrash: per-model arm cycles in the thrash window
    by_model: dict[str, int] = {}
    for o in outcomes:
        if _within(o.get("ts"), now, cfg.thrash_window_hours):
            by_model[o.get("model_id")] = by_model.get(o.get("model_id"), 0) + 1
    for mid, n in by_model.items():
        if n >= cfg.max_arm_cycles_per_model:
            reason = f"thrash: {mid} armed {n}x within {cfg.thrash_window_hours}h"
            trip(reason, ts=now.isoformat())
            return reason

    # correlated mass-demotion
    recent_dem = [d for d in state.get("demotions", [])
                  if _within(d.get("ts"), now, cfg.correlation_window_minutes / 60.0)]
    if len(recent_dem) >= cfg.max_simultaneous_demotions:
        reason = f"correlated mass-demotion: {len(recent_dem)} within {cfg.correlation_window_minutes}min"
        trip(reason, ts=now.isoformat())
        return reason

    return None
