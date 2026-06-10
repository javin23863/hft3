"""Tests for IbkrWebClient, GatewayAuth, OAuthAuth.

No real network — all HTTP calls use injected mock sessions or transports.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from equities_lane.src.execution.ibkr_web_client import (
    GatewayAuth,
    IbkrWebClient,
    LiveAccountRefusal,
    OAuthAuth,
    _hmac_sha256_sign,
    _oauth_authorization_header,
    _rsa_sha256_sign,
)


PAPER_ACCOUNT = "DU123456"
LIVE_ACCOUNT = "U9999999"  # not the paper account


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data: Any, status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._data


class _MockSession:
    """Minimal mock session that stores calls for assertion."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[tuple[str, str]] = []  # (method, url)

    def _resolve(self, method: str, url: str) -> _FakeResponse:
        self.calls.append((method, url))
        for key, val in self._responses.items():
            if key in url:
                return _FakeResponse(val)
        return _FakeResponse({})

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._resolve("GET", url)

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._resolve("POST", url)

    def delete(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._resolve("DELETE", url)


def _client_with_session(
    session_responses: dict[str, Any] | None = None,
    base_url: str = "https://localhost:5000/v1/api",
    paper_account: str = PAPER_ACCOUNT,
) -> tuple[IbkrWebClient, _MockSession]:
    sess = _MockSession(session_responses)
    auth = GatewayAuth(base_url=base_url, session=sess)
    client = IbkrWebClient(auth=auth, paper_account_id=paper_account)
    return client, sess


# ---------------------------------------------------------------------------
# Live-account guard
# ---------------------------------------------------------------------------


def test_live_account_guard_raises_on_non_paper_account() -> None:
    client, _ = _client_with_session(
        {"/iserver/accounts": {"accounts": [PAPER_ACCOUNT]}},
    )
    with pytest.raises(LiveAccountRefusal):
        client.place_order(LIVE_ACCOUNT, {"orderType": "LMT"})


def test_live_account_guard_raises_when_account_not_in_api_response() -> None:
    # Even if the account id matches paper, if it's absent from /iserver/accounts → refuse.
    client, _ = _client_with_session(
        {"/iserver/accounts": {"accounts": []}},  # empty accounts list
    )
    with pytest.raises(LiveAccountRefusal):
        client.place_order(PAPER_ACCOUNT, {"orderType": "LMT"})


def test_live_account_guard_passes_for_paper_account() -> None:
    client, sess = _client_with_session(
        {
            "/iserver/accounts": {"accounts": [PAPER_ACCOUNT]},
            "/iserver/account/DU123456/orders": {"order_id": "ord-001", "status": "PreSubmitted"},
        }
    )
    result = client.place_order(PAPER_ACCOUNT, {"orderType": "LMT", "side": "BUY"})
    assert result.get("order_id") == "ord-001"
    # Ensure the order URL was actually called.
    methods_urls = [(m, u) for m, u in sess.calls]
    assert any("orders" in u and m == "POST" for m, u in methods_urls)


def test_live_account_guard_raises_when_paper_account_not_configured() -> None:
    sess = _MockSession({})
    auth = GatewayAuth(session=sess)
    client = IbkrWebClient(auth=auth, paper_account_id="")  # blank
    with pytest.raises(LiveAccountRefusal, match="IBKR_ACCOUNT_ID_PAPER is not set"):
        client.place_order(PAPER_ACCOUNT, {})


# ---------------------------------------------------------------------------
# Order placement — correct URL shape
# ---------------------------------------------------------------------------


def test_place_order_hits_correct_url() -> None:
    client, sess = _client_with_session(
        {
            "/iserver/accounts": {"accounts": [PAPER_ACCOUNT]},
            f"/iserver/account/{PAPER_ACCOUNT}/orders": {"order_id": "ord-777"},
        }
    )
    client.place_order(PAPER_ACCOUNT, {"orderType": "MKT"})

    post_calls = [u for m, u in sess.calls if m == "POST"]
    assert any(f"/iserver/account/{PAPER_ACCOUNT}/orders" in u for u in post_calls)


def test_cancel_order_hits_correct_url() -> None:
    order_id = "42"
    client, sess = _client_with_session(
        {
            "/iserver/accounts": {"accounts": [PAPER_ACCOUNT]},
            f"/iserver/account/{PAPER_ACCOUNT}/order/{order_id}": {"status": "Cancelled"},
        }
    )
    client.cancel_order(PAPER_ACCOUNT, order_id)

    delete_calls = [u for m, u in sess.calls if m == "DELETE"]
    assert any(f"/order/{order_id}" in u for u in delete_calls)


def test_order_status_hits_correct_url() -> None:
    order_id = "99"
    client, sess = _client_with_session(
        {f"/iserver/account/orders/{order_id}": {"status": "Filled"}},
    )
    result = client.order_status(order_id)

    get_calls = [u for m, u in sess.calls if m == "GET"]
    assert any(f"/iserver/account/orders/{order_id}" in u for u in get_calls)
    assert result.get("status") == "Filled"


# ---------------------------------------------------------------------------
# Gateway auth — TLS verify flag
# ---------------------------------------------------------------------------


def test_gateway_auth_verify_false_for_localhost() -> None:
    auth = GatewayAuth(base_url="https://localhost:5000/v1/api")
    assert auth._verify is False


def test_gateway_auth_verify_true_for_remote_host() -> None:
    auth = GatewayAuth(base_url="https://example.ibkr.com/v1/api")
    assert auth._verify is True


def test_gateway_auth_verify_false_for_127_0_0_1() -> None:
    auth = GatewayAuth(base_url="https://127.0.0.1:5000/v1/api")
    assert auth._verify is False


# ---------------------------------------------------------------------------
# Marketdata snapshot — request shape
# ---------------------------------------------------------------------------


def test_marketdata_snapshot_request_shape() -> None:
    conids = [265598, 8314]
    fields = [31, 84, 86]
    client, sess = _client_with_session(
        {"/iserver/marketdata/snapshot": [{"conid": 265598, "31": "150.25"}]},
    )
    result = client.marketdata_snapshot(conids, fields)

    assert isinstance(result, list)
    get_calls = [u for m, u in sess.calls if m == "GET"]
    assert len(get_calls) == 1
    url = get_calls[0]
    assert "conids=265598,8314" in url
    assert "fields=31,84,86" in url


def test_marketdata_snapshot_no_fields() -> None:
    client, sess = _client_with_session(
        {"/iserver/marketdata/snapshot": []},
    )
    client.marketdata_snapshot([12345])

    get_calls = [u for m, u in sess.calls if m == "GET"]
    assert "conids=12345" in get_calls[0]
    assert "fields=" not in get_calls[0]


# ---------------------------------------------------------------------------
# WebSocket builders
# ---------------------------------------------------------------------------


def test_ws_url_https_to_wss() -> None:
    client, _ = _client_with_session(base_url="https://localhost:5000/v1/api")
    assert client.ws_url().startswith("wss://")
    assert client.ws_url().endswith("/ws")


def test_subscribe_fills_message_structure() -> None:
    msg = json.loads(IbkrWebClient.subscribe_fills_message(PAPER_ACCOUNT))
    assert msg["topic"] == "sor"
    assert msg["subscribe"] is True
    assert msg["account"] == PAPER_ACCOUNT


def test_subscribe_orders_message_structure() -> None:
    msg = json.loads(IbkrWebClient.subscribe_orders_message(PAPER_ACCOUNT))
    assert msg["topic"] == "or"
    assert msg["subscribe"] is True
    assert msg["account"] == PAPER_ACCOUNT


# ---------------------------------------------------------------------------
# OAuthAuth — signing path (mock / stub assertion)
# ---------------------------------------------------------------------------


def test_require_cryptography_raises_not_implemented_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """_require_cryptography raises NotImplementedError when cryptography is absent."""
    import importlib.util
    from equities_lane.src.execution import ibkr_web_client as _m

    # Patch find_spec to simulate cryptography being absent.
    original = importlib.util.find_spec

    def _fake_find_spec(name: str, *args: object, **kwargs: object) -> object:
        if name == "cryptography":
            return None
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec)

    # Patch builtins.__import__ so that `import cryptography` raises ImportError.
    import builtins
    original_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "cryptography":
            raise ImportError("simulated absent cryptography")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises((NotImplementedError, ImportError)):
        _m._require_cryptography()


