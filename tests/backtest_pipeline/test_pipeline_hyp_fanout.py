"""Tests for pipeline_hyp_fanout."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from backtest_pipeline.src.pipeline_hyp_fanout import fan_out_hyp_reports


def test_fan_out_writes_dirs_without_npz(tmp_path: Path) -> None:
    mbo_result = {
        "all_hypotheses": [
            {
                "hypothesis_id": 1,
                "hypothesis_name": "Test hyp 1",
                "num_trades": 3,
                "net_pnl_usd": 1.5,
                "win_rate": 0.5,
                "expectancy_usd": 0.5,
            },
            {
                "hypothesis_id": 5,
                "hypothesis_name": "Spread blowout",
                "num_trades": 10,
                "net_pnl_usd": -2.0,
                "win_rate": 0.4,
                "expectancy_usd": -0.2,
            },
            {
                "hypothesis_id": 2,
                "hypothesis_name": "Other",
                "num_trades": 0,
                "net_pnl_usd": 0.0,
                "win_rate": 0.0,
                "expectancy_usd": 0.0,
            },
        ]
    }
    out_root = tmp_path / "pipeline_runs"
    rows = fan_out_hyp_reports(
        mbo_result,
        "CPI_2024_09_11_TIGHT",
        out_root,
        repo_root=tmp_path,
        only_model_ids={"HYP_1", "HYP_5"},
        latency_ms=1.0,
    )
    assert len(rows) == 2
    assert (out_root / "HYP_1_CPI_2024_09_11_TIGHT" / "result.json").is_file()
    assert (out_root / "HYP_5_CPI_2024_09_11_TIGHT" / "report.md").is_file()
    payload = json.loads(
        (out_root / "HYP_5_CPI_2024_09_11_TIGHT" / "result.json").read_text(encoding="utf-8")
    )
    assert payload["engine_kind"] == "hyp_mbo"
    assert "SignalBacktester" in payload["backend_label"]
