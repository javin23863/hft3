from __future__ import annotations

import os
import time
from pathlib import Path

import desk_env


def test_resolve_desk_keys_prefers_crypto_keys_env(tmp_path: Path, monkeypatch):
    keys = tmp_path / "desk.env"
    keys.write_text("AWS_ACCESS_KEY_ID=from-desk\n", encoding="utf-8")
    monkeypatch.delenv("CRYPTO_KEYS_ENV", raising=False)
    monkeypatch.delenv("MACRO_KEYS_ENV", raising=False)
    monkeypatch.delenv("QXL_KEYS_ENV", raising=False)
    monkeypatch.setenv("CRYPTO_KEYS_ENV", str(keys))
    assert desk_env.resolve_desk_keys_path() == keys


def test_load_sibling_quantx_pointer(tmp_path: Path, monkeypatch):
    repo = tmp_path / "hft3"
    qx = tmp_path / "quant-x"
    repo.mkdir()
    qx.mkdir()
    keys = tmp_path / "vault.env"
    keys.write_text("AWS_ACCESS_KEY_ID=sib\n", encoding="utf-8")
    (qx / ".env").write_text(f"QXL_KEYS_ENV={keys}\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("QXL_KEYS_ENV", raising=False)
    loaded = desk_env.load_sibling_pointer_envs(repo)
    assert loaded == [qx / ".env"]
    assert os.environ["QXL_KEYS_ENV"] == str(keys)
    assert desk_env.resolve_desk_keys_path() == keys


def test_resolve_btc_node_env_prefers_repo_root(tmp_path: Path):
    repo = tmp_path / "hft3"
    repo.mkdir()
    env_file = repo / ".btc-node.env"
    env_file.write_text("BTC_RPC_URL=http://127.0.0.1:8332\n", encoding="utf-8")
    assert desk_env.resolve_btc_node_env_path(repo) == env_file


def test_resolve_btc_node_status_prefers_chi404_cache(tmp_path: Path):
    repo = tmp_path / "hft3"
    repo.mkdir()
    cache_dir = repo / "runtime/cache/node_hosts"
    cache_dir.mkdir(parents=True)
    cached = cache_dir / "chi404-btc-node-status.json"
    cached.write_text('{"synced": true, "source": "chi404"}', encoding="utf-8")
    assert desk_env.resolve_btc_node_status_path(repo) == cached


def test_resolve_btc_node_status_sibling_cae(tmp_path: Path):
    repo = tmp_path / "hft3"
    cae = tmp_path / "crypto-alpha-engine"
    status_dir = cae / "runtime" / "state"
    status_dir.mkdir(parents=True)
    status = status_dir / "btc-node-status.json"
    status.write_text('{"synced": true}', encoding="utf-8")
    repo.mkdir()
    assert desk_env.resolve_btc_node_status_path(repo) == status.resolve()
    result = desk_env.read_btc_node_status(repo)
    assert result["synced"] is True
    assert "status_age_hours" in result
    assert result.get("stale") is not True


def test_read_btc_node_status_stale_when_old(tmp_path: Path):
    repo = tmp_path / "hft3"
    cae = tmp_path / "crypto-alpha-engine"
    status_dir = cae / "runtime" / "state"
    status_dir.mkdir(parents=True)
    status = status_dir / "btc-node-status.json"
    status.write_text('{"synced": true}', encoding="utf-8")
    repo.mkdir()
    old_mtime = time.time() - (25 * 3600)
    os.utime(status, (old_mtime, old_mtime))
    result = desk_env.read_btc_node_status(repo, max_age_hours=24)
    assert result["synced"] is True
    assert result["stale"] is True
    assert result["status_age_hours"] >= 24
