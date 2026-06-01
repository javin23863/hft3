"""Tests for crypto_lane.src.config.env_loader."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from crypto_lane.src.config import env_loader as env_mod


@pytest.fixture(autouse=True)
def _isolate_loaded_files_cache(monkeypatch):
    """Replace the module-level _LOADED_FILES cache so tests don't leak state.

    The env_loader caches discovered files in a module-global list. We swap in
    a fresh list per-test to force ensure_crypto_env to re-run.
    """
    monkeypatch.setattr(env_mod, "_LOADED_FILES", [])


def test_redacted_env_report_never_includes_key_values(monkeypatch):
    """The report must surface only set/missing labels, never raw secret values."""
    monkeypatch.setenv("HFT3_CRYPTO_B2_KEY_ID", "supersecretvalue_aaa")
    monkeypatch.setenv("HFT3_CRYPTO_KRAKEN_API_KEY", "shhh_kraken_token")
    monkeypatch.setenv("BTC_RPC_PASS", "rpc_password_secret")

    report = env_mod.redacted_env_report()

    flat = repr(report)
    assert "supersecretvalue_aaa" not in flat
    assert "shhh_kraken_token" not in flat
    assert "rpc_password_secret" not in flat

    keys = report["keys"]
    assert keys["HFT3_CRYPTO_B2_KEY_ID"] == "set"
    assert keys["HFT3_CRYPTO_KRAKEN_API_KEY"] == "set"
    assert keys["BTC_RPC_PASS"] == "set"
    for value in keys.values():
        assert value in ("set", "missing")


def test_require_env_raises_on_missing_key(monkeypatch):
    """require_env should raise a clear RuntimeError citing the missing keys."""
    monkeypatch.delenv("HFT3_NONEXISTENT_KEY_XYZ", raising=False)
    with pytest.raises(RuntimeError) as ei:
        env_mod.require_env("HFT3_NONEXISTENT_KEY_XYZ")
    msg = str(ei.value)
    assert "HFT3_NONEXISTENT_KEY_XYZ" in msg
    assert "Missing env keys" in msg or "missing" in msg.lower()


def test_kraken_aliases_resolve(monkeypatch, tmp_path):
    """Setting KRAKEN_API_KEY should populate HFT3_CRYPTO_KRAKEN_API_KEY via alias.

    Point the loader at an empty tmp_path so the real repo .env (which may
    define HFT3_CRYPTO_KRAKEN_API_KEY) cannot mask the alias under test.
    """
    monkeypatch.setattr(env_mod, "repo_root_from_lane", lambda: tmp_path)
    monkeypatch.setattr(env_mod, "repo_env_paths", lambda: [])
    monkeypatch.delenv("HFT3_CRYPTO_KRAKEN_API_KEY", raising=False)
    monkeypatch.setenv("KRAKEN_API_KEY", "kraken_value_123")

    resolved = env_mod.require_env("HFT3_CRYPTO_KRAKEN_API_KEY")
    assert resolved["HFT3_CRYPTO_KRAKEN_API_KEY"] == "kraken_value_123"


def test_kraken_canonical_wins_over_alias(monkeypatch, tmp_path):
    """If both canonical and alias are set, canonical must not be overwritten."""
    monkeypatch.setattr(env_mod, "repo_root_from_lane", lambda: tmp_path)
    monkeypatch.setattr(env_mod, "repo_env_paths", lambda: [])
    monkeypatch.setenv("HFT3_CRYPTO_KRAKEN_API_KEY", "canonical_wins")
    monkeypatch.setenv("KRAKEN_API_KEY", "alias_loses")

    resolved = env_mod.require_env("HFT3_CRYPTO_KRAKEN_API_KEY")
    assert resolved["HFT3_CRYPTO_KRAKEN_API_KEY"] == "canonical_wins"


def test_dotenv_does_not_override_preset_env(monkeypatch, tmp_path):
    """A pre-set env var must NOT be overwritten by .env contents on load."""
    var_name = "HFT3_CRYPTO_PRESET_TEST_VAR"
    monkeypatch.setenv(var_name, "preset_value")

    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(f"{var_name}=dotenv_value\n", encoding="utf-8")

    # Force the loader's repo_root to point at tmp_path
    monkeypatch.setattr(env_mod, "repo_root_from_lane", lambda: tmp_path)
    monkeypatch.setattr(env_mod, "repo_env_paths", lambda: [dotenv_file])

    env_mod.ensure_crypto_env()

    assert os.environ[var_name] == "preset_value"


def test_plain_env_loader_skips_comments_and_blanks(monkeypatch, tmp_path):
    """Verify the fallback parser ignores comments and blank lines."""
    var_name = "HFT3_CRYPTO_PLAIN_TEST_VAR"
    monkeypatch.delenv(var_name, raising=False)
    monkeypatch.delenv("HFT3_CRYPTO_ANOTHER_VAR", raising=False)

    env_file = tmp_path / "custom.env"
    env_file.write_text(
        "# header comment\n"
        "\n"
        f"{var_name}=ok_value\n"
        "# another comment\n"
        "  \n"
        "HFT3_CRYPTO_ANOTHER_VAR=also_ok\n",
        encoding="utf-8",
    )

    env_mod._load_plain_env(env_file)

    assert os.environ[var_name] == "ok_value"
    assert os.environ["HFT3_CRYPTO_ANOTHER_VAR"] == "also_ok"
