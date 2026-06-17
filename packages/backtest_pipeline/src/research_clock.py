"""Closed three-category research clock taxonomy (VectorBT Gate 8).

Authority:
- docs/project/OPPORTUNITY_RESEARCH_SPEC.md (scheduled-event, context-feature
  uplift, continuous intraday clocks)
- docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md Acceptance Gate item 8
"""
from __future__ import annotations

RESEARCH_CLOCK_SCHEDULED_EVENT = "scheduled_event"
RESEARCH_CLOCK_CONTEXT_FEATURE_UPLIFT = "context_feature_uplift"
RESEARCH_CLOCK_CONTINUOUS_INTRADAY = "continuous_intraday"

RESEARCH_CLOCK_VALUES: frozenset[str] = frozenset(
    {
        RESEARCH_CLOCK_SCHEDULED_EVENT,
        RESEARCH_CLOCK_CONTEXT_FEATURE_UPLIFT,
        RESEARCH_CLOCK_CONTINUOUS_INTRADAY,
    }
)

# Pilot-era label for scheduled-event screening; accepted for backward compatibility.
RESEARCH_CLOCK_LEGACY_ALIASES: dict[str, str] = {
    "event_window_pilot": RESEARCH_CLOCK_SCHEDULED_EVENT,
    "context_uplift": RESEARCH_CLOCK_CONTEXT_FEATURE_UPLIFT,
}


class ResearchClockError(ValueError):
    """Raised when research_clock is outside the closed three-category enum."""


def canonicalize_research_clock(value: str) -> str:
    """Return the canonical enum member for a research_clock label."""
    normalized = str(value).strip()
    if not normalized:
        raise ResearchClockError("research_clock_empty")
    lowered = normalized.lower().replace("-", "_")
    if lowered in RESEARCH_CLOCK_VALUES:
        return lowered
    aliased = RESEARCH_CLOCK_LEGACY_ALIASES.get(lowered)
    if aliased is not None:
        return aliased
    allowed = sorted(RESEARCH_CLOCK_VALUES)
    raise ResearchClockError(
        f"research_clock_invalid:{normalized}; allowed={allowed}"
    )


def validate_research_clock(value: str, *, context: str = "research_clock") -> str:
    """Validate and return canonical research_clock or raise ResearchClockError."""
    try:
        return canonicalize_research_clock(value)
    except ResearchClockError as exc:
        raise ResearchClockError(f"{context}:{exc}") from exc


def research_clock_validation_errors(
    value: object,
    *,
    context: str = "research_clock",
) -> list[str]:
    """Return fail-closed validation error tokens (empty when valid)."""
    try:
        validate_research_clock(str(value), context=context)
    except (ResearchClockError, TypeError):
        return [f"{context}_invalid"]
    return []
