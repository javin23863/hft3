from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import pytest

from tools.latency_baseline.recorder import (
    LatencyRecorder,
    build_latency_sample,
    load_jsonl,
)
from tools.latency_baseline.run import main as latency_baseline_main
from tools.latency_baseline.summary import build_summary, write_summary_reports


class _FakeBrokerConnector:
    def __init__(self, *, cancel_ack: bool = True) -> None:
        self.cancel_ack = cancel_ack
        self.connected = False
        self.symbol = "ESM6"
        self.exchange = "CME"
        self._ns = time.perf_counter_ns()
        self._submit_seq = 0
        self._pending: list[dict] = []
        self._ready_for_market = True
        self._last_client_id = ""
        self._last_broker_id = ""

    def connect(self) -> None:
        self.connected = True

    def subscribe_mbo(self, symbol: str, exchange: str) -> None:
        self.symbol = symbol
        self.exchange = exchange

    def send_order(self, symbol: str, side: str, qty: int, price: float) -> str:
        self._submit_seq += 1
        self._ns = max(self._ns, time.perf_counter_ns())
        self._last_client_id = f"fake-{self._submit_seq}"
        self._last_broker_id = f"broker-{self._submit_seq}"
        submit_ns = self._next_ns(10)
        ack_ns = self._next_ns(1000)
        self._pending.extend(
            [
                {
                    "event_type": "order_submit",
                    "client_order_id": self._last_client_id,
                    "order_id": self._last_client_id,
                    "symbol": symbol,
                    "exchange": self.exchange,
                    "side": side,
                    "qty": qty,
                    "price": price,
                    "ts_emit_ns": submit_ns,
                    "local_monotonic_receive_ns": submit_ns,
                },
                {
                    "event_type": "order_ack",
                    "client_order_id": self._last_client_id,
                    "order_id": self._last_client_id,
                    "broker_order_id": self._last_broker_id,
                    "symbol": symbol,
                    "exchange": self.exchange,
                    "local_monotonic_receive_ns": ack_ns,
                },
            ]
        )
        return self._last_client_id

    def cancel_order(self, order_id: str) -> None:
        if self.cancel_ack:
            cancel_ns = self._next_ns(500)
            self._pending.append(
                {
                    "event_type": "cancel",
                    "client_order_id": self._last_client_id,
                    "order_id": self._last_client_id,
                    "broker_order_id": order_id,
                    "symbol": self.symbol,
                    "exchange": self.exchange,
                    "local_monotonic_receive_ns": cancel_ns,
                }
            )
        self._ready_for_market = True

    def poll_events(self) -> list[dict]:
        if self._pending:
            out = self._pending
            self._pending = []
            return out
        if self._ready_for_market:
            self._ready_for_market = False
            ns = self._next_ns(100)
            return [
                {
                    "event_type": "quote",
                    "symbol": self.symbol,
                    "exchange": self.exchange,
                    "bid_price": 5000.0,
                    "ask_price": 5000.25,
                    "price": 5000.0,
                    "local_monotonic_receive_ns": ns,
                }
            ]
        return []

    def close(self) -> None:
        self.connected = False

    def _next_ns(self, delta_us: int) -> int:
        self._ns += delta_us * 1000
        return self._ns


def test_tick_to_send_is_separate_from_send_to_ack() -> None:
    record = build_latency_sample(
        run_id="r1",
        environment="paper",
        broker="rithmic",
        venue="CME",
        symbol="ES",
        strategy_id="latency_probe",
        order_action="new",
        timestamps={
            "market_event_received_ts": 1_000_000,
            "features_ready_ts": 1_010_000,
            "decision_ready_ts": 1_030_000,
            "risk_check_ready_ts": 1_040_000,
            "order_ready_ts": 1_050_000,
            "order_send_ts": 1_060_000,
            "ack_received_ts": 2_060_000,
        },
    )
    assert record["tick_to_decision_us"] == pytest.approx(30.0)
    assert record["decision_to_send_us"] == pytest.approx(30.0)
    assert record["tick_to_send_us"] == pytest.approx(60.0)
    assert record["send_to_ack_us"] == pytest.approx(1000.0)
    assert record["cancel_to_send_us"] is None
    assert record["cancel_to_ack_us"] is None


