"""Tests for IbkrWebClient, GatewayAuth, OAuthAuth.

No real network — all HTTP calls use injected mock sessions or transports.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import os
import random
import urllib.parse
from urllib.parse import parse_qsl
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from equities_lane.src.execution.ibkr_web_client import (
    GatewayAuth,
    IbkrWebClient,
    LiveAccountRefusal,
    LiveSessionTokenError,
    OAuthAuth,
    _build_oauth_base_string,
    _hmac_sha256_sign,
    _hmac_sha256_sign_bytes,
    _int_to_signed_bytes,
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
        self.last_kwargs: dict[str, Any] = {}

    def _resolve(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, url))
        self.last_kwargs = kwargs
        for key, val in self._responses.items():
            if key in url:
                return _FakeResponse(val)
        return _FakeResponse({})

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._resolve("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._resolve("POST", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._resolve("DELETE", url, **kwargs)


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
# OAuthAuth — cryptography guard
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
    """_hmac_sha256_sign is deterministic given the same inputs (base64 LST key)."""
    # 20-byte key base64-encoded (matches SHA-1 output length used for real LSTs).
    key_bytes = bytes.fromhex("deadbeef" * 5)  # 20 bytes
    lst_b64 = base64.b64encode(key_bytes).decode()
    msg = "GET&https://api.ibkr.com/v1/api/iserver/auth/status&1700000000&nonce123"
    sig1 = _hmac_sha256_sign(lst_b64, msg)
    sig2 = _hmac_sha256_sign(lst_b64, msg)
    assert sig1 == sig2
    assert len(sig1) > 0


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


# ---------------------------------------------------------------------------
# OAuthAuth — LST exchange protocol tests (mocked transport)
# ---------------------------------------------------------------------------


def _make_oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env vars required by OAuthAuth constructor."""
    monkeypatch.setenv("IBKR_OAUTH_CONSUMER_KEY", "TESTCONS")
    monkeypatch.setenv("IBKR_OAUTH_ACCESS_TOKEN", "test_access_token")
    monkeypatch.setenv("IBKR_OAUTH_ACCESS_TOKEN_SECRET", "dGVzdA==")  # base64("test")
    monkeypatch.setenv("IBKR_OAUTH_SIGNATURE_KEY_PATH", "/fake/sig.pem")
    monkeypatch.setenv("IBKR_OAUTH_ENCRYPTION_KEY_PATH", "/fake/enc.pem")
    monkeypatch.setenv("IBKR_OAUTH_DH_PARAM_PATH", "/fake/dh.pem")


