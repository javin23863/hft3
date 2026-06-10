"""Tests for the measured_rtt_ms field in the endpoint status artifact (Finding 2a).

All HTTP is mocked — no real network calls.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from equities_lane.src import ibkr_endpoint
from equities_lane.src.ibkr_endpoint import endpoint_status


REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "packages" / "equities_lane" / "config" / "ibkr_endpoint.yaml"
DU_PAPER = "DU123456"


def _ok_http_get(url: str) -> dict:
    if "/iserver/auth/status" in url:
        return {"authenticated": True, "connected": True}
    if "/iserver/accounts" in url:
        return {"accounts": [DU_PAPER]}
    return {}


def _ok_http_post(url: str) -> dict:
    return {"status": "ok"}


def _set_paper_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBKR_ACCOUNT_ID_PAPER", DU_PAPER)


# ---------------------------------------------------------------------------
# measured_rtt_ms field
# ---------------------------------------------------------------------------


def test_measured_rtt_ms_present_when_probes_succeed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When all probes complete, measured_rtt_ms is a non-negative number."""
    _set_paper_env(monkeypatch)
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        http_get=_ok_http_get,
        http_post=_ok_http_post,
        write_status=False,
    )

    assert "measured_rtt_ms" in status
    rtt = status["measured_rtt_ms"]
    assert isinstance(rtt, (int, float)), f"measured_rtt_ms should be numeric, got {type(rtt)}"
    assert rtt >= 0.0, f"measured_rtt_ms should be non-negative, got {rtt}"


def test_measured_rtt_ms_written_to_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """measured_rtt_ms is persisted to the status artifact JSON file."""
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
    assert "measured_rtt_ms" in artifact
    assert isinstance(artifact["measured_rtt_ms"], (int, float))


def test_measured_rtt_ms_null_when_all_probes_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When all probes raise, measured_rtt_ms is null (None serialises as null)."""
    _set_paper_env(monkeypatch)
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    def _raising(url: str) -> dict:
        raise ConnectionError("simulated failure")

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        http_get=_raising,
        http_post=_raising,
        write_status=False,
    )

    assert "measured_rtt_ms" in status
    assert status["measured_rtt_ms"] is None


def test_measured_rtt_ms_schema_version_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Adding measured_rtt_ms must not change the schema_version string."""
    _set_paper_env(monkeypatch)
    monkeypatch.setattr(ibkr_endpoint, "_candidate_env_files", lambda _r, _c: [])

    status = endpoint_status(
        tmp_path,
        config_path=CONFIG,
        http_get=_ok_http_get,
        http_post=_ok_http_post,
        write_status=False,
    )

    assert status["schema_version"] == "ibkr_endpoint_status_v2"
