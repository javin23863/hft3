from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from data_system.rithmic_trial.capture.live_capture import LiveCapture
from data_system.rithmic_trial.config import load_config
from data_system.rithmic_trial.connector.fixture_connector import FixtureConnector
from data_system.rithmic_trial.convert.hftbacktest_converter import convert_to_npz
from data_system.rithmic_trial.normalize.mapper import normalize_file
from data_system.rithmic_trial.pipeline import cmd_process
from data_system.rithmic_trial.reports.emit_reports import build_latency_profile, emit_all_reports
from data_system.rithmic_trial.validate.book_reconstruction import reconstruct_book
from data_system.rithmic_trial.validate.quality_checks import validate_events


@pytest.fixture
def trial_cfg(tmp_path: Path):
    cfg_src = _REPO / "data_system" / "config" / "rithmic_trial.yaml"
    text = cfg_src.read_text(encoding="utf-8").replace("repo_root: .", f"repo_root: {tmp_path}")
    cfg_path = tmp_path / "rithmic_trial.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    os.environ["RITHMIC_TRIAL_ENABLED"] = "1"
    return load_config(cfg_path)


def test_fixture_capture_normalize_reports(trial_cfg, tmp_path: Path) -> None:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    connector = FixtureConnector(trial_cfg)
    connector.connect()
    events = connector.poll_events()
    assert events

    capture = LiveCapture(trial_cfg, date=date)
    n = capture.append_raw(events)
    assert n == len(events)
    lim = connector.limitations()
    manifest_path = capture.finalize(
        connector.detected_event_types(),
        lim.get("missing_event_types", []),
        lim,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["row_count"] == len(events)

    norm_path = trial_cfg.normalized_dir(date) / "events.ndjson"
    normalized, _ = normalize_file(capture.raw_path, trial_cfg, norm_path)
    quality = validate_events(normalized)
    assert quality["status"] == "pass"
    book = reconstruct_book(normalized)
    assert book["status"] == "pass"

    npz_path = trial_cfg.replay_dir(date) / f"{trial_cfg.symbol}_{date}_trial.npz"
    conversion = convert_to_npz(normalized, npz_path)
    assert conversion["status"] == "pass"
    assert npz_path.exists()

    latency = build_latency_profile(normalized)
    assert latency["feed_latency_us"]["count"] > 0
    assert latency["feed_latency_us"]["avg_us"] is not None

    reports_dir = trial_cfg.reports_dir(date)
    paths = emit_all_reports(
        reports_dir,
        manifest=manifest,
        normalized_path=norm_path,
        events=normalized,
        quality=quality,
        book=book,
        conversion=conversion,
        schema_mapping={"schema_version": "normalized_v1"},
    )
    assert len(paths) == 6
    assert (reports_dir / "latency_profile.json").exists()


def test_process_cli(trial_cfg, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    connector = FixtureConnector(trial_cfg)
    connector.connect()
    capture = LiveCapture(trial_cfg, date=date)
    capture.append_raw(connector.poll_events())
    capture.finalize(connector.detected_event_types(), [], connector.limitations())

    cfg_path = tmp_path / "rithmic_trial.yaml"
    args = type("Args", (), {"config": str(cfg_path), "date": date, "symbol": trial_cfg.symbol})()
    assert cmd_process(args) == 0
    expected_npz = trial_cfg.replay_dir(date) / f"{trial_cfg.symbol}_{date}_trial.npz"
    assert expected_npz.exists()
    conversion_report = trial_cfg.reports_dir(date) / "hftbacktest_conversion_report.json"
    assert conversion_report.exists()
    report = json.loads(conversion_report.read_text(encoding="utf-8"))
    assert report.get("status") == "pass"


def test_replay_sample_smoke(trial_cfg, tmp_path: Path) -> None:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    connector = FixtureConnector(trial_cfg)
    connector.connect()
    capture = LiveCapture(trial_cfg, date=date)
    capture.append_raw(connector.poll_events())
    norm_path = trial_cfg.normalized_dir(date) / "events.ndjson"
    normalized, _ = normalize_file(capture.raw_path, trial_cfg, norm_path)
    npz_path = trial_cfg.replay_dir(date) / f"{trial_cfg.symbol}_{date}_trial.npz"
    result = convert_to_npz(normalized, npz_path)
    assert result["status"] == "pass"

    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "data_system.rithmic_trial.pipeline",
            "replay-sample",
            "--npz",
            str(npz_path),
        ],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    payload = json.loads(r.stdout.strip())
    assert "error" not in payload
