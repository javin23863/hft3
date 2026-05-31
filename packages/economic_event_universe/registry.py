"""Load event_universe.yaml — canonical macro release metadata."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_CONFIG = Path(__file__).resolve().parent / "config" / "event_universe.yaml"

# Types with SOURCED calendar rows in events.csv today.
_SOURCED_CALENDAR_FILES = frozenset({"bls_cpi.csv", "bls_nfp.csv", "prop_flatten.csv"})


@lru_cache(maxsize=1)
def load_event_universe() -> dict[str, Any]:
    raw = yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}
    return raw


def event_definitions() -> dict[str, dict[str, Any]]:
    return dict(load_event_universe().get("events", {}))


def get_event_def(event_type: str) -> dict[str, Any]:
    defs = event_definitions()
    if event_type not in defs:
        raise KeyError(f"Unknown event_type in event_universe.yaml: {event_type}")
    return defs[event_type]


def default_snapshot_offsets() -> tuple[int, ...]:
    offs = load_event_universe().get("defaults", {}).get("snapshot_offsets_sec", [])
    return tuple(int(x) for x in offs)


def research_ready_types() -> list[str]:
    """Types that must have SOURCED calendars and may appear in canonical events.csv."""
    return sorted(
        et
        for et, cfg in event_definitions().items()
        if cfg.get("status") in ("RESEARCH_READY", "REQUIRED")
    )


def required_event_types() -> list[str]:
    return research_ready_types()


def catalog_types() -> list[str]:
    return sorted(
        et for et, cfg in event_definitions().items() if cfg.get("status") == "CATALOG"
    )


def context_priority(event_type: str) -> int:
    return int(get_event_def(event_type).get("context_priority", 50))
