"""Tests for Binance L2 depth recorder (message handling logic)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from crypto_lane.src.data_io.binance_l2_recorder import BinanceL2Recorder, DEFAULT_SYMBOLS


@pytest.fixture
def tmp_dir() -> Path:
    d = Path(tempfile.mkdtemp())
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


class TestBinanceL2Recorder:
    def test_writes_ndjson_line(self, tmp_dir: Path):
        recorder = BinanceL2Recorder(symbols=["btcusdt"], output_dir=tmp_dir)
        fh = open(tmp_dir / "test.ndjson", "w", encoding="utf-8")
        recorder._files["btcusdt"] = fh

        recorder._write_line("btcusdt", {"e": "depthUpdate", "s": "BTCUSDT"})
        fh.close()

        lines = (tmp_dir / "test.ndjson").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["e"] == "depthUpdate"

    def test_subscribe_message(self, tmp_dir: Path):
        import asyncio

        recorder = BinanceL2Recorder(symbols=["btcusdt"], output_dir=tmp_dir)

        sent_messages = []

        class FakeWebSocket:
            async def send(self, msg: str):
                sent_messages.append(msg)

        asyncio.run(recorder._subscribe(FakeWebSocket()))
        assert len(sent_messages) == 1
        sub = json.loads(sent_messages[0])
        assert sub["method"] == "SUBSCRIBE"
        assert sub["params"] == ["btcusdt@depth@100ms"]

    def test_handle_depth_update(self, tmp_dir: Path):
        recorder = BinanceL2Recorder(symbols=["btcusdt"], output_dir=tmp_dir)
        fh = open(tmp_dir / "test.ndjson", "w", encoding="utf-8")
        recorder._files["btcusdt"] = fh

        import asyncio

        async def run():
            msg = json.dumps({"e": "depthUpdate", "s": "btcusdt", "b": [["50000.0", "1.5"]], "a": [["50001.0", "2.0"]]})
            await recorder._handle_message(None, msg)

        asyncio.run(run())
        fh.close()

        content = (tmp_dir / "test.ndjson").read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        assert parsed["e"] == "depthUpdate"
        assert parsed["s"] == "btcusdt"

    def test_handle_wrong_symbol_ignored(self, tmp_dir: Path):
        recorder = BinanceL2Recorder(symbols=["btcusdt"], output_dir=tmp_dir)
        fh = open(tmp_dir / "test.ndjson", "w", encoding="utf-8")
        recorder._files["btcusdt"] = fh

        import asyncio

        async def run():
            msg = json.dumps({"e": "depthUpdate", "s": "ETHUSDT", "b": [], "a": []})
            await recorder._handle_message(None, msg)

        asyncio.run(run())
        fh.close()

        content = (tmp_dir / "test.ndjson").read_text(encoding="utf-8").strip()
        assert content == ""