def test_hmac_sha256_sign_deterministic() -> None:
    """_hmac_sha256_sign is deterministic given the same inputs."""
    lst_hex = "deadbeef" * 8  # 32 bytes
    msg = "GET&https://api.ibkr.com/v1/api/iserver/auth/status&1700000000&nonce123"
    sig1 = _hmac_sha256_sign(lst_hex, msg)
    sig2 = _hmac_sha256_sign(lst_hex, msg)
    assert sig1 == sig2
    assert len(sig1) > 0


def test_oauth_authorization_header_contains_required_fields() -> None:
    lst_hex = "cafebabe" * 8
    header = _oauth_authorization_header(
        consumer_key="testconsumer",
        access_token="testtoken",
        lst_hex=lst_hex,
        method="GET",
        url="https://api.ibkr.com/v1/api/iserver/accounts",
    )
    assert 'oauth_consumer_key="testconsumer"' in header
    assert 'oauth_token="testtoken"' in header
    assert 'oauth_signature_method="HMAC-SHA256"' in header
    assert "oauth_signature=" in header
    assert "oauth_timestamp=" in header
    assert "oauth_nonce=" in header


def test_rsa_sha256_sign_requires_cryptography() -> None:
    """_rsa_sha256_sign should be callable when cryptography is installed."""
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.backends import default_backend

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        sig = _rsa_sha256_sign(private_key, b"test message")
        assert isinstance(sig, str)
        assert len(sig) > 0
    except ImportError:
        pytest.skip("cryptography not installed; RSA sign path not tested")


# ---------------------------------------------------------------------------
# Session probes — auth_status / tickle / accounts
# ---------------------------------------------------------------------------


def test_auth_status_returns_api_response() -> None:
    client, _ = _client_with_session(
        {"/iserver/auth/status": {"authenticated": True, "connected": True}}
    )
    result = client.auth_status()
    assert result.get("authenticated") is True


def test_tickle_calls_post() -> None:
    client, sess = _client_with_session({"/tickle": {"status": "ok"}})
    client.tickle()
    post_calls = [u for m, u in sess.calls if m == "POST"]
    assert any("/tickle" in u for u in post_calls)


def test_accounts_parses_list_response() -> None:
    client, _ = _client_with_session(
        {"/iserver/accounts": {"accounts": [PAPER_ACCOUNT, "DU654321"]}}
    )
    result = client.accounts()
    assert PAPER_ACCOUNT in result
    assert len(result) == 2
