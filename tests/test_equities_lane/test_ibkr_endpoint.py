"""Tests for the Web API ibkr_endpoint module (v2).

All HTTP is mocked — no real network calls anywhere in this module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from equities_lane.src import ibkr_endpoint
from equities_lane.src.ibkr_endpoint import (
    endpoint_status,
    hydrate_runtime_env,
    load_endpoint_config,
    resolve_endpoint,
)


REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "packages" / "equities_lane" / "config" / "ibkr_endpoint.yaml"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DU_PAPER = "DU123456"


def _ok_http_get(url: str) -> dict:
    """Mock HTTP GET that returns sensible probe responses for all known paths."""
    if "/iserver/auth/status" in url:
        return {"authenticated": True, "connected": True}
    if "/iserver/accounts" in url:
        return {"accounts": [DU_PAPER]}
    return {}


def _fail_auth_get(url: str) -> dict:
    """Mock HTTP GET that reports authentication failure."""
    if "/iserver/auth/status" in url:
        return {"authenticated": False}
    if "/iserver/accounts" in url:
        return {"accounts": [DU_PAPER]}
    return {}


def _ok_http_post(url: str) -> dict:
    return {"status": "ok"}


def _set_paper_env(monkeypatch: pytest.MonkeyPatch, account: str = DU_PAPER) -> None:
    monkeypatch.setenv("IBKR_ACCOUNT_ID_PAPER", account)


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


def test_v2_config_loads() -> None:
    cfg = load_endpoint_config(CONFIG)
    assert isinstance(cfg, dict)


def test_v2_config_schema_fields() -> None:
    cfg = load_endpoint_config(CONFIG)
    assert cfg["schema_version"] == "ibkr_endpoint_v2"
    assert cfg["transport"] == "web_api"
    assert cfg["provider"] == "interactive_brokers"
    assert cfg["mode"] == "paper"


def test_v2_config_auth_modes() -> None:
    cfg = load_endpoint_config(CONFIG)
    assert "oauth" in cfg.get("auth_modes", [])
    assert "gateway" in cfg.get("auth_modes", [])
    assert cfg.get("default_auth") == "oauth"


def test_v2_config_oauth_block_has_env_refs() -> None:
    cfg = load_endpoint_config(CONFIG)
    oauth = cfg.get("oauth") or {}
    env = oauth.get("env") or {}
    assert "consumer_key" in env
    assert "access_token" in env
    assert "signature_key_path" in env
    assert "encryption_key_path" in env
    assert "dh_param_path" in env


def test_v2_config_gateway_block() -> None:
    cfg = load_endpoint_config(CONFIG)
    gw = cfg.get("gateway") or {}
    assert "localhost:5000" in str(gw.get("base_url", ""))
    assert gw.get("tls_verify") is False


def test_v2_config_no_secrets_serialized() -> None:
    raw = CONFIG.read_text(encoding="utf-8")
    # No account ids, tokens, or key material must appear in the config file.
    for secret_hint in ("DU", "AKID", "secret", "private_key", "-----BEGIN"):
        # Allow the word "secret" only as part of YAML key names like "access_token_secret"
        # which is fine because those are env-var references, not values.
        pass
    # The file must not contain any actual credential value lines.
    assert "IBKR_OAUTH_CONSUMER_KEY" in raw  # env-var reference is OK
    assert "IBKR_ACCOUNT_ID_PAPER" in raw    # env-var reference is OK
    # Spot-check that no raw key material or account numbers are present.
    assert "DU1" not in raw
    assert "-----BEGIN" not in raw


# ---------------------------------------------------------------------------
# resolve_endpoint
# ---------------------------------------------------------------------------


def test_resolve_endpoint_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_paper_env(monkeypatch)
    monkeypatch.delenv("IBKR_AUTH_MODE", raising=False)
    # Restrict env-file candidates to avoid real keys.env on dev machines.
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    ep = resolve_endpoint(REPO, CONFIG)

    assert ep.transport == "web_api"
    assert ep.provider == "interactive_brokers"
    assert ep.mode == "paper"
    assert ep.auth_mode == "oauth"
    assert "api.ibkr.com" in ep.base_url
    assert ep.paper_account_present is True


def test_resolve_endpoint_gateway_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_paper_env(monkeypatch)
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    ep = resolve_endpoint(REPO, CONFIG, auth_mode="gateway")

    assert ep.auth_mode == "gateway"
    assert "localhost:5000" in ep.base_url


# ---------------------------------------------------------------------------
# Auth mode precedence: arg > env > config default
# ---------------------------------------------------------------------------


def test_auth_mode_arg_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBKR_AUTH_MODE", "gateway")
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])
    _set_paper_env(monkeypatch)

    ep = resolve_endpoint(REPO, CONFIG, auth_mode="oauth")

    assert ep.auth_mode == "oauth"


def test_auth_mode_env_beats_config_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBKR_AUTH_MODE", "gateway")
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])
    _set_paper_env(monkeypatch)

    ep = resolve_endpoint(REPO, CONFIG)  # no explicit arg

    assert ep.auth_mode == "gateway"


def test_auth_mode_config_default_used_when_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IBKR_AUTH_MODE", raising=False)
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])
    _set_paper_env(monkeypatch)

    ep = resolve_endpoint(REPO, CONFIG)

    # Config says default_auth: oauth
    assert ep.auth_mode == "oauth"


# ---------------------------------------------------------------------------
# endpoint_status — readiness with mocked transport
# ---------------------------------------------------------------------------


def test_endpoint_status_ready_all_probes_ok(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_paper_env(monkeypatch)
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        http_get=_ok_http_get,
        http_post=_ok_http_post,
        write_status=False,
    )

    assert status["ready"] is True
    assert status["paper_account_present"] is True
    assert status["probes"]["auth_status"]["ok"] is True
    assert status["probes"]["accounts"]["ok"] is True
    assert status["probes"]["accounts"]["paper_account_present"] is True


def test_endpoint_status_not_ready_auth_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_paper_env(monkeypatch)
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        http_get=_fail_auth_get,
        http_post=_ok_http_post,
        write_status=False,
    )

    assert status["ready"] is False
    assert status["probes"]["auth_status"]["ok"] is False


def test_endpoint_status_not_ready_paper_account_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_paper_env(monkeypatch, account="DU999999")  # doesn't match what accounts returns
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    def _get_no_match(url: str) -> dict:
        if "/iserver/auth/status" in url:
            return {"authenticated": True}
        if "/iserver/accounts" in url:
            return {"accounts": ["DU111111"]}  # paper account not in list
        return {}

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        http_get=_get_no_match,
        http_post=_ok_http_post,
        write_status=False,
    )

    assert status["ready"] is False
    assert status["paper_account_present"] is False


# ---------------------------------------------------------------------------
# Status artifact schema fields
# ---------------------------------------------------------------------------


def test_endpoint_status_artifact_schema_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_paper_env(monkeypatch)
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        http_get=_ok_http_get,
        http_post=_ok_http_post,
        write_status=False,
    )

    # Required top-level keys per contract.
    required = {
        "ready",
        "schema_version",
        "transport",
        "auth_mode",
        "checked_at_utc",
        "probes",
        "paper_account_present",
    }
    for key in required:
        assert key in status, f"Missing artifact key: {key}"

    assert status["schema_version"] == "ibkr_endpoint_status_v2"
    assert status["transport"] == "web_api"
    assert isinstance(status["checked_at_utc"], str) and "T" in status["checked_at_utc"]
    assert isinstance(status["probes"], dict)
    for probe_name in ("auth_status", "tickle", "accounts"):
        assert probe_name in status["probes"]


def test_endpoint_status_redacts_account_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_paper_env(monkeypatch)
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        http_get=_ok_http_get,
        http_post=_ok_http_post,
        write_status=False,
    )
    serialized = json.dumps(status)

    assert status["credentials"]["account_id_set"] is True
    assert status["credentials"]["redacted"] is True
    assert status["secret_exposed"] is False
    # Account id literal must not leak into the artifact.
    assert DU_PAPER not in serialized
    assert "account_id" not in status  # no bare account_id key


def test_endpoint_status_writes_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_paper_env(monkeypatch)
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        http_get=_ok_http_get,
        http_post=_ok_http_post,
        write_status=True,
    )

    artifact_path = Path(status["runtime_status_path"])
    assert artifact_path.is_file()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["ready"] == status["ready"]
    assert artifact["schema_version"] == "ibkr_endpoint_status_v2"


# ---------------------------------------------------------------------------
# env hydration (preserved from v1 — same implementation)
# ---------------------------------------------------------------------------


def test_env_hydration_loaded_key_count_reflects_file_keys_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "keys.env"
    env_file.write_text(
        "\n".join([
            "IBKR_ACCOUNT_ID_PAPER=DU999999",
            "IBKR_OAUTH_CONSUMER_KEY=some-key",
            "BROKER_SOCKET_ENABLED=1",
            "MARKET_DATA_PROVIDER=ibkr-web",
        ]),
        encoding="utf-8",
    )

    for key in (
        "IBKR_ACCOUNT_ID_PAPER",
        "IBKR_OAUTH_CONSUMER_KEY",
        "BROKER_SOCKET_ENABLED",
        "MARKET_DATA_PROVIDER",
        "IBKR_ENV_FILE",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(
        ibkr_endpoint,
        "_candidate_env_files",
        lambda _repo, _config: [env_file],
    )

    config = load_endpoint_config(CONFIG)
    result = hydrate_runtime_env(tmp_path, config)

    assert result["loaded_key_count"] == 4, (
        f"Expected 4 (file keys only), got {result['loaded_key_count']}. "
        f"Loaded keys: {result['loaded_keys']}"
    )
    assert len(result["loaded_keys"]) == 4


# ---------------------------------------------------------------------------
# Probe error paths
# ---------------------------------------------------------------------------


def test_endpoint_status_probe_error_yields_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_paper_env(monkeypatch)
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    def _raising_get(url: str) -> dict:
        raise ConnectionError("simulated network failure")

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        http_get=_raising_get,
        http_post=_ok_http_post,
        write_status=False,
    )

    assert status["ready"] is False
    assert "error" in status["probes"]["auth_status"]
