"""Content-addressed immutable replay data preparation."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from backtest_pipeline.src.hft_campaign._hashing import sha256_file, sha256_hex
from backtest_pipeline.src.hftbacktest_realism import (
    classify_hftbacktest_events,
    count_l3_orphans,
    validate_hftbacktest_event_array,
)

from backtest_pipeline.src.l3_orphan_filter import (
    ADD_ORDER_EVENT,
    CANCEL_ORDER_EVENT,
    FILL_EVENT,
    MODIFY_ORDER_EVENT,
    filter_l3_orphans as _filter_l3_orphans,
    is_l3_data as _is_l3_data,
)
from backtest_pipeline.src.hft_campaign._hashing import sha256_file, sha256_hex

EVENT_NORMALIZATION_VERSION = "1"
L2_L3_CLASSIFICATION_VERSION = "1"
ORPHAN_HANDLING_IMPL_VERSION = "filter_l3_orphans_v1"
TIMESTAMP_UNIT_DECLARATION = "nanoseconds"
HFTBACKTEST_EVENT_DTYPE_VERSION = "hftbacktest.event_dtype.v1"


def _git_commit(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:
        return "unknown"


def _orphan_handling_impl_hash() -> str:
    import inspect

    from backtest_pipeline.src import l3_orphan_filter

    source = inspect.getsource(l3_orphan_filter.filter_l3_orphans)
    return sha256_hex(source)


def _load_events(data_path: str) -> np.ndarray:
    d = np.load(data_path, allow_pickle=False)
    return d["data"]


def _event_type_counts(events: np.ndarray) -> dict[str, int]:
    ev_types = (events["ev"].astype(np.int64)) & 0xFF
    counts: dict[str, int] = {}
    for code in np.unique(ev_types):
        counts[str(int(code))] = int(np.sum(ev_types == code))
    return counts


@dataclass(frozen=True)
class PreparedDataKey:
    source_file_hash: str
    lake_manifest_hash: str
    event_normalization_version: str
    l2_l3_classification_version: str
    orphan_handling_impl_hash: str
    timestamp_unit_declaration: str
    symbol: str
    event_id: str
    hftbacktest_event_dtype_version: str
    hft3_commit: str

    def prepared_data_hash(self) -> str:
        return sha256_hex(
            {
                "source_file_hash": self.source_file_hash,
                "lake_manifest_hash": self.lake_manifest_hash,
                "event_normalization_version": self.event_normalization_version,
                "l2_l3_classification_version": self.l2_l3_classification_version,
                "orphan_handling_impl_hash": self.orphan_handling_impl_hash,
                "timestamp_unit_declaration": self.timestamp_unit_declaration,
                "symbol": self.symbol,
                "event_id": self.event_id,
                "hftbacktest_event_dtype_version": self.hftbacktest_event_dtype_version,
                "hft3_commit": self.hft3_commit,
            }
        )


@dataclass
class PreparedReplayData:
    prepared_data_hash: str
    path: Path
    l2_l3_classification: str
    original_row_count: int
    final_row_count: int
    removed_orphan_count: int
    removed_order_ids_sample: list[int]
    source_data_hash: str
    transformed_data_hash: str


def build_prepared_data_key(
    *,
    source_npz_path: Path,
    repo_root: Path,
    symbol: str,
    event_id: str,
    lake_manifest_hash: str = "",
) -> PreparedDataKey:
    return PreparedDataKey(
        source_file_hash=sha256_file(source_npz_path),
        lake_manifest_hash=lake_manifest_hash or "",
        event_normalization_version=EVENT_NORMALIZATION_VERSION,
        l2_l3_classification_version=L2_L3_CLASSIFICATION_VERSION,
        orphan_handling_impl_hash=_orphan_handling_impl_hash(),
        timestamp_unit_declaration=TIMESTAMP_UNIT_DECLARATION,
        symbol=symbol,
        event_id=event_id,
        hftbacktest_event_dtype_version=HFTBACKTEST_EVENT_DTYPE_VERSION,
        hft3_commit=_git_commit(repo_root),
    )


def prepare_replay_data(
    *,
    source_npz_path: Path,
    repo_root: Path,
    symbol: str,
    event_id: str,
    lake_manifest_hash: str = "",
    force_rebuild: bool = False,
) -> PreparedReplayData:
    """Build or reuse content-addressed prepared replay data."""
    from workbench.src.artifacts.paths import hftbacktest_prepared_data_dir

    source_npz_path = Path(source_npz_path)
    key = build_prepared_data_key(
        source_npz_path=source_npz_path,
        repo_root=repo_root,
        symbol=symbol,
        event_id=event_id,
        lake_manifest_hash=lake_manifest_hash,
    )
    prepared_hash = key.prepared_data_hash()
    out_dir = hftbacktest_prepared_data_dir(prepared_hash)
    complete_marker = out_dir / ".complete"
    events_path = out_dir / "events.npz"

    if complete_marker.is_file() and events_path.is_file() and not force_rebuild:
        meta = json.loads((out_dir / "preparation_metrics.json").read_text(encoding="utf-8"))
        return PreparedReplayData(
            prepared_data_hash=prepared_hash,
            path=events_path,
            l2_l3_classification=str(meta.get("l2_l3_classification", "")),
            original_row_count=int(meta.get("original_row_count", 0)),
            final_row_count=int(meta.get("final_row_count", 0)),
            removed_orphan_count=int(meta.get("removed_orphan_count", 0)),
            removed_order_ids_sample=list(meta.get("removed_order_ids_sample", [])),
            source_data_hash=str(meta.get("source_data_hash", key.source_file_hash)),
            transformed_data_hash=str(meta.get("transformed_data_hash", "")),
        )

    events = _load_events(str(source_npz_path))
    original_count = len(events)
    validation_before = validate_hftbacktest_event_array(events)
    classification = classify_hftbacktest_events(events)
    use_l3 = _is_l3_data(events)
    orphan_count_before, _orphan_ids = count_l3_orphans(events) if use_l3 else (0, [])

    removed_ids: list[int] = []
    if use_l3 and orphan_count_before > 0:
        ev_types = (events["ev"].astype(np.int64)) & 0xFF
        known_ids: set[int] = set()
        keep = np.ones(len(events), dtype=bool)

        for i in range(len(events)):
            et = int(ev_types[i])
            oid = int(events[i]["order_id"])
            if et == ADD_ORDER_EVENT:
                known_ids.add(oid)
            elif et in (CANCEL_ORDER_EVENT, MODIFY_ORDER_EVENT, FILL_EVENT):
                if oid not in known_ids:
                    keep[i] = False
                    if len(removed_ids) < 32:
                        removed_ids.append(oid)
        transformed = events[keep]
    else:
        transformed = events

    validation_after = validate_hftbacktest_event_array(transformed)

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_base = out_dir / "events_build"
    np.savez_compressed(tmp_base, data=transformed)
    os.replace(tmp_base.with_name(f"{tmp_base.name}.npz"), events_path)
    transformed_hash = sha256_file(events_path)

    source_manifest = {
        "source_npz_path": str(source_npz_path),
        "source_file_hash": key.source_file_hash,
        "lake_manifest_hash": key.lake_manifest_hash,
        "symbol": symbol,
        "event_id": event_id,
    }
    transformation_manifest = {
        "prepared_data_hash": prepared_hash,
        "preparation_key": {
            "event_normalization_version": key.event_normalization_version,
            "l2_l3_classification_version": key.l2_l3_classification_version,
            "orphan_handling_impl_hash": key.orphan_handling_impl_hash,
            "timestamp_unit_declaration": key.timestamp_unit_declaration,
            "hftbacktest_event_dtype_version": key.hftbacktest_event_dtype_version,
            "hft3_commit": key.hft3_commit,
        },
        "l2_l3_classification": classification,
        "orphan_filter_applied": use_l3,
        "transformation_permitted_reason": "l3_orphan_events_removed_for_engine_compatibility",
        "removed_orphan_count": int(orphan_count_before),
        "removed_order_ids_sample": removed_ids,
        "removed_order_ids_hash": sha256_hex(sorted(set(removed_ids))),
    }
    preparation_metrics = {
        "original_row_count": original_count,
        "final_row_count": len(transformed),
        "event_type_counts_before": _event_type_counts(events),
        "event_type_counts_after": _event_type_counts(transformed),
        "l2_l3_classification": classification,
        "removed_orphan_count": int(orphan_count_before),
        "removed_order_ids_sample": removed_ids,
        "source_data_hash": key.source_file_hash,
        "transformed_data_hash": transformed_hash,
    }
    validation_payload = {
        "validation_before_transformation": validation_before,
        "validation_after_transformation": validation_after,
    }

    (out_dir / "source_manifest.json").write_text(
        json.dumps(source_manifest, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "transformation_manifest.json").write_text(
        json.dumps(transformation_manifest, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "validation.json").write_text(
        json.dumps(validation_payload, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "preparation_metrics.json").write_text(
        json.dumps(preparation_metrics, indent=2) + "\n", encoding="utf-8"
    )
    complete_marker.write_text(prepared_hash + "\n", encoding="utf-8")

    return PreparedReplayData(
        prepared_data_hash=prepared_hash,
        path=events_path,
        l2_l3_classification=classification,
        original_row_count=original_count,
        final_row_count=len(transformed),
        removed_orphan_count=int(orphan_count_before),
        removed_order_ids_sample=removed_ids,
        source_data_hash=key.source_file_hash,
        transformed_data_hash=transformed_hash,
    )


def validate_prepared_data_dir(prepared_dir: Path, *, expected_hash: str) -> list[str]:
    reasons: list[str] = []
    prepared_dir = Path(prepared_dir)
    if not (prepared_dir / ".complete").is_file():
        reasons.append("prepared_data_incomplete")
    events_path = prepared_dir / "events.npz"
    if not events_path.is_file():
        reasons.append("prepared_data_events_missing")
        return reasons
    actual_hash = sha256_file(events_path)
    metrics_path = prepared_dir / "preparation_metrics.json"
    if metrics_path.is_file():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        recorded = str(metrics.get("transformed_data_hash", ""))
        if recorded and recorded != actual_hash:
            reasons.append("prepared_data_transformed_hash_mismatch")
    if expected_hash and prepared_dir.name != expected_hash:
        reasons.append("prepared_data_hash_directory_mismatch")
    return reasons