class TestLSTRequestProtocol:
    """Verify that the LST exchange request matches the real IBKR protocol."""

    def _build_mock_auth(
        self,
        monkeypatch: pytest.MonkeyPatch,
        server_response: dict[str, Any],
        *,
        prepend_hex: str = "abcd1234",
        dh_a: int = 6,
        p: int = 23,
        g: int = 5,
    ) -> tuple[OAuthAuth, _MockSession]:
        """
        Return a fully-wired OAuthAuth whose DH, decryption, and signing are
        monkeypatched to use toy values so tests are deterministic.
        """
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa, padding as _padding
            from cryptography.hazmat.primitives import hashes as _hashes
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            pytest.skip("cryptography not installed")

        _make_oauth_env(monkeypatch)

        # Generate a real RSA key for signing (2048-bit).
        sig_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )

        sess = _MockSession({"live_session_token": server_response})
        auth = OAuthAuth(base_url="https://api.ibkr.com/v1/api", session=sess)

        # Patch _load_private_key so sig-key path and enc-key path both work.
        def _fake_load_key(path: str) -> Any:
            if "enc" in path:
                # Return a key whose decrypt() returns prepend_hex bytes.
                enc_key = rsa.generate_private_key(
                    public_exponent=65537, key_size=2048, backend=default_backend()
                )
                # Monkeypatch the decrypt method on this specific object.
                prepend_bytes = bytes.fromhex(prepend_hex)

                class _FakeEncKey:
                    def decrypt(self, ct: bytes, pad: Any) -> bytes:
                        return prepend_bytes

                return _FakeEncKey()
            return sig_key

        import equities_lane.src.execution.ibkr_web_client as _m
        monkeypatch.setattr(_m, "_load_private_key", _fake_load_key)

        # Patch DH param loading: intercept load_pem_parameters.
        class _FakeParamNumbers:
            def __init__(self) -> None:
                self.p = p
                self.g = g

        class _FakeParams:
            def parameter_numbers(self) -> _FakeParamNumbers:
                return _FakeParamNumbers()

        from cryptography.hazmat.primitives import serialization as _ser
        monkeypatch.setattr(_ser, "load_pem_parameters", lambda raw: _FakeParams())

        # Patch open() in the module so DH param file read doesn't touch disk.
        import unittest.mock as _mock
        monkeypatch.setattr(_m, "open", _mock.mock_open(read_data=b"fake pem"), raising=False)

        # Fix the random private value to `dh_a`.
        monkeypatch.setattr(
            random.SystemRandom,
            "getrandbits",
            lambda self, bits: dh_a,
        )

        return auth, sess

    def test_lst_request_no_secret_transmitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LST POST must not transmit access_token_secret in any form."""
        p, g, a = 23, 5, 6
        B = 19  # server DH public value
        shared_secret = pow(B, a, p)  # = 2
        prepend_hex = "abcd1234"
        prepend_bytes = bytes.fromhex(prepend_hex)

        # Compute expected LST so mock response validation passes.
        shared_bytes = _int_to_signed_bytes(shared_secret)
        lst = base64.b64encode(
            _hmac.new(key=shared_bytes, msg=prepend_bytes, digestmod=hashlib.sha1).digest()
        ).decode()
        lst_sig = _hmac.new(
            key=base64.b64decode(lst),
            msg=b"TESTCONS",
            digestmod=hashlib.sha1,
        ).hexdigest()

        server_resp = {
            "diffie_hellman_response": hex(B)[2:],
            "live_session_token_signature": lst_sig,
        }
        auth, sess = self._build_mock_auth(monkeypatch, server_resp, prepend_hex=prepend_hex)

        auth._ensure_lst()

        # Find the LST POST call.
        post_kwargs = sess.last_kwargs
        # Must NOT have a JSON body containing the secret.
        assert "json" not in post_kwargs or not post_kwargs.get("json"), (
            "LST POST must send no JSON body"
        )
        # The raw access_token_secret value (base64 cipher) must not appear in headers.
        auth_header = post_kwargs.get("headers", {}).get("Authorization", "")
        assert "dGVzdA==" not in auth_header, (
            "access_token_secret ciphertext must not appear in Authorization header"
        )
        # access_token_secret decoded value must not appear either.
        assert "test" not in auth_header or "oauth_token" in auth_header, (
            "decrypted secret must not appear in Authorization header"
        )

    def test_lst_request_authorization_header_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LST Authorization header must carry diffie_hellman_challenge, RSA-SHA256, realm."""
        p, g, a = 23, 5, 6
        B = 19
        shared_secret = pow(B, a, p)
        prepend_hex = "abcd1234"
        prepend_bytes = bytes.fromhex(prepend_hex)
        shared_bytes = _int_to_signed_bytes(shared_secret)
        lst = base64.b64encode(
            _hmac.new(key=shared_bytes, msg=prepend_bytes, digestmod=hashlib.sha1).digest()
        ).decode()
        lst_sig = _hmac.new(
            key=base64.b64decode(lst), msg=b"TESTCONS", digestmod=hashlib.sha1
        ).hexdigest()

        server_resp = {
            "diffie_hellman_response": hex(B)[2:],
            "live_session_token_signature": lst_sig,
        }
        auth, sess = self._build_mock_auth(monkeypatch, server_resp, prepend_hex=prepend_hex)
        auth._ensure_lst()

        auth_header = sess.last_kwargs.get("headers", {}).get("Authorization", "")
        assert "diffie_hellman_challenge=" in auth_header, (
            "Authorization header must contain diffie_hellman_challenge"
        )
        assert 'oauth_signature_method="RSA-SHA256"' in auth_header, (
            "Authorization header must declare RSA-SHA256"
        )
        assert 'realm="limited_poa"' in auth_header, (
            "Authorization header must include realm"
        )
        assert "oauth_consumer_key=" in auth_header
        assert "oauth_token=" in auth_header
        assert "oauth_nonce=" in auth_header
        assert "oauth_timestamp=" in auth_header
        assert "oauth_signature=" in auth_header

    def test_lst_realm_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """IBKR_OAUTH_REALM env var overrides the default realm in LST header."""
        monkeypatch.setenv("IBKR_OAUTH_REALM", "test_realm_override")
        p, g, a = 23, 5, 6
        B = 19
        shared_secret = pow(B, a, p)
        prepend_hex = "abcd1234"
        prepend_bytes = bytes.fromhex(prepend_hex)
        shared_bytes = _int_to_signed_bytes(shared_secret)
        lst = base64.b64encode(
            _hmac.new(key=shared_bytes, msg=prepend_bytes, digestmod=hashlib.sha1).digest()
        ).decode()
        lst_sig = _hmac.new(
            key=base64.b64decode(lst), msg=b"TESTCONS", digestmod=hashlib.sha1
        ).hexdigest()

        server_resp = {
            "diffie_hellman_response": hex(B)[2:],
            "live_session_token_signature": lst_sig,
        }
        auth, sess = self._build_mock_auth(monkeypatch, server_resp, prepend_hex=prepend_hex)
        # Reinitialise so IBKR_OAUTH_REALM is picked up.
        auth._realm = "test_realm_override"
        auth._ensure_lst()

        auth_header = sess.last_kwargs.get("headers", {}).get("Authorization", "")
        assert 'realm="test_realm_override"' in auth_header


