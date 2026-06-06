"""Calendar row helpers shared by Fed fetchers."""

from __future__ import annotations

from typing import Any

from economic_event_universe.registry import get_event_def


def calendar_row(
    event_type: str,
    release_date: str,
    *,
    source: str = "Fed",
    source_url: str | None = None,
    release_time: str | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    cfg = get_event_def(event_type)
    return {
        "release_date": release_date,
        "event_type": event_type,
        "source": source,
        "source_url": source_url or str(cfg.get("official_source_url", "")),
        "timezone": timezone or str(cfg.get("timezone", "America/New_York")),
        "release_time": release_time or str(cfg.get("anchor_time", "08:30:00")),
    }
