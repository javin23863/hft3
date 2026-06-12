"""Shared snapshot offset loading from event_universe.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Tuple

_CONFIG = Path(__file__).resolve().parent / "config" / "event_universe.yaml"


@lru_cache(maxsize=4)
def _load_offsets_from_yaml(yaml_key: str) -> Tuple[int, ...]:
    import yaml

    path = _CONFIG
    if not path.is_file():
        return ()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    offs = raw.get("defaults", {}).get(yaml_key, [])
    if not offs:
        return ()
    return tuple(int(x) for x in offs)


def default_snapshot_offsets_sec() -> Tuple[int, ...]:
    """Second-granularity L3/sensor offsets (single source: event_universe.yaml)."""
    loaded = _load_offsets_from_yaml("snapshot_offsets_sec")
    if loaded:
        return loaded
    return (-10, -5, -1, 0, 1, 2, 5, 10)
