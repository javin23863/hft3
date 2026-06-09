"""Walk-forward period enforcement — B4 invariant.

Discovery: 2018-2020 (tune allowed)
Confirmation: 2021-2022 (frozen params)
Holdout: 2023-2024 (evaluate-only)
Recent holdout: 2025 (evaluate-only)
Sim shadow: 2026+ (CHI404 only)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PeriodAction(str, Enum):
    TUNE_ALLOWED = "TUNE_ALLOWED"
    EVALUATE_ONLY = "EVALUATE_ONLY"
    BLOCKED = "BLOCKED"


@dataclass
class WalkForwardPeriod:
    name: str
    start_year: int
    end_year: int
    action: PeriodAction
    is_holdout: bool = False


PERIODS = [
    WalkForwardPeriod("Discovery", 2018, 2020, PeriodAction.TUNE_ALLOWED),
    WalkForwardPeriod("Confirmation", 2021, 2022, PeriodAction.EVALUATE_ONLY),
    WalkForwardPeriod("Holdout", 2023, 2024, PeriodAction.EVALUATE_ONLY, is_holdout=True),
    WalkForwardPeriod("Recent holdout", 2025, 2025, PeriodAction.EVALUATE_ONLY, is_holdout=True),
    WalkForwardPeriod("Sim shadow", 2026, 2099, PeriodAction.BLOCKED),
]


_EVENT_ID_YEAR_RE = re.compile(r"(?:^|_)(\d{4})_\d{2}_\d{2}")


def extract_event_year(event_id: str) -> Optional[int]:
    """Extract the year from an event_id like CPI_2024_09_11_TIGHT -> 2024."""
    m = _EVENT_ID_YEAR_RE.search(event_id)
    if m:
        return int(m.group(1))
    return None


def classify_period(event_year: int) -> Optional[WalkForwardPeriod]:
    for p in PERIODS:
        if p.start_year <= event_year <= p.end_year:
            return p
    return None


def check_tuning_allowed(
    event_id: str,
    *,
    is_tuning_stage: bool = True,
    fixture_mode: bool = False,
) -> tuple[bool, str]:
    """Check whether tuning is permitted for this event given walk-forward rules.

    Returns (allowed, reason). If not allowed, the caller must skip tuning.
    """
    year = extract_event_year(event_id)
    if year is None:
        return True, "unknown_event_year"

    period = classify_period(year)
    if period is None:
        return True, f"year_{year}_outside_known_periods"

    if fixture_mode:
        return True, f"fixture_mode_bypass_{period.name}"

    if period.action == PeriodAction.TUNE_ALLOWED:
        return True, f"tuning_allowed_{period.name}"

    if period.action == PeriodAction.EVALUATE_ONLY:
        return False, f"evaluate_only_{period.name}_{year}_is_holdout={period.is_holdout}"

    if period.action == PeriodAction.BLOCKED:
        return False, f"blocked_{period.name}_{year}"

    return True, f"unclassified_{event_id}"


def check_evaluation_allowed(event_id: str, *, fixture_mode: bool = False) -> tuple[bool, str]:
    """Check whether evaluation is permitted for this event.

    Sim shadow (2026+) events are blocked for workstation research.
    """
    year = extract_event_year(event_id)
    if year is None:
        return True, "unknown_event_year"

    period = classify_period(year)
    if period is None:
        return True, f"year_{year}_outside_known_periods"

    if period.action == PeriodAction.BLOCKED and not fixture_mode:
        return False, f"blocked_{period.name}_{year}_requires_CHI404"

    return True, f"evaluation_allowed_{period.name}_{year}"
