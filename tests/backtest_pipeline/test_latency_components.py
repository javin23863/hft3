"""Tests for HftBacktest three-component latency helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "packages"))

from backtest_pipeline.src.latency_components import (  # noqa: E402
    build_new_send_to_ack_from_live_stats,
    critical_bands_measured,
    default_component_bands,
    resolve_new_send_to_ack_ms,
)
from backtest_pipeline.src.chi404_latency import resolve_latency_model  # noqa: E402


def test_build_new_send_to_ack_distribution() -> None:
    dist = build_new_send_to_ack_from_live_stats(
        {"count": 200, "p50_us": 3537.0, "p99_us": 9810.0}
    )
    assert dist is not None
    assert dist["ms"]["p99_ms"] == pytest.approx(9.81)
    assert dist["metric"] == "new_send_to_ack"


def test_resolve_new_send_to_ack_prefers_canonical_block() -> None:
    summary = {
        "new_send_to_ack_ms": build_new_send_to_ack_from_live_stats(
            {"count": 10, "p99_us": 5000.0}
        ),
        "live_order_ack_p99_ms": 9.0,
    }
    p99, block, source = resolve_new_send_to_ack_ms(summary)
    assert p99 == pytest.approx(5.0)
    assert block is not None
    assert source == "new_send_to_ack_ms.authoritative"


def test_default_component_bands_marks_critical_open() -> None:
    bands = default_component_bands()
    assert bands["feed_latency_us"]["measurement_status"] == "OPEN"
    assert not critical_bands_measured(bands)


def test_resolve_latency_model_stress_regime() -> None:
    summary_path = _REPO / "runtime" / "latency_reports" / "latency_summary.json"
    if not summary_path.is_file():
        pytest.skip("latency_summary.json missing")
    model = resolve_latency_model(regime="stress", chi404_summary=summary_path)
    assert model["latency_model_family"] == "ConstantLatency"
    assert model["order_entry_latency_ms"] is not None
    assert model["order_response_latency_ms"] is not None
    assert model["order_entry_latency_ms"] + model["order_response_latency_ms"] == pytest.approx(
        model["latency_p99_ms"], rel=1e-6
    )
