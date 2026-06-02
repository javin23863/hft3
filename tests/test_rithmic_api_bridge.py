from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from data_system.rithmic_trial.connector._rithmic_api_bridge import (  # noqa: E402
    CConnectionConfig,
    CMarketDataEvent,
    COrderEvent,
    ConnectionConfig,
    MarketDataEvent,
    OrderEvent,
    RithmicApiError,
    locate_library,
)
from data_system.rithmic_trial.connector.rithmic_api_connector import (  # noqa: E402
    RithmicApiConnector,
    RithmicApiLibraryNotFoundError,
)


_LIB_PATH = locate_library()
SO_AVAILABLE = _LIB_PATH is not None
SO_SKIP_REASON = (
    "C++ R|API+ gateway not built; run cmake --build build on CHI404 or local"
)
so_not_available = not SO_AVAILABLE
so_not_available = not SO_AVAILABLE


def _has_bridge() -> bool:
    return SO_AVAILABLE


@pytest.mark.skipif(so_not_available, reason=SO_SKIP_REASON)
def test_bridge_module_loads_when_so_present() -> None:
    assert SO_AVAILABLE
    from data_system.rithmic_trial.connector import _rithmic_api_bridge as br

    assert hasattr(br, "RithmicApiBridge")
    assert hasattr(br, "RithmicApiError")
    assert br.RithmicApiError is RithmicApiError


def test_market_data_event_dataclass_fields() -> None:
    ev = MarketDataEvent(
        timestamp_ns=1234,
        order_id=42,
        action="T",
        side="B",
        price=5000.25,
        size=1,
    )
    assert ev.timestamp_ns == 1234
    assert ev.order_id == 42
    assert ev.action == "T"
    assert ev.side == "B"
    assert ev.price == 5000.25
    assert ev.size == 1
    d = ev.to_dict()
    assert d == {
        "timestamp_ns": 1234,
        "order_id": 42,
        "action": "T",
        "side": "B",
        "price": 5000.25,
        "size": 1,
    }


def test_market_data_event_from_c_roundtrip() -> None:
    c_ev = CMarketDataEvent()
    c_ev.timestamp_ns = 999
    c_ev.order_id = 7
    c_ev.action = b"T"
    c_ev.side = b"A"
    c_ev.price = 1.5
    c_ev.size = 3
    py = MarketDataEvent.from_c(c_ev)
    assert py.timestamp_ns == 999
    assert py.order_id == 7
    assert py.action == "T"
    assert py.side == "A"
    assert py.price == 1.5
    assert py.size == 3


def test_connection_config_to_c_retains_strings() -> None:
    cfg = ConnectionConfig(
        environment="Rithmic Test",
        username="alice",
        password="secret",
        app_name="HFT3",
        app_version="1.0",
        ssl_cert_path="/etc/ssl/r.pem",
        log_file_path="/var/log/r.log",
        md_connect_point="login_agent_tpc",
        ts_connect_point="login_agent_opc",
        rep_connect_point="login_agent_historyc",
        env_vars=["MML_DOMAIN_NAME=rithmic_uat", "MML_LOG_TYPE=log_net", "USER=alice"],
    )
    c = cfg.to_c()
    assert isinstance(c, CConnectionConfig)
    assert c.environment == b"Rithmic Test"
    assert c.username == b"alice"
    assert c.password == b"secret"
    assert c.app_name == b"HFT3"
    assert c.app_version == b"1.0"
    assert c.ssl_cert_path == b"/etc/ssl/r.pem"
    assert c.log_file_path == b"/var/log/r.log"
    assert c.md_connect_point == b"login_agent_tpc"
    assert c.ts_connect_point == b"login_agent_opc"
    assert c.rep_connect_point == b"login_agent_historyc"
    assert c.env_vars_count == 3
    assert c.env_vars[0] == b"MML_DOMAIN_NAME=rithmic_uat"
    assert c.env_vars[1] == b"MML_LOG_TYPE=log_net"
    assert c.env_vars[2] == b"USER=alice"
    assert c.env_vars[3] is None
    assert hasattr(c, "_refs")
    assert len(c._refs) == 10
    assert len(c._env_b_list) == 3


def test_connection_config_to_c_with_empty_env_vars() -> None:
    cfg = ConnectionConfig(environment="x", username="u", password="p")
    c = cfg.to_c()
    assert c.env_vars_count == 0
    assert c.env_vars[0] is None


@pytest.mark.skipif(so_not_available, reason=SO_SKIP_REASON)
def test_bridge_create_accepts_empty_config() -> None:
    from data_system.rithmic_trial.connector._rithmic_api_bridge import RithmicApiBridge

    bridge = RithmicApiBridge.load()
    cfg = ConnectionConfig()
    handle = bridge.create(cfg)._handle
    assert handle is not None
    bridge.destroy()