def test_action_specific_metrics_do_not_bleed_between_order_and_cancel() -> None:
    cancel = build_latency_sample(
        run_id="r1",
        environment="paper",
        broker="rithmic",
        venue="CME",
        symbol="ES",
        strategy_id="latency_probe",
        order_action="cancel",
        timestamps={
            "decision_ready_ts": 10_000,
            "cancel_send_ts": 20_000,
            "cancel_ack_received_ts": 80_000,
        },
    )

    assert cancel["cancel_to_send_us"] == pytest.approx(10.0)
    assert cancel["cancel_to_ack_us"] == pytest.approx(60.0)
    assert cancel["tick_to_decision_us"] is None
    assert cancel["decision_to_send_us"] is None
    assert cancel["tick_to_send_us"] is None
    assert cancel["send_to_ack_us"] is None


def test_recorder_persists_jsonl_under_dated_data_root(tmp_path: Path) -> None:
    recorder = LatencyRecorder(
        repo_root=tmp_path,
        run_id="run-a",
        environment="paper",
        broker="rithmic",
        venue="CME",
        symbol="ES",
        strategy_id="latency_probe",
    )
    recorder.write_sample(
        order_action="new",
        timestamps={
            "market_event_received_ts": 1,
            "decision_ready_ts": 11,
            "order_send_ts": 21,
            "ack_received_ts": 31,
        },
        timestamp_utc="2026-06-04T00:00:00Z",
    )
    path = recorder.sample_path()
    assert path == tmp_path / "data" / "latency_baselines" / "2026-06-04" / "run-a.jsonl"
    records = load_jsonl(path)
    assert len(records) == 1
    assert records[0]["run_id"] == "run-a"


def test_summary_views_and_baseline_hard_fail(tmp_path: Path) -> None:
    records = [
        build_latency_sample(
            run_id="base",
            environment="paper",
            broker="rithmic",
            venue="CME",
            symbol="ES",
            strategy_id="latency_probe",
            order_action="new",
            timestamps={
                "market_event_received_ts": 1_000_000,
                "decision_ready_ts": 1_010_000,
                "order_send_ts": 1_020_000,
                "ack_received_ts": 1_520_000,
            },
        )
        for _ in range(3)
    ]
    baseline = build_summary(
        records,
        run_id="base",
        sample_path=tmp_path / "base.jsonl",
        baseline_path=None,
    )
    reports = tmp_path / "reports"
    _, _, current = write_summary_reports(baseline, reports_root=reports, update_current_baseline=True)
    assert current is not None

    slower = [
        build_latency_sample(
            run_id="slow",
            environment="paper",
            broker="rithmic",
            venue="CME",
            symbol="ES",
            strategy_id="latency_probe",
            order_action="new",
            timestamps={
                "market_event_received_ts": 1_000_000,
                "decision_ready_ts": 1_020_000,
                "order_send_ts": 1_040_000,
                "ack_received_ts": 1_540_000,
            },
        )
        for _ in range(3)
    ]
    summary = build_summary(
        slower,
        run_id="slow",
        sample_path=tmp_path / "slow.jsonl",
        baseline_path=current,
    )
    assert "tick_to_send_us" in summary["views"]["offensive"]
    assert "send_to_ack_us" in summary["views"]["round_trip"]
    assert summary["comparison"]["status"] == "fail"
    hard = summary["comparison"]["hard_failures"]
    assert hard and hard[0]["metric"] == "tick_to_send_us"


def test_synthetic_cli_writes_jsonl_and_reports(tmp_path: Path) -> None:
    rc = latency_baseline_main(
        [
            "--mode",
            "synthetic",
            "--repo-root",
            str(tmp_path),
            "--run-id",
            "syn1",
            "--duration",
            "1",
            "--samples",
            "9",
            "--env",
            "paper",
            "--broker",
            "rithmic",
            "--symbol",
            "ES",
            "--exchange",
            "CME",
            "--strategy",
            "latency_probe",
        ]
    )
    assert rc == 0
    samples = list((tmp_path / "data" / "latency_baselines").glob("*/*.jsonl"))
    assert len(samples) == 1
    records = load_jsonl(samples[0])
    assert len(records) == 9
    actions = {record["order_action"] for record in records}
    assert actions == {"new", "cancel", "replace"}
    summary_path = tmp_path / "reports" / "latency_baselines" / "syn1_summary.json"
    report = json.loads(summary_path.read_text(encoding="utf-8"))
    assert report["primary_kpi"] == "tick_to_send_us"
    assert report["metrics"]["tick_to_send_us"]["count"] == 3
    assert report["metrics"]["send_to_ack_us"]["count"] == 3
    assert report["metrics"]["cancel_to_ack_us"]["count"] == 3
    assert (tmp_path / "reports" / "latency_baselines" / "syn1_summary.md").is_file()


