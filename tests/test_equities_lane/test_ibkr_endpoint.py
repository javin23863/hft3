from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from equities_lane.src import ibkr_endpoint
from equities_lane.src.ibkr_endpoint import endpoint_status, resolve_endpoint


REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "packages" / "equities_lane" / "config" / "ibkr_endpoint.yaml"


def _enable_broker_socket_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROKER_SOCKET_ENABLED", "1")
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "ibkr-socket")
    monkeypatch.setenv("IBKR_SOCKET_HOST", "127.0.0.1")
    monkeypatch.setenv("IBKR_SOCKET_PORT", "7497")
    monkeypatch.setenv("IBKR_SOCKET_CLIENT_ID_PRIMARY", "17")
    monkeypatch.setenv("IBKR_SOCKET_CLIENT_ID_MARKETDATA", "18")
    monkeypatch.setenv("IBKR_ACCOUNT_ID_PRIMARY", "DU123456")


def test_resolve_endpoint_uses_external_broker_socket_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_broker_socket_env(monkeypatch)

    endpoint = resolve_endpoint(REPO, CONFIG)

    assert endpoint.profile == "ibkr_broker_socket"
    assert endpoint.transport == "tws_socket"
    assert endpoint.mode == "external"
    assert endpoint.port == 7497
    assert endpoint.client_id == 17
    assert endpoint.market_data_client_id == 18
    assert endpoint.account_present is True


def test_socket_mode_env_does_not_change_external_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_broker_socket_env(monkeypatch)
    monkeypatch.setenv("IBKR_SOCKET_MODE", "ignored")

    endpoint = resolve_endpoint(REPO, CONFIG)

    assert endpoint.mode == "external"
    assert endpoint.port == 7497
    assert endpoint.candidate_ports == [7497, 7496, 4002, 4001]


def test_endpoint_status_redacts_account_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _enable_broker_socket_env(monkeypatch)

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        socket_probe=lambda _host, _port, _timeout: True,
        write_status=False,
    )
    serialized = json.dumps(status)

    assert status["socket"]["reachable"] is True
    assert status["credentials"]["account_id_set"] is True
    assert status["credentials"]["redacted"] is True
    assert status["secret_exposed"] is False
    assert "DU123456" not in serialized
    assert "account_id" not in status


def test_endpoint_status_hydrates_quantx_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("IBKR_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("IBKR_ACCOUNT_ID_PRIMARY", raising=False)
    monkeypatch.delenv("BROKER_SOCKET_ENABLED", raising=False)
    monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
    env_file = tmp_path / "keys.env"
    env_file.write_text(
        "\n".join(
            [
                "IBKR_ACCOUNT_ID_PRIMARY=DU999999",
                "IBKR_SOCKET_PORT=7497",
                "BROKER_SOCKET_ENABLED=1",
                "MARKET_DATA_PROVIDER=ibkr-socket",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("IBKR_ENV_FILE", str(env_file))

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        socket_probe=lambda _host, _port, _timeout: True,
        write_status=False,
    )
    serialized = json.dumps(status)

    assert status["status"] == "READY_TO_CONNECT"
    assert status["credentials"]["account_id_set"] is True
    assert str(env_file) in status["env_hydration"]["loaded_paths"]
    assert status["blocking_gates"] == []
    assert status["routing_gates"] == []
    assert "DU999999" not in serialized


def test_endpoint_status_blocks_when_socket_is_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _enable_broker_socket_env(monkeypatch)

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        socket_probe=lambda _host, _port, _timeout: False,
        write_status=False,
    )

    assert status["status"] == "BLOCKING"
    assert status["socket"]["reachable"] is False
    assert any(gate["gate"] == "ibkr_socket" for gate in status["blocking_gates"])


def test_missing_account_is_routing_warning_not_endpoint_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("IBKR_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("IBKR_ACCOUNT_ID_PRIMARY", raising=False)
    monkeypatch.delenv("IBKR_ENV_FILE", raising=False)
    monkeypatch.setenv("BROKER_SOCKET_ENABLED", "1")
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "ibkr-socket")
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _repo, _config: [])

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        socket_probe=lambda _host, _port, _timeout: True,
        write_status=False,
    )

    assert status["status"] == "READY_TO_CONNECT"
    assert not any(gate["gate"] == "ibkr_account" for gate in status["blocking_gates"])
    assert any(gate["gate"] == "ibkr_account" for gate in status["routing_gates"])


def test_connect_mode_reports_missing_ibapi_without_faking_handshake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if importlib.util.find_spec("ibapi") is not None:
        pytest.skip("ibapi is installed; real handshake coverage belongs to endpoint integration runs")
    _enable_broker_socket_env(monkeypatch)

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        connect=True,
        socket_probe=lambda _host, _port, _timeout: True,
        write_status=False,
    )

    assert status["status"] == "BLOCKING"
    assert status["api"]["api_client_status"] == "IBAPI_PACKAGE_MISSING"
    assert any(gate["gate"] == "ibkr_api_package" for gate in status["blocking_gates"])


def test_connect_mode_blocks_on_broker_disclaimer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_broker_socket_env(monkeypatch)
    monkeypatch.setattr(ibkr_endpoint, "_ibapi_available", lambda: True)
    monkeypatch.setattr(
        ibkr_endpoint,
        "_headless_ibapi_handshake",
        lambda _endpoint, _timeout: {
            "api_client_status": "BROKER_DISCLAIMER_PENDING",
            "api_package_present": True,
            "connected": False,
            "errors": [
                {
                    "code": 10141,
                    "message": "Broker disclaimer must first be accepted for API connection.",
                }
            ],
        },
    )

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        connect=True,
        socket_probe=lambda _host, _port, _timeout: True,
        write_status=False,
    )

    assert status["status"] == "BLOCKING"
    assert status["headless_handshake_required"] is True
    assert status["api"]["api_client_status"] == "BROKER_DISCLAIMER_PENDING"
    assert any(
        gate["gate"] == "ibkr_broker_disclaimer" and gate["vendor_error_code"] == 10141
        for gate in status["blocking_gates"]
    )
