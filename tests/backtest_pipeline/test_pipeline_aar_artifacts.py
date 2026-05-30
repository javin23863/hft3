"""Tests for hybrid-gate AAR artifact materialization."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from backtest_pipeline.src.pipeline_aar_artifacts import write_hybrid_aar_artifacts


def test_write_hybrid_aar_artifacts(tmp_path: Path) -> None:
    out = write_hybrid_aar_artifacts(
        tmp_path / "aar",
        event_id="CPI_2024_09_11_TIGHT",
        symbol="MES.v.0",
        hybrid_payload={
            "result": {"num_trades": 10, "balance": 1.5, "fee": 0.0, "position": 0.0, "steps": 100},
            "gate_latency_note": "test",
        },
        ablation_matrix={
            "modes": [
                {
                    "mode_id": "hybrid_full",
                    "use_ofi": True,
                    "use_vpin": True,
                    "metrics": {"num_trades": 10, "net_pnl_after_fee": 1.5, "mean_vpin": 0.2},
                }
            ]
        },
        latency_ms=1.0,
        latency_source="test",
    )
    diag = json.loads((out / "diagnostics.json").read_text(encoding="utf-8"))
    assert diag["execution_assumptions"] == "quote_engine"
    assert diag["num_trades"] == 10
    assert len(diag["ablation_modes"]) == 1