@pytest.mark.skipif(so_not_available, reason=SO_SKIP_REASON)
def test_bridge_methods_require_handle() -> None:
    from data_system.rithmic_trial.connector._rithmic_api_bridge import RithmicApiBridge

    bridge = RithmicApiBridge.load()
    with pytest.raises(RithmicApiError):
        bridge.subscribe_mbo("ES", "CME")


@pytest.mark.skipif(so_not_available, reason=SO_SKIP_REASON)
def test_bridge_send_order_rejects_multi_char_side() -> None:
    from data_system.rithmic_trial.connector._rithmic_api_bridge import RithmicApiBridge

    bridge = RithmicApiBridge.load()
    cfg = ConnectionConfig()
    bridge.create(cfg)
    with pytest.raises(ValueError, match="single character"):
        bridge.send_order("ES", "BUY", 1, 5000.0)
    bridge.destroy()


def test_connector_subscribe_mbo_requires_connected() -> None:
    cfg_path = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "data_system"
        / "config"
        / "rithmic_api_test.yaml"
    )
    connector = RithmicApiConnector(config_path=cfg_path)
    with pytest.raises(RuntimeError, match="Not connected"):
        connector.subscribe_mbo("ES", "CME")
    with pytest.raises(RuntimeError, match="Not connected"):
        connector.send_order("ES", "BUY", 1, 5000.0)
    with pytest.raises(RuntimeError, match="Not connected"):
        connector.cancel_order("FIX-1")


def test_connector_send_order_rejects_unknown_side() -> None:
    cfg_path = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "data_system"
        / "config"
        / "rithmic_api_test.yaml"
    )
    connector = RithmicApiConnector(config_path=cfg_path)
    connector._connected = True
    connector._bridge = object()  # bypass NotConnectedError; side validation fires first
    with pytest.raises(ValueError, match="unknown side"):
        connector.send_order("ES", "long", 1, 5000.0)
    with pytest.raises(ValueError, match="unknown side"):
        connector.send_order("ES", "x", 1, 5000.0)


def test_connector_libraries_missing_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_so = tmp_path / "does_not_exist.so"
    monkeypatch.setenv("HFT3_RITHMIC_GATEWAY_SO", str(fake_so))
    cfg_path = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "data_system"
        / "config"
        / "rithmic_api_test.yaml"
    )
    monkeypatch.setenv("RITHMIC_USERNAME", "u")
    monkeypatch.setenv("RITHMIC_PASSWORD", "p")
    connector = RithmicApiConnector(config_path=cfg_path)
    with pytest.raises(RithmicApiLibraryNotFoundError) as ei:
        connector.connect()
    msg = str(ei.value)
    assert "cmake" in msg.lower()
    assert "--build" in msg


def test_connector_poll_events_returns_empty_when_no_bridge() -> None:
    cfg_path = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "data_system"
        / "config"
        / "rithmic_api_test.yaml"
    )
    connector = RithmicApiConnector(config_path=cfg_path)
    assert connector.poll_events() == []
    types = connector.detected_event_types()
    assert "order_submit" in types
    assert "order_ack" in types
    assert "fill" in types
    lim = connector.limitations()
    assert lim["connector"] == "rithmic_api"
    connector.close()


def test_order_event_dataclass_fields() -> None:
    ev = OrderEvent(
        timestamp_ns=1234,
        order_id=42,
        event_type="A",
        side="B",
        order_type="L",
        price=5000.25,
        size=1,
        filled_size=0,
        total_filled=0,
        total_unfilled=1,
    )
    assert ev.timestamp_ns == 1234
    assert ev.order_id == 42
    assert ev.event_type == "A"
    assert ev.side == "B"
    assert ev.order_type == "L"
    assert ev.price == 5000.25
    assert ev.size == 1
    assert ev.filled_size == 0
    assert ev.total_filled == 0
    assert ev.total_unfilled == 1
    d = ev.to_dict()
    assert d == {
        "timestamp_ns": 1234,
        "order_id": 42,
        "event_type": "A",
        "side": "B",
        "order_type": "L",
        "price": 5000.25,
        "size": 1,
        "filled_size": 0,
        "total_filled": 0,
        "total_unfilled": 1,
    }