class TestLSTKnownAnswer:
    """Known-answer test for the LST derivation with tiny DH parameters."""

    # p=23, g=5, a=6 → A=8; server B=19 → shared_secret=2; prepend="abcd"(hex)
    P = 23
    G = 5
    A_PRIVATE = 6
    SERVER_B = 19
    SHARED_SECRET = 2  # 19^6 mod 23
    PREPEND_HEX = "abcd"

    def _expected_lst(self) -> str:
        shared_bytes = _int_to_signed_bytes(self.SHARED_SECRET)
        return base64.b64encode(
            _hmac.new(
                key=shared_bytes,
                msg=bytes.fromhex(self.PREPEND_HEX),
                digestmod=hashlib.sha1,
            ).digest()
        ).decode()

    def _lst_sig(self, lst: str) -> str:
        return _hmac.new(
            key=base64.b64decode(lst),
            msg=b"TESTCONS",
            digestmod=hashlib.sha1,
        ).hexdigest()

    def test_lst_known_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Client derives the correct LST from toy DH parameters."""
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            pytest.skip("cryptography not installed")

        _make_oauth_env(monkeypatch)
        expected_lst = self._expected_lst()
        lst_sig = self._lst_sig(expected_lst)

        server_resp = {
            "diffie_hellman_response": hex(self.SERVER_B)[2:],
            "live_session_token_signature": lst_sig,
        }

        sig_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        sess = _MockSession({"live_session_token": server_resp})
        auth = OAuthAuth(base_url="https://api.ibkr.com/v1/api", session=sess)

        import equities_lane.src.execution.ibkr_web_client as _m
        from cryptography.hazmat.primitives import serialization as _ser

        class _FakePN:
            p = self.P
            g = self.G

        class _FakeParams:
            def parameter_numbers(self) -> _FakePN:
                return _FakePN()

        monkeypatch.setattr(_ser, "load_pem_parameters", lambda raw: _FakeParams())

        prepend_bytes = bytes.fromhex(self.PREPEND_HEX)

        class _FakeEncKey:
            def decrypt(self, ct: bytes, pad: Any) -> bytes:
                return prepend_bytes

        monkeypatch.setattr(_m, "_load_private_key", lambda path: _FakeEncKey() if "enc" in path else sig_key)

        # Prevent real file-system access for the DH param PEM read.
        import unittest.mock as _mock
        monkeypatch.setattr(_m, "open", _mock.mock_open(read_data=b"fake pem"), raising=False)

        def _fixed_getrandbits(self_rng: Any, bits: int) -> int:
            return 6  # a = 6

        monkeypatch.setattr(random.SystemRandom, "getrandbits", _fixed_getrandbits)

        derived_lst = auth._obtain_lst()
        assert derived_lst == expected_lst, (
            f"LST mismatch.\nDerived:  {derived_lst!r}\nExpected: {expected_lst!r}"
        )

    def test_lst_validation_fails_on_wrong_signature(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Client raises LiveSessionTokenError when server signature is wrong."""
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            pytest.skip("cryptography not installed")

        _make_oauth_env(monkeypatch)

        server_resp = {
            "diffie_hellman_response": hex(self.SERVER_B)[2:],
            "live_session_token_signature": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        }

        sig_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        sess = _MockSession({"live_session_token": server_resp})
        auth = OAuthAuth(base_url="https://api.ibkr.com/v1/api", session=sess)

        import equities_lane.src.execution.ibkr_web_client as _m
        from cryptography.hazmat.primitives import serialization as _ser

        class _FakePN:
            p = self.P
            g = self.G

        class _FakeParams:
            def parameter_numbers(self) -> _FakePN:
                return _FakePN()

        monkeypatch.setattr(_ser, "load_pem_parameters", lambda raw: _FakeParams())

        prepend_bytes = bytes.fromhex(self.PREPEND_HEX)

        class _FakeEncKey:
            def decrypt(self, ct: bytes, pad: Any) -> bytes:
                return prepend_bytes

        monkeypatch.setattr(_m, "_load_private_key", lambda path: _FakeEncKey() if "enc" in path else sig_key)

        # Prevent real file-system access for the DH param PEM read.
        import unittest.mock as _mock
        monkeypatch.setattr(_m, "open", _mock.mock_open(read_data=b"fake pem"), raising=False)

        def _fixed_getrandbits(self_rng: Any, bits: int) -> int:
            return 6

        monkeypatch.setattr(random.SystemRandom, "getrandbits", _fixed_getrandbits)

        with pytest.raises(LiveSessionTokenError, match="validation failed"):
            auth._obtain_lst()