def test_invalid_samples_fail_loudly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="order_send_ts before decision_ready_ts"):
        build_latency_sample(
            run_id="bad",
            environment="paper",
            broker="rithmic",
            venue="CME",
            symbol="ES",
            strategy_id="latency_probe",
            order_action="new",
            timestamps={
                "market_event_received_ts": 1_000,
                "decision_ready_ts": 3_000,
                "order_send_ts": 2_000,
                "ack_received_ts": 4_000,
            },
        )

    bad_jsonl = tmp_path / "bad.jsonl"
    bad_jsonl.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="latency sample must be a JSON object"):
        load_jsonl(bad_jsonl)


def test_broker_mode_fails_loudly_until_execution_adapter_is_wired(tmp_path: Path) -> None:
    rc = latency_baseline_main(["--repo-root", str(tmp_path), "--run-id", "blocked"])
    assert rc == 2
    blocker = tmp_path / "reports" / "latency_baselines" / "blocked_broker_blocker.json"
    payload = json.loads(blocker.read_text(encoding="utf-8"))
    assert payload["blocker"] == "BROKER_MODE_REQUIRES_EXECUTION_ADAPTER"
    assert payload["principle"] == "do_not_treat_ack_latency_as_placement_speed"


def test_broker_mode_writes_multiple_order_and_cancel_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_fake_broker(monkeypatch, _FakeBrokerConnector(cancel_ack=True), tmp_path)

    rc = latency_baseline_main(
        [
            "--repo-root",
            str(tmp_path),
            "--run-id",
            "broker-multi",
            "--env",
            "paper",
            "--broker",
            "rithmic",
            "--symbol",
            "ES",
            "--exchange",
            "CME",
            "--samples",
            "2",
            "--duration",
            "5",
            "--ack-timeout-sec",
            "0.01",
            "--cancel-after-ack",
        ]
    )

    assert rc == 0
    sample_path = next((tmp_path / "data" / "latency_baselines").glob("*/*.jsonl"))
    records = load_jsonl(sample_path)
    assert [row["order_action"] for row in records] == ["new", "cancel", "new", "cancel"]
    assert sum(row["order_action"] == "new" for row in records) == 2
    assert sum(row["order_action"] == "cancel" for row in records) == 2
    assert all(row["cancel_to_send_us"] is None for row in records if row["order_action"] == "new")
    assert all(row["tick_to_send_us"] is None for row in records if row["order_action"] == "cancel")
    summary = json.loads((tmp_path / "reports" / "latency_baselines" / "broker-multi_summary.json").read_text(encoding="utf-8"))
    assert summary["metrics"]["tick_to_send_us"]["count"] == 2
    assert summary["metrics"]["cancel_to_send_us"]["count"] == 2
    assert summary["broker_artifacts"]["records_written"] == "4"


def test_broker_mode_records_cancel_timeout_without_faking_ack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_fake_broker(monkeypatch, _FakeBrokerConnector(cancel_ack=False), tmp_path)

    rc = latency_baseline_main(
        [
            "--repo-root",
            str(tmp_path),
            "--run-id",
            "broker-cancel-timeout",
            "--env",
            "paper",
            "--broker",
            "rithmic",
            "--symbol",
            "ES",
            "--exchange",
            "CME",
            "--samples",
            "2",
            "--duration",
            "5",
            "--ack-timeout-sec",
            "0.01",
            "--cancel-after-ack",
        ]
    )

    assert rc == 0
    sample_path = next((tmp_path / "data" / "latency_baselines").glob("*/*.jsonl"))
    records = load_jsonl(sample_path)
    assert [row["order_action"] for row in records] == ["new", "cancel"]
    cancel = records[-1]
    assert cancel["success"] is False
    assert cancel["reject_reason"] == "cancel_ack_timeout"
    assert cancel["cancel_to_ack_us"] is None
    summary = json.loads((tmp_path / "reports" / "latency_baselines" / "broker-cancel-timeout_summary.json").read_text(encoding="utf-8"))
    assert summary["broker_artifacts"]["stop_reason"] == "cancel_ack_timeout"


def _patch_fake_broker(monkeypatch: pytest.MonkeyPatch, connector: _FakeBrokerConnector, repo_root: Path) -> None:
    packages_path = Path.cwd() / "packages"
    if str(packages_path) not in sys.path:
        sys.path.insert(0, str(packages_path))
    import data_system.rithmic_trial.config as config_mod
    import data_system.rithmic_trial.connector as connector_mod

    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda _path: SimpleNamespace(exchange="CME", connector="fake", repo_root=repo_root),
    )
    monkeypatch.setattr(connector_mod, "build_connector", lambda _cfg: connector)
