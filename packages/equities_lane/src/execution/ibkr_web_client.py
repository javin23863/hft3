"""Thin client for the IBKR Client Portal (Web) API — paper-shadow needs.

No ``ibind`` dependency is used here.  ibind is noted as a future option if a
higher-level SDK wrapper is desired once it can be verified offline.

WebSocket support
-----------------
``websockets`` is not currently listed as a dependency of this package.  The
``ws_url()`` method and subscribe-message builders are provided; call
``connect_ws(handler)`` to open a live WebSocket if you add ``websockets`` as
a dependency and the package is importable.  Without it, message builders still
work for testing.

Auth modes
----------
GatewayAuth
    HTTP session against clientportal.gw on localhost.  ``verify=False`` is
    applied ONLY when the host resolves to localhost / 127.0.0.1.  Any other
    host gets normal TLS verification.

OAuthAuth
    OAuth 1.0a flow:
      1. Sign a live-session-token request with RSA-SHA256 (private signature
         key at ``IBKR_OAUTH_SIGNATURE_KEY_PATH``).  Body carries a
         Diffie-Hellman public key derived from the DH params at
         ``IBKR_OAUTH_DH_PARAM_PATH``.
      2. Decrypt the returned encrypted LST with the private encryption key at
         ``IBKR_OAUTH_ENCRYPTION_KEY_PATH``.
      3. HMAC-SHA256 sign every subsequent request using the LST as the key.

    Signing is implemented using the ``cryptography`` package.  If that package
    is absent at runtime, methods raise ``NotImplementedError`` with a clear
    message rather than failing silently.

Live-account guard
------------------
Every order method calls ``_assert_paper_account(account_id)`` before touching
the API.  It resolves ``/iserver/accounts`` and refuses unless:
  - ``account_id`` matches the env ``IBKR_ACCOUNT_ID_PAPER`` value, AND
  - that account id is present in the live ``/iserver/accounts`` response.
Raises ``LiveAccountRefusal`` on any mismatch.  This is not config-trust — it
is verified per call.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Callable
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LiveAccountRefusal(RuntimeError):
    """Raised when an order operation targets a non-paper account."""


# ---------------------------------------------------------------------------
# Auth base
# ---------------------------------------------------------------------------


class _AuthBase:
    """Shared interface for auth adapters."""

    def get(self, url: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    def post(self, url: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    def delete(self, url: str, **kwargs: Any) -> Any:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# GatewayAuth
# ---------------------------------------------------------------------------


class GatewayAuth(_AuthBase):
    """Auth adapter for clientportal.gw (localhost gateway, cookie/session).

    ``verify=False`` is applied only when the host is localhost or 127.0.0.1
    to handle the self-signed certificate.  Any non-localhost base_url gets
    normal TLS verification.

    Args:
        base_url: Gateway base URL, e.g. ``https://localhost:5000/v1/api``.
        session: Optional injected requests.Session (or compatible mock).
    """

    def __init__(
        self,
        base_url: str = "https://localhost:5000/v1/api",
        *,
        session: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session
        parsed = urlparse(self.base_url)
        host = parsed.hostname or ""
        self._verify: bool = host not in ("localhost", "127.0.0.1")

    def _sess(self) -> Any:
        if self._session is not None:
            return self._session
        import requests  # type: ignore

        if not hasattr(self, "_real_session"):
            object.__setattr__(self, "_real_session", requests.Session())
        return self._real_session  # type: ignore[attr-defined]

    def get(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("verify", self._verify)
        resp = self._sess().get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def post(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("verify", self._verify)
        resp = self._sess().post(url, **kwargs)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code}

    def delete(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("verify", self._verify)
        resp = self._sess().delete(url, **kwargs)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code}


# ---------------------------------------------------------------------------
# OAuthAuth
# ---------------------------------------------------------------------------


def _require_cryptography() -> None:
    try:
        import cryptography  # noqa: F401
    except ImportError as exc:
        raise NotImplementedError(
            "OAuthAuth requires the 'cryptography' package.  "
            "Install it with: pip install cryptography"
        ) from exc


def _load_private_key(path: str) -> Any:
    """Load a PEM private key from *path* using cryptography.hazmat."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key  # type: ignore

    raw = open(path, "rb").read()
    return load_pem_private_key(raw, password=None)


def _rsa_sha256_sign(private_key: Any, message: bytes) -> str:
    """Return Base64-encoded RSA-SHA256 signature of *message*."""
    from cryptography.hazmat.primitives import hashes  # type: ignore
    from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore

    sig = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def _dh_generate_keypair(dh_params_path: str) -> tuple[Any, bytes]:
    """Generate a DH keypair.  Returns (private_key, public_key_bytes_hex_encoded)."""
    from cryptography.hazmat.primitives.serialization import (  # type: ignore
        Encoding,
        PublicFormat,
        load_pem_parameters,
    )

    raw = open(dh_params_path, "rb").read()
    parameters = load_pem_parameters(raw)
    private_key = parameters.generate_private_key()
    pub_bytes = private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return private_key, pub_bytes


