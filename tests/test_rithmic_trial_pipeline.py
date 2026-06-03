from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from data_system.rithmic_trial.capture.live_capture import LiveCapture
from data_system.rithmic_trial.config import load_config
from data_system.rithmic_trial.connector.fixture_connector import FixtureConnector
from data_system.rithmic_trial.convert.hftbacktest_converter import convert_to_npz
from data_system.rithmic_trial.normalize.mapper import normalize_file
from data_system.rithmic_trial.pipeline import cmd_order_latency_burst, cmd_process
from data_system.rithmic_trial.reports.emit_reports import build_latency_profile, emit_all_reports
from data_system.rithmic_trial.validate.book_reconstruction import reconstruct_book
from data_system.rithmic_trial.validate.quality_checks import validate_events


@pytest.fixture
def trial_cfg(tmp_path: Path):
    cfg_src = _REPO / "packages" / "data_system" / "config" / "rithmic_trial.yaml"
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
    assert len(paths) >= 6
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

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_REPO), str(_REPO / "packages"), str(_REPO / "apps")]
    )
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
        env=env,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    payload = json.loads(r.stdout.strip())
    assert "error" not in payload


def test_order_latency_burst_writes_rithmic_test_summary(
    trial_cfg,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeOrderLatencyConnector()
    monkeypatch.setattr("data_system.rithmic_trial.pipeline.build_connector", lambda cfg: fake)
    args = type(
        "Args",
        (),
        {
            "config": str(trial_cfg.repo_root / "rithmic_trial.yaml"),
            "symbol": "MES",
            "exchange": "CME",
            "side": "BUY",
            "qty": 1,
            "price": 5000.0,
            "count": 1,
            "ack_timeout_sec": 1.0,
            "interval_ms": 0.0,
            "run_id": "unit-test-run",
            "subscribe_md": False,
            "cancel_after_ack": True,
        },
    )()

    assert cmd_order_latency_burst(args) == 0
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary_path = trial_cfg.reports_dir(date) / "rithmic_test_order_summary_unit-test-run.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "pass"
    assert summary["system"] == "Rithmic Test"
    assert summary["gateway"] == "Orangeburg"
    assert summary["ack_count"] == 1
    assert summary["cancel_submit_count"] == 1
    assert summary["cancel_broker_order_ids"] == ["12345"]
    assert summary["cancel_count"] == 1
    assert "paper" not in summary_path.name


class _FakeOrderLatencyConnector:
    def __init__(self) -> None:
        self._events: list[dict[str, object]] = []
        self._seq = 0

    def connect(self) -> None:
        return None

    def subscribe_mbo(self, symbol: str, exchange: str) -> None:
        return None

    def send_order(self, symbol: str, side: str, qty: int, price: float) -> str:
        self._seq += 1
        client_order_id = f"hft3-fake-{self._seq}"
        base_ns = time.perf_counter_ns()
        wall_ns = time.time_ns()
        self._events.extend(
            [
                {
                    "event_type": "order_submit",
                    "order_id": client_order_id,
                    "client_order_id": client_order_id,
                    "symbol": symbol,
                    "exchange": "CME",
                    "side": side,
                    "qty": qty,
                    "price": price,
                    "local_monotonic_receive_ns": base_ns,
                    "local_receive_timestamp_ns": wall_ns,
                },
                {
                    "event_type": "order_ack",
                    "order_id": client_order_id,
                    "client_order_id": client_order_id,
                    "broker_order_id": "12345",
                    "symbol": symbol,
                    "exchange": "CME",
                    "side": side,
                    "price": price,
                    "local_monotonic_receive_ns": base_ns + 250_000,
                    "local_receive_timestamp_ns": wall_ns + 250_000,
                },
            ]
        )
        return client_order_id

    def poll_events(self) -> list[dict[str, object]]:
        events, self._events = self._events, []
        return events

    def cancel_order(self, order_id: str) -> None:
        base_ns = time.perf_counter_ns()
        self._events.append(
            {
                "event_type": "cancel",
                "order_id": f"hft3-fake-{self._seq}",
                "client_order_id": f"hft3-fake-{self._seq}",
                "broker_order_id": order_id,
                "symbol": "MES",
                "exchange": "CME",
                "local_monotonic_receive_ns": base_ns,
                "local_receive_timestamp_ns": time.time_ns(),
            }
        )

    def close(self) -> None:
        return None
