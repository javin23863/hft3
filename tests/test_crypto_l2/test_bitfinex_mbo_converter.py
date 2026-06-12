"""Tests for Bitfinex R0 MBO NDJSON→NPZ converter."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from hftbacktest.types import ADD_ORDER_EVENT, CANCEL_ORDER_EVENT

from crypto_lane.src.data_io.bitfinex_mbo_converter import (
    convert_ndjson_to_npz,
    convert_ndjson_to_npz_with_meta,
    _parse_events_from_ndjson,
)


def _write_fixture(path: Path) -> None:
    lines = [
        {
            "symbol": "tBTCUSD",
            "type": "snapshot",
            "orders": [[1001, 61000.0, 0.5], [1002, 61001.0, -0.3]],
            "_local_ts_utc": "2026-06-10T15:44:34.000000+00:00",
        },
        {
            "symbol": "tBTCUSD",
            "type": "update",
            "order_id": 1003,
            "price": 60999.0,
            "amount": 0.1,
            "_local_ts_utc": "2026-06-10T15:44:34.100000+00:00",
        },
        {
            "symbol": "tBTCUSD",
            "type": "update",
            "order_id": 1001,
            "price": 0,
            "amount": 1,
            "_local_ts_utc": "2026-06-10T15:44:34.200000+00:00",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in lines) + "\n", encoding="utf-8")


def test_parse_bitfinex_mbo_events(tmp_path: Path):
    ndjson = tmp_path / "sample.ndjson"
    _write_fixture(ndjson)
    events, _mode = _parse_events_from_ndjson(ndjson, 1_000_000_000)
    assert len(events) == 4  # 2 snapshot adds + 1 update add + 1 cancel


def test_convert_bitfinex_mbo_writes_l3_meta(tmp_path: Path):
    ndjson = tmp_path / "sample.ndjson"
    npz = tmp_path / "out_mbo.npz"
    _write_fixture(ndjson)
    convert_ndjson_to_npz_with_meta(ndjson, npz, symbol="BTC_USD")
    assert npz.is_file()
    meta = json.loads(npz.with_name(npz.stem + ".meta.json").read_text(encoding="utf-8"))
    assert meta["data_class"] == "L3_MBO"
    assert meta["execution_classification"] == "L3_VALIDATED"
    assert len(np.load(npz)["data"]) >= 4


def _write_reconnect_fixture(path: Path) -> None:
    lines = [
        {
            "symbol": "tBTCUSD",
            "type": "snapshot",
            "orders": [[1001, 61000.0, 0.5], [1002, 61001.0, -0.3]],
            "_local_ts_utc": "2026-06-10T15:44:34.000000+00:00",
        },
        {
            "symbol": "tBTCUSD",
            "type": "update",
            "order_id": 1003,
            "price": 60999.0,
            "amount": 0.1,
            "_local_ts_utc": "2026-06-10T15:44:34.100000+00:00",
        },
        {
            "symbol": "tBTCUSD",
            "type": "snapshot",
            "orders": [[1002, 61001.0, -0.3], [1004, 61002.0, 0.2]],
            "_local_ts_utc": "2026-06-10T15:44:34.200000+00:00",
        },
        {
            "symbol": "tBTCUSD",
            "type": "update",
            "order_id": 1002,
            "price": 0,
            "amount": -1,
            "_local_ts_utc": "2026-06-10T15:44:34.300000+00:00",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in lines) + "\n", encoding="utf-8")


def test_reconnect_snapshot_emits_cancel_all(tmp_path: Path):
    ndjson = tmp_path / "reconnect.ndjson"
    _write_reconnect_fixture(ndjson)
    events, _mode = _parse_events_from_ndjson(ndjson, 1_000_000_000)

    kind_mask = 0xFF
    oid_idx = 5

    second_snap_add_pos = next(
        i for i, ev in enumerate(events)
        if (ev[0] & kind_mask) == (ADD_ORDER_EVENT & kind_mask) and ev[oid_idx] == 1004
    )
    cancel_oids_before = {
        ev[oid_idx]
        for ev in events[:second_snap_add_pos]
        if (ev[0] & kind_mask) == (CANCEL_ORDER_EVENT & kind_mask)
    }
    assert 1001 in cancel_oids_before, "no synthetic cancel for oid 1001 before reconnect snapshot"
    assert 1002 in cancel_oids_before, "no synthetic cancel for oid 1002 before reconnect snapshot"
    assert 1003 in cancel_oids_before, "no synthetic cancel for oid 1003 before reconnect snapshot"

    seen_active: dict[int, bool] = {}
    violations = 0
    for ev in events:
        k = ev[0] & kind_mask
        oid = ev[oid_idx]
        if k == (ADD_ORDER_EVENT & kind_mask):
            if oid in seen_active:
                violations += 1
            seen_active[oid] = True
        elif k == (CANCEL_ORDER_EVENT & kind_mask):
            if oid not in seen_active:
                violations += 1
            seen_active.pop(oid, None)
    assert violations == 0, f"{violations} dup-add or unknown-cancel violations in reconnect fixture"

    # End-state: fixture second snapshot has 1002 and 1004; 1002 is then cancelled by update.
    # Expected open orders after all events: only 1004.
    assert set(seen_active.keys()) == {1004}, (
        f"end-state active orders {set(seen_active.keys())} != expected {{1004}}"
    )


def test_multi_file_merge_cancels_between_files(tmp_path: Path):
    shared_oid = 2001
    file1_lines = [
        {
            "symbol": "tBTCUSD",
            "type": "snapshot",
            "orders": [[shared_oid, 60000.0, 1.0], [2002, 60001.0, -0.5]],
            "_local_ts_utc": "2026-06-10T15:44:34.000000+00:00",
        },
        {
            "symbol": "tBTCUSD",
            "type": "update",
            "order_id": 2003,
            "price": 59999.0,
            "amount": 0.3,
            "_local_ts_utc": "2026-06-10T15:44:34.100000+00:00",
        },
    ]
    file2_lines = [
        {
            "symbol": "tBTCUSD",
            "type": "snapshot",
            "orders": [[shared_oid, 60000.0, 1.0], [2004, 60002.0, -0.2]],
            "_local_ts_utc": "2026-06-10T15:44:35.000000+00:00",
        },
    ]
    f1 = tmp_path / "day1.ndjson"
    f2 = tmp_path / "day2.ndjson"
    f1.write_text("\n".join(json.dumps(r) for r in file1_lines) + "\n", encoding="utf-8")
    f2.write_text("\n".join(json.dumps(r) for r in file2_lines) + "\n", encoding="utf-8")

    npz = tmp_path / "merged.npz"
    convert_ndjson_to_npz([f1, f2], npz)
    data = np.load(npz)["data"]

    kind_mask = 0xFF
    seen_active: dict[int, bool] = {}
    violations = 0
    for row in data:
        k = int(row["ev"]) & kind_mask
        oid = int(row["order_id"])
        if k == (ADD_ORDER_EVENT & kind_mask):
            if oid in seen_active:
                violations += 1
            seen_active[oid] = True
        elif k == (CANCEL_ORDER_EVENT & kind_mask):
            if oid not in seen_active:
                violations += 1
            seen_active.pop(oid, None)
    assert violations == 0, f"{violations} dup-add or unknown-cancel violations in multi-file merge"

    # Ordering: cancels for file-1 oids (2001, 2002, 2003) must appear before file-2 snapshot ADDs.
    second_snap_add_pos = next(
        i for i, row in enumerate(data)
        if (int(row["ev"]) & kind_mask) == (ADD_ORDER_EVENT & kind_mask) and int(row["order_id"]) == 2004
    )
    file1_oids = {2001, 2002, 2003}
    cancel_oids_before = {
        int(row["order_id"])
        for row in data[:second_snap_add_pos]
        if (int(row["ev"]) & kind_mask) == (CANCEL_ORDER_EVENT & kind_mask)
    }
    assert file1_oids == cancel_oids_before, (
        f"expected cancels for {file1_oids} before file-2 ADD rows, got {cancel_oids_before}"
    )


def test_real_timestamps_preserved(tmp_path: Path):
    # Two update lines 100 ms apart; after rebase to start_time_ns delta must be 100_000_000 ns.
    lines = [
        {
            "symbol": "tBTCUSD",
            "type": "snapshot",
            "orders": [[3001, 60000.0, 1.0]],
            "_local_ts_utc": "2026-06-10T15:44:34.000000+00:00",
        },
        {
            "symbol": "tBTCUSD",
            "type": "update",
            "order_id": 3002,
            "price": 59999.0,
            "amount": 0.5,
            "_local_ts_utc": "2026-06-10T15:44:34.100000+00:00",
        },
    ]
    ndjson = tmp_path / "real_ts.ndjson"
    ndjson.write_text("\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8")
    npz = tmp_path / "real_ts.npz"
    start_ns = 1_000_000_000
    convert_ndjson_to_npz(ndjson, npz, start_time_ns=start_ns)
    data = np.load(npz)["data"]

    # Snapshot ADD for 3001 → first event at start_ns; update ADD for 3002 → 100 ms later.
    kind_mask = 0xFF
    add_rows = [row for row in data if (int(row["ev"]) & kind_mask) == (ADD_ORDER_EVENT & kind_mask)]
    assert len(add_rows) == 2
    snap_ts = int(add_rows[0]["local_ts"])
    update_ts = int(add_rows[1]["local_ts"])
    assert snap_ts == start_ns, f"first ADD local_ts {snap_ts} != start_ns {start_ns}"
    assert update_ts - snap_ts == 100_000_000, (
        f"delta {update_ts - snap_ts} ns != 100_000_000 ns (100 ms)"
    )


def test_missing_ts_fallback(tmp_path: Path):
    # Lines without _local_ts_utc → legacy counter path, monotonic output, no crash.
    lines = [
        {"symbol": "tBTCUSD", "type": "snapshot", "orders": [[4001, 60000.0, 1.0], [4002, 60001.0, -0.5]]},
        {"symbol": "tBTCUSD", "type": "update", "order_id": 4003, "price": 59999.0, "amount": 0.3},
        {"symbol": "tBTCUSD", "type": "update", "order_id": 4001, "price": 0, "amount": 1},
    ]
    ndjson = tmp_path / "no_ts.ndjson"
    ndjson.write_text("\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8")
    npz = tmp_path / "no_ts.npz"
    convert_ndjson_to_npz(ndjson, npz)
    data = np.load(npz)["data"]
    assert len(data) >= 4
    local_ts_vals = [int(row["local_ts"]) for row in data]
    for i in range(len(local_ts_vals) - 1):
        assert local_ts_vals[i] < local_ts_vals[i + 1], (
            f"non-monotonic local_ts at index {i}: {local_ts_vals[i]} >= {local_ts_vals[i + 1]}"
        )


def test_same_ts_cancel_before_readd(tmp_path: Path):
    # Reconnect: oid 5001 is open in session 1; snapshot 2 (same _local_ts_utc as the cancels)
    # re-lists it.  After _normalize_replay_clock the CANCEL must precede the re-ADD in NPZ row order,
    # and the dup-add scan must find zero violations.
    lines = [
        {
            "symbol": "tBTCUSD",
            "type": "snapshot",
            "orders": [[5001, 62000.0, 0.5], [5002, 62001.0, -0.3]],
            "_local_ts_utc": "2026-06-10T16:00:00.000000+00:00",
        },
        # Reconnect snapshot — SAME timestamp as what the cancels will carry.
        {
            "symbol": "tBTCUSD",
            "type": "snapshot",
            "orders": [[5001, 62000.0, 0.5], [5003, 62002.0, 0.2]],
            "_local_ts_utc": "2026-06-10T16:00:00.000000+00:00",
        },
    ]
    ndjson = tmp_path / "same_ts.ndjson"
    ndjson.write_text("\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8")
    npz = tmp_path / "same_ts.npz"
    convert_ndjson_to_npz(ndjson, npz)
    data = np.load(npz)["data"]

    kind_mask = 0xFF
    # Find first re-ADD of oid 5001 after the reconnect.
    readd_pos = None
    cancel_pos = None
    for i, row in enumerate(data):
        k = int(row["ev"]) & kind_mask
        oid = int(row["order_id"])
        if oid == 5001:
            if k == (CANCEL_ORDER_EVENT & kind_mask) and cancel_pos is None:
                cancel_pos = i
            elif k == (ADD_ORDER_EVENT & kind_mask) and cancel_pos is not None and readd_pos is None:
                readd_pos = i
    assert cancel_pos is not None, "no synthetic CANCEL found for oid 5001"
    assert readd_pos is not None, "no re-ADD found for oid 5001 after its CANCEL"
    assert cancel_pos < readd_pos, (
        f"CANCEL for oid 5001 at row {cancel_pos} is NOT before re-ADD at row {readd_pos}"
    )

    # dup-add scan over entire NPZ
    seen_active: dict[int, bool] = {}
    violations = 0
    for row in data:
        k = int(row["ev"]) & kind_mask
        oid = int(row["order_id"])
        if k == (ADD_ORDER_EVENT & kind_mask):
            if oid in seen_active:
                violations += 1
            seen_active[oid] = True
        elif k == (CANCEL_ORDER_EVENT & kind_mask):
            seen_active.pop(oid, None)
    assert violations == 0, f"{violations} dup-add violations — sort key regression"


def test_mixed_mode_merge_raises(tmp_path: Path):
    # file1 has _local_ts_utc (real-ts mode); file2 has no timestamps (counter mode).
    # convert_ndjson_to_npz must raise ValueError before producing output.
    file1_lines = [
        {
            "symbol": "tBTCUSD",
            "type": "snapshot",
            "orders": [[6001, 60000.0, 1.0]],
            "_local_ts_utc": "2026-06-10T16:00:00.000000+00:00",
        },
    ]
    file2_lines = [
        {
            "symbol": "tBTCUSD",
            "type": "snapshot",
            "orders": [[6002, 60001.0, -0.5]],
            # no _local_ts_utc → counter mode
        },
    ]
    f1 = tmp_path / "real_ts.ndjson"
    f2 = tmp_path / "counter_ts.ndjson"
    f1.write_text("\n".join(json.dumps(r) for r in file1_lines) + "\n", encoding="utf-8")
    f2.write_text("\n".join(json.dumps(r) for r in file2_lines) + "\n", encoding="utf-8")

    npz = tmp_path / "mixed.npz"
    with pytest.raises(ValueError, match="Mixed timestamp modes"):
        convert_ndjson_to_npz([f1, f2], npz)
