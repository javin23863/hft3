"""Source priority resolver for the MBO release lane.

The lane supports two production sources: Rithmic History Plant
(``rithmic_api``) and Databento GLBX.MDP3.  Rithmic is tried first
because it is the historical data entitlement the project owns; Databento
is the fallback for windows/symbols Rithmic does not have or fails on.

The resolver is a pure function over a (release, symbol) pair.  It
inspects what is already on disk (``release_event_path.json`` from any
source) and short-circuits — no network call — when the slot is already
filled.  When neither source has produced a valid slot, it returns
``None`` and the caller (the download orchestrator) decides what to do
(skip, retry, fall back, or escalate).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mbo_release_lane.constants import SOURCE_PRIORITY, SOURCE_VENDOR_RITHMIC
from mbo_release_lane.rithmic_topology_guard import (
    RithmicTopologyError,
    is_windows,
)
from mbo_release_lane.storage import (
    load_release_event_path,
    release_slot_dir,
)

logger = logging.getLogger(__name__)


@dataclass
class SourceSlotStatus:
    """One source's status for a (release, symbol) slot."""

    source: str
    skipped_reason: str | None = None
    on_disk: bool = False
    on_disk_source: str | None = None
    manifest: dict[str, Any] | None = None

    def is_filled(self) -> bool:
        if not self.on_disk:
            return False
        if not self.manifest:
            return False
        rep = self.manifest.get("release_event_path", {})
        return rep.get("validation_status") == "valid" and int(rep.get("event_count", 0)) > 0


def _slot_status_for_source(repo_root: Path, release_id: str, symbol: str, source: str) -> SourceSlotStatus:
    slot = release_slot_dir(repo_root, release_id, symbol)
    manifest = load_release_event_path(slot)
    on_disk_vendor = None
    if manifest:
        on_disk_vendor = manifest.get("release_event_path", {}).get("source_vendor")
    return SourceSlotStatus(
        source=source,
        on_disk=manifest is not None,
        on_disk_source=on_disk_vendor,
        manifest=manifest,
    )


def resolve_source(
    repo_root: Path,
    release_id: str,
    symbol: str,
    *,
    force_source: str | None = None,
) -> str | None:
    """Pick the source to use for one (release, symbol) slot.

    Returns the source name (``"rithmic_api"`` / ``"databento"``) or
    ``None`` when no source should run (slot is already filled by
    any source, or no source is applicable).

    ``force_source`` overrides the priority list — used by the CLI
    ``--source`` flag and by retry paths.
    """
    if force_source:
        return force_source

    for src in SOURCE_PRIORITY:
        status = _slot_status_for_source(repo_root, release_id, symbol, src)
        if status.is_filled():
            # Slot already valid for this source.  Short-circuit.
            return None
        # If the slot is on disk for a *different* source, treat it as
        # filled too — we don't want to overwrite a Databento slot
        # with Rithmic data, or vice-versa, without an explicit flag.
        if status.on_disk and status.on_disk_source and status.on_disk_source != src:
            return None

    # No slot filled by any source.  Return the first source whose
    # topology is reachable from this host.
    for src in SOURCE_PRIORITY:
        if src == SOURCE_VENDOR_RITHMIC and is_windows():
            logger.debug("Skipping %s on Windows (BLUEPRINT §4)", src)
            continue
        return src

    return None


def attempt_rithmic_fill(
    repo_root: Path,
    release_id: str,
    symbol: str,
    exchange: str,
    start_utc: Any,
    end_utc: Any,
    *,
    scheduled_release_timestamp: str,
    max_pages: int = 50,
) -> tuple[SourceSlotStatus, dict[str, Any] | None, str | None]:
    """Try to fill one slot from Rithmic.  Returns (status, manifest, error)."""
    from datetime import datetime, timezone

    from mbo_release_lane.rithmic_source import (
        derive_npz_from_rithmic_release,
        fetch_event_window,
        write_release_artifact,
    )

    if is_windows():
        return (
            SourceSlotStatus(source=SOURCE_VENDOR_RITHMIC, skipped_reason="windows"),
            None,
            "topology: refused on Windows — Rithmic MBO fill source must run on CHI404 (BLUEPRINT §4)",
        )

    try:
        result = fetch_event_window(
            release_id=release_id,
            symbol=symbol,
            exchange=exchange,
            start_utc=start_utc if isinstance(start_utc, datetime) else datetime.fromisoformat(str(start_utc)),
            end_utc=end_utc if isinstance(end_utc, datetime) else datetime.fromisoformat(str(end_utc)),
            max_pages=max_pages,
        )
    except RithmicTopologyError as exc:
        return (
            SourceSlotStatus(source=SOURCE_VENDOR_RITHMIC, skipped_reason="windows"),
            None,
            str(exc),
        )
    except Exception as exc:  # connect / auth / parse
        return (
            SourceSlotStatus(source=SOURCE_VENDOR_RITHMIC, on_disk=False),
            None,
            f"rithmic fetch exception: {exc}",
        )

    if not result.is_valid_mbo:
        # Hard labeling rule: do NOT write a manifest for non-MBO data.
        return (
            SourceSlotStatus(
                source=SOURCE_VENDOR_RITHMIC,
                on_disk=False,
            ),
            None,
            result.error or f"rithmic returned data_label={result.data_label}, not mbo",
        )

    rep = write_release_artifact(
        repo_root,
        result,
        scheduled_release_timestamp=scheduled_release_timestamp,
    )
    if rep is None:
        return (
            SourceSlotStatus(source=SOURCE_VENDOR_RITHMIC, on_disk=False),
            None,
            "write_release_artifact returned None",
        )

    # Try to derive NPZ.  Failure here is logged but does not block the
    # release_event_path manifest; downstream code will retry derivation.
    try:
        npz = derive_npz_from_rithmic_release(repo_root, release_id, symbol)
        if npz:
            logger.info("Rithmic NPZ derived: %s", npz)
    except Exception as exc:
        logger.warning("Rithmic NPZ derivation failed for %s %s: %s", release_id, symbol, exc)

    return (
        SourceSlotStatus(
            source=SOURCE_VENDOR_RITHMIC,
            on_disk=True,
            on_disk_source=SOURCE_VENDOR_RITHMIC,
            manifest=rep,
        ),
        rep,
        None,
    )
