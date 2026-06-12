"""mtime-keyed caches for the few large artifacts.

The Stage A result is multi-MB (per-event expectancy arrays). Both the
pipeline and models zones need it, and the file-watcher can fire often, so we
parse it at most once per file change and project away the heavy arrays.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from . import paths

_cache: dict[Path, tuple[float, Any]] = {}


def _cached_json(path: Path) -> Optional[Any]:
    try:
        mtime = path.stat().st_mtime
    except (FileNotFoundError, OSError):
        _cache.pop(path, None)
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    data = paths.read_json(path)
    if data is not None:
        _cache[path] = (mtime, data)
    return data


def stage_a_raw() -> Optional[dict]:
    """Full Stage A result (cached). Callers must project before returning."""
    return _cached_json(paths.STAGE_A_RESULT)


def stage_a_cells_compact() -> list[dict]:
    """Per-hypothesis Stage A cells with the heavy per-event arrays stripped."""
    raw = stage_a_raw()
    if not isinstance(raw, dict):
        return []
    out: list[dict] = []
    for cell in raw.get("cells", []):
        if not isinstance(cell, dict):
            continue
        out.append(
            {
                "hypothesis_id": cell.get("hypothesis_id"),
                "hypothesis_name": cell.get("hypothesis_name"),
                "event_type": cell.get("event_type"),
                "band_ms": cell.get("band_ms"),
                "n_events": cell.get("n_events"),
                "n_events_with_vix": cell.get("n_events_with_vix"),
                "total_trades": cell.get("total_trades"),
                "mean_expectancy_usd": cell.get("mean_expectancy_usd"),
                "mean_win_rate": cell.get("mean_win_rate"),
                "p5_expectancy_tail_usd": cell.get("p5_expectancy_tail_usd"),
            }
        )
    return out
