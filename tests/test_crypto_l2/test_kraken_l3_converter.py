"""Tests for Kraken L3 NDJSON→NPZ converter."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
from hftbacktest.types import ADD_ORDER_EVENT, BUY_EVENT, CANCEL_ORDER_EVENT, EXCH_EVENT, SELL_EVENT, event_dtype

from crypto_lane.src.data_io.kraken_l3_converter import (
    KrakenOrderBook,
    convert_ndjson_to_npz,
)


@pytest.fixture
def tmp_dir() -> Path:
    d = Path(tempfile.mkdtemp())
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def _ndjson(tmp_dir: Path, lines: list[str]) -> Path:
    p = tmp_dir / "test.ndjson"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _make_snapshot(ts_utc: int, bids: list, asks: list) -> str:
    return json.dumps({
        "type": "snapshot",
        "data": {"bs": bids, "as": asks},
        "channel": "book",
        "timestamp_utc": ts_utc,
    })


def _make_update(side: str, entries: list) -> str:
    key = "b" if side == "bid" else "a"
    return json.dumps({
        "type": "update",
        "data": {key: entries},
        "channel": "book",
    })


class TestKrakenOrderBook:
    def test_snapshot_populates_book(self):
        book = KrakenOrderBook()
        data = {
            "bs": [["50000.0", "1.5"], ["49999.9", "2.0"]],
            "as": [["50000.1", "1.0"], ["50000.2", "3.0"]],
            "timestamp_utc": 1000,
        }
        events = book.apply_snapshot(data)
        assert len(events) == 4
        assert book.bids[50000.0] == 1.5
        assert book.bids[49999.9] == 2.0
        assert book.asks[50000.1] == 1.0
        assert book.asks[50000.2] == 3.0

    def test_update_adds_bid(self):
        book = KrakenOrderBook()
        book.bids[50000.0] = 1.0
        data = {"b": [["50000.5", "2.0"]]}
        events = book.apply_update(data, 2000)
        assert book.bids[50000.5] == 2.0
        assert any(ev[0] & ADD_ORDER_EVENT and ev[0] & BUY_EVENT for ev in events)

    def test_update_removes_ask(self):
        book = KrakenOrderBook()
        book.asks[50000.1] = 1.0
        data = {"a": [["50000.1", "0"]]}
        events = book.apply_update(data, 2000)
        assert 50000.1 not in book.asks
        assert any((ev[0] & 0xf) == CANCEL_ORDER_EVENT for ev in events)

    def test_update_zero_qty_not_in_book_ignored(self):
        book = KrakenOrderBook()
        data = {"a": [["99999.0", "0"]]}
        events = book.apply_update(data, 2000)
        assert len(events) == 0
        assert 99999.0 not in book.asks

    def test_update_increases_qty(self):
        book = KrakenOrderBook()
        book.bids[50000.0] = 1.0
        data = {"b": [["50000.0", "3.0"]]}
        events = book.apply_update(data, 2000)
        assert book.bids[50000.0] == 3.0
        assert any(ev[0] & ADD_ORDER_EVENT for ev in events)

    def test_snapshot_replaces_book(self):
        book = KrakenOrderBook()
        book.bids[49000.0] = 5.0
        data = {
            "bs": [["50000.0", "1.0"]],
            "as": [["50001.0", "1.0"]],
            "timestamp_utc": 2000,
        }
        book.apply_snapshot(data)
        assert 49000.0 not in book.bids
        assert book.bids[50000.0] == 1.0


class TestConvertNdjsonToNpz:
    def test_converts_snapshot_and_updates(self, tmp_dir: Path):
        lines = [
            _make_snapshot(1000, [["50000.0", "1.0"]], [["50000.1", "1.0"]]),
            _make_update("bid", [["50000.0", "2.0"]]),
            _make_update("ask", [["50000.1", "0"]]),
        ]
        ndjson_path = _ndjson(tmp_dir, lines)
        npz_path = tmp_dir / "out.npz"

        result = convert_ndjson_to_npz(ndjson_path, npz_path)
        assert result == npz_path
        assert npz_path.exists()

        data = np.load(npz_path)["data"]
        assert len(data) > 0
        assert data.dtype == event_dtype
        assert data[0]["px"] == 50000.0
        assert data[0]["qty"] == 1.0

    def test_empty_file_raises(self, tmp_dir: Path):
        ndjson_path = _ndjson(tmp_dir, [])
        npz_path = tmp_dir / "out.npz"

        with pytest.raises(ValueError, match="No events"):
            convert_ndjson_to_npz(ndjson_path, npz_path)

    def test_assigns_monotonic_timestamps(self, tmp_dir: Path):
        lines = [
            _make_snapshot(1000, [["50000.0", "1.0"]], [["50000.1", "1.0"]]),
            _make_update("bid", [["50000.0", "2.0"]]),
        ]
        ndjson_path = _ndjson(tmp_dir, lines)
        npz_path = tmp_dir / "out.npz"

        convert_ndjson_to_npz(ndjson_path, npz_path, start_time_ns=1_000_000_000, step_ns=1_000_000)
        data = np.load(npz_path)["data"]
        timestamps = data["exch_ts"]
        assert np.all(np.diff(timestamps) >= 0)

    def test_converts_multiple_snapshots(self, tmp_dir: Path):
        lines = [
            _make_snapshot(1000, [["100.0", "10.0"]], [["101.0", "10.0"]]),
            _make_snapshot(2000, [["100.5", "5.0"]], [["101.5", "5.0"]]),
        ]
        ndjson_path = _ndjson(tmp_dir, lines)
        npz_path = tmp_dir / "out.npz"

        convert_ndjson_to_npz(ndjson_path, npz_path)
        data = np.load(npz_path)["data"]
        assert len(data) == 4

    def test_event_flags_correct(self, tmp_dir: Path):
        lines = [
            _make_snapshot(1000, [["100.0", "1.0"]], [["101.0", "1.0"]]),
        ]
        ndjson_path = _ndjson(tmp_dir, lines)
        npz_path = tmp_dir / "out.npz"

        convert_ndjson_to_npz(ndjson_path, npz_path)
        data = np.load(npz_path)["data"]
        bid_events = data[data["ev"] & BUY_EVENT > 0]
        ask_events = data[data["ev"] & SELL_EVENT > 0]
        assert len(bid_events) == 1
        assert len(ask_events) == 1
        assert bid_events[0]["px"] == 100.0
        assert ask_events[0]["px"] == 101.0
