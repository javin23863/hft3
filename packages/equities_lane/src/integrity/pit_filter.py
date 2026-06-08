"""Point-in-time filtration for equity and option data.

A run is rejected with a precise reason if:
- any equity tick has ts_ns > decision_ts_ns (future leakage)
- any option quote has ts_ns > decision_ts_ns (future leakage)
- any contract has listed_at_ts_ns > decision_ts_ns (retroactive pick)
- any float metadata row has as_of_date > session_date (forward float)

Clean runs (PIT violations = 0) are returned with leakage_status=CLEAN.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from equities_lane.src.models import SessionTick


@dataclass(frozen=True)
class PITCheckResult:
    is_pit_clean: bool
    contamination_count: int
    rejection_reason: str | None
    contaminated_samples: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "is_pit_clean": self.is_pit_clean,
            "contamination_count": self.contamination_count,
            "rejection_reason": self.rejection_reason,
            "contaminated_samples": list(self.contaminated_samples),
        }


def check_equity_ticks(
    ticks: Iterable[SessionTick],
    decision_ts_ns: int,
    *,
    max_samples: int = 5,
) -> PITCheckResult:
    """Reject the run if any tick has ts_ns > decision_ts_ns."""
    bad: list[str] = []
    count = 0
    for t in ticks:
        if t.ts_ns > decision_ts_ns:
            count += 1
            if len(bad) < max_samples:
                bad.append(
                    f"equity tick ts_ns={t.ts_ns} > decision_ts_ns={decision_ts_ns} "
                    f"(future leakage, +{(t.ts_ns - decision_ts_ns) / 1e9:.3f}s)"
                )
    if count > 0:
        return PITCheckResult(
            is_pit_clean=False,
            contamination_count=count,
            rejection_reason=(
                f"equity leakage: {count} tick(s) have ts_ns > decision_ts_ns "
                f"({(ticks and decision_ts_ns)}); first: {bad[0] if bad else ''}"
            ),
            contaminated_samples=tuple(bad),
        )
    return PITCheckResult(is_pit_clean=True, contamination_count=0, rejection_reason=None)


def check_option_quotes(
    quotes: Iterable[dict],
    decision_ts_ns: int,
    *,
    max_samples: int = 5,
) -> PITCheckResult:
    """Reject the run if any option quote has ts_ns > decision_ts_ns.

    Accepts either 'quote_ts_ns' or 'ts_ns' key for timestamp.
    """
    bad: list[str] = []
    count = 0
    for q in quotes:
        ts = int(q.get("quote_ts_ns", q.get("ts_ns", 0)) or 0)
        if ts > decision_ts_ns:
            count += 1
            if len(bad) < max_samples:
                bad.append(
                    f"option quote ts_ns={ts} > decision_ts_ns={decision_ts_ns} "
                    f"(future leakage, +{(ts - decision_ts_ns) / 1e9:.3f}s) "
                    f"symbol={q.get('symbol', '?')!r}"
                )
    if count > 0:
        return PITCheckResult(
            is_pit_clean=False,
            contamination_count=count,
            rejection_reason=(
                f"option leakage: {count} quote(s) have ts_ns > decision_ts_ns; "
                f"first: {bad[0] if bad else ''}"
            ),
            contaminated_samples=tuple(bad),
        )
    return PITCheckResult(is_pit_clean=True, contamination_count=0, rejection_reason=None)


def check_option_contracts(
    contracts: Iterable[dict],
    decision_ts_ns: int,
    *,
    max_samples: int = 5,
) -> PITCheckResult:
    """Reject the run if any contract was listed after the decision timestamp."""
    bad: list[str] = []
    count = 0
    for c in contracts:
        listed = int(c.get("listed_at_ts_ns", 0) or 0)
        if listed > decision_ts_ns:
            count += 1
            if len(bad) < max_samples:
                bad.append(
                    f"option contract {c.get('contract_symbol', '?')!r} "
                    f"listed at {listed} > decision_ts_ns={decision_ts_ns} "
                    f"(retroactive selection, +{(listed - decision_ts_ns) / 1e9:.3f}s)"
                )
    if count > 0:
        return PITCheckResult(
            is_pit_clean=False,
            contamination_count=count,
            rejection_reason=(
                f"option contract leakage: {count} contract(s) listed after decision; "
                f"first: {bad[0] if bad else ''}"
            ),
            contaminated_samples=tuple(bad),
        )
    return PITCheckResult(is_pit_clean=True, contamination_count=0, rejection_reason=None)


def check_float_metadata(as_of_date: str, session_date: str) -> PITCheckResult:
    """Reject the run if float metadata as_of_date is after session_date."""
    from datetime import date
    try:
        as_of = date.fromisoformat(as_of_date)
        sess = date.fromisoformat(session_date)
    except ValueError as e:
        return PITCheckResult(
            is_pit_clean=False,
            contamination_count=1,
            rejection_reason=f"float metadata date parse failure: {e}",
        )
    if as_of > sess:
        return PITCheckResult(
            is_pit_clean=False,
            contamination_count=1,
            rejection_reason=(
                f"float leakage: as_of_date {as_of_date} is after session_date {session_date} "
                f"(forward float, +{(as_of - sess).days}d)"
            ),
            contaminated_samples=(f"float as_of_date={as_of_date} session_date={session_date}",),
        )
    return PITCheckResult(is_pit_clean=True, contamination_count=0, rejection_reason=None)
