"""L3-only lane policy: reject non-MBO / degraded sessions on research paths."""
from __future__ import annotations

from equities_lane.src.types import SessionMeta


class L3OnlyViolation(Exception):
    """Raised when a session is not full L3 (MBO) tape."""


def require_l3_session(
    meta: SessionMeta,
    *,
    l3_only: bool,
    allow_degraded: bool = False,
    context: str = "run",
) -> None:
    if not l3_only or allow_degraded:
        return
    if meta.degraded.degraded_mode:
        notes = "; ".join(meta.degraded.assumptions) or "degraded_mode=true"
        raise L3OnlyViolation(
            f"L3-only lane: cannot {context} on degraded session "
            f"{meta.symbol} {meta.session_date} ({notes})"
        )
