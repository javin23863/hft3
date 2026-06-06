"""Calendar row helpers shared by Fed fetchers."""

from __future__ import annotations

from typing import Any, Iterable

from economic_event_universe.registry import get_event_def


def calendar_row(
    event_type: str,
    release_date: str,
    *,
    source: str | None = None,
    source_url: str | None = None,
    release_time: str | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    cfg = get_event_def(event_type)
    agency = str(cfg.get("agency", ""))
    return {
        "release_date": release_date,
        "event_type": event_type,
        "source": source or agency,
        "source_url": source_url or str(cfg.get("official_source_url", "")),
        "timezone": timezone or str(cfg.get("timezone", "America/New_York")),
        "release_time": release_time or str(cfg.get("anchor_time", "08:30:00")),
    }


def derive_core_from_parent(
    rows: Iterable[dict],
    parent_type: str,
    child_type: str,
) -> list[dict]:
    """Co-release: CORE_CPI from CPI, CORE_PPI from PPI, etc."""
    out: list[dict] = []
    for row in rows:
        if row["event_type"] != parent_type:
            continue
        child = dict(row)
        child["event_type"] = child_type
        cfg = get_event_def(child_type)
        child["source_url"] = str(cfg.get("official_source_url", child.get("source_url", "")))
        out.append(child)
    return out


def derive_building_permits_from_housing(rows: Iterable[dict]) -> list[dict]:
    return derive_core_from_parent(rows, "HOUSING_STARTS", "BUILDING_PERMITS")
