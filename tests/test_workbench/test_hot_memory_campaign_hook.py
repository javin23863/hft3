"""Campaign runner hot-memory telemetry hook."""

from __future__ import annotations

import json
from pathlib import Path

from workbench.src.run.campaign_runner import run_campaign

REPO = Path(__file__).resolve().parents[2]


def test_dry_run_includes_hot_memory_telemetry():
    result = run_campaign(REPO, "HYP_5", "MES.v.0", dry_run=True, allow_partial=True)
    assert result.status == "DRY_RUN"
    preview_path = (
        REPO / "artifacts" / "research_cards" / "workbench_runs" / result.campaign_id / "dry_run_preview.json"
    )
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    telemetry = preview["diagnostics"]["hot_memory_telemetry"]
    assert "hot_executable" in telemetry
    assert "ES" in telemetry["hot_executable"]
    assert "resident" in telemetry
    assert "core_protected_symbols" in telemetry
