from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from data_system.rithmic_trial.connector.rtrader_bridge import (
    RTraderBridgeConnector,
    _parse_log_line,
)
from data_system.rithmic_trial.config import TrialConfig
from data_system.rithmic_trial.pipeline import cmd_capture
from data_system.rithmic_trial.unattended import run_unattended

import importlib.util
import sys as _sys

_DOWNLOAD_SCRIPT = _REPO / "scripts" / "rithmic_download_test.py"
_spec = importlib.util.spec_from_file_location("rithmic_download_test", _DOWNLOAD_SCRIPT)
assert _spec and _spec.loader, "rithmic_download_test.py not found"
_download_mod = importlib.util.module_from_spec(_spec)
_sys.modules["rithmic_download_test"] = _download_mod
_spec.loader.exec_module(_download_mod)


class _AsyncNoop:
    """Async no-op stand-in for connect()/disconnect() in tests."""

    async def __call__(self, *args, **kwargs):  # noqa: D401
        return None


class _AsyncFn:
    """Async callable that returns a fixed value, for list_system_names()."""

    def __init__(self, value):
        self.value = value

    async def __call__(self, *args, **kwargs):
        return self.value


@pytest.fixture
def trial_cfg(tmp_path: Path) -> TrialConfig:
    return TrialConfig(
        enabled=True,
        connector="rtrader",
        symbol="MES",
        exchange="CME",
        contract="",
        capture_environment="paper_or_trial",
        source="rithmic_trial",
        schema_version="normalized_v1",
        repo_root=_REPO,
        raw_root=tmp_path / "raw",
        normalized_root=tmp_path / "norm",
        replay_root=tmp_path / "replay",
        reports_root=tmp_path / "reports",
        rtrader={"wine_prefix": "/root/.wine-rtrader"},
    )


def test_parse_log_line_rejects_bad_price(trial_cfg: TrialConfig) -> None:
    assert _parse_log_line("TRADE MES .", trial_cfg) is None


def test_rtrader_bridge_refuses_windows(trial_cfg: TrialConfig) -> None:
    bridge = RTraderBridgeConnector(trial_cfg)
    with patch("data_system.rithmic_trial.connector.rtrader_bridge.is_windows", return_value=True):
        with pytest.raises(RuntimeError, match="CHI404"):
            bridge.connect()


def test_capture_refuses_windows_rtrader() -> None:
    cfg_path = _REPO / "data_system/config/rithmic_trial.yaml"
    args = type(
        "Args",
        (),
        {
            "config": str(cfg_path),
            "date": None,
            "symbol": None,
            "duration_sec": 1,
            "poll_interval_sec": 0.01,
            "force": True,
        },
    )()
    with patch.dict("os.environ", {"RITHMIC_TRIAL_CONNECTOR": "rtrader"}, clear=False):
        with patch("data_system.rithmic_trial.pipeline.is_windows", return_value=True):
            assert cmd_capture(args) == 1


def test_unattended_refuses_windows(tmp_path: Path) -> None:
    cfg_path = tmp_path / "trial.yaml"
    cfg_path.write_text("enabled: true\nconnector: rtrader\n", encoding="utf-8")
    with patch("data_system.rithmic_trial.unattended.is_windows", return_value=True):
        assert run_unattended(cfg_path, start_rtrader=False) == 1


def test_rithmic_download_test_refuses_windows() -> None:
    with patch("data_system.rithmic_trial.platform.is_windows", return_value=True):
        with patch("rithmic_download_test.is_windows", return_value=True):
            assert _download_mod.main([]) == 1


def test_rithmic_download_test_probe_only_path() -> None:
    """--probe-only runs through connect -> list_system_names -> disconnect without data."""
    fake_client = type(
        "C",
        (),
        {
            "connected": True,
            "connect": _AsyncNoop(),
            "disconnect": _AsyncNoop(),
            "list_system_names": _AsyncFn(["Rithmic Test", "Rithmic Paper Trading"]),
        },
    )
    with patch("rithmic_download_test.is_windows", return_value=False):
        with patch.object(_download_mod, "RithmicHistoricalClient", lambda *a, **kw: fake_client):
            with patch.dict(
                "os.environ",
                {
                    "RITHMIC_USER": "u",
                    "RITHMIC_PASSWORD": "p",
                    "RITHMIC_SYSTEM_NAME": "Rithmic Test",
                },
                clear=False,
            ):
                rc = _download_mod.main(["--probe-only"])
    assert rc == 0


def test_rithmic_download_test_probe_only_invalid_system_name() -> None:
    """If the configured system_name is not in the list, probe-only returns 1."""
    fake_client = type(
        "C",
        (),
        {
            "connected": True,
            "connect": _AsyncNoop(),
            "disconnect": _AsyncNoop(),
            "list_system_names": _AsyncFn(["Rithmic Test"]),
        },
    )
    with patch("rithmic_download_test.is_windows", return_value=False):
        with patch.object(_download_mod, "RithmicHistoricalClient", lambda *a, **kw: fake_client):
            with patch.dict(
                "os.environ",
                {
                    "RITHMIC_USER": "u",
                    "RITHMIC_PASSWORD": "p",
                    "RITHMIC_SYSTEM_NAME": "WRONG_NAME",
                },
                clear=False,
            ):
                rc = _download_mod.main(["--probe-only"])
    assert rc == 1