def _dh_compute_shared_secret(private_key: Any, peer_pub_bytes: bytes) -> bytes:
    """Compute DH shared secret from our private key and peer's DER-encoded public key."""
    from cryptography.hazmat.primitives.asymmetric.dh import DHPublicNumbers  # type: ignore  # noqa: F401
    from cryptography.hazmat.primitives.serialization import load_der_public_key  # type: ignore

    peer_public = load_der_public_key(peer_pub_bytes)
    return private_key.exchange(peer_public)


def _decrypt_lst(encrypted_lst_b64: str, encryption_key_path: str, shared_secret: bytes) -> str:
    """Decrypt the live-session-token using RSAOAEP+SHA256 and the shared DH secret.

    The IBKR OAuth 1.0a protocol derives the final decryption key by XOR-ing
    the RSA-decrypted intermediate with the DH shared secret.  The exact byte
    layout follows IBKR's Web API OAuth documentation.

    This implementation decrypts with the RSA encryption key (PKCS1 OAEP /
    SHA-256) and XORs the result with the SHA-256 digest of the shared secret
    to produce the LST bytes, then returns the hex string.
    """
    from cryptography.hazmat.primitives import hashes  # type: ignore
    from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore

    enc_key = _load_private_key(encryption_key_path)
    cipher_bytes = base64.b64decode(encrypted_lst_b64)
    decrypted = enc_key.decrypt(cipher_bytes, padding.OAEP(padding.MGF1(hashes.SHA256()), hashes.SHA256(), None))
    # Derive LST: XOR decrypted bytes with SHA-256 of shared secret (IBKR spec).
    digest = hashlib.sha256(shared_secret).digest()
    lst_bytes = bytes(a ^ b for a, b in zip(decrypted, digest * (len(decrypted) // len(digest) + 1)))
    return lst_bytes[: len(decrypted)].hex()


def _hmac_sha256_sign(lst_hex: str, message: str) -> str:
    """HMAC-SHA256 sign *message* using the LST bytes derived from *lst_hex*."""
    key = bytes.fromhex(lst_hex)
    sig = hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


def _oauth_authorization_header(
    consumer_key: str,
    access_token: str,
    lst_hex: str,
    method: str,
    url: str,
) -> str:
    """Build the OAuth Authorization header for a signed request.

    Signature base string = METHOD&url&timestamp&nonce (simplified IBKR variant).
    """
    timestamp = str(int(time.time()))
    nonce = base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("=")
    base_string = f"{method.upper()}&{url}&{timestamp}&{nonce}"
    signature = _hmac_sha256_sign(lst_hex, base_string)
    return (
        f'OAuth realm="test_realm",'
        f' oauth_consumer_key="{consumer_key}",'
        f' oauth_token="{access_token}",'
        f' oauth_signature_method="HMAC-SHA256",'
        f' oauth_timestamp="{timestamp}",'
        f' oauth_nonce="{nonce}",'
        f' oauth_signature="{signature}"'
    )


class OAuthAuth(_AuthBase):
    """Auth adapter for IBKR OAuth 1.0a (direct, fully unattended).

    The live-session-token (LST) is obtained lazily on the first request and
    cached for the session lifetime.  Call ``refresh_lst()`` to force renewal.

    All key paths are read from environment variables at construction time so
    that no secrets are hard-coded.

    Env vars consumed:
      IBKR_OAUTH_CONSUMER_KEY
      IBKR_OAUTH_ACCESS_TOKEN
      IBKR_OAUTH_ACCESS_TOKEN_SECRET
      IBKR_OAUTH_SIGNATURE_KEY_PATH
      IBKR_OAUTH_ENCRYPTION_KEY_PATH
      IBKR_OAUTH_DH_PARAM_PATH

    Args:
        base_url: IBKR production OAuth URL, default ``https://api.ibkr.com/v1/api``.
        session: Optional injected requests.Session (or compatible mock).
    """

    def __init__(
        self,
        base_url: str = "https://api.ibkr.com/v1/api",
        *,
        session: Any | None = None,
    ) -> None:
        _require_cryptography()
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._lst: str | None = None
        self._consumer_key = os.environ.get("IBKR_OAUTH_CONSUMER_KEY", "")
        self._access_token = os.environ.get("IBKR_OAUTH_ACCESS_TOKEN", "")
        self._access_token_secret = os.environ.get("IBKR_OAUTH_ACCESS_TOKEN_SECRET", "")
        self._sig_key_path = os.environ.get("IBKR_OAUTH_SIGNATURE_KEY_PATH", "")
        self._enc_key_path = os.environ.get("IBKR_OAUTH_ENCRYPTION_KEY_PATH", "")
        self._dh_param_path = os.environ.get("IBKR_OAUTH_DH_PARAM_PATH", "")

    def _sess(self) -> Any:
        if self._session is not None:
            return self._session
        import requests  # type: ignore

        if not hasattr(self, "_real_session"):
            object.__setattr__(self, "_real_session", requests.Session())
        return self._real_session  # type: ignore[attr-defined]

    def _ensure_lst(self) -> str:
        if self._lst:
            return self._lst
        self._lst = self._obtain_lst()
        return self._lst

    def _obtain_lst(self) -> str:
        """Perform the DH-challenge live-session-token exchange."""
        dh_private, dh_pub_bytes = _dh_generate_keypair(self._dh_param_path)
        dh_pub_b64 = base64.b64encode(dh_pub_bytes).decode()

        # Sign the DH challenge with our RSA signature key.
        sig_key = _load_private_key(self._sig_key_path)
        to_sign = f"{self._consumer_key}+{dh_pub_b64}".encode()
        rsa_sig = _rsa_sha256_sign(sig_key, to_sign)

        body = {
            "diffie_hellman_challenge": dh_pub_b64,
            "consumer_key": self._consumer_key,
            "access_token": self._access_token,
            "access_token_secret": self._access_token_secret,
            "signature": rsa_sig,
        }
        url = f"{self.base_url}/oauth/live_session_token"
        resp = self._sess().post(url, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        encrypted_lst = data["diffie_hellman_response"]
        peer_pub_b64 = data["dh_server_public"]
        peer_pub_bytes = base64.b64decode(peer_pub_b64)
        shared_secret = _dh_compute_shared_secret(dh_private, peer_pub_bytes)
        return _decrypt_lst(encrypted_lst, self._enc_key_path, shared_secret)

    def refresh_lst(self) -> None:
        """Force renewal of the live-session-token."""
        self._lst = None

    def _signed_headers(self, method: str, url: str) -> dict[str, str]:
        lst = self._ensure_lst()
        auth = _oauth_authorization_header(self._consumer_key, self._access_token, lst, method, url)
        return {"Authorization": auth, "Content-Type": "application/json"}

    def get(self, url: str, **kwargs: Any) -> Any:
        headers = self._signed_headers("GET", url)
        kwargs.setdefault("headers", {}).update(headers)
        resp = self._sess().get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def post(self, url: str, **kwargs: Any) -> Any:
        headers = self._signed_headers("POST", url)
        kwargs.setdefault("headers", {}).update(headers)
        resp = self._sess().post(url, **kwargs)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code}

    def delete(self, url: str, **kwargs: Any) -> Any:
        headers = self._signed_headers("DELETE", url)
        kwargs.setdefault("headers", {}).update(headers)
        resp = self._sess().delete(url, **kwargs)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code}


# ---------------------------------------------------------------------------
# IbkrWebClient
# ---------------------------------------------------------------------------


class IbkrWebClient:
    """Thin IBKR Client Portal (Web) API client.

    All HTTP calls are routed through the injected ``auth`` adapter so that
    tests can mock transport entirely (no real network).

    Args:
        auth: An instance of ``GatewayAuth`` or ``OAuthAuth``.  If omitted,
              ``GatewayAuth`` with localhost defaults is used.
        paper_account_id: Paper account id.  When ``None`` (default), read from
                          ``IBKR_ACCOUNT_ID_PAPER`` env var.  An explicit value
                          (including ``""``) is used verbatim — pass ``""`` to
                          leave it unconfigured regardless of env.  Required for
                          order methods; raises ``LiveAccountRefusal`` otherwise.
    """

    def __init__(
        self,
        auth: _AuthBase | None = None,
        *,
        paper_account_id: str | None = None,
    ) -> None:
        self._auth: _AuthBase = auth or GatewayAuth()
        self._paper_account_id: str = (
            paper_account_id
            if paper_account_id is not None
            else os.environ.get("IBKR_ACCOUNT_ID_PAPER", "")
        )

    # ------------------------------------------------------------------
    # Session probes
    # ------------------------------------------------------------------

    def auth_status(self) -> dict[str, Any]:
        """GET /iserver/auth/status — returns raw API response."""
        base = self._auth.base_url
        return self._auth.get(f"{base}/iserver/auth/status")

    def tickle(self) -> dict[str, Any]:
        """POST /tickle — keep the session alive; call every ~60 s."""
        base = self._auth.base_url
        return self._auth.post(f"{base}/tickle")

    def accounts(self) -> list[str]:
        """GET /iserver/accounts — returns list of account ids."""
        base = self._auth.base_url
        response = self._auth.get(f"{base}/iserver/accounts")
        if isinstance(response, dict):
            return list(response.get("accounts") or [])
        if isinstance(response, list):
            return [str(a) for a in response]
        return []

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def marketdata_snapshot(
        self,
        conids: list[int | str],
        fields: list[str | int] | None = None,
    ) -> list[dict[str, Any]]:
        """GET /iserver/marketdata/snapshot.

        Args:
            conids: List of contract ids.
            fields: Optional list of field codes (e.g. [31, 84, 86]).

        Returns:
            List of snapshot dicts, one per conid.
        """
        base = self._auth.base_url
        params_str = f"conids={','.join(str(c) for c in conids)}"
        if fields:
            params_str += f"&fields={','.join(str(f) for f in fields)}"
        url = f"{base}/iserver/marketdata/snapshot?{params_str}"
        response = self._auth.get(url)
        if isinstance(response, list):
            return response
        if isinstance(response, dict):
            return [response]
        return []

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def _assert_paper_account(self, account_id: str) -> None:
        """Refuse to place/cancel orders unless account_id == paper account.

        Resolves /iserver/accounts per call — not config-trust.
        Raises LiveAccountRefusal on any mismatch.
        """
        if not self._paper_account_id:
            raise LiveAccountRefusal(
                "IBKR_ACCOUNT_ID_PAPER is not set in the environment. "
                "Order methods require an explicit paper account id."
            )
        if account_id != self._paper_account_id:
            raise LiveAccountRefusal(
                f"Refusing order: account_id '{account_id}' does not match "
                f"the configured paper account (IBKR_ACCOUNT_ID_PAPER). "
                "Live account guard prevents non-paper order submission."
            )
        live_accounts = self.accounts()
        if account_id not in live_accounts:
            raise LiveAccountRefusal(
                f"Refusing order: account_id '{account_id}' is not present in "
                f"/iserver/accounts response ({live_accounts}). "
                "The account may not be authenticated or may be a live account."
            )

    def place_order(self, account_id: str, order: dict[str, Any]) -> dict[str, Any]:
        """POST /iserver/account/{account_id}/orders.

        Raises ``LiveAccountRefusal`` if account_id is not the paper account.
        """
        self._assert_paper_account(account_id)
        base = self._auth.base_url
        url = f"{base}/iserver/account/{account_id}/orders"
        return self._auth.post(url, json={"orders": [order]})

    def cancel_order(self, account_id: str, order_id: str | int) -> dict[str, Any]:
        """DELETE /iserver/account/{account_id}/order/{orderId}.

        Raises ``LiveAccountRefusal`` if account_id is not the paper account.
        """
        self._assert_paper_account(account_id)
        base = self._auth.base_url
        url = f"{base}/iserver/account/{account_id}/order/{order_id}"
        return self._auth.delete(url)

    def order_status(self, order_id: str | int) -> dict[str, Any]:
        """GET /iserver/account/orders/{orderId} — no live-account guard needed."""
        base = self._auth.base_url
        url = f"{base}/iserver/account/orders/{order_id}"
        return self._auth.get(url)

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    def ws_url(self) -> str:
        """Return the WebSocket URL for this endpoint.

        Converts https:// → wss:// and appends /ws.
        """
        base = self._auth.base_url
        ws = base.replace("https://", "wss://").replace("http://", "ws://")
        return f"{ws}/ws"

    @staticmethod
    def subscribe_fills_message(account_id: str) -> str:
        """Build the subscription message for order-fill events.

        Returns a JSON string to send over the WebSocket connection.
        """
        return json.dumps({"topic": "sor", "subscribe": True, "account": account_id})

    @staticmethod
    def subscribe_orders_message(account_id: str) -> str:
        """Build the subscription message for live order-status updates."""
        return json.dumps({"topic": "or", "subscribe": True, "account": account_id})

    def connect_ws(self, handler: Callable[[str], None]) -> None:
        """Open a WebSocket connection and call *handler* for each message.

        Requires the ``websockets`` package (sync ``websockets.sync.client``
        available in websockets >= 11).  Add ``websockets>=11`` to
        requirements.txt to enable this method.

        Args:
            handler: Callable receiving raw message strings.
        """
        try:
            from websockets.sync.client import connect  # type: ignore
        except ImportError as exc:
            raise NotImplementedError(
                "connect_ws requires 'websockets>=11'. "
                "Add it to packages/equities_lane/requirements.txt."
            ) from exc

        url = self.ws_url()
        verify_ssl = "localhost" not in url and "127.0.0.1" not in url
        with connect(url, ssl=verify_ssl or None) as ws:
            for message in ws:
                handler(str(message))
