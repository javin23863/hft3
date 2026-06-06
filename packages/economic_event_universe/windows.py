"""Download windows and L3 snapshot offset presets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from economic_event_universe.registry import default_download_window, default_snapshot_offsets, get_event_def


def snapshot_offsets(event_type: str | None = None) -> tuple[int, ...]:
    if event_type:
        cfg = get_event_def(event_type)
        raw = cfg.get("snapshot_offsets_sec")
        if raw:
            return tuple(int(x) for x in raw)
    return default_snapshot_offsets()


def download_window(event_type: str) -> tuple[int, int]:
    cfg = get_event_def(event_type)
    win = cfg.get("download_window") or {}
    default_start, default_end = default_download_window()
    return int(win.get("start_offset_seconds", default_start)), int(
        win.get("end_offset_seconds", default_end)
    )


def generate_snapshot_times(anchor_utc: datetime, offsets: Sequence[int] | None = None) -> list[datetime]:
    if anchor_utc.tzinfo is None:
        anchor_utc = anchor_utc.replace(tzinfo=timezone.utc)
    offs = offsets or default_snapshot_offsets()
    return sorted(anchor_utc + timedelta(seconds=int(s)) for s in offs)


def window_name_for(event_type: str) -> str:
    return str(get_event_def(event_type).get("window_name", "TIGHT"))
