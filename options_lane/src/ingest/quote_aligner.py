"""Event-time quote alignment for parity groups (filtration-safe)."""
from __future__ import annotations

from collections import defaultdict

from options_lane.src.models import LegQuote, ParityGroup, QuoteSnapshot
from options_lane.src.parity_engine import required_parity_roles


def align_quotes(
    group: ParityGroup,
    quotes: list[LegQuote],
) -> list[QuoteSnapshot]:
    """
    Build snapshots at each distinct timestamp where at least one leg updates.
    Each snapshot holds the latest quote per role with timestamp_ns <= event time.
    """
    by_role: dict[str, list[LegQuote]] = defaultdict(list)
    for q in quotes:
        by_role[q.role].append(q)
    for role in by_role:
        by_role[role].sort(key=lambda x: x.timestamp_ns)

    event_times = sorted({q.timestamp_ns for q in quotes})
    latest: dict[str, LegQuote] = {}
    snapshots: list[QuoteSnapshot] = []

    for t_ns in event_times:
        for role, series in by_role.items():
            while series and series[0].timestamp_ns <= t_ns:
                latest[role] = series.pop(0)
        if latest:
            snapshots.append(
                QuoteSnapshot(
                    group_id=group.id,
                    timestamp_ns=t_ns,
                    quotes=dict(latest),
                )
            )
    return snapshots


def snapshot_has_required_legs(group: ParityGroup, snapshot: QuoteSnapshot) -> bool:
    return required_parity_roles(group).issubset(snapshot.quotes.keys())
