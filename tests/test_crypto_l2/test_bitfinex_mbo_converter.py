"""Tests for Bitfinex R0 MBO NDJSON→NPZ converter."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from crypto_lane.src.data_io.bitfinex_mbo_converter import (
    convert_ndjson_to_npz_with_meta,
    _parse_events_from_ndjson,
)


def _write_fixture(path: Path) -> None:
    lines = [
        {
            "symbol": "tBTCUSD",
            "type": "snapshot",
            "orders": [[1001, 61000.0, 0.5], [1002, 61001.0, -0.3]],
        },
        {"symbol": "tBTCUSD", "type": "update", "order_id": 1003, "price": 60999.0, "amount": 0.1},
        {"symbol": "tBTCUSD", "type": "update", "order_id": 1001, "price": 0, "amount": 1},
    ]
    path.write_text("\n".join(json.dumps(row) for row in lines) + "\n", encoding="utf-8")


def test_parse_bitfinex_mbo_events(tmp_path: Path):
    ndjson = tmp_path / "sample.ndjson"
    _write_fixture(ndjson)
    events = _parse_events_from_ndjson(ndjson, 1_000_000_000)
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
