"""K12 enforcement suite for slippage_calibration module."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from crypto_lane.src.validation.slippage_calibration import (
    MIN_ENVELOPE_STD_BPS,
    acceptance_for,
    is_calibration_stale,
    write_calibration_artifact,
)


def _minimal_artifact(**overrides) -> dict:
    base = {
        "calibration_version": 1,
        "fit_utc": "2026-06-11T00:00:00+00:00",
        "fit_epoch": time.time(),
        "sessions": [],
        "newest_capture_mtime_utc": "",
        "newest_capture_mtime_epoch": 0.0,
        "results": [
            {
                "symbol": "BTCUSD",
                "execution_classification": "L3_VALIDATED",
                "fill_rate": 0.95,
                "mean_slippage_bps": 1.2,
                "slippage_bps_std": 0.3,
                "slippage_bps_p95": 2.1,
                "slippage_vs_mid_bps": 1.5,
                "slippage_vs_mid_bps_std": 0.4,
                "slippage_vs_mid_bps_p95": 2.5,
                "envelope_basis": "slippage_vs_mid_bps",
                "num_fills": 31,
                "num_intents": 33,
                "queue_model": "L3FifoQueueModel",
                "latency_ms": 50.0,
                "acceptance": "OK",
            }
        ],
        "signal_profile": "alternating +/-1.0 blocks, period 50, max_steps 20000",
    }
    base.update(overrides)
    return base


def test_artifact_roundtrip(tmp_path: Path) -> None:
    artifact = _minimal_artifact()
    out = tmp_path / "runtime" / "crypto_latency" / "slippage_calibration.json"
    written = write_calibration_artifact(artifact, out)

    assert written == out
    assert out.exists()
    raw = out.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert parsed["calibration_version"] == 1
    assert "fit_utc" in parsed
    assert "fit_epoch" in parsed
    assert "sessions" in parsed
    assert "results" in parsed
    assert parsed["results"][0]["num_fills"] == 31
    assert parsed["results"][0]["acceptance"] == "OK"
    # sort_keys=True: verify keys are lexicographically ordered
    keys = list(parsed.keys())
    assert keys == sorted(keys)
    # indent=2: verify indented formatting
    assert "  " in raw


def test_stale_when_absent(tmp_path: Path) -> None:
    stale, reason = is_calibration_stale(tmp_path / "missing.json", tmp_path)
    assert stale is True
    assert "absent" in reason


def test_stale_when_newer_capture(tmp_path: Path) -> None:
    now = time.time()
    artifact = _minimal_artifact(fit_epoch=now - 1000)
    out = tmp_path / "slippage_calibration.json"
    write_calibration_artifact(artifact, out)

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    ndjson = raw_dir / "bitfinex_mbo_BTCUSD_x.ndjson"
    ndjson.write_text("{}\n", encoding="utf-8")
    os.utime(ndjson, (now, now))

    stale, reason = is_calibration_stale(out, raw_dir)
    assert stale is True
    assert ndjson.name in reason


def test_fresh_when_artifact_newer(tmp_path: Path) -> None:
    now = time.time()
    artifact = _minimal_artifact(fit_epoch=now + 3600)
    out = tmp_path / "slippage_calibration.json"
    write_calibration_artifact(artifact, out)

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    ndjson = raw_dir / "bitfinex_mbo_BTCUSD_x.ndjson"
    ndjson.write_text("{}\n", encoding="utf-8")
    old_time = now - 100
    os.utime(ndjson, (old_time, old_time))

    stale, reason = is_calibration_stale(out, raw_dir)
    assert stale is False
    assert reason == "fresh"


def test_stale_when_no_ok_acceptance(tmp_path: Path) -> None:
    now = time.time()
    artifact = _minimal_artifact(
        fit_epoch=now + 3600,
        results=[
            {
                "symbol": "BTCUSD",
                "execution_classification": "L3_VALIDATED",
                "fill_rate": 0.1,
                "mean_slippage_bps": 0.0,
                "slippage_bps_std": 0.0,
                "slippage_bps_p95": 0.0,
                "slippage_vs_mid_bps": 0.0,
                "slippage_vs_mid_bps_std": 0.0,
                "slippage_vs_mid_bps_p95": 0.0,
                "envelope_basis": "slippage_vs_mid_bps",
                "num_fills": 5,
                "num_intents": 10,
                "queue_model": "L3FifoQueueModel",
                "latency_ms": 50.0,
                "acceptance": "INSUFFICIENT",
            }
        ],
    )
    out = tmp_path / "slippage_calibration.json"
    write_calibration_artifact(artifact, out)

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    ndjson = raw_dir / "bitfinex_mbo_BTCUSD_x.ndjson"
    ndjson.write_text("{}\n", encoding="utf-8")
    old_time = now - 100
    os.utime(ndjson, (old_time, old_time))

    stale, reason = is_calibration_stale(out, raw_dir)
    assert stale is True
    assert "acceptance" in reason or "OK" in reason


def test_unparseable_artifact_stale(tmp_path: Path) -> None:
    out = tmp_path / "slippage_calibration.json"
    out.write_bytes(b"\xff\xfe garbage not json \x00\x01")

    stale, reason = is_calibration_stale(out, tmp_path)
    assert stale is True
    assert "unparseable" in reason


def test_stale_when_no_captures(tmp_path: Path) -> None:
    now = time.time()
    artifact = _minimal_artifact(fit_epoch=now + 3600)
    out = tmp_path / "slippage_calibration.json"
    write_calibration_artifact(artifact, out)

    # variant 1: raw_dir absent entirely
    absent_dir = tmp_path / "nonexistent_raw"
    stale, reason = is_calibration_stale(out, absent_dir)
    assert stale is True
    assert "no captures" in reason

    # variant 2: raw_dir exists but contains zero bitfinex_mbo_*.ndjson files
    empty_dir = tmp_path / "empty_raw"
    empty_dir.mkdir()
    stale2, reason2 = is_calibration_stale(out, empty_dir)
    assert stale2 is True
    assert "no captures" in reason2


def _result_row(acceptance: str, num_fills: int = 5, std: float = 0.0) -> dict:
    return {
        "symbol": "BTCUSD",
        "execution_classification": "L3_VALIDATED",
        "fill_rate": 0.9,
        "mean_slippage_bps": 1.0,
        "slippage_bps_std": std,
        "slippage_bps_p95": 1.5,
        "slippage_vs_mid_bps": 1.2,
        "slippage_vs_mid_bps_std": std,
        "slippage_vs_mid_bps_p95": 2.0,
        "envelope_basis": "slippage_vs_mid_bps",
        "num_fills": num_fills,
        "num_intents": num_fills + 2,
        "queue_model": "L3FifoQueueModel",
        "latency_ms": 50.0,
        "acceptance": acceptance,
    }


def _fresh_artifact_with_rows(tmp_path: Path, rows: list) -> tuple:
    """Write artifact with fit_epoch in future + one old ndjson. Returns (path, raw_dir)."""
    now = time.time()
    artifact = _minimal_artifact(fit_epoch=now + 3600, results=rows)
    out = tmp_path / "slippage_calibration.json"
    write_calibration_artifact(artifact, out)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    ndjson = raw_dir / "bitfinex_mbo_BTCUSD_x.ndjson"
    ndjson.write_text("{}\n", encoding="utf-8")
    old_time = now - 100
    os.utime(ndjson, (old_time, old_time))
    return out, raw_dir


def test_degenerate_zero_variance_not_ok(tmp_path: Path) -> None:
    out, raw_dir = _fresh_artifact_with_rows(
        tmp_path, [_result_row("DEGENERATE_ZERO_VARIANCE", num_fills=31, std=0.0)]
    )
    stale, reason = is_calibration_stale(out, raw_dir)
    assert stale is True
    assert "acceptance" in reason or "OK" in reason


def test_acceptance_ok_makes_fresh(tmp_path: Path) -> None:
    out, raw_dir = _fresh_artifact_with_rows(
        tmp_path, [_result_row("OK", num_fills=31, std=0.4)]
    )
    stale, reason = is_calibration_stale(out, raw_dir)
    assert stale is False
    assert reason == "fresh"


def test_acceptance_insufficient_makes_stale(tmp_path: Path) -> None:
    out, raw_dir = _fresh_artifact_with_rows(
        tmp_path, [_result_row("INSUFFICIENT", num_fills=5, std=0.0)]
    )
    stale, reason = is_calibration_stale(out, raw_dir)
    assert stale is True
    assert "acceptance" in reason or "OK" in reason


def test_min_envelope_std_bps_exceeds_float_epsilon() -> None:
    # Guard: constant must be strictly above the float-epsilon noise value observed in prod.
    assert MIN_ENVELOPE_STD_BPS > 2.3e-16


def test_acceptance_for_ok() -> None:
    assert acceptance_for(num_fills=31, exec_cls="L3_VALIDATED", slippage_vs_mid_bps_std=0.4) == "OK"


def test_acceptance_for_degenerate_zero_variance() -> None:
    # 2.2e-16 is float epsilon — must be classified degenerate, not OK.
    assert acceptance_for(num_fills=31, exec_cls="L3_VALIDATED", slippage_vs_mid_bps_std=2.2e-16) == "DEGENERATE_ZERO_VARIANCE"


def test_acceptance_for_insufficient_fills() -> None:
    assert acceptance_for(num_fills=5, exec_cls="L3_VALIDATED", slippage_vs_mid_bps_std=0.4) == "INSUFFICIENT"
