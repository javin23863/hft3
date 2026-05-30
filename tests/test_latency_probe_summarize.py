"""Unit tests for CHI404 latency probe summarize logic."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PROBE_DIR = _REPO / "scripts" / "latency_probe"
if str(_PROBE_DIR) not in sys.path:
    sys.path.insert(0, str(_PROBE_DIR))

from summarize_latency import (  # noqa: E402
    _build_trial_order_ack_appendix,
    _collect_cyclictest,
    build_summary,
    network_p99_us,
)
from trial_profile import latest_latency_profile, profile_untrusted  # noqa: E402


def test_network_p99_us_uses_worst_path() -> None:
    network = {
        "gateway_ping": {"status": "ok", "p99_ms": 0.193},
        "rithmic_tcp_65000": {"status": "ok", "p99_ms": 3.957},
    }
    p99_us, source = network_p99_us(network)
    assert p99_us == pytest.approx(3957.0)
    assert source == "rithmic_tcp_65000"


def test_collect_cyclictest_loaded_only(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "run1"
    idle = raw / "cyclictest_idle_cpu2"
    loaded = raw / "cyclictest_loaded_cpu2"
    idle.mkdir(parents=True)
    loaded.mkdir(parents=True)
    (idle / "percentiles.json").write_text(json.dumps({"p99_us": 999}), encoding="utf-8")
    (loaded / "percentiles.json").write_text(json.dumps({"p99_us": 11}), encoding="utf-8")

    result = _collect_cyclictest(raw)
    assert result["max_p99_us"] == 11
    assert result["gate_mode"] == "loaded_only"
    assert result["loaded_runs"] == 1


def test_build_summary_network_fail_from_fixture(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    raw = repo / "runtime" / "latency_reports" / "raw" / "testrun"
    raw.mkdir(parents=True)
    crit = repo / "infrastructure" / "chi404"
    crit.mkdir(parents=True)
    (crit / "PASS_CRITERIA.json").write_text(
        json.dumps({"cyclictest_p99_max_us": 20, "network_p99_max_us": 500}),
        encoding="utf-8",
    )
    (raw / "cyclictest_loaded_cpu2" / "percentiles.json").parent.mkdir(parents=True)
    (raw / "cyclictest_loaded_cpu2" / "percentiles.json").write_text(
        json.dumps({"p99_us": 11, "samples": 1000}),
        encoding="utf-8",
    )
    (raw / "network.json").write_text(
        json.dumps(
            {
                "gateway_ping": {"status": "ok", "p99_ms": 0.193},
                "rithmic_tcp_65000": {"status": "ok", "p99_ms": 3.957},
            }
        ),
        encoding="utf-8",
    )

    summary = build_summary(repo, "testrun", include_trial_appendix=False)
    assert summary["network_pass"] is False
    assert summary["network_p99_worst_source"] == "rithmic_tcp_65000"
    assert summary["order_ack_p99_ms"] is None
    assert summary["gates"]["order_ack_pass"] is None


def test_trial_appendix_missing_profile(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    appendix = _build_trial_order_ack_appendix(repo, include=True)
    assert appendix["status"] == "missing"
    assert appendix["authoritative"] is False


def test_profile_untrusted_fixture() -> None:
    assert profile_untrusted({}, {"manifest": {"known_limitations": {"connector": "fixture"}}}) is True


def test_trial_appendix_accepts_rtrader_bridge_connector(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    date_dir = repo / "reports" / "rithmic_trial" / "2026-05-30"
    date_dir.mkdir(parents=True)
    (date_dir / "latency_profile.json").write_text(
        json.dumps(
            {
                "order_submit_to_ack_us": {"count": 1, "p99_us": 8000.0},
                "order_rtt_ms": 8.0,
                "status": "pass",
            }
        ),
        encoding="utf-8",
    )
    (date_dir / "data_capture_report.json").write_text(
        json.dumps({"manifest": {"known_limitations": {"connector": "rtrader_bridge"}}}),
        encoding="utf-8",
    )
    appendix = _build_trial_order_ack_appendix(repo, include=True)
    assert appendix["status"] == "ok"
    assert appendix["order_ack_p99_ms"] == 8.0


def test_latest_latency_profile_reads_order_stats(tmp_path: Path) -> None:
    date_dir = tmp_path / "reports" / "rithmic_trial" / "2026-05-30"
    date_dir.mkdir(parents=True)
    (date_dir / "latency_profile.json").write_text(
        json.dumps(
            {
                "order_submit_to_ack_us": {"count": 2, "p99_us": 4500.0},
                "order_rtt_ms": 4.5,
                "status": "pass",
            }
        ),
        encoding="utf-8",
    )
    (date_dir / "data_capture_report.json").write_text(
        json.dumps({"manifest": {"known_limitations": {"connector": "rtrader"}}}),
        encoding="utf-8",
    )
    profile = latest_latency_profile(tmp_path)
    assert profile is not None
    assert profile["trusted"] is True
    assert profile["connector"] == "rtrader"
    assert profile["order_submit_to_ack_us"]["p99_us"] == 4500.0