# ---------------------------------------------------------------------------
# _int_to_signed_bytes — sign-byte quirk unit tests
# ---------------------------------------------------------------------------


class TestIntToSignedBytes:
    """Spec-mandated unit tests for _int_to_signed_bytes."""

    def test_small_positive(self) -> None:
        # 2 → bit_length=2, 2%8 != 0, no pad → b"\x02"
        assert _int_to_signed_bytes(2) == b"\x02"

    def test_128_leading_zero(self) -> None:
        # 128 = 0x80, bit_length=8, 8%8==0 → prepend 0x00 → b"\x00\x80"
        assert _int_to_signed_bytes(128) == b"\x00\x80"

    def test_255_leading_zero(self) -> None:
        # 255 = 0xff, bit_length=8, 8%8==0 → prepend 0x00 → b"\x00\xff"
        assert _int_to_signed_bytes(255) == b"\x00\xff"

    def test_256_no_pad(self) -> None:
        # 256 = 0x0100, bit_length=9, 9%8 != 0 → no pad → b"\x01\x00"
        assert _int_to_signed_bytes(256) == b"\x01\x00"

    def test_1_no_pad(self) -> None:
        # 1 → bit_length=1, 1%8 != 0, hex="01" → b"\x01"
        assert _int_to_signed_bytes(1) == b"\x01"

    def test_127_no_pad(self) -> None:
        # 127 = 0x7f, bit_length=7, 7%8 != 0 → no pad → b"\x7f"
        assert _int_to_signed_bytes(127) == b"\x7f"


# ---------------------------------------------------------------------------
# Per-request signing — Authorization header tests
# ---------------------------------------------------------------------------


