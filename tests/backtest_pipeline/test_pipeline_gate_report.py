"""Tests for pipeline_gate_report finalize."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from backtest_pipeline.src.pipeline_gate_report import finalize_catalog_models


def test_finalize_smoke_fills_not_run_rows() -> None:
    executed = [
        {
            "model_id": "HYBRID_EXECUTION",
            "engine_kind": "pdf_hybrid_replay",
            "status": "PASS",
            "artifact_dir": "research_cards/HYBRID_EXECUTION_hybrid_replay",
            "num_trades": 5,
            "net_pnl_usd": 1.0,
            "backend_label": "ReplayRunner quote-engine (queue fills)",
        },
        {
            "model_id": "SECOND_WAVE_CONTINUATION",
            "engine_kind": "hyp_mbo",
            "status": "PASS",
            "artifact_dir": "research_cards/pipeline_runs/SECOND_WAVE_CONTINUATION_X",
            "num_trades": 1,
            "net_pnl_usd": 0.1,
            "backend_label": "SignalBacktester MBO pipeline (research path)",
        },
    ]
    models = finalize_catalog_models(executed, "smoke")
    assert len(models) == 55
    not_run = [m for m in models if m["status"] == "NOT_RUN_SMOKE"]
    assert len(not_run) == 53
    assert models[0]["model_id"]  # sorted by all_model_ids order
