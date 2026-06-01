"""Tests for crypto_lane.src.ingest.btc_node_env."""
from __future__ import annotations

import os

import pytest

from crypto_lane.src.ingest.btc_node_env import NodeEnv, NodeEnvError


_REQUIRED = ("BTC_RPC_URL", "BTC_RPC_USER", "BTC_RPC_PASS", "BTC_ZMQ_RAWBLOCK", "BTC_ZMQ_RAWTX")


def _write_env(path, mapping, *, mode_0600=True):
    path.write_text(
        "".join(f"{k}={v}\n" for k, v in mapping.items()), encoding="utf-8"
    )
    if mode_0600 and os.name != "nt":
        os.chmod(path, 0o600)


def test_load_env_with_all_required_keys(tmp_path):
    """A valid .btc-node.env with all required keys should load cleanly."""
    p = tmp_path / ".btc-node.env"
    _write_env(
        p,
        {
            "BTC_RPC_URL": "http://127.0.0.1:8332/",
            "BTC_RPC_USER": "alice",
            "BTC_RPC_PASS": "wonderland",
            "BTC_ZMQ_RAWBLOCK": "tcp://127.0.0.1:28332",
            "BTC_ZMQ_RAWTX": "tcp://127.0.0.1:28333",
        },
    )
    env = NodeEnv.load(p)
    assert env.btc_rpc_url == "http://127.0.0.1:8332/"
    assert env.btc_rpc_user == "alice"
    assert env.btc_rpc_pass == "wonderland"
    assert env.btc_zmq_rawblock == "tcp://127.0.0.1:28332"
    assert env.btc_zmq_rawtx == "tcp://127.0.0.1:28333"


def test_load_env_missing_required_key_raises(tmp_path):
    """Omit one required key; expect NodeEnvError naming the missing key."""
    p = tmp_path / ".btc-node.env"
    _write_env(
        p,
        {
            "BTC_RPC_URL": "http://127.0.0.1:8332/",
            "BTC_RPC_USER": "u",
            "BTC_RPC_PASS": "p",
            "BTC_ZMQ_RAWBLOCK": "tcp://127.0.0.1:28332",
            # BTC_ZMQ_RAWTX intentionally omitted
        },
    )
    with pytest.raises(NodeEnvError) as ei:
        NodeEnv.load(p)
    assert "BTC_ZMQ_RAWTX" in str(ei.value)


def test_load_env_missing_file_raises_clear_error(tmp_path):
    """Passing an explicit nonexistent path must raise NodeEnvError, not raw IO."""
    missing = tmp_path / "absent.env"
    with pytest.raises(NodeEnvError) as ei:
        NodeEnv.load(missing)
    assert ".btc-node.env" in str(ei.value)


def test_load_env_ignores_comments_and_blank_lines(tmp_path):
    """Comments, blank lines, and bare lines without '=' should not pollute the dict."""
    p = tmp_path / ".btc-node.env"
    body = (
        "# header comment line\n"
        "\n"
        "BTC_RPC_URL=http://127.0.0.1:8332/\n"
        "  \n"
        "# another comment\n"
        "BTC_RPC_USER=u\n"
        "BTC_RPC_PASS=p\n"
        "GARBAGE_NO_EQUALS_LINE\n"
        "BTC_ZMQ_RAWBLOCK=tcp://127.0.0.1:28332\n"
        "BTC_ZMQ_RAWTX=tcp://127.0.0.1:28333\n"
    )
    p.write_text(body, encoding="utf-8")
    if os.name != "nt":
        os.chmod(p, 0o600)
    env = NodeEnv.load(p)
    assert env.btc_rpc_url == "http://127.0.0.1:8332/"
    assert env.btc_zmq_rawtx == "tcp://127.0.0.1:28333"
