from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

from connectors.rithmic.smoke_test import (
    RithmicProtocolSmokeRunner,
    SmokeSettings,
    main as smoke_main,
)


def test_external_smoke_blocks_test_orangeburg_endpoint(tmp_path: Path) -> None:
    settings = SmokeSettings(
        env="external",
        symbol_root="ES",
        exchange="CME",
        duration_sec=0,
        config_path=tmp_path / "external.yaml",
        url="wss://rituz00100.00.rithmic.com",
        system_name="Rithmic Test",
        gateway="Orangeburg",
        username="unit_user",
        password="unit_password",
    )

    codes = {blocker.code for blocker in settings.blockers()}

    assert "EXTERNAL_PROFILE_POINTS_TO_TEST" in codes
    assert "EXTERNAL_PROFILE_POINTS_TO_ORANGEBURG" in codes
    assert "EXTERNAL_PROTOCOL_ENDPOINT_IS_TEST_OR_ORANGEBURG" in codes


def test_external_smoke_missing_url_blocks_without_secret_leak(
    tmp_path: Path,
    capsys,
) -> None:
    config = tmp_path / "external.yaml"
    config.write_text(
        """
system: Rithmic Paper Trading
gateway: Chicago Area
protocol:
  url: ""
""",
        encoding="utf-8",
    )

    rc = smoke_main(
        [
            "--env",
            "external",
            "--config",
            str(config),
            "--symbol-root",
            "ES",
            "--exchange",
            "CME",
            "--duration",
            "0",
        ],
        environ={"RITHMIC_USERNAME": "unit_user", "RITHMIC_PASSWORD": "unit_password"},
    )

    captured = capsys.readouterr()
    assert rc == 2
    assert "EXTERNAL_PROTOCOL_URL_MISSING" in captured.err
    assert "unit_user" not in captured.err
    assert "unit_password" not in captured.err


def test_external_smoke_fake_client_writes_raw_jsonl_and_report_without_credentials(
    tmp_path: Path,
) -> None:
    settings = SmokeSettings(
        env="external",
        symbol_root="ES",
        exchange="CME",
        duration_sec=0,
        config_path=tmp_path / "external.yaml",
        raw_root=tmp_path / "raw",
        report_root=tmp_path / "reports",
        url="external-chicago.example.rithmic.com",
        system_name="Rithmic Paper Trading",
        gateway="Chicago Area",
        username="unit_user",
        password="unit_password",
    )

    report = asyncio.run(
        RithmicProtocolSmokeRunner(settings, client_factory=_FakeRithmicClient).run()
    )

    assert report["status"] == "pass"
    assert report["proof_of_life"] is True
    raw_path = Path(report["market_data"]["path"])
    report_path = Path(report["report_path"])
    assert raw_path.is_file()
    assert report_path.is_file()
    assert report["symbol"]["front_month"] == "ESM6"
    assert report["market_data"]["message_counts"] == {
        "BBO": 1,
        "LAST_TRADE": 1,
        "ORDER_BOOK": 1,
    }

    raw_text = raw_path.read_text(encoding="utf-8")
    report_text = report_path.read_text(encoding="utf-8")
    assert "unit_user" not in raw_text
    assert "unit_password" not in raw_text
    assert "unit_user" not in report_text
    assert "unit_password" not in report_text

    rows = [json.loads(line) for line in raw_text.splitlines()]
    assert {row["message_type"] for row in rows} == {"LAST_TRADE", "BBO", "ORDER_BOOK"}
    assert all(row["symbol"] == "ESM6" for row in rows)
    assert all(row["exchange"] == "CME" for row in rows)


def test_external_smoke_check_config_ready_does_not_print_credentials(
    tmp_path: Path,
    capsys,
) -> None:
    config = tmp_path / "external.yaml"
    config.write_text(
        """
system: Rithmic Paper Trading
gateway: Chicago Area
protocol:
  url: external-chicago.example.rithmic.com
""",
        encoding="utf-8",
    )

    rc = smoke_main(
        ["--env", "external", "--config", str(config), "--check-config"],
        environ={"RITHMIC_USERNAME": "unit_user", "RITHMIC_PASSWORD": "unit_password"},
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert "ready_to_connect" in captured.out
    assert "unit_user" not in captured.out
    assert "unit_password" not in captured.out


def test_external_smoke_real_client_blocks_off_chi404(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.setattr("connectors.rithmic.smoke_test.is_chi404_runtime", lambda: False)
    config = tmp_path / "external.yaml"
    config.write_text(
        """
system: Rithmic Paper Trading
gateway: Chicago Area
protocol:
  url: external-chicago.example.rithmic.com
""",
        encoding="utf-8",
    )

    rc = smoke_main(
        ["--env", "external", "--config", str(config), "--duration", "0"],
        environ={"RITHMIC_USERNAME": "unit_user", "RITHMIC_PASSWORD": "unit_password"},
    )

    captured = capsys.readouterr()
    assert rc == 2
    assert "CHI404_REQUIRED" in captured.err
    assert "unit_user" not in captured.err
    assert "unit_password" not in captured.err


class _FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    async def emit(self, payload) -> None:
        for handler in self.handlers:
            result = handler(payload)
            if inspect.isawaitable(result):
                await result


class _FakeRithmicClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.on_tick = _FakeEvent()
        self.on_order_book = _FakeEvent()
        self.on_market_depth = _FakeEvent()
        self.connected_plants = []
        self.disconnected = False

    async def connect(self, *, plants) -> None:
        self.connected_plants = list(plants)

    async def disconnect(self) -> None:
        self.disconnected = True

    async def list_exchanges(self):
        return [{"exchange": "CME"}, {"exchange": "CBOT"}]

    async def get_front_month_contract(self, symbol: str, exchange: str) -> str:
        assert symbol == "ES"
        assert exchange == "CME"
        return "ESM6"

    async def subscribe_to_market_data(self, symbol: str, exchange: str, data_type: int) -> None:
        assert symbol == "ESM6"
        assert exchange == "CME"
        assert data_type == 7
        await self.on_tick.emit(
            {
                "data_type": 1,
                "datetime": datetime.now(timezone.utc),
                "price": 5000.25,
                "size": 1,
            }
        )
        await self.on_tick.emit(
            {
                "data_type": 2,
                "datetime": datetime.now(timezone.utc),
                "bid_price": 5000.0,
                "ask_price": 5000.25,
            }
        )
        await self.on_order_book.emit(
            {
                "template_id": 156,
                "bid_price": 5000.0,
                "ask_price": 5000.25,
                "size": 3,
            }
        )
