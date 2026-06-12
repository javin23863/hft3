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
from native_probe_orders import _percentile  # noqa: E402
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


def test_trial_appendix_promotes_at_1000_pairs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    date_dir = repo / "reports" / "rithmic_trial" / "2026-05-30"
    date_dir.mkdir(parents=True)
    (date_dir / "latency_profile.json").write_text(
        json.dumps(
            {
                "order_submit_to_ack_us": {
                    "count": 1100,
                    "p50_us": 3000.0,
                    "p90_us": 4500.0,
                    "p99_us": 5000.0,
                    "p999_us": 6000.0,
                },
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
    assert appendix["authoritative"] is True
    assert appendix["order_ack_p99_ms"] == 5.0


def test_build_summary_promotes_measured_ack(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    raw = repo / "runtime" / "latency_reports" / "raw" / "testrun"
    raw.mkdir(parents=True)
    crit = repo / "infrastructure" / "chi404"
    crit.mkdir(parents=True)
    (crit / "PASS_CRITERIA.json").write_text(
        json.dumps({"cyclictest_p99_max_us": 20, "network_p99_max_us": 500000}),
        encoding="utf-8",
    )
    (raw / "cyclictest_loaded_cpu2" / "percentiles.json").parent.mkdir(parents=True)
    (raw / "cyclictest_loaded_cpu2" / "percentiles.json").write_text(
        json.dumps({"p99_us": 11}), encoding="utf-8"
    )
    (raw / "network.json").write_text(
        json.dumps({"gateway_ping": {"status": "ok", "p99_ms": 0.2}}),
        encoding="utf-8",
    )
    date_dir = repo / "reports" / "rithmic_trial" / "2026-05-30"
    date_dir.mkdir(parents=True)
    (date_dir / "latency_profile.json").write_text(
        json.dumps(
            {
                "order_submit_to_ack_us": {"count": 1100, "p99_us": 4200.0},
                "status": "pass",
            }
        ),
        encoding="utf-8",
    )
    (date_dir / "data_capture_report.json").write_text(
        json.dumps({"manifest": {"known_limitations": {"connector": "rtrader_bridge"}}}),
        encoding="utf-8",
    )
    summary = build_summary(repo, "testrun", include_trial_appendix=True)
    assert summary["order_ack_p99_ms"] == pytest.approx(4.2)
    assert summary["order_ack_measured"] is True
    assert summary["paper_order_latency"]["authoritative"] is True


def test_build_summary_tcp_not_used_as_order_ack(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    raw = repo / "runtime" / "latency_reports" / "raw" / "testrun"
    raw.mkdir(parents=True)
    crit = repo / "infrastructure" / "chi404"
    crit.mkdir(parents=True)
    (crit / "PASS_CRITERIA.json").write_text(
        json.dumps({"cyclictest_p99_max_us": 20, "network_p99_max_us": 500000}),
        encoding="utf-8",
    )
    (raw / "cyclictest_loaded_cpu2" / "percentiles.json").parent.mkdir(parents=True)
    (raw / "cyclictest_loaded_cpu2" / "percentiles.json").write_text(
        json.dumps({"p99_us": 11}), encoding="utf-8"
    )
    (raw / "network.json").write_text(
        json.dumps({"rithmic_tcp_65000": {"status": "ok", "p99_ms": 4.094}}),
        encoding="utf-8",
    )
    summary = build_summary(repo, "testrun", include_trial_appendix=False)
    assert summary["order_ack_p99_ms"] is None
    assert summary["order_ack_measured"] is False
    assert summary["network_health"]["network_health_only"] is True
    assert summary["network"]["rithmic_tcp_65000"]["network_health_only"] is True


# ---------------------------------------------------------------------------
# Native probe ingestion tests (collect_native_probe_orders / build_summary)
# ---------------------------------------------------------------------------

def _make_repo_skeleton(repo: Path) -> None:
    """Write minimum PASS_CRITERIA + runtime/raw/testrun skeleton."""
    crit = repo / "infrastructure" / "chi404"
    crit.mkdir(parents=True)
    (crit / "PASS_CRITERIA.json").write_text(
        json.dumps({"cyclictest_p99_max_us": 20, "network_p99_max_us": 500_000}),
        encoding="utf-8",
    )
    raw = repo / "runtime" / "latency_reports" / "raw" / "testrun"
    (raw / "cyclictest_loaded_cpu2").mkdir(parents=True)
    (raw / "cyclictest_loaded_cpu2" / "percentiles.json").write_text(
        json.dumps({"p99_us": 11}), encoding="utf-8"
    )
    (raw / "network.json").write_text(
        json.dumps({"gateway_ping": {"status": "ok", "p99_ms": 0.2}}),
        encoding="utf-8",
    )


def _make_probe_sample(
    *,
    order_action: str = "new",
    success: bool = True,
    send_to_ack_us: float | None = 3500.0,
    order_send_ts: int = 1_700_000_000_000_000_000,
    ack_received_ts: int = 1_700_000_003_500_000_000,
    run_id: str = "run_a",
    symbol: str = "ESM6",
) -> dict:
    return {
        "run_id": run_id,
        "timestamp_utc": "2026-06-11T00:00:00Z",
        "environment": "paper",
        "broker": "rithmic",
        "venue": "CME",
        "symbol": symbol,
        "strategy_id": "probe",
        "model_id": "probe",
        "trade_manager_id": "probe",
        "order_action": order_action,
        "side": "buy",
        "order_type": "limit",
        "quantity": 1,
        "tick_to_decision_us": null,
        "decision_to_send_trigger_us": null,
        "tick_to_send_trigger_us": null,
        "decision_to_send_us": null,
        "tick_to_send_us": null,
        "rithmic_send_call_us": null,
        "send_to_ack_us": send_to_ack_us,
        "cancel_to_send_us": null,
        "cancel_to_ack_us": null,
        "replace_to_send_us": null,
        "replace_to_ack_us": null,
        "success": success,
        "reject_reason": "",
        "raw_timestamps": {
            "market_event_received_ts": 0,
            "features_ready_ts": 0,
            "decision_ready_ts": 0,
            "risk_check_ready_ts": 0,
            "order_ready_ts": 0,
            "order_api_call_start_ts": 0,
            "order_api_call_end_ts": 0,
            "order_send_ts": order_send_ts,
            "order_send_return_ts": order_send_ts,
            "ack_received_ts": ack_received_ts,
            "cancel_decision_ts": 0,
            "cancel_send_ts": 0,
            "cancel_ack_received_ts": 0,
            "replace_send_ts": null,
            "replace_ack_received_ts": null,
        },
        "broker_order_id": 1,
    }


null = None  # JSON null alias for sample dicts above


def _write_jsonl(path: Path, samples: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(s) for s in samples)
    path.write_text(lines + "\n", encoding="utf-8")


def test_native_probe_1100_samples_promotes_measured(tmp_path: Path) -> None:
    """1100 valid new+success samples → measured=True, authoritative, source native_cpp."""
    repo = tmp_path / "repo"
    _make_repo_skeleton(repo)
    samples = [
        _make_probe_sample(send_to_ack_us=3000.0 + i, order_send_ts=1_700_000_000_000_000_001,
                           ack_received_ts=1_700_000_003_500_000_001)
        for i in range(1100)
    ]
    _write_jsonl(repo / "data" / "latency_baselines" / "2026-06-11" / "run_a.jsonl", samples)

    summary = build_summary(repo, "testrun", include_trial_appendix=False)

    pol = summary["paper_order_latency"]
    assert pol["measured"] is True
    assert pol["authoritative"] is True
    assert pol["paired_count"] == 1100
    assert pol["source"] == "rithmic_latency_probe_native_cpp"
    assert pol["measurement_tier"] == "native_cpp_probe"
    assert pol["hot_path_language"] == "c++"
    assert pol["wrapper"] == "none"

    # p99 via linear interpolation (numpy default): idx=0.99*1099=1088.01 → 4088.01 µs
    values = [3000.0 + i for i in range(1100)]
    expected_p99_ms = _percentile(sorted(values), 99.0) / 1000.0
    assert summary["order_ack_p99_ms"] == pytest.approx(expected_p99_ms, rel=1e-4)
    assert summary["order_ack_measured"] is True


def test_native_probe_12_samples_partial(tmp_path: Path) -> None:
    """12 valid samples → measured=False, native_probe_partial.paired_count==12."""
    repo = tmp_path / "repo"
    _make_repo_skeleton(repo)
    samples = [
        _make_probe_sample(send_to_ack_us=3000.0 + i, order_send_ts=1_700_000_000_000_000_001,
                           ack_received_ts=1_700_000_003_500_000_001)
        for i in range(12)
    ]
    _write_jsonl(repo / "data" / "latency_baselines" / "2026-06-11" / "run_a.jsonl", samples)

    summary = build_summary(repo, "testrun", include_trial_appendix=False)

    pol = summary["paper_order_latency"]
    assert pol["measured"] is False
    assert pol["native_probe_partial"]["paired_count"] == 12


def test_native_probe_wins_over_trial_appendix(tmp_path: Path) -> None:
    """Native ≥1000 AND rtrader trial appendix present → native source wins."""
    repo = tmp_path / "repo"
    _make_repo_skeleton(repo)

    # write 1100 native probe samples
    samples = [
        _make_probe_sample(send_to_ack_us=3000.0 + i, order_send_ts=1_700_000_000_000_000_001,
                           ack_received_ts=1_700_000_003_500_000_001)
        for i in range(1100)
    ]
    _write_jsonl(repo / "data" / "latency_baselines" / "2026-06-11" / "run_a.jsonl", samples)

    # also write a trial appendix profile (rtrader_bridge, 1100 pairs)
    date_dir = repo / "reports" / "rithmic_trial" / "2026-05-30"
    date_dir.mkdir(parents=True)
    (date_dir / "latency_profile.json").write_text(
        json.dumps({
            "order_submit_to_ack_us": {"count": 1100, "p99_us": 8000.0},
            "status": "pass",
        }),
        encoding="utf-8",
    )
    (date_dir / "data_capture_report.json").write_text(
        json.dumps({"manifest": {"known_limitations": {"connector": "rtrader_bridge"}}}),
        encoding="utf-8",
    )

    summary = build_summary(repo, "testrun", include_trial_appendix=True)

    pol = summary["paper_order_latency"]
    assert pol["source"] == "rithmic_latency_probe_native_cpp"
    assert pol["measured"] is True
    assert pol["authoritative"] is True


def test_native_probe_filters_invalid_samples(tmp_path: Path) -> None:
    """success=False, order_action=cancel, send_to_ack_us=null excluded from paired_count."""
    repo = tmp_path / "repo"
    _make_repo_skeleton(repo)
    valid = [
        _make_probe_sample(send_to_ack_us=3000.0 + i, order_send_ts=1_700_000_000_000_000_001,
                           ack_received_ts=1_700_000_003_500_000_001)
        for i in range(10)
    ]
    noise = [
        # success=False
        _make_probe_sample(success=False, send_to_ack_us=3100.0,
                           order_send_ts=1_700_000_000_000_000_001,
                           ack_received_ts=1_700_000_003_500_000_001),
        # order_action=cancel
        _make_probe_sample(order_action="cancel", send_to_ack_us=3200.0,
                           order_send_ts=1_700_000_000_000_000_001,
                           ack_received_ts=1_700_000_003_500_000_001),
        # send_to_ack_us=null
        _make_probe_sample(send_to_ack_us=None, order_send_ts=1_700_000_000_000_000_001,
                           ack_received_ts=1_700_000_003_500_000_001),
        # raw ts = 0 (both zero)
        _make_probe_sample(send_to_ack_us=3300.0, order_send_ts=0, ack_received_ts=0),
    ]
    _write_jsonl(
        repo / "data" / "latency_baselines" / "2026-06-11" / "run_a.jsonl",
        valid + noise,
    )

    summary = build_summary(repo, "testrun", include_trial_appendix=False)

    pol = summary["paper_order_latency"]
    # only 10 valid → partial path, measured stays False
    assert pol["measured"] is False
    assert pol["native_probe_partial"]["paired_count"] == 10


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
