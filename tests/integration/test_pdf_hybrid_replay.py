"""Integration: PDF_MODEL_4 hybrid replay on real Databento NPZ (skip if missing)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from backtest.adapters.rithmic_replay_loader import resolve_event_npz

_pdf_script = _REPO / "scripts" / "run_pdf_hybrid_replay.py"
_spec = importlib.util.spec_from_file_location("run_pdf_hybrid_replay", _pdf_script)
_pdf_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_pdf_mod)


@pytest.mark.integration
def test_pdf_hybrid_replay_on_real_npz() -> None:
    event_id = "CPI_2024_09_11_TIGHT"
    try:
        npz_path = resolve_event_npz(event_id, _REPO)
    except FileNotFoundError:
        pytest.skip(
            f"Real NPZ missing for {event_id}. "
            "Run: python scripts/run_offline_pipeline.py --skip-download "
            f"--event-id {event_id}"
        )
    event_meta = _pdf_mod.load_event_row(event_id, _REPO / "packages" / "data_system" / "config" / "events.csv")
    payload = _pdf_mod.run_pdf_hybrid_replay(
        event_id=event_id,
        npz_path=npz_path,
        event_meta=event_meta,
        tick_size=0.25,
        latency_ms=1.0,
        latency_source="integration test",
        queue_model="LogProbQueueModel2",
        step_ns=1_000_000_000,
    )
    result = payload["result"]
    assert "error" not in result
    assert result["steps"] > 0
    assert isinstance(result["balance"], float)
    assert result["num_trades"] >= 0
