"""Thin client for the IBKR Client Portal (Web) API — paper-shadow needs.

No ``ibind`` dependency is used here.  ibind is noted as a future option if a
higher-level SDK wrapper is desired once it can be verified offline.

WebSocket support
-----------------
``websockets>=12`` is required for async WebSocket support.  Use
``connect_ws(session_token)`` (async) which returns an ``IbkrWsSession``.
The ``ws_url()`` helper and subscribe-message builders remain importable
without websockets; websockets is imported lazily inside ``connect_ws`` so
the REST-only path never requires it.

Auth modes
----------
GatewayAuth
    HTTP session against clientportal.gw on localhost.  ``verify=False`` is
    applied ONLY when the host resolves to localhost / 127.0.0.1.  Any other
    host gets normal TLS verification.

OAuthAuth
    OAuth 1.0a flow (real IBKR Client Portal protocol):
      1. Sign a live-session-token request with RSA-SHA256 (private signature
         key at ``IBKR_OAUTH_SIGNATURE_KEY_PATH``).  The Authorization header
         carries a Diffie-Hellman public key (``diffie_hellman_challenge``).
         The access-token secret is decrypted LOCALLY with the private
         encryption key; it is never transmitted.
      2. Compute the LST from the DH shared secret and the decrypted prepend
         via HMAC-SHA1.  Validate against ``live_session_token_signature``.
      3. HMAC-SHA256 sign every subsequent request using the LST as the key.

    Signing is implemented using the ``cryptography`` package.  If that
    package is absent at runtime, methods raise ``NotImplementedError`` with a
    clear message rather than failing silently.

    Env vars consumed:
      IBKR_OAUTH_CONSUMER_KEY
      IBKR_OAUTH_ACCESS_TOKEN
      IBKR_OAUTH_ACCESS_TOKEN_SECRET   — base64-encoded ciphertext; decrypted
                                         locally with the encryption key; NEVER
                                         transmitted to the server.
      IBKR_OAUTH_SIGNATURE_KEY_PATH    — RSA private key (PEM) for signing
      IBKR_OAUTH_ENCRYPTION_KEY_PATH   — RSA private key (PEM) for decryption
      IBKR_OAUTH_DH_PARAM_PATH         — DH params (PEM) for key exchange
      IBKR_OAUTH_REALM                 — OAuth realm, default "limited_poa"

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
import random
import time
from typing import Any, AsyncIterator, Callable
from urllib.parse import parse_qsl, quote, quote_plus, urlparse, urlsplit

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LiveAccountRefusal(RuntimeError):
    """Raised when an order operation targets a non-paper account."""


class LiveSessionTokenError(RuntimeError):
    """Raised when the LST HMAC validation against the server signature fails."""


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
# OAuthAuth — pure helper functions
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
    """Return Base64-encoded RSA-SHA256 signature of *message* (PKCS1v15)."""
    from cryptography.hazmat.primitives import hashes  # type: ignore
    from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore

    sig = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def _int_to_signed_bytes(x: int) -> bytes:
    """Convert a non-negative integer to big-endian bytes with an IBKR sign-byte quirk.

    Standard big-endian representation, but if ``x.bit_length() % 8 == 0`` a
    leading 0x00 byte is prepended — matching IBKR's server-side behaviour when
    deriving the DH shared secret bytes for HMAC keying.

    Examples:
        _int_to_signed_bytes(2)   → b"\\x02"
        _int_to_signed_bytes(128) → b"\\x00\\x80"  (bit_length=8 → prepend 0x00)
        _int_to_signed_bytes(255) → b"\\x00\\xff"  (bit_length=8 → prepend 0x00)
        _int_to_signed_bytes(256) → b"\\x01\\x00"  (bit_length=9 → no prepend)
    """
    h = hex(x)[2:]  # strip "0x"
    if len(h) % 2:
        h = "0" + h
    raw = bytes.fromhex(h)
    if x.bit_length() % 8 == 0:
        raw = b"\x00" + raw
    return raw


def _hmac_sha256_sign_bytes(key: bytes, message: bytes) -> bytes:
    """HMAC-SHA256 over raw bytes — pure function, testable in isolation.

    Args:
        key: Raw HMAC key bytes.
        message: Raw message bytes.

    Returns:
        32-byte HMAC-SHA256 digest.
    """
    return hmac.new(key, message, hashlib.sha256).digest()


def _percent_encode(s: str) -> str:
    """RFC 5849 §3.6 percent-encoding: encode every character except unreserved."""
    return quote(s, safe="")


def _build_oauth_base_string(
    method: str,
    base_url: str,
    query_params: list[tuple[str, str]],
    oauth_params: dict[str, str],
    body_params: list[tuple[str, str]] | None = None,
) -> str:
    """Construct an OAuth 1.0a signature base string per RFC 5849 §3.4.1.

    This is a pure function — all inputs are explicit, no side effects.

    Args:
        method: HTTP method, e.g. ``"POST"``.
        base_url: The request URL *without* query string, e.g.
                  ``"http://example.com/request"``.
        query_params: List of ``(key, value)`` pairs from the query string,
                      already URL-decoded (will be re-encoded here).
        oauth_params: OAuth protocol parameters *excluding* ``realm``.
                      ``oauth_signature`` must NOT be present.
        body_params: Optional list of ``(key, value)`` pairs from the
                     ``application/x-www-form-urlencoded`` body, already
                     URL-decoded.

    Returns:
        The signature base string as defined in RFC 5849 §3.4.1.
    """
    # Collect all parameter pairs.
    all_params: list[tuple[str, str]] = []
    all_params.extend(query_params)
    all_params.extend(body_params or [])
    for k, v in oauth_params.items():
        if k != "oauth_signature" and k != "realm":
            all_params.append((k, v))

    # Percent-encode each name and value, then sort lexicographically.
    encoded: list[tuple[str, str]] = [(_percent_encode(k), _percent_encode(v)) for k, v in all_params]
    encoded.sort()

    normalized_params = "&".join(f"{k}={v}" for k, v in encoded)

    base_string = (
        _percent_encode(method.upper())
        + "&"
        + _percent_encode(base_url)
        + "&"
        + _percent_encode(normalized_params)
    )
    return base_string


def _hmac_sha256_sign(lst_b64: str, message: str) -> str:
    """HMAC-SHA256 sign *message* using the LST (base64-encoded) as key.

    Args:
        lst_b64: The live-session-token as a base64-encoded string.
        message: The message to sign (UTF-8 encoded internally).

    Returns:
        Base64-encoded HMAC-SHA256 digest.
    """
    key = base64.b64decode(lst_b64)
    sig = hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


def _oauth_authorization_header(
    consumer_key: str,
    access_token: str,
    lst_b64: str,
    method: str,
    url: str,
    realm: str = "limited_poa",
) -> str:
    """Build the OAuth Authorization header for a signed per-request call.

    Implements RFC 5849 §3.4.1 base string construction for HMAC-SHA256
    signing.  Query-string parameters from *url* are split into the param set
    so they are included in the signature but not double-encoded in the URL.

    Args:
        consumer_key: OAuth consumer key.
        access_token: OAuth access token.
        lst_b64: Live-session-token (base64).  Used as the HMAC-SHA256 key.
        method: HTTP method (``"GET"``, ``"POST"``, …).
        url: Full request URL, possibly with query string.
        realm: OAuth realm string.  Default ``"limited_poa"``.

    Returns:
        Value for the ``Authorization`` HTTP header.
    """
    # Split URL into base (no query) + query params.
    split = urlsplit(url)
    base_url = f"{split.scheme}://{split.netloc}{split.path}"
    query_params: list[tuple[str, str]] = parse_qsl(split.query, keep_blank_values=True)

    timestamp = str(int(time.time()))
    nonce = base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("=")

    oauth_params: dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce,
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": timestamp,
        "oauth_token": access_token,
    }

    base_string = _build_oauth_base_string(
        method=method,
        base_url=base_url,
        query_params=query_params,
        oauth_params=oauth_params,
    )

    key = base64.b64decode(lst_b64)
    sig_bytes = hmac.new(key, base_string.encode("utf-8"), hashlib.sha256).digest()
    oauth_signature = quote_plus(base64.b64encode(sig_bytes))

    # Build sorted header — realm first, then oauth params alphabetically,
    # then oauth_signature last (conventional placement).
    sorted_pairs = sorted(oauth_params.items())
    pairs_str = ", ".join(f'{k}="{v}"' for k, v in sorted_pairs)
    return f'OAuth realm="{realm}", {pairs_str}, oauth_signature="{oauth_signature}"'


# ---------------------------------------------------------------------------
# LST exchange helpers (private to OAuthAuth._obtain_lst)
# ---------------------------------------------------------------------------


def _lst_authorization_header(
    consumer_key: str,
    access_token: str,
    dh_challenge_hex: str,
    prepend: str,
    sig_key: Any,
    url: str,
    realm: str,
) -> tuple[str, str, str]:
    """Build the RSA-SHA256 Authorization header for the LST exchange endpoint.

    Returns a 3-tuple of ``(header_value, nonce, timestamp)`` so the caller
    can reconstruct the prepend-prefixed base string for testing.

    The signature base string is::

        prepend + "POST&" + percent_encode(url) + "&" + percent_encode(params)

    where ``prepend`` is the hex-encoded decrypted access-token-secret bytes
    (never transmitted).

    Args:
        consumer_key: OAuth consumer key.
        access_token: OAuth access token (the token, not its secret).
        dh_challenge_hex: Lowercase hex of the client DH public value A.
        prepend: Hex string prepended to the base string (NOT percent-encoded).
        sig_key: Loaded RSA private key object for signing.
        url: The LST endpoint URL (no query string).
        realm: OAuth realm.

    Returns:
        ``(authorization_header, nonce, timestamp)``
    """
    timestamp = str(int(time.time()))
    nonce = base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("=")

    oauth_params: dict[str, str] = {
        "diffie_hellman_challenge": dh_challenge_hex,
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce,
        "oauth_signature_method": "RSA-SHA256",
        "oauth_timestamp": timestamp,
        "oauth_token": access_token,
    }

    # RFC 5849 base string (no query or body params for this endpoint).
    rfc_base = _build_oauth_base_string(
        method="POST",
        base_url=url,
        query_params=[],
        oauth_params=oauth_params,
    )

    # Prepend the decrypted secret hex BEFORE the base string (not %-encoded).
    base_string = prepend + rfc_base

    # RSA-SHA256 sign.
    from cryptography.hazmat.primitives import hashes  # type: ignore
    from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore

    sig_bytes = sig_key.sign(base_string.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    oauth_signature = quote_plus(base64.b64encode(sig_bytes))

    # Build sorted header — realm first, params in sorted order, signature last.
    sorted_pairs = sorted(oauth_params.items())
    pairs_str = ", ".join(f'{k}="{v}"' for k, v in sorted_pairs)
    header = f'OAuth realm="{realm}", {pairs_str}, oauth_signature="{oauth_signature}"'
    return header, nonce, timestamp


# ---------------------------------------------------------------------------
# OAuthAuth
# ---------------------------------------------------------------------------


class OAuthAuth(_AuthBase):
    """Auth adapter for IBKR OAuth 1.0a (direct, fully unattended).

    The live-session-token (LST) is obtained lazily on the first request and
    cached for the session lifetime.  Call ``refresh_lst()`` to force renewal.

    All key paths are read from environment variables at construction time so
    that no secrets are hard-coded.  The access-token secret is decrypted
    LOCALLY and is never transmitted to IBKR servers.

    Env vars consumed:
      IBKR_OAUTH_CONSUMER_KEY
      IBKR_OAUTH_ACCESS_TOKEN
      IBKR_OAUTH_ACCESS_TOKEN_SECRET   — base64 ciphertext; decrypted locally
      IBKR_OAUTH_SIGNATURE_KEY_PATH
      IBKR_OAUTH_ENCRYPTION_KEY_PATH
      IBKR_OAUTH_DH_PARAM_PATH
      IBKR_OAUTH_REALM                 — default "limited_poa"

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
        self._realm = os.environ.get("IBKR_OAUTH_REALM", "limited_poa")

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
        """Perform the real IBKR OAuth 1.0a live-session-token exchange.

        Protocol summary
        ----------------
        1. Load DH params (p, g) from the PEM file; generate a random 256-bit
           private value ``a``; compute ``A = pow(g, a, p)``.
        2. Decrypt the access-token secret ciphertext LOCALLY (PKCS1v15) to
           obtain the ``prepend`` bytes (hex-encoded).  These are NEVER sent
           to the server.
        3. Build an RFC 5849 base string for POST /oauth/live_session_token,
           prepend the decrypted prepend hex, then RSA-SHA256 sign the result.
        4. POST to the endpoint with an Authorization header carrying
           ``diffie_hellman_challenge`` (hex of A) and the RSA signature.
           No secrets in the body.
        5. From the response, compute ``shared_secret = pow(B, a, p)``,
           convert to signed bytes, then derive:
           ``lst = base64(HMAC-SHA1(key=signed_bytes, msg=prepend_bytes))``.
        6. Validate: ``HMAC-SHA1(key=decoded_lst, msg=consumer_key).hexdigest()
           == live_session_token_signature``; raise ``LiveSessionTokenError``
           on mismatch.
        """
        from cryptography.hazmat.primitives.serialization import load_pem_parameters  # type: ignore
        from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore

        # --- Step 1: DH parameter loading and private-value generation ------
        dh_pem = open(self._dh_param_path, "rb").read()
        params = load_pem_parameters(dh_pem)
        pn = params.parameter_numbers()
        p: int = pn.p
        g: int = pn.g

        # 256-bit random private value.
        a: int = random.SystemRandom().getrandbits(256)
        A: int = pow(g, a, p)
        dh_challenge_hex: str = hex(A)[2:]  # lowercase, no "0x"

        # --- Step 2: Decrypt access-token secret → prepend (local only) -----
        enc_key = _load_private_key(self._enc_key_path)
        prepend_bytes: bytes = enc_key.decrypt(
            base64.b64decode(self._access_token_secret),
            padding.PKCS1v15(),
        )
        prepend: str = prepend_bytes.hex()

        # --- Step 3 & 4: Build signed Authorization header and POST ---------
        sig_key = _load_private_key(self._sig_key_path)
        lst_url = f"{self.base_url}/oauth/live_session_token"

        auth_header, _nonce, _ts = _lst_authorization_header(
            consumer_key=self._consumer_key,
            access_token=self._access_token,
            dh_challenge_hex=dh_challenge_hex,
            prepend=prepend,
            sig_key=sig_key,
            url=lst_url,
            realm=self._realm,
        )

        resp = self._sess().post(
            lst_url,
            headers={"Authorization": auth_header},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        # --- Step 5: Compute LST from DH response ---------------------------
        dh_response_hex: str = data["diffie_hellman_response"]
        B: int = int(dh_response_hex, 16)
        shared_secret: int = pow(B, a, p)
        shared_secret_bytes: bytes = _int_to_signed_bytes(shared_secret)

        lst: str = base64.b64encode(
            hmac.new(
                key=shared_secret_bytes,
                msg=bytes.fromhex(prepend),
                digestmod=hashlib.sha1,
            ).digest()
        ).decode()

        # --- Step 6: Validate server's LST signature ------------------------
        lst_sig_hex: str = data["live_session_token_signature"]
        expected_sig = hmac.new(
            key=base64.b64decode(lst),
            msg=self._consumer_key.encode("utf-8"),
            digestmod=hashlib.sha1,
        ).hexdigest()
        if expected_sig != lst_sig_hex:
            raise LiveSessionTokenError(
                "Live-session-token validation failed: HMAC-SHA1 of consumer_key "
                f"under derived LST does not match server's live_session_token_signature. "
                f"Expected {expected_sig!r}, got {lst_sig_hex!r}. "
                "Check that the DH params, encryption key, and access-token secret are correct."
            )

        return lst

    def refresh_lst(self) -> None:
        """Force renewal of the live-session-token."""
        self._lst = None

    def _signed_headers(self, method: str, url: str) -> dict[str, str]:
        lst = self._ensure_lst()
        auth = _oauth_authorization_header(
            consumer_key=self._consumer_key,
            access_token=self._access_token,
            lst_b64=lst,
            method=method,
            url=url,
            realm=self._realm,
        )
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

    async def connect_ws(
        self,
        session_token: str,
        *,
        ws_connect: Callable[..., Any] | None = None,
    ) -> "IbkrWsSession":
        """Open an async WebSocket connection and return an ``IbkrWsSession``.

        The session token must be obtained from ``tickle()`` by the caller
        before invoking this method.  The token is sent in the initial
        handshake frame and is never logged.

        Live-account guard note: this client implements **read-only**
        WebSocket subscriptions only (order/trade/market-data events).
        Order placement over WebSocket is intentionally not implemented;
        the live-account guard applies to REST order placement methods only.

        Gateway mode connects to ``wss://localhost:5000/v1/api/ws`` with TLS
        verification disabled (localhost self-signed cert, same constraint as
        GatewayAuth).  OAuth mode connects to
        ``wss://api.ibkr.com/v1/api/ws`` with full TLS verification.

        Args:
            session_token: Session token from a recent ``tickle()`` call.
            ws_connect: Optional injectable async callable returning a context
                manager that yields an object with ``send``, ``recv``, and
                ``close`` coroutines.  Defaults to ``websockets.connect``
                (imported lazily so the REST-only path never imports
                websockets).

        Returns:
            A connected ``IbkrWsSession`` ready for subscriptions.
        """
        if ws_connect is None:
            try:
                from websockets import connect as _ws_connect  # type: ignore
            except ImportError as exc:
                raise NotImplementedError(
                    "connect_ws requires 'websockets>=12'. "
                    "Add it to packages/equities_lane/requirements.txt."
                ) from exc
            ws_connect = _ws_connect

        url = self.ws_url()
        is_localhost = "localhost" in url or "127.0.0.1" in url

        if is_localhost:
            import ssl as _ssl
            ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            raw_ws = await ws_connect(url, ssl=ctx)
        else:
            raw_ws = await ws_connect(url)

        session = IbkrWsSession(raw_ws)
        await session._handshake(session_token)
        return session


# ---------------------------------------------------------------------------
# IbkrWsSession
# ---------------------------------------------------------------------------


class IbkrWsSession:
    """Async WebSocket session for IBKR Client Portal streaming events.

    Obtained via ``IbkrWebClient.connect_ws()``.  Provides read-only
    subscriptions — order placement over WebSocket is intentionally absent.
    No reconnect logic; callers that need reconnect should wrap this class.

    The session token passed to ``IbkrWebClient.connect_ws()`` is consumed
    during the handshake and is not stored on this object.
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def _handshake(self, session_token: str) -> None:
        """Send the session frame required by the IBKR WS protocol."""
        frame = json.dumps({"session": session_token})
        await self._ws.send(frame)

    async def subscribe_orders(self) -> None:
        """Subscribe to live order-status updates (``sor+{}``)."""
        await self._ws.send("sor+{}")

    async def subscribe_trades(self) -> None:
        """Subscribe to trade/fill events (``str+{}``)."""
        await self._ws.send("str+{}")

    async def subscribe_market_data(self, conid: int | str, fields: list[str | int]) -> None:
        """Subscribe to market-data ticks for *conid*.

        Args:
            conid: IBKR contract id.
            fields: List of field codes, e.g. ``[31, 84, 86]``.
        """
        payload = json.dumps({"fields": [str(f) for f in fields]})
        await self._ws.send(f"smd+{conid}+{payload}")

    async def unsubscribe_orders(self) -> None:
        """Unsubscribe from live order-status updates (``uor+{}``)."""
        await self._ws.send("uor+{}")

    async def unsubscribe_trades(self) -> None:
        """Unsubscribe from trade/fill events (``ustr+{}``)."""
        await self._ws.send("ustr+{}")

    async def unsubscribe_market_data(self, conid: int | str) -> None:
        """Unsubscribe from market-data ticks for *conid* (``umd+<conid>+{}``)."""
        await self._ws.send(f"umd+{conid}+{{}}")

    async def ping(self) -> None:
        """Send a ``tic`` heartbeat frame to the server."""
        await self._ws.send("tic")

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Async iterator yielding parsed JSON dicts from the server.

        Non-JSON frames (e.g. raw protocol messages) are yielded as
        ``{"raw": <text>}``.  Server heartbeat frames
        (``{"topic":"system","hb":...}``) are passed through as-is.
        """
        while True:
            try:
                raw = await self._ws.recv()
            except Exception:
                return
            try:
                yield json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                yield {"raw": raw}

    async def close(self) -> None:
        """Close the underlying WebSocket connection."""
        await self._ws.close()
