"""Immutable point-in-time feature timeline artifacts."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from backtest_pipeline.src.hft_campaign._hashing import sha256_hex
from features_engine.src.features.npz_feed import iter_mbo_events, load_npz_events
from replay.market_data_adapter import HistoricalReplayMarketDataAdapter

FEATURE_TIMELINE_VERSION = "1"


@dataclass(frozen=True)
class FeatureTimelineKey:
    prepared_data_hash: str
    feature_set_id: str
    feature_set_hash: str
    research_clock: str
    feature_latency_ns: int
    feature_implementation_hash: str
    cross_asset_source_hashes: tuple[str, ...]
    sensor_source_hashes: tuple[str, ...]
    warmup_configuration_hash: str

    def timeline_hash(self) -> str:
        return sha256_hex(
            {
                "prepared_data_hash": self.prepared_data_hash,
                "feature_set_id": self.feature_set_id,
                "feature_set_hash": self.feature_set_hash,
                "research_clock": self.research_clock,
                "feature_latency_ns": self.feature_latency_ns,
                "feature_implementation_hash": self.feature_implementation_hash,
                "cross_asset_source_hashes": list(self.cross_asset_source_hashes),
                "sensor_source_hashes": list(self.sensor_source_hashes),
                "warmup_configuration_hash": self.warmup_configuration_hash,
                "feature_timeline_version": FEATURE_TIMELINE_VERSION,
            }
        )


def build_feature_timeline(
    *,
    prepared_data_path: Path,
    key: FeatureTimelineKey,
    tick_size: float = 0.25,
    feature_latency_ms: float = 1.0,
    force_rebuild: bool = False,
) -> Path:
    from workbench.src.artifacts.paths import hftbacktest_feature_timeline_dir

    timeline_hash = key.timeline_hash()
    out_dir = hftbacktest_feature_timeline_dir(timeline_hash)
    complete = out_dir / ".complete"
    timeline_path = out_dir / "timeline.npz"
    if complete.is_file() and timeline_path.is_file() and not force_rebuild:
        return timeline_path

    raw_events = load_npz_events(str(prepared_data_path))
    mda = HistoricalReplayMarketDataAdapter(
        raw_events,
        tick_size=tick_size,
        latency_ms=feature_latency_ms,
    )
    feature_latency_ns = int(feature_latency_ms * 1_000_000)
    rows: list[tuple[int, int, np.ndarray]] = []
    seen_ts: set[int] = set()
    for ev in iter_mbo_events(raw_events):
        ts = int(ev.timestamp_ns)
        if ts in seen_ts:
            continue
        seen_ts.add(ts)
        feature_ts = max(0, ts - feature_latency_ns)
        state = mda.sync_to_timestamp(feature_ts)
        if state is None:
            continue
        vector = np.asarray(state.feature_vector, dtype=np.float64)
        availability_ts = int(getattr(state, "feature_availability_ts", feature_ts))
        rows.append((ts, availability_ts, vector))
        mda.sync_to_timestamp(ts)

    if not rows:
        event_ts = np.array([], dtype=np.int64)
        availability = np.array([], dtype=np.int64)
        features = np.zeros((0, 64), dtype=np.float64)
    else:
        event_ts = np.array([r[0] for r in rows], dtype=np.int64)
        availability = np.array([r[1] for r in rows], dtype=np.int64)
        features = np.stack([r[2] for r in rows])

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_base = out_dir / "timeline_build"
    np.savez_compressed(
        tmp_base,
        event_ts=event_ts,
        feature_availability_ts=availability,
        feature_vectors=features,
    )
    os.replace(tmp_base.with_name(f"{tmp_base.name}.npz"), timeline_path)
    manifest = {
        "timeline_hash": timeline_hash,
        "prepared_data_hash": key.prepared_data_hash,
        "feature_set_id": key.feature_set_id,
        "feature_set_hash": key.feature_set_hash,
        "research_clock": key.research_clock,
        "feature_implementation_hash": key.feature_implementation_hash,
        "row_count": int(len(event_ts)),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    complete.write_text(timeline_hash + "\n", encoding="utf-8")
    return timeline_path


def feature_timeline_parity_check(
    *,
    prepared_data_path: Path,
    timeline_path: Path,
    tick_size: float = 0.25,
    feature_latency_ms: float = 1.0,
    max_rows: int = 256,
) -> tuple[bool, list[str]]:
    """Compare immutable timeline against incremental MDA feature path."""
    reasons: list[str] = []
    data = np.load(timeline_path, allow_pickle=False)
    event_ts = data["event_ts"]
    availability = data["feature_availability_ts"]
    vectors = data["feature_vectors"]
    if len(event_ts) == 0:
        return True, reasons

    raw_events = load_npz_events(str(prepared_data_path))
    mda = HistoricalReplayMarketDataAdapter(
        raw_events,
        tick_size=tick_size,
        latency_ms=feature_latency_ms,
    )
    feature_latency_ns = int(feature_latency_ms * 1_000_000)
    limit = min(len(event_ts), max_rows)
    for i in range(limit):
        ts = int(event_ts[i])
        feature_ts = max(0, ts - feature_latency_ns)
        state = mda.sync_to_timestamp(feature_ts)
        if state is None:
            reasons.append(f"incremental_state_missing_at_{i}")
            continue
        incremental = np.asarray(state.feature_vector, dtype=np.float64)
        if incremental.shape != vectors[i].shape:
            reasons.append(f"feature_shape_mismatch_at_{i}")
            continue
        if not np.allclose(incremental, vectors[i], rtol=1e-6, atol=1e-8):
            reasons.append(f"feature_vector_mismatch_at_{i}")
        mda.sync_to_timestamp(ts)
    if len(availability) != len(event_ts):
        reasons.append("availability_length_mismatch")
    return not reasons, reasons
