"""R|Trader bridge ingests .cur.txt logs from VM SMB watch."""
from __future__ import annotations

from pathlib import Path

import pytest

from data_system.rithmic_trial.config import TrialConfig
from data_system.rithmic_trial.connector.rtrader_bridge import RTraderBridgeConnector


@pytest.fixture
def trial_cfg(tmp_path: Path) -> TrialConfig:
    return TrialConfig(
        enabled=True,
        connector="rtrader",
        symbol="MES",
        exchange="CME",
        contract="",
        capture_environment="external_or_trial",
        source="rithmic_trial",
        schema_version="normalized_v1",
        repo_root=tmp_path,
        raw_root=tmp_path / "raw",
        normalized_root=tmp_path / "norm",
        replay_root=tmp_path / "replay",
        reports_root=tmp_path / "reports",
        rtrader={"watch_dirs": []},
    )


def test_rtrader_bridge_ingests_cur_txt(trial_cfg: TrialConfig, tmp_path: Path) -> None:
    watch = tmp_path / "watch"
    watch.mkdir()
    log = watch / "Rithmic Trader Pro.cur.txt"
    log.write_text("TRADE MES 5123.25\n", encoding="utf-8")

    trial_cfg.rtrader["watch_dirs"] = [str(watch)]
    conn = RTraderBridgeConnector(trial_cfg)
    conn.connect()
    events = conn.poll_events()
    assert events
    assert events[0]["event_type"] == "trade"
    assert events[0]["symbol"] == "MES"


def test_rtrader_bridge_ingests_comma_log(tmp_path: Path) -> None:
    from data_system.rithmic_trial.config import TrialConfig
    from data_system.rithmic_trial.connector.rtrader_bridge import RTraderBridgeConnector

    cfg = TrialConfig(
        enabled=True,
        connector="rtrader",
        symbol="MES",
        exchange="CME",
        contract="",
        capture_environment="external_or_trial",
        source="rithmic_trial",
        schema_version="normalized_v1",
        repo_root=tmp_path,
        raw_root=tmp_path / "raw",
        normalized_root=tmp_path / "norm",
        replay_root=tmp_path / "replay",
        reports_root=tmp_path / "reports",
        rtrader={"watch_dirs": []},
    )
    watch = tmp_path / "watch"
    watch.mkdir()
    (watch / "rithmic_test.log").write_text(
        "2026-05-29 21:30:00.000000,Trade,MES,5000.00,1\n", encoding="utf-8"
    )
    cfg.rtrader["watch_dirs"] = [str(watch)]
    conn = RTraderBridgeConnector(cfg)
    conn.connect()
    events = conn.poll_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "trade"
    assert events[0]["price"] == 5000.0
    assert events[0].get("exchange_timestamp")


def test_rtrader_bridge_skips_trace_noise(trial_cfg: TrialConfig, tmp_path: Path) -> None:
    watch = tmp_path / "watch"
    watch.mkdir()
    log = watch / "Rithmic Trader Pro.cur.txt"
    log.write_text(
        "ritpz04063.04.rithmic.com connecting\nTRADE MES 5123.25\n",
        encoding="utf-8",
    )

    trial_cfg.rtrader["watch_dirs"] = [str(watch)]
    conn = RTraderBridgeConnector(trial_cfg)
    conn.connect()
    events = conn.poll_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "trade"


def test_rtrader_bridge_skips_probe_txt(trial_cfg: TrialConfig, tmp_path: Path) -> None:
    watch = tmp_path / "watch"
    watch.mkdir()
    probe = watch / "headless_probe.txt"
    probe.write_text("TRADE MES 5123.25\n", encoding="utf-8")

    trial_cfg.rtrader["watch_dirs"] = [str(watch)]
    trial_cfg.rtrader["log_globs"] = ["**/*.txt"]
    conn = RTraderBridgeConnector(trial_cfg)
    conn.connect()
    assert conn.poll_events() == []