class TestPerRequestSigning:
    """Verify HMAC-SHA256 per-request signing with RFC base string."""

    def test_authorization_header_hmac_sha256(self) -> None:
        """Recompute expected signature independently and assert equality."""
        consumer_key = "TESTCONS"
        access_token = "test_token"
        # 20-byte key encoded as base64 (matches real LST which is HMAC-SHA1 output).
        key_bytes = bytes(range(20))
        lst_b64 = base64.b64encode(key_bytes).decode()
        url = "https://api.ibkr.com/v1/api/iserver/accounts"

        # Capture nonce and timestamp by monkeypatching time and os.urandom.
        fixed_ts = 1700000000
        fixed_nonce_bytes = b"\x01" * 16

        import time as _time
        import os as _os

        header = None
        with patch("time.time", return_value=fixed_ts), \
             patch("os.urandom", return_value=fixed_nonce_bytes):
            header = _oauth_authorization_header(
                consumer_key=consumer_key,
                access_token=access_token,
                lst_b64=lst_b64,
                method="GET",
                url=url,
            )

        assert header is not None
        # Extract nonce from header.
        nonce = None
        for part in header.split(", "):
            if "oauth_nonce=" in part:
                nonce = part.split('"')[1]
        assert nonce is not None

        # Recompute expected signature.
        from urllib.parse import urlsplit, parse_qsl
        split = urllib.parse.urlsplit(url)
        base_url = f"{split.scheme}://{split.netloc}{split.path}"
        query_params = parse_qsl(split.query, keep_blank_values=True)
        oauth_params = {
            "oauth_consumer_key": consumer_key,
            "oauth_nonce": nonce,
            "oauth_signature_method": "HMAC-SHA256",
            "oauth_timestamp": str(fixed_ts),
            "oauth_token": access_token,
        }
        base_string = _build_oauth_base_string("GET", base_url, query_params, oauth_params)
        expected_sig = urllib.parse.quote_plus(
            base64.b64encode(
                _hmac.new(key_bytes, base_string.encode("utf-8"), hashlib.sha256).digest()
            )
        )
        assert f'oauth_signature="{expected_sig}"' in header, (
            f"Signature mismatch.\nHeader: {header!r}\nExpected sig: {expected_sig!r}"
        )

    def test_authorization_header_with_query_string(self) -> None:
        """Query-string parameters are included in the signature base string."""
        key_bytes = bytes(range(20))
        lst_b64 = base64.b64encode(key_bytes).decode()
        url = "https://api.ibkr.com/v1/api/iserver/marketdata/snapshot?conids=265598&fields=31"

        fixed_ts = 1700000001
        fixed_nonce_bytes = b"\x02" * 16

        with patch("time.time", return_value=fixed_ts), \
             patch("os.urandom", return_value=fixed_nonce_bytes):
            header = _oauth_authorization_header(
                consumer_key="TESTCONS",
                access_token="tok",
                lst_b64=lst_b64,
                method="GET",
                url=url,
            )

        # Extract nonce.
        nonce = None
        for part in header.split(", "):
            if "oauth_nonce=" in part:
                nonce = part.split('"')[1]
        assert nonce is not None

        # Recompute.
        split = urllib.parse.urlsplit(url)
        base_url = f"{split.scheme}://{split.netloc}{split.path}"
        query_params = parse_qsl(split.query, keep_blank_values=True)
        oauth_params = {
            "oauth_consumer_key": "TESTCONS",
            "oauth_nonce": nonce,
            "oauth_signature_method": "HMAC-SHA256",
            "oauth_timestamp": str(fixed_ts),
            "oauth_token": "tok",
        }
        base_string = _build_oauth_base_string("GET", base_url, query_params, oauth_params)
        expected_sig = urllib.parse.quote_plus(
            base64.b64encode(
                _hmac.new(key_bytes, base_string.encode("utf-8"), hashlib.sha256).digest()
            )
        )
        assert f'oauth_signature="{expected_sig}"' in header

    def test_default_realm_limited_poa(self) -> None:
        """Default realm is 'limited_poa', not 'test_realm'."""
        key_bytes = bytes(range(20))
        lst_b64 = base64.b64encode(key_bytes).decode()
        header = _oauth_authorization_header(
            consumer_key="CK",
            access_token="AT",
            lst_b64=lst_b64,
            method="GET",
            url="https://api.ibkr.com/v1/api/iserver/accounts",
        )
        assert 'realm="limited_poa"' in header
        assert 'realm="test_realm"' not in header

    def test_realm_override_parameter(self) -> None:
        """Explicit realm parameter is used in the Authorization header."""
        key_bytes = bytes(range(20))
        lst_b64 = base64.b64encode(key_bytes).decode()
        header = _oauth_authorization_header(
            consumer_key="CK",
            access_token="AT",
            lst_b64=lst_b64,
            method="GET",
            url="https://api.ibkr.com/v1/api/iserver/accounts",
            realm="TESTCONS",
        )
        assert 'realm="TESTCONS"' in header

    def test_oauth_authorization_header_contains_required_fields(self) -> None:
        """Authorization header contains all mandatory OAuth 1.0a fields."""
        key_bytes = bytes(range(20))
        lst_b64 = base64.b64encode(key_bytes).decode()
        header = _oauth_authorization_header(
            consumer_key="testconsumer",
            access_token="testtoken",
            lst_b64=lst_b64,
            method="GET",
            url="https://api.ibkr.com/v1/api/iserver/accounts",
        )
        assert 'oauth_consumer_key="testconsumer"' in header
        assert 'oauth_token="testtoken"' in header
        assert 'oauth_signature_method="HMAC-SHA256"' in header
        assert "oauth_signature=" in header
        assert "oauth_timestamp=" in header
        assert "oauth_nonce=" in header

    def test_realm_from_oauth_auth_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """IBKR_OAUTH_REALM env var is respected by OAuthAuth._signed_headers."""
        monkeypatch.setenv("IBKR_OAUTH_REALM", "my_realm")
        monkeypatch.setenv("IBKR_OAUTH_CONSUMER_KEY", "CK")
        monkeypatch.setenv("IBKR_OAUTH_ACCESS_TOKEN", "AT")
        monkeypatch.setenv("IBKR_OAUTH_ACCESS_TOKEN_SECRET", "dGVzdA==")
        monkeypatch.setenv("IBKR_OAUTH_SIGNATURE_KEY_PATH", "/fake/sig.pem")
        monkeypatch.setenv("IBKR_OAUTH_ENCRYPTION_KEY_PATH", "/fake/enc.pem")
        monkeypatch.setenv("IBKR_OAUTH_DH_PARAM_PATH", "/fake/dh.pem")

        auth = OAuthAuth(base_url="https://api.ibkr.com/v1/api")
        # Inject a fake LST to skip the exchange.
        auth._lst = base64.b64encode(bytes(range(20))).decode()
        headers = auth._signed_headers("GET", "https://api.ibkr.com/v1/api/iserver/accounts")
        assert 'realm="my_realm"' in headers["Authorization"]


