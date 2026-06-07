from __future__ import annotations

import os
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
