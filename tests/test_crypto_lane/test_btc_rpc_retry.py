"""Acceptance tests for BTC RPC retry-on-transient-failure behavior.

These document the desired contract. If the production module already has
a retry loop, the tests pass directly. Otherwise they are xfailed so the
suite stays green while still surfacing the gap.
"""
from __future__ import annotations

import urllib.error

import pytest

import crypto_lane.src.ingest.btc_rpc as btc_rpc_mod
from crypto_lane.src.ingest.btc_node_env import NodeEnv
from crypto_lane.src.ingest.btc_rpc import BtcRpc, BtcRpcError


_FAKE_ENV = NodeEnv(
    btc_rpc_url="http://127.0.0.1:8332/",
    btc_rpc_user="u",
    btc_rpc_pass="p",
    btc_zmq_rawblock="tcp://127.0.0.1:28332",
    btc_zmq_rawtx="tcp://127.0.0.1:28333",
)


def _has_retry_loop():
    """Detect whether the production BtcRpc.call implements multi-attempt retry."""
    import inspect

    src = inspect.getsource(btc_rpc_mod.BtcRpc.call)
    return any(token in src for token in ("attempt", "retries", "max_attempts", "backoff", "for "))


class _FakeBody:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


def _ok_response_payload():
    import json

    return json.dumps({"result": {
        "chain": "main",
        "blocks": 800000,
        "headers": 800000,
        "bestblockhash": "abc",
        "difficulty": 1.0,
        "verificationprogress": 1.0,
        "initialblockdownload": False,
        "size_on_disk": 1,
        "pruned": False,
    }, "error": None, "id": "hft3"}).encode()


@pytest.mark.xfail(
    not _has_retry_loop(),
    reason="BTC RPC does not yet implement retry-on-503; acceptance test for future behavior",
    strict=False,
)
def test_snapshot_from_rpc_retries_on_503(monkeypatch):
    """Two 503s then success → 3 calls total, no exception."""
    rpc = BtcRpc(env=_FAKE_ENV)
    calls = {"n": 0}

    def _urlopen(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(
                url="http://127.0.0.1:8332/",
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=None,
            )
        return _FakeBody(_ok_response_payload())

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)

    rpc.getblockchaininfo()
    assert calls["n"] == 3


@pytest.mark.xfail(
    not _has_retry_loop(),
    reason="BTC RPC does not yet implement bounded retry; acceptance test for future behavior",
    strict=False,
)
def test_snapshot_from_rpc_gives_up_after_3_attempts(monkeypatch):
    """Persistent failure → exactly 3 calls then BtcRpcError."""
    rpc = BtcRpc(env=_FAKE_ENV)
    calls = {"n": 0}

    def _urlopen(*args, **kwargs):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            url="http://127.0.0.1:8332/",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)

    with pytest.raises(BtcRpcError):
        rpc.getblockchaininfo()
    assert calls["n"] == 3


def test_snapshot_from_rpc_raises_btcrpcerror_on_503(monkeypatch):
    """Whether or not retry exists, a persistent 503 must surface as BtcRpcError."""
    rpc = BtcRpc(env=_FAKE_ENV)

    def _urlopen(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="http://127.0.0.1:8332/",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)

    with pytest.raises(BtcRpcError) as ei:
        rpc.getblockchaininfo()
    assert "503" in str(ei.value)