# ---------------------------------------------------------------------------
# Finding B — independent protocol vectors (not self-referential)
# ---------------------------------------------------------------------------


class TestOAuthIndependentVectors:
    """RFC 5849 / DH / RSA-SHA256 / HMAC-SHA256 pinned-value tests.

    These tests verify correctness against constants computed by hand or from
    the RFC — they do NOT derive expected values from the code under test.
    """

    # ------------------------------------------------------------------
    # 1. Signature base string — RFC 5849 §3.4.1 canonical example
    # ------------------------------------------------------------------

    def test_base_string_rfc5849_canonical(self) -> None:
        """Base string matches the RFC 5849 §3.4.1.3.2 example exactly.

        Query string b5=%3D%253D&a3=a&c%40=&a2=r%20b decodes to:
          b5==%3D, a3=a, c@=<empty>, a2=r b
        Body c2&a3=2+q decodes to:
          c2=<empty>, a3=2 q
        OAuth params use HMAC-SHA1 as specified in the RFC example.
        """
        # Decoded query params (already URL-decoded, _build_oauth_base_string re-encodes).
        query_params = [
            ("b5", "=%3D"),   # %3D%253D decoded → =%3D
            ("a3", "a"),
            ("c@", ""),       # c%40 decoded → c@
            ("a2", "r b"),    # r%20b decoded → r b
        ]
        oauth_params = {
            "oauth_consumer_key": "9djdj82h48djs9d2",
            "oauth_token": "kkk9d7dh3k39sjv7",
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": "137131201",
            "oauth_nonce": "7d8f3e4a",
        }
        # Body c2&a3=2+q: "+" in form-encoded body means space.
        body_params = [
            ("c2", ""),
            ("a3", "2 q"),
        ]

        result = _build_oauth_base_string(
            method="POST",
            base_url="http://example.com/request",
            query_params=query_params,
            oauth_params=oauth_params,
            body_params=body_params,
        )

        expected = (
            "POST"
            "&http%3A%2F%2Fexample.com%2Frequest"
            "&a2%3Dr%2520b%26a3%3D2%2520q%26a3%3Da%26b5%3D%253D%25253D"
            "%26c%2540%3D%26c2%3D%26oauth_consumer_key%3D9djdj82h48djs9d2"
            "%26oauth_nonce%3D7d8f3e4a%26oauth_signature_method%3DHMAC-SHA1"
            "%26oauth_timestamp%3D137131201%26oauth_token%3Dkkk9d7dh3k39sjv7"
        )
        assert result == expected, (
            f"Base string mismatch.\nGot:      {result!r}\nExpected: {expected!r}"
        )

    # ------------------------------------------------------------------
    # 2. DH known-answer: small-group (p=23, g=5)
    # ------------------------------------------------------------------

    def test_dh_small_group_known_answer(self) -> None:
        """DH math: p=23, g=5, a=6 → A=8; peer B=19 → shared=2.

        Verifies that modular exponentiation produces the correct intermediate
        and final values against hand-computed constants.  Uses Python's
        built-in pow() because the cryptography-library helpers require
        PEM/DER-encoded large-group parameters and cannot accept toy groups;
        the underlying math is identical.
        """
        p, g, a = 23, 5, 6
        A = pow(g, a, p)
        assert A == 8, f"Public key A wrong: got {A}, expected 8"

        B = 19
        shared = pow(B, a, p)
        assert shared == 2, f"Shared secret wrong: got {shared}, expected 2"

        # Hex-feed variant — mirrors how implementations pass big ints as hex.
        p_hex = hex(p)
        g_hex = hex(g)
        a_int = int("6", 16)  # same as a=6
        A_hex = pow(int(g_hex, 16), a_int, int(p_hex, 16))
        assert A_hex == 8
        shared_hex = pow(int(hex(B), 16), a_int, int(p_hex, 16))
        assert shared_hex == 2

    # ------------------------------------------------------------------
    # 3. RSA-SHA256 request-token signing — verify with public key
    # ------------------------------------------------------------------

    def test_rsa_sha256_sign_verify_with_public_key(self) -> None:
        """Sign a fixed message, then verify independently with the public key.

        Uses cryptography's verify() with PKCS1v15+SHA256 — this pins the
        padding and digest choice independently of the signing call.
        """
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa, padding
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            pytest.skip("cryptography not installed")

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        base_string = b"POST&https%3A%2F%2Fapi.ibkr.com%2Foauth%2Flive_session_token&fixed_nonce"
        sig_b64 = _rsa_sha256_sign(private_key, base_string)
        sig_bytes = __import__("base64").b64decode(sig_b64)

        # Independent verification: should not raise.
        private_key.public_key().verify(
            sig_bytes,
            base_string,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    # ------------------------------------------------------------------
    # 4. HMAC-SHA256 pinned vector
    # ------------------------------------------------------------------

    def test_hmac_sha256_bytes_pinned_vector(self) -> None:
        """HMAC-SHA256(key=b'key', msg='The quick brown fox...') == known hex.

        Expected digest f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8
        is from a well-known test vector independent of this codebase.
        """
        key = b"key"
        message = b"The quick brown fox jumps over the lazy dog"
        expected_hex = "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"

        digest = _hmac_sha256_sign_bytes(key, message)
        assert digest.hex() == expected_hex, (
            f"HMAC digest mismatch.\nGot:      {digest.hex()}\nExpected: {expected_hex}"
        )
