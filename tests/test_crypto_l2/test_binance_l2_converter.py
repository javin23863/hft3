"""Tests for Binance L2 NDJSON→NPZ converter."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
from hftbacktest.types import ADD_ORDER_EVENT, BUY_EVENT, CANCEL_ORDER_EVENT, EXCH_EVENT, SELL_EVENT, event_dtype

from crypto_lane.src.data_io.binance_l2_converter import BinanceOrderBook, convert_ndjson_to_npz


@pytest.fixture
def tmp_dir() -> Path:
    d = Path(tempfile.mkdtemp())
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def _depth_update(bids: list, asks: list) -> str:
    return json.dumps({
        "e": "depthUpdate",
        "E": 123456789,
        "s": "btcusdt",
        "U": 1,
        "u": 10,
        "b": bids,
        "a": asks,
    })


class TestBinanceOrderBook:
    def test_apply_depth_update_adds_bids(self):
        book = BinanceOrderBook()
        data = {"b": [["50000.0", "1.5"], ["49999.9", "2.0"]], "a": [["50001.0", "1.0"]]}
        events = book.apply_depth_update(data, 1000)
        assert book.bids[50000.0] == 1.5
        assert book.bids[49999.9] == 2.0
        assert book.asks[50001.0] == 1.0
        assert any(ev[0] & ADD_ORDER_EVENT and ev[0] & BUY_EVENT for ev in events)
        assert any(ev[0] & SELL_EVENT for ev in events)

    def test_apply_depth_update_removes_level(self):
        book = BinanceOrderBook()
        book.bids[50000.0] = 1.0
        book.asks[50001.0] = 1.0
        data = {"b": [["50000.0", "0"]], "a": [["50001.0", "0"]]}
        events = book.apply_depth_update(data, 2000)
        assert 50000.0 not in book.bids
        assert 50001.0 not in book.asks
        cancel_events = [ev for ev in events if (ev[0] & 0xf) == CANCEL_ORDER_EVENT]
        assert len(cancel_events) == 2

    def test_apply_depth_update_modifies_qty(self):
        book = BinanceOrderBook()
        book.bids[50000.0] = 1.0
        data = {"b": [["50000.0", "5.0"]], "a": []}
        events = book.apply_depth_update(data, 2000)
        assert book.bids[50000.0] == 5.0
        add_events = [ev for ev in events if (ev[0] & 0xf) == ADD_ORDER_EVENT]
        assert len(add_events) == 1


class TestConvertBinanceL2:
    def test_converts_depth_updates(self, tmp_dir: Path):
        lines = [
            _depth_update([["50000.0", "1.0"]], [["50001.0", "1.0"]]),
            _depth_update([["50000.0", "2.0"]], [["50001.0", "0"]]),
        ]
        ndjson_path = tmp_dir / "test.ndjson"
        ndjson_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        npz_path = tmp_dir / "out.npz"

        result = convert_ndjson_to_npz(ndjson_path, npz_path)
        assert result == npz_path

        data = np.load(npz_path)["data"]
        assert len(data) > 0
        assert data.dtype == event_dtype
        assert data[0]["px"] == 50000.0
        assert data[0]["qty"] == 1.0

    def test_empty_file_raises(self, tmp_dir: Path):
        ndjson_path = tmp_dir / "empty.ndjson"
        ndjson_path.write_text("", encoding="utf-8")
        npz_path = tmp_dir / "out.npz"

        with pytest.raises(ValueError, match="No events"):
            convert_ndjson_to_npz(ndjson_path, npz_path)

    def test_ignores_non_depth_messages(self, tmp_dir: Path):
        lines = [
            json.dumps({"e": "trade", "s": "btcusdt", "p": "50000.0", "q": "1.0"}),
            _depth_update([["50000.0", "1.0"]], [["50001.0", "1.0"]]),
        ]
        ndjson_path = tmp_dir / "test.ndjson"
        ndjson_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        npz_path = tmp_dir / "out.npz"

        convert_ndjson_to_npz(ndjson_path, npz_path)
        data = np.load(npz_path)["data"]
        assert len(data) > 0

    def test_uses_event_time_when_available(self, tmp_dir: Path):
        lines = [
            json.dumps({"e": "depthUpdate", "E": 1000, "s": "btcusdt", "U": 1, "u": 1, "b": [["100.0", "1.0"]], "a": [["101.0", "1.0"]]}),
            json.dumps({"e": "depthUpdate", "E": 2000, "s": "btcusdt", "U": 2, "u": 2, "b": [["100.0", "2.0"]], "a": []}),
        ]
        ndjson_path = tmp_dir / "test.ndjson"
        ndjson_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        npz_path = tmp_dir / "out.npz"

        convert_ndjson_to_npz(ndjson_path, npz_path)
        data = np.load(npz_path)["data"]
        assert data[0]["exch_ts"] == 1000_000_000
        assert data[-1]["exch_ts"] == 2000_000_000

    def test_snapshot_seeds_book(self, tmp_dir: Path):
        snap = {"lastUpdateId": 5, "bids": [["100.0", "10.0"]], "asks": [["101.0", "10.0"]]}
        snap_path = tmp_dir / "snap.json"
        snap_path.write_text(json.dumps(snap), encoding="utf-8")

        lines = [
            json.dumps({"e": "depthUpdate", "E": 2000, "s": "btcusdt", "U": 1, "u": 3, "b": [["100.0", "1.0"]], "a": []}),
            json.dumps({"e": "depthUpdate", "E": 3000, "s": "btcusdt", "U": 4, "u": 6, "b": [["102.0", "5.0"]], "a": [["103.0", "5.0"]]}),
        ]
        ndjson_path = tmp_dir / "test.ndjson"
        ndjson_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        npz_path = tmp_dir / "out.npz"

        result = convert_ndjson_to_npz(ndjson_path, npz_path, snapshot_path=snap_path)
        data = np.load(result)["data"]
        assert len(data) > 2
        px = data["px"]
        assert 100.0 in px
        assert 101.0 in px

    def test_monotonic_timestamps(self, tmp_dir: Path):
        lines = [
            _depth_update([["100.0", "1.0"]], [["101.0", "1.0"]]),
            _depth_update([["100.0", "2.0"]], [["101.0", "0.0"]]),
        ]
        ndjson_path = tmp_dir / "test.ndjson"
        ndjson_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        npz_path = tmp_dir / "out.npz"

        convert_ndjson_to_npz(ndjson_path, npz_path, start_time_ns=1_000_000_000, step_ns=1_000_000)
        data = np.load(npz_path)["data"]
        assert np.all(np.diff(data["exch_ts"]) >= 0)
