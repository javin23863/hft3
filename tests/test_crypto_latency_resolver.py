"""Tests for crypto order-ack latency resolver (LATENCY.md §3)."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from backtest_pipeline.src.crypto_latency import (
    CRYPTO_LATENCY_BANDS_MS,
    is_crypto_latency_measured,
    resolve_crypto_replay_latency_ms,
)


def _write_summary(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "latency_summary.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_cli_wins(tmp_path):
    latency_ms, source = resolve_crypto_replay_latency_ms(cli_latency_ms=50.0)
    assert latency_ms == 50.0
    assert source == "cli:--latency-ms"


def test_cli_out_of_band_raises():
    with pytest.raises(ValueError, match="outside LATENCY.md §3 band"):
        resolve_crypto_replay_latency_ms(cli_latency_ms=500.0)


def test_measured_summary_resolves(tmp_path):
    p = _write_summary(tmp_path, {
        "paper_order_latency": {"measured": True},
        "order_ack_p99_ms": 42.0,
    })
    latency_ms, source = resolve_crypto_replay_latency_ms(summary_path=p)
    assert latency_ms == 42.0
    assert source == "crypto_paper_order_latency.authoritative"


def test_unmeasured_summary_raises(tmp_path):
    p = _write_summary(tmp_path, {
        "paper_order_latency": {"measured": False},
    })
    with pytest.raises(ValueError, match="UNMEASURED"):
        resolve_crypto_replay_latency_ms(summary_path=p)


def test_missing_summary_raises(tmp_path):
    nonexistent = tmp_path / "does_not_exist.json"
    with pytest.raises(ValueError, match="UNMEASURED"):
        resolve_crypto_replay_latency_ms(summary_path=nonexistent)


def test_measured_but_zero_p99_raises(tmp_path):
    p = _write_summary(tmp_path, {
        "paper_order_latency": {"measured": True},
        "order_ack_p99_ms": 0,
    })
    with pytest.raises(ValueError, match="UNMEASURED"):
        resolve_crypto_replay_latency_ms(summary_path=p)


def test_is_measured_false_on_garbage(tmp_path):
    p = tmp_path / "garbage.json"
    p.write_text("not json }{", encoding="utf-8")
    assert is_crypto_latency_measured(p) is False


def test_bands_constant():
    assert CRYPTO_LATENCY_BANDS_MS == [5.0, 50.0, 200.0]
