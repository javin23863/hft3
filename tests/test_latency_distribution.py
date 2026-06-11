"""Tests for scripts/latency_probe/build_ack_distribution.py.

Verifies:
- All required keys are present in the artifact.
- Percentiles are monotonically non-decreasing.
- histogram.counts sum to n_pairs.
- Output is deterministic across two consecutive runs on the same data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_PROBE_DIR = REPO_ROOT / "scripts" / "latency_probe"
if str(_PROBE_DIR) not in sys.path:
    sys.path.insert(0, str(_PROBE_DIR))

from build_ack_distribution import build_distribution  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic JSONL fixture helpers
# ---------------------------------------------------------------------------

_REQUIRED_TOP_KEYS = {
    "run_ids",
    "symbol",
    "n_pairs",
    "unit",
    "percentiles",
    "histogram",
    "moments",
    "samples_source_files",
    "generated_at",
    "note",
}

_REQUIRED_PERCENTILE_KEYS = {
    "p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99", "p99.9",
    "min", "max",
}

_REQUIRED_MOMENT_KEYS = {"mean", "std", "skew", "kurtosis"}

_ORDERED_PERCENTILES = ["min", "p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99", "p99.9", "max"]


def _make_jsonl_line(
    run_id: str,
    send_to_ack_us: float,
    order_send_ts: int = 100_000_000,
    ack_received_ts: int = 103_000_000,
    symbol: str = "MESM6",
    order_action: str = "new",
    success: bool = True,
) -> str:
    return json.dumps(
        {
            "run_id": run_id,
            "timestamp_utc": "2026-06-11T07:21:30Z",
            "environment": "paper",
            "broker": "rithmic",
            "venue": "CME",
            "symbol": symbol,
            "strategy_id": "latency_probe",
            "model_id": "latency_probe",
            "trade_manager_id": "native_cpp_probe",
            "order_action": order_action,
            "side": "BUY",
            "order_type": "limit",
            "quantity": 1,
            "send_to_ack_us": send_to_ack_us,
            "success": success,
            "raw_timestamps": {
                "order_send_ts": order_send_ts,
                "ack_received_ts": ack_received_ts,
            },
        }
    )


def _write_fixture(tmp_path: Path, latencies: list[float]) -> None:
    """Write a single JSONL fixture file with the given send_to_ack_us values."""
    date_dir = tmp_path / "data" / "latency_baselines" / "2099-01-01"
    date_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, lat in enumerate(latencies):
        lines.append(
            _make_jsonl_line(
                run_id="fixture_run",
                send_to_ack_us=lat,
                order_send_ts=100_000_000 + i * 1000,
                ack_received_ts=100_000_000 + i * 1000 + int(lat * 1000),
            )
        )
    (date_dir / "fixture_run.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 20-sample fixture with known distribution
# ---------------------------------------------------------------------------

_N_PAIRS = 20
# Latencies spanning ~1000 us to 10000 us (log-varied)
_FIXTURE_LATENCIES = [
    1000.0, 1200.0, 1400.0, 1600.0, 1800.0,
    2000.0, 2200.0, 2500.0, 2800.0, 3000.0,
    3200.0, 3500.0, 3800.0, 4000.0, 4500.0,
    5000.0, 6000.0, 7000.0, 8500.0, 10000.0,
]
assert len(_FIXTURE_LATENCIES) == _N_PAIRS


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_all_required_keys_present(tmp_path: Path) -> None:
    _write_fixture(tmp_path, _FIXTURE_LATENCIES)
    artifact = build_distribution(tmp_path)

    assert _REQUIRED_TOP_KEYS <= set(artifact.keys()), (
        f"Missing top-level keys: {_REQUIRED_TOP_KEYS - set(artifact.keys())}"
    )
    assert _REQUIRED_PERCENTILE_KEYS <= set(artifact["percentiles"].keys()), (
        f"Missing percentile keys: {_REQUIRED_PERCENTILE_KEYS - set(artifact['percentiles'].keys())}"
    )
    assert _REQUIRED_MOMENT_KEYS <= set(artifact["moments"].keys()), (
        f"Missing moment keys: {_REQUIRED_MOMENT_KEYS - set(artifact['moments'].keys())}"
    )
    assert "bin_edges_us" in artifact["histogram"]
    assert "counts" in artifact["histogram"]


def test_n_pairs_matches_fixture(tmp_path: Path) -> None:
    _write_fixture(tmp_path, _FIXTURE_LATENCIES)
    artifact = build_distribution(tmp_path)
    assert artifact["n_pairs"] == _N_PAIRS


def test_percentiles_monotone(tmp_path: Path) -> None:
    _write_fixture(tmp_path, _FIXTURE_LATENCIES)
    artifact = build_distribution(tmp_path)
    pct = artifact["percentiles"]
    prev_val = pct[_ORDERED_PERCENTILES[0]]
    for key in _ORDERED_PERCENTILES[1:]:
        cur_val = pct[key]
        assert cur_val >= prev_val, (
            f"Percentile not monotone: {key}={cur_val} < prev={prev_val}"
        )
        prev_val = cur_val


def test_histogram_counts_sum_to_n_pairs(tmp_path: Path) -> None:
    _write_fixture(tmp_path, _FIXTURE_LATENCIES)
    artifact = build_distribution(tmp_path)
    total = sum(artifact["histogram"]["counts"])
    assert total == artifact["n_pairs"], (
        f"Histogram counts sum {total} != n_pairs {artifact['n_pairs']}"
    )


def test_histogram_has_50_bins(tmp_path: Path) -> None:
    _write_fixture(tmp_path, _FIXTURE_LATENCIES)
    artifact = build_distribution(tmp_path)
    edges = artifact["histogram"]["bin_edges_us"]
    counts = artifact["histogram"]["counts"]
    assert len(edges) == 51, f"Expected 51 edges (50 bins), got {len(edges)}"
    assert len(counts) == 50, f"Expected 50 counts, got {len(counts)}"


def test_unit_is_us(tmp_path: Path) -> None:
    _write_fixture(tmp_path, _FIXTURE_LATENCIES)
    artifact = build_distribution(tmp_path)
    assert artifact["unit"] == "us"


def test_note_contains_rithmic_paper(tmp_path: Path) -> None:
    _write_fixture(tmp_path, _FIXTURE_LATENCIES)
    artifact = build_distribution(tmp_path)
    assert "Rithmic paper-system ack" in artifact["note"]


def test_deterministic_across_two_runs(tmp_path: Path) -> None:
    """Two consecutive calls on the same fixture must yield identical
    percentiles, histogram counts, moments, and n_pairs.
    (generated_at may differ; we don't check it.)
    """
    _write_fixture(tmp_path, _FIXTURE_LATENCIES)
    a1 = build_distribution(tmp_path)
    a2 = build_distribution(tmp_path)

    assert a1["n_pairs"] == a2["n_pairs"]
    assert a1["percentiles"] == a2["percentiles"]
    assert a1["histogram"]["counts"] == a2["histogram"]["counts"]
    assert a1["histogram"]["bin_edges_us"] == a2["histogram"]["bin_edges_us"]
    assert a1["moments"] == a2["moments"]


def test_invalid_records_are_skipped(tmp_path: Path) -> None:
    """Non-new orders, failed orders, and missing timestamps are skipped."""
    date_dir = tmp_path / "data" / "latency_baselines" / "2099-01-01"
    date_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        # valid
        _make_jsonl_line("r1", 3000.0, order_send_ts=1, ack_received_ts=2),
        # skipped: not a "new" order
        _make_jsonl_line("r1", 3000.0, order_action="cancel", order_send_ts=1, ack_received_ts=2),
        # skipped: success=False
        _make_jsonl_line("r1", 3000.0, success=False, order_send_ts=1, ack_received_ts=2),
        # skipped: zero order_send_ts
        _make_jsonl_line("r1", 3000.0, order_send_ts=0, ack_received_ts=2),
    ]
    (date_dir / "mixed.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    artifact = build_distribution(tmp_path)
    assert artifact["n_pairs"] == 1
