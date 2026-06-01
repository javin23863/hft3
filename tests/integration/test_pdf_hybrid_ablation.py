"""Integration: PDF defensive ablation on real NPZ (skip if missing)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from backtest.adapters.rithmic_replay_loader import resolve_event_npz
from backtest_pipeline.src.event_meta import load_event_row
from backtest_pipeline.src.pdf_defensive_config import all_defensive_configs
from backtest_pipeline.src.pdf_hybrid_ablation import run_defensive_ablation_matrix


def _cpi_npz_or_skip():
    event_id = "CPI_2024_09_11_TIGHT"
    try:
        npz_path = resolve_event_npz(event_id, _REPO)
    except FileNotFoundError:
        pytest.skip(
            f"Real NPZ missing for {event_id}. "
            "Run: python scripts/run_offline_pipeline.py --skip-download "
            f"--event-id {event_id}"
        )
    if not npz_path.is_file():
        pytest.skip(f"NPZ not on disk: {npz_path}")
    event_meta = load_event_row(event_id, _REPO / "packages" / "data_system" / "config" / "events.csv")
    return npz_path, event_meta


@pytest.mark.integration
def test_pdf_defensive_ablation_smoke_two_modes() -> None:
    npz_path, event_meta = _cpi_npz_or_skip()
    from backtest_pipeline.src.pdf_defensive_config import DefensiveConfig

    matrix = run_defensive_ablation_matrix(
        npz_path=npz_path,
        event_meta=event_meta,
        latency_ms=1.0,
        configs=[
            DefensiveConfig(use_ofi=False, use_vpin=False),
            DefensiveConfig(use_ofi=True, use_vpin=True),
        ],
        max_steps=500,
    )
    assert len(matrix["modes"]) == 2
    _assert_matrix_health(matrix)


@pytest.mark.integration
@pytest.mark.slow
def test_pdf_defensive_ablation_four_modes_chi404_latency() -> None:
    npz_path, event_meta = _cpi_npz_or_skip()
    from backtest_pipeline.src.chi404_latency import (
        DEFAULT_CHI404_SUMMARY,
        resolve_replay_latency_ms,
    )

    latency_ms = None
    try:
        resolve_replay_latency_ms(latency_ms=None, chi404_summary=DEFAULT_CHI404_SUMMARY)
        latency_ms = None
    except ValueError:
        latency_ms = 1.0

    matrix = run_defensive_ablation_matrix(
        npz_path=npz_path,
        event_meta=event_meta,
        latency_ms=latency_ms,
        max_steps=500,
    )
    assert len(matrix["modes"]) == len(all_defensive_configs())
    if latency_ms is not None:
        assert matrix.get("latency_source") in ("CLI --latency-ms", None)
    else:
        assert matrix.get("latency_source")
    _assert_matrix_health(matrix)


def _assert_matrix_health(matrix: dict) -> None:
    for mode in matrix["modes"]:
        assert "error" not in mode.get("metrics", {})
        m = mode["metrics"]
        assert m["steps"] > 0
        assert "net_pnl_after_fee" in m
        assert "quote_refresh_count" in m
