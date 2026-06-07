"""Tests for unified event data resolver."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from data_system.src.event_data_resolver import (
    VIX_OPT_SYMBOL,
    _mbo_slot_status,
    _vix_slot_status,
    build_priority_lane_coverage,
    resolve_sensor_for_event,
    resolve_vix_raw_for_event,
    sensor_filename,
)
from data_system.src.npz_resolver import npz_filename, resolve_npz_for_event
from mbo_release_lane.storage import raw_dbn_path, release_slot_dir, write_json


def _write_release_manifest(slot: Path, *, validation: str = "valid", event_count: int = 100) -> None:
    from mbo_release_lane.storage import build_release_event_path

    payload = build_release_event_path(
        release_id=slot.parts[-2],
        release_name=slot.parts[-2],
        scheduled_release_timestamp="2024-01-11T13:30:00+00:00",
        actual_release_timestamp="2024-01-11T13:30:00+00:00",
        symbol=slot.parts[-1].replace("_", "/"),
        venue="CME",
        window_start="2024-01-11T13:29:00+00:00",
        window_end="2024-01-11T13:30:10+00:00",
        events_ref=str(raw_dbn_path(slot)),
        event_count=event_count,
        first_sequence=1,
        last_sequence=event_count,
        sequence_gap_count=0,
        source_vendor="databento",
        dataset_id="GLBX.MDP3",
        validation_status=validation,
    )
    write_json(slot / "release_event_path.json", payload)


def test_resolve_vix_raw_in_mbo_release_slot(tmp_path: Path):
    repo = tmp_path / "repo"
    eid = "CPI_2024_01_11_TIGHT"
    slot = release_slot_dir(repo, eid, VIX_OPT_SYMBOL)
    raw = raw_dbn_path(slot)
    slot.mkdir(parents=True)
    raw.write_bytes(b"dbn")

    path, ok = resolve_vix_raw_for_event(repo, eid)
    assert path == raw
    assert ok is False  # truncated/non-DBN bytes fail header validation


def test_vix_corrupt_raw_marked_invalid(tmp_path: Path):
    repo = tmp_path / "repo"
    eid = "CPI_2024_01_11_TIGHT"
    slot = release_slot_dir(repo, eid, VIX_OPT_SYMBOL)
    slot.mkdir(parents=True)
    raw_dbn_path(slot).write_bytes(b"not-a-valid-dbn")

    st = _vix_slot_status(repo, eid, datetime(2024, 1, 11, tzinfo=timezone.utc))
    assert st.status == "invalid"
    assert st.raw_ok is False
    assert st.sensor_ok is False


def test_resolve_sensor_parquet(tmp_path: Path):
    repo = tmp_path / "repo"
    eid = "CPI_2024_01_11_TIGHT"
    sensors = repo / "data" / "sensors"
    sensors.mkdir(parents=True)
    target = sensors / sensor_filename(eid)
    pd.DataFrame(
        [{"event_id": eid, "offset_sec": 0, "sensor": "VIX_ATM_STRIKE", "level": 18.5, "ts_ns": 1}]
    ).to_parquet(target, index=False)

    path, ok = resolve_sensor_for_event(repo, eid)
    assert ok
    assert path == target


def test_all_nan_sensor_parquet_not_present(tmp_path: Path):
    repo = tmp_path / "repo"
    eid = "CPI_2024_01_11_TIGHT"
    sensors = repo / "data" / "sensors"
    sensors.mkdir(parents=True)
    target = sensors / sensor_filename(eid)
    pd.DataFrame(
        [{"event_id": eid, "offset_sec": 0, "sensor": "VIX_ATM_STRIKE", "level": float("nan"), "ts_ns": 1}]
    ).to_parquet(target, index=False)

    _, ok = resolve_sensor_for_event(repo, eid)
    assert ok is False


def test_zero_byte_npz_not_present(tmp_path: Path):
    repo = tmp_path / "repo"
    eid = "CPI_2024_01_11_TIGHT"
    sym = "ES.v.0"
    npz_dir = repo / "data" / "npz"
    npz_dir.mkdir(parents=True)
    (npz_dir / npz_filename(sym, eid)).write_bytes(b"")

    _, present, _ = resolve_npz_for_event(repo, eid, sym, (sym,))
    assert present is False


@pytest.mark.parametrize(
    "npz,raw_bytes,validation,deriveable,expected",
    [
        (True, b"x", "valid", True, "complete"),
        (False, None, None, False, "not_downloaded"),
        (False, b"bad", "valid", False, "invalid"),
    ],
)
def test_mbo_slot_status_matrix(
    tmp_path: Path, npz: bool, raw_bytes, validation, deriveable, expected: str
):
    repo = tmp_path / "repo"
    eid = "CPI_2024_01_11_TIGHT"
    sym = "ES.v.0"
    slot = release_slot_dir(repo, eid, sym)
    slot.mkdir(parents=True, exist_ok=True)
    if raw_bytes is not None:
        raw_dbn_path(slot).write_bytes(raw_bytes)
        if validation:
            _write_release_manifest(slot, validation=validation)
    if npz:
        npz_dir = repo / "data" / "npz"
        npz_dir.mkdir(parents=True, exist_ok=True)
        (npz_dir / npz_filename(sym, eid)).write_bytes(b"npz-bytes")

    st = _mbo_slot_status(repo, eid, sym, (sym,))
    assert st.status == expected


@pytest.mark.parametrize(
    "start,raw_bytes,sensor_level,expected",
    [
        (datetime(2020, 1, 1, tzinfo=timezone.utc), None, None, "skipped_pre_cmbp1"),
        (datetime(2024, 1, 11, tzinfo=timezone.utc), None, 18.0, "complete"),
        (datetime(2024, 1, 11, tzinfo=timezone.utc), b"readable", None, "derivable"),
        (datetime(2024, 1, 11, tzinfo=timezone.utc), b"bad", None, "invalid"),
        (datetime(2024, 1, 11, tzinfo=timezone.utc), None, None, "not_downloaded"),
    ],
)
def test_vix_slot_status_matrix(tmp_path: Path, start, raw_bytes, sensor_level, expected: str, monkeypatch):
    repo = tmp_path / "repo"
    eid = "CPI_2024_01_11_TIGHT"
    if raw_bytes is not None:
        slot = release_slot_dir(repo, eid, VIX_OPT_SYMBOL)
        slot.mkdir(parents=True, exist_ok=True)
        raw_dbn_path(slot).write_bytes(raw_bytes)
        readable = raw_bytes == b"readable"
        monkeypatch.setattr(
            "mbo_release_lane.storage.validate_dbn_readable",
            lambda p: p.read_bytes() == b"readable",
        )
        monkeypatch.setattr(
            "mbo_release_lane.storage.dbn_has_quote_records",
            lambda p: readable,
        )
    if sensor_level is not None:
        sensors = repo / "data" / "sensors"
        sensors.mkdir(parents=True)
        pd.DataFrame(
            [
                {
                    "event_id": eid,
                    "offset_sec": 0,
                    "sensor": "VIX_ATM_STRIKE",
                    "level": sensor_level,
                    "ts_ns": 1,
                }
            ]
        ).to_parquet(sensors / sensor_filename(eid), index=False)

    st = _vix_slot_status(repo, eid, start)
    assert st.status == expected


def test_derive_sensor_parquet_all_nan_returns_none(tmp_path: Path, monkeypatch):
    from mbo_release_lane.sensor_adapter import derive_sensor_parquet_for_event

    repo = tmp_path / "repo"
    eid = "CPI_2024_01_11_TIGHT"
    slot = release_slot_dir(repo, eid, VIX_OPT_SYMBOL)
    slot.mkdir(parents=True, exist_ok=True)
    raw_dbn_path(slot).write_bytes(b"raw")

    monkeypatch.setattr(
        "mbo_release_lane.sensor_adapter.has_deriveable_vix_raw",
        lambda *_: True,
    )
    monkeypatch.setattr(
        "mbo_release_lane.sensor_adapter.resolve_vix_raw_for_event",
        lambda *_: (raw_dbn_path(slot), True),
    )
    monkeypatch.setattr(
        "mbo_release_lane.sensor_adapter.derive_sensors_from_vix_raw",
        lambda *_, **__: pd.DataFrame(
            [{"event_id": eid, "offset_sec": 0, "sensor": "VIX_ATM_STRIKE", "level": float("nan"), "ts_ns": 1}]
        ),
    )
    monkeypatch.setattr(
        "mbo_release_lane.sensor_adapter._sensor_df_has_finite_level",
        lambda df: False,
    )

    assert derive_sensor_parquet_for_event(repo, eid, anchor_ns=1) is None


def test_build_priority_lane_coverage_smoke(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    report = build_priority_lane_coverage(repo)
    assert "mbo" in report
    assert "vix" in report
    assert report["window_count"] >= 0