def test_order_event_from_c_roundtrip() -> None:
    c_ev = COrderEvent()
    c_ev.timestamp_ns = 9999
    c_ev.order_id = 7
    c_ev.event_type = b"F"
    c_ev.side = b"A"
    c_ev.order_type = b"L"
    c_ev.price = 5001.5
    c_ev.size = 2
    c_ev.filled_size = 2
    c_ev.total_filled = 2
    c_ev.total_unfilled = 0
    py = OrderEvent.from_c(c_ev)
    assert py.timestamp_ns == 9999
    assert py.order_id == 7
    assert py.event_type == "F"
    assert py.side == "A"
    assert py.order_type == "L"
    assert py.price == 5001.5
    assert py.size == 2
    assert py.filled_size == 2
    assert py.total_filled == 2
    assert py.total_unfilled == 0


def test_connector_poll_order_events_emits_empty_when_no_bridge() -> None:
    cfg_path = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "data_system"
        / "config"
        / "rithmic_api_test.yaml"
    )
    connector = RithmicApiConnector(config_path=cfg_path)
    assert connector.poll_order_events() == []
    connector.close()


def test_connector_send_order_queues_synthetic_submit() -> None:
    cfg_path = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "data_system"
        / "config"
        / "rithmic_api_test.yaml"
    )
    connector = RithmicApiConnector(config_path=cfg_path)
    connector._connected = True
    connector._bridge = _FakeBridge()

    connector.send_order("MES", "BUY", 1, 5000.0)
    connector.send_order("MES", "SELL", 2, 5001.0)

    events = connector.poll_order_events()
    assert len(events) == 2
    assert events[0]["event_type"] == "order_submit"
    assert events[0]["symbol"] == "MES"
    assert events[0]["side"] == "BUY"
    assert events[0]["qty"] == 1
    assert events[0]["price"] == 5000.0
    assert events[0]["order_id"] == "local-1"
    assert events[1]["order_id"] == "local-2"
    assert events[1]["side"] == "SELL"

    assert connector.poll_order_events() == []
    connector.close()


def test_connector_poll_order_events_adapts_bridge_events() -> None:
    cfg_path = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "data_system"
        / "config"
        / "rithmic_api_test.yaml"
    )
    connector = RithmicApiConnector(config_path=cfg_path)
    connector._connected = True
    bridge = _FakeBridge(
        OrderEvent(timestamp_ns=10, order_id=99, event_type="A", side="B", order_type="L"),
        OrderEvent(timestamp_ns=11, order_id=99, event_type="F", side="B", order_type="L",
                   price=5000.0, size=1, filled_size=1, total_filled=1, total_unfilled=0),
        OrderEvent(timestamp_ns=12, order_id=99, event_type="C", side="B", order_type="L"),
    )
    connector._bridge = bridge

    events = connector.poll_order_events()
    assert [e["event_type"] for e in events] == ["order_ack", "fill", "cancel"]
    assert [e["bridge_event_type"] for e in events] == ["A", "F", "C"]
    assert events[0]["order_id"] == "99"
    assert events[1]["price"] == 5000.0
    assert events[1]["filled_size"] == 1
    assert events[2]["order_id"] == "99"
    assert connector.poll_order_events() == []
    connector.close()


def test_connector_detected_event_types_includes_order_events() -> None:
    cfg_path = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "data_system"
        / "config"
        / "rithmic_api_test.yaml"
    )
    connector = RithmicApiConnector(config_path=cfg_path)
    types = connector.detected_event_types()
    assert "order_submit" in types
    assert "order_ack" in types
    assert "fill" in types
    assert "cancel" in types
    assert "order_replace" in types
    assert "reject" in types
    assert "order_failure" in types


def test_connector_repository_connect_point_from_repository_login_block() -> None:
    """Regression: repo connect point must come from repository_login.sCnnctPt,
    NOT from login_params.sPnlCnnctPt (the previous bug sent login_agent_pnlc
    which Rithmic rejected with 'Repository Connection Broken').
    """
    cfg_path = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "data_system"
        / "config"
        / "rithmic_api_test.yaml"
    )
    connector = RithmicApiConnector(config_path=cfg_path)
    cfg = connector._build_connection_config()
    assert cfg.rep_connect_point == "login_agent_repositoryc"
    assert cfg.rep_connect_point != "login_agent_pnlc"
    assert cfg.md_connect_point == "login_agent_tpc"
    assert cfg.ts_connect_point == "login_agent_opc"


class _FakeBridge:
    def __init__(self, *queued_order_events: OrderEvent) -> None:
        self._order_events = list(queued_order_events)

    def try_pop_order_event(self) -> OrderEvent | None:
        if not self._order_events:
            return None
        return self._order_events.pop(0)

    def try_pop_event(self):
        return None

    def send_order(self, *args, **kwargs) -> None:
        return None

    def cancel_order(self, *args, **kwargs) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def destroy(self) -> None:
        return None
