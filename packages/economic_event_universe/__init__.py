"""Timezone-aware macro economic event universe for HFT research."""

from economic_event_universe.calendar import (
    apply_holiday_adjustment,
    list_upcoming,
    resolve_release_datetime,
)
from economic_event_universe.timezone import anchor_utc, format_release_for_user, to_user_tz
from economic_event_universe.windows import generate_snapshot_times, snapshot_offsets

__all__ = [
    "list_upcoming",
    "resolve_release_datetime",
    "apply_holiday_adjustment",
    "anchor_utc",
    "to_user_tz",
    "format_release_for_user",
    "snapshot_offsets",
    "generate_snapshot_times",
    "CrossAssetSnapshotProvider",
    "DefaultSnapshotProvider",
]


def __getattr__(name: str):
    if name in ("CrossAssetSnapshotProvider", "DefaultSnapshotProvider"):
        from economic_event_universe.snapshot import CrossAssetSnapshotProvider, DefaultSnapshotProvider

        return {"CrossAssetSnapshotProvider": CrossAssetSnapshotProvider, "DefaultSnapshotProvider": DefaultSnapshotProvider}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
