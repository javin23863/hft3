"""Import pipeline: classify → parse → validate → store release_event_path."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mbo_release_lane.classification import (
    assert_true_mbo,
    classification_to_blockers,
    classify_normalized_events,
)
from mbo_release_lane.constants import DEFAULT_DATASET_ID, SOURCE_VENDOR
from mbo_release_lane.databento_mbo_parser import parse_databento_mbo_file
from mbo_release_lane.hashing import build_hashes
from mbo_release_lane.storage import (
    build_release_event_path,
    events_jsonl_path,
    hashes_path,
    raw_dbn_path,
    release_event_path_manifest,
    release_slot_dir,
    validation_report_path,
    write_events_jsonl,
    write_json,
)
from mbo_release_lane.validate import validate_event_stream


@dataclass
class ImportResult:
    release_id: str
    symbol: str
    slot_dir: Path
    validation_status: str
    event_count: int
    blockers: list[dict[str, Any]]
    paths_written: list[str]


def import_release_window(
    repo_root: Path,
    *,
    release_id: str,
    release_name: str,
    symbol: str,
    raw_dbn_src: Path,
    window_start: str,
    window_end: str,
    scheduled_release_timestamp: str,
    actual_release_timestamp: str | None = None,
    dataset_id: str = DEFAULT_DATASET_ID,
    skip_classification: bool = False,
) -> ImportResult:
    """Full MBO-only import for one release × symbol."""
    slot = release_slot_dir(repo_root, release_id, symbol)
    slot.mkdir(parents=True, exist_ok=True)

    if not skip_classification:
        assert_true_mbo(raw_dbn_src)

    dest_raw = raw_dbn_path(slot)
    if raw_dbn_src.resolve() != dest_raw.resolve():
        shutil.copy2(raw_dbn_src, dest_raw)

    events = parse_databento_mbo_file(
        dest_raw,
        release_id=release_id,
        symbol=symbol,
        dataset_id=dataset_id,
    )

    norm_class = classify_normalized_events(events)
    if not norm_class.is_true_mbo:
        blockers = classification_to_blockers(norm_class)
        validation = {
            "release_id": release_id,
            "symbol": symbol,
            "blocker_status": "blocked",
            "replay_valid": False,
            **blockers.to_dict(),
        }
        write_json(validation_report_path(slot), {"mbo_release_validation_report": validation})
        manifest = build_release_event_path(
            release_id=release_id,
            release_name=release_name,
            scheduled_release_timestamp=scheduled_release_timestamp,
            actual_release_timestamp=actual_release_timestamp or scheduled_release_timestamp,
            symbol=symbol,
            venue="GLBX",
            window_start=window_start,
            window_end=window_end,
            events_ref=str(events_jsonl_path(slot)),
            event_count=0,
            first_sequence=None,
            last_sequence=None,
            sequence_gap_count=0,
            source_vendor=SOURCE_VENDOR,
            dataset_id=dataset_id,
            validation_status="invalid",
        )
        write_json(release_event_path_manifest(slot), manifest)
        return ImportResult(
            release_id=release_id,
            symbol=symbol,
            slot_dir=slot,
            validation_status="invalid",
            event_count=0,
            blockers=blockers.to_dict()["blockers"],
            paths_written=[str(validation_report_path(slot)), str(release_event_path_manifest(slot))],
        )

    ev_path = events_jsonl_path(slot)
    write_events_jsonl(events, ev_path)

    blocker_report, validation = validate_event_stream(events)
    validation_payload = {"mbo_release_validation_report": validation}
    write_json(validation_report_path(slot), validation_payload)

    status = "valid" if not blocker_report.blocked else "invalid"
    first_seq = int(events[0]["sequence_number"]) if events else None
    last_seq = int(events[-1]["sequence_number"]) if events else None
    gap_count = int(validation.get("sequence_gaps", 0))

    manifest = build_release_event_path(
        release_id=release_id,
        release_name=release_name,
        scheduled_release_timestamp=scheduled_release_timestamp,
        actual_release_timestamp=actual_release_timestamp or scheduled_release_timestamp,
        symbol=symbol,
        venue="GLBX",
        window_start=window_start,
        window_end=window_end,
        events_ref=str(ev_path.relative_to(repo_root)) if ev_path.is_relative_to(repo_root) else str(ev_path),
        event_count=len(events),
        first_sequence=first_seq,
        last_sequence=last_seq,
        sequence_gap_count=gap_count,
        source_vendor=SOURCE_VENDOR,
        dataset_id=dataset_id,
        validation_status=status,
    )
    write_json(release_event_path_manifest(slot), manifest)

    hash_payload = build_hashes(
        raw_dbn=dest_raw,
        events_jsonl=ev_path,
        validation=validation_payload,
    )
    write_json(hashes_path(slot), hash_payload)

    return ImportResult(
        release_id=release_id,
        symbol=symbol,
        slot_dir=slot,
        validation_status=status,
        event_count=len(events),
        blockers=blocker_report.to_dict()["blockers"],
        paths_written=[
            str(dest_raw),
            str(ev_path),
            str(release_event_path_manifest(slot)),
            str(validation_report_path(slot)),
            str(hashes_path(slot)),
        ],
    )
