"""Checkout-local shim for `python -m economic_event_universe...`.

The implementation package lives under `packages/economic_event_universe`; this
shim makes documented module commands work from a clean repo checkout.
"""

from __future__ import annotations

from pathlib import Path
import sys

_REAL_PACKAGE = Path(__file__).resolve().parents[1] / "packages" / "economic_event_universe"
_PACKAGES = Path(__file__).resolve().parents[1] / "packages"
_APPS = Path(__file__).resolve().parents[1] / "apps"
for _path in (_PACKAGES, _APPS):
    _text = str(_path)
    if _path.is_dir() and _text not in sys.path:
        sys.path.insert(0, _text)
__path__.insert(0, str(_REAL_PACKAGE))  # type: ignore[name-defined]

from economic_event_universe.calendar import (  # noqa: E402
    apply_holiday_adjustment,
    list_upcoming,
    resolve_release_datetime,
)
from economic_event_universe.service import (  # noqa: E402
    event_type_status,
    inventory,
    list_calendar_rows,
    list_event_types,
    list_runnable_events,
    resolve_event_context,
    snapshot_artifacts_for_event,
)
from economic_event_universe.timezone import anchor_utc, format_release_for_user, to_user_tz  # noqa: E402
from economic_event_universe.windows import generate_snapshot_times, snapshot_offsets  # noqa: E402

__all__ = [
    "list_upcoming",
    "resolve_release_datetime",
    "apply_holiday_adjustment",
    "anchor_utc",
    "to_user_tz",
    "format_release_for_user",
    "snapshot_offsets",
    "generate_snapshot_times",
    "list_event_types",
    "list_calendar_rows",
    "list_runnable_events",
    "resolve_event_context",
    "event_type_status",
    "snapshot_artifacts_for_event",
    "inventory",
    "CrossAssetSnapshotProvider",
    "DefaultSnapshotProvider",
]


def __getattr__(name: str):
    if name in ("CrossAssetSnapshotProvider", "DefaultSnapshotProvider"):
        from economic_event_universe.snapshot import CrossAssetSnapshotProvider, DefaultSnapshotProvider

        return {"CrossAssetSnapshotProvider": CrossAssetSnapshotProvider, "DefaultSnapshotProvider": DefaultSnapshotProvider}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
