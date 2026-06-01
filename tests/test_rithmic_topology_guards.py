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
    cfg_path = _REPO / "packages" / "data_system" / "config" / "rithmic_trial.yaml"
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
