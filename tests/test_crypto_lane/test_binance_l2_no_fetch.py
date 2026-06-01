"""Tests for the --no-fetch flag in the Binance L2 NDJSON -> NPZ converter CLI.

These tests guard against regression of the Stream 3 fix: --no-fetch must
suppress the live REST snapshot fetch. They also confirm the inverse paths
(fetches happen when a symbol is supplied, and snapshot files short-circuit
the fetch).
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

import pytest

from crypto_lane.src.data_io import binance_l2_converter as blc


@pytest.fixture(autouse=True)
def _ensure_urllib_in_module():
    """The converter does `import urllib.request` lazily inside _fetch_snapshot.

    Bind it to the module namespace so monkeypatch.setattr can target it.
    """
    blc.urllib = __import__("urllib")  # type: ignore[attr-defined]
    blc.urllib.request = urllib.request  # type: ignore[attr-defined]
    yield


SENTINEL_FETCH_ERROR = RuntimeError("SHOULD_NOT_FETCH")


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _make_ndjson(path: Path) -> None:
    """Write a tiny but valid Binance diff-depth NDJSON fixture."""
    lines: List[str] = [
        json.dumps({
            "e": "depthUpdate",
            "E": 1700000000000,
            "s": "BTCUSDT",
            "U": 101,
            "u": 110,
            "b": [["50000.00", "0.10"]],
            "a": [["50001.00", "0.20"]],
        }),
        json.dumps({
            "e": "depthUpdate",
            "E": 1700000001000,
            "s": "BTCUSDT",
            "U": 111,
            "u": 120,
            "b": [],
            "a": [["50002.00", "0.30"]],
        }),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_snapshot_file(path: Path) -> None:
    path.write_text(
        json.dumps({
            "lastUpdateId": 100,
            "E": 1699999999000,
            "bids": [["49999.00", "1.00"], ["49998.00", "2.00"]],
            "asks": [["50001.00", "1.00"], ["50002.00", "2.00"]],
        }),
        encoding="utf-8",
    )


def _build_args(
    ndjson: Path,
    output: Path,
    routing_symbol: str = "BTCUSDT",
    snapshot: str = None,
    no_fetch: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        ndjson=str(ndjson),
        output=str(output),
        routing_symbol=routing_symbol,
        snapshot=snapshot,
        no_fetch=no_fetch,
        start_time_ns=1_000_000_000,
        step_ns=1_000_000,
    )


def test_no_fetch_does_not_invoke_urlopen(tmp_path, monkeypatch):
    ndjson = tmp_path / "feed.ndjson"
    out = tmp_path / "out.npz"
    _make_ndjson(ndjson)

    def _boom(*args, **kwargs):
        raise SENTINEL_FETCH_ERROR

    monkeypatch.setattr(blc.urllib.request, "urlopen", _boom)

    args = _build_args(ndjson, out, no_fetch=True)
    rc = blc.cmd_convert_binance_l2(args)

    assert rc == 0, "cmd_convert_binance_l2 should succeed when --no-fetch is set"
    assert out.exists(), "NPZ should be produced without a live fetch"


def test_fetch_does_invoke_urlopen_when_symbol_set(tmp_path, monkeypatch):
    ndjson = tmp_path / "feed.ndjson"
    out = tmp_path / "out.npz"
    _make_ndjson(ndjson)

    snap_payload = {
        "lastUpdateId": 100,
        "E": 1699999999000,
        "bids": [["49999.00", "1.00"]],
        "asks": [["50001.00", "1.00"]],
    }
    calls: List[Any] = []

    def _fake_urlopen(url, timeout=15, *args, **kwargs):
        calls.append(url)
        return _FakeResponse(snap_payload)

    monkeypatch.setattr(blc.urllib.request, "urlopen", _fake_urlopen)

    args = _build_args(ndjson, out, no_fetch=False)
    rc = blc.cmd_convert_binance_l2(args)

    assert rc == 0
    assert calls, "urlopen should be invoked when --routing-symbol is set without --no-fetch"
    assert out.exists()


def test_snapshot_path_skips_fetch(tmp_path, monkeypatch):
    ndjson = tmp_path / "feed.ndjson"
    snap = tmp_path / "snap.json"
    out = tmp_path / "out.npz"
    _make_ndjson(ndjson)
    _make_snapshot_file(snap)

    def _boom(*args, **kwargs):
        raise SENTINEL_FETCH_ERROR

    monkeypatch.setattr(blc.urllib.request, "urlopen", _boom)

    args = _build_args(ndjson, out, snapshot=str(snap), no_fetch=False)
    rc = blc.cmd_convert_binance_l2(args)

    assert rc == 0
    assert out.exists(), "NPZ should be produced from the provided snapshot file"


def test_no_fetch_with_snapshot_path_still_skips_fetch(tmp_path, monkeypatch):
    """--no-fetch combined with --snapshot must not attempt a live fetch."""
    ndjson = tmp_path / "feed.ndjson"
    snap = tmp_path / "snap.json"
    out = tmp_path / "out.npz"
    _make_ndjson(ndjson)
    _make_snapshot_file(snap)

    def _boom(*args, **kwargs):
        raise SENTINEL_FETCH_ERROR

    monkeypatch.setattr(blc.urllib.request, "urlopen", _boom)

    args = _build_args(ndjson, out, snapshot=str(snap), no_fetch=True)
    rc = blc.cmd_convert_binance_l2(args)

    assert rc == 0
    assert out.exists()
