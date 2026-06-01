"""Tests for Kraken L3 WebSocket recorder (message handling logic)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from crypto_lane.src.data_io.kraken_l3_recorder import KrakenL3Recorder, _map_symbol


@pytest.fixture
def tmp_dir() -> Path:
    d = Path(tempfile.mkdtemp())
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


class TestKrakenL3Recorder:
    def test_writes_ndjson_line(self, tmp_dir: Path):
        recorder = KrakenL3Recorder(symbols=["BTC/USD"], output_dir=tmp_dir)
        sym = "BTC/USD"
        fh = open(tmp_dir / "test.ndjson", "w", encoding="utf-8")
        recorder._files[sym] = fh

        recorder._write_line(sym, {"type": "snapshot", "data": {"bs": []}, "channel": "book"})
        fh.close()

        lines = (tmp_dir / "test.ndjson").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["type"] == "snapshot"

    def test_subscribe_message_maps_symbols(self, tmp_dir: Path):
        import asyncio

        recorder = KrakenL3Recorder(symbols=["BTC/USD"], output_dir=tmp_dir)

        sent_messages = []

        class FakeWebSocket:
            async def send(self, msg: str):
                sent_messages.append(msg)

        asyncio.run(recorder._subscribe(FakeWebSocket()))
        assert len(sent_messages) == 1
        sub = json.loads(sent_messages[0])
        assert sub["event"] == "subscribe"
        assert sub["pair"] == ["XBT/USD"]
        assert sub["subscription"]["name"] == "book"
        assert sub["subscription"]["depth"] == 1000

    def test_heartbeat_ignored(self, tmp_dir: Path):
        recorder = KrakenL3Recorder(symbols=["BTC/USD"], output_dir=tmp_dir)
        fh = open(tmp_dir / "test.ndjson", "w", encoding="utf-8")
        recorder._files["BTC/USD"] = fh

        import asyncio

        class FakeWS:
            async def recv(self):
                return '{"event": "heartbeat"}'
            async def send(self, msg):
                pass

        async def run():
            await recorder.handle_message('{"event": "heartbeat"}')

        asyncio.run(run())
        fh.close()
        content = (tmp_dir / "test.ndjson").read_text(encoding="utf-8").strip()
        assert content == ""

    def test_multiple_symbols_set_on_recorder(self, tmp_dir: Path):
        recorder = KrakenL3Recorder(symbols=["BTC/USD", "ETH/USD"], output_dir=tmp_dir)
        assert recorder.user_symbols == ["BTC/USD", "ETH/USD"]
        assert recorder.kraken_symbols == ["XBT/USD", "ETH/USD"]

    def test_symbol_mapping(self):
        assert _map_symbol("BTC/USD") == "XBT/USD"
        assert _map_symbol("XBT/USD") == "XBT/USD"
        assert _map_symbol("ETH/USD") == "ETH/USD"
        assert _map_symbol("SOL/USD") == "SOL/USD"

    def test_checksum_message_skipped(self, tmp_dir: Path):
        recorder = KrakenL3Recorder(symbols=["BTC/USD"], output_dir=tmp_dir)
        fh = open(tmp_dir / "test.ndjson", "w", encoding="utf-8")
        recorder._files["BTC/USD"] = fh

        import asyncio

        async def run():
            await recorder.handle_message(
                '[119930880, {"c": "323149093"}, "book-1000", "XBT/USD"]'
            )

        asyncio.run(run())
        fh.close()
        content = (tmp_dir / "test.ndjson").read_text(encoding="utf-8").strip()
        assert content == ""

    def test_v1_snapshot_written(self, tmp_dir: Path):
        recorder = KrakenL3Recorder(symbols=["BTC/USD"], output_dir=tmp_dir)
        fh = open(tmp_dir / "test.ndjson", "w", encoding="utf-8")
        recorder._files["BTC/USD"] = fh

        import asyncio

        async def run():
            await recorder.handle_message(
                '[119930880, {"bs":[["50000.0","1.5"]],"as":[["50001.0","2.0"]]}, "book-1000", "XBT/USD"]'
            )

        asyncio.run(run())
        fh.close()
        lines = (tmp_dir / "test.ndjson").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["type"] == "snapshot"
        assert parsed["data"]["bs"][0][0] == "50000.0"

    def test_v1_update_written(self, tmp_dir: Path):
        recorder = KrakenL3Recorder(symbols=["ETH/USD"], output_dir=tmp_dir)
        fh = open(tmp_dir / "test.ndjson", "w", encoding="utf-8")
        recorder._files["ETH/USD"] = fh

        import asyncio

        async def run():
            await recorder.handle_message(
                '[119930880, {"a":[["50001.0","0"]],"b":[["50000.0","2.0"]]}, "book-1000", "ETH/USD"]'
            )

        asyncio.run(run())
        fh.close()
        lines = (tmp_dir / "test.ndjson").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["type"] == "update"
        assert "a" in parsed["data"]

    def test_v2_snapshot_written(self, tmp_dir: Path):
        recorder = KrakenL3Recorder(symbols=["BTC/USD"], output_dir=tmp_dir)
        fh = open(tmp_dir / "test.ndjson", "w", encoding="utf-8")
        recorder._files["BTC/USD"] = fh

        import asyncio

        msg = json.dumps({
            "channel": "book",
            "type": "snapshot",
            "data": {"bids": [["50000.0", "1.5"]], "asks": [["50001.0", "2.0"]]},
            "symbol": "XBT/USD",
        })

        async def run():
            await recorder.handle_message(msg)

        asyncio.run(run())
        fh.close()
        lines = (tmp_dir / "test.ndjson").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["type"] == "snapshot"
        assert "bs" in parsed["data"]
        assert "as" in parsed["data"]

    def test_subscription_status_logged(self, tmp_dir: Path, capsys):
        recorder = KrakenL3Recorder(symbols=["ETH/USD"], output_dir=tmp_dir)

        import asyncio

        async def run():
            await recorder.handle_message(
                json.dumps({"event": "subscriptionStatus", "status": "subscribed", "pair": "ETH/USD"})
            )

        asyncio.run(run())
        captured = capsys.readouterr()
        assert "subscribed" in captured.err

    def test_unknown_dict_skipped(self, tmp_dir: Path):
        recorder = KrakenL3Recorder(symbols=["BTC/USD"], output_dir=tmp_dir)
        fh = open(tmp_dir / "test.ndjson", "w", encoding="utf-8")
        recorder._files["BTC/USD"] = fh

        import asyncio

        async def run():
            await recorder.handle_message(
                json.dumps({"something": "unrelated"})
            )

        asyncio.run(run())
        fh.close()
        content = (tmp_dir / "test.ndjson").read_text(encoding="utf-8").strip()
        assert content == ""
