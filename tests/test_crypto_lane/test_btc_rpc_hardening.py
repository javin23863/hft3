"""BTC RPC hardening tests (Stream 2)."""
from __future__ import annotations

import urllib.error
from dataclasses import fields

from crypto_lane.src.ingest.btc_node_env import NodeEnv
from crypto_lane.src.ingest.btc_rpc import BtcRpc, BtcRpcError, ChainInfo


_FAKE_ENV = NodeEnv(
    btc_rpc_url="http://127.0.0.1:8332/",
    btc_rpc_user="u",
    btc_rpc_pass="p",
    btc_zmq_rawblock="tcp://127.0.0.1:28332",
    btc_zmq_rawtx="tcp://127.0.0.1:28333",
)


def test_median_time_removed_from_chain_info():
    field_names = {f.name for f in fields(ChainInfo)}
    assert "median_time" not in field_names


def test_getblockchaininfo_raises_on_401(monkeypatch):
    rpc = BtcRpc(env=_FAKE_ENV)

    def _raise_http(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="http://127.0.0.1:8332/",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", _raise_http)
    try:
        rpc.getblockchaininfo()
    except BtcRpcError as exc:
        assert "HTTP 401" in str(exc)
        return
    raise AssertionError("expected BtcRpcError")


def test_getblockchaininfo_raises_on_url_error(monkeypatch):
    rpc = BtcRpc(env=_FAKE_ENV)

    def _raise_url(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _raise_url)
    try:
        rpc.getblockchaininfo()
    except BtcRpcError as exc:
        msg = str(exc).lower()
        assert "url error" in msg or "transport" in msg
        return
    raise AssertionError("expected BtcRpcError")
