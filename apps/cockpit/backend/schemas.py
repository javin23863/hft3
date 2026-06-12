"""Shared status vocabulary + small helpers for cockpit zone aggregators.

Kept deliberately light: aggregators return plain dicts (FastAPI serializes
them directly). This module only fixes the *vocabulary* so the four zones and
the front-end agree on what a status dot means.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# Stage / component status — drives the colored dot in the UI.
OK = "ok"            # ran, healthy
RUNNING = "running"  # in flight now
STALE = "stale"      # ran but older than its freshness budget
FAIL = "fail"        # ran and failed / gate red
MISSING = "missing"  # artifact absent — stage not yet run
UNKNOWN = "unknown"

# Health roll-up for a whole zone / the alert center.
GREEN = "green"
AMBER = "amber"
RED = "red"

# Severity for individual alerts.
SEV_INFO = "info"
SEV_WARN = "warn"
SEV_CRIT = "crit"


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string (tolerating a trailing 'Z') to aware UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        txt = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_seconds(value: Optional[str]) -> Optional[float]:
    """Seconds between an ISO timestamp and now, or None if unparseable."""
    dt = parse_iso(value)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()


def freshness(value: Optional[str], stale_after_s: float) -> str:
    """OK if within budget, STALE if older, MISSING if no timestamp."""
    age = age_seconds(value)
    if age is None:
        return MISSING
    return STALE if age > stale_after_s else OK
