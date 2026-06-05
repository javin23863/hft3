"""Catalog-backed event end-to-end workbench smoke."""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LATENCY = REPO / "runtime" / "latency_reports" / "latency_summary.json"


def _available_catalog_event():
    from workbench.src.data.event_catalog import list_campaign_events, load_periods

    candidates = []
    for period in load_periods(REPO):
        for event in list_campaign_events("SPREAD_BLOWOUT_RECOMPRESSION", period, "MES.v.0", REPO):
            if event.npz_present and event.npz_symbol_used == "MES.v.0" and event.npz_path.is_file():
                candidates.append(event)
    if not candidates:
        pytest.skip("no MES catalog MBO NPZ present locally")
    return min(candidates, key=lambda event: event.npz_path.stat().st_size)


def test_workbench_catalog_event_e2e():
    from workbench.src.run.engine import WorkbenchEngine

    event = _available_catalog_event()
    engine = WorkbenchEngine(REPO)
    out = engine.run(
        "SPREAD_BLOWOUT_RECOMPRESSION",
        event.event_id,
        symbol="MES.v.0",
        npz_path=event.npz_path,
        chi404_summary=LATENCY if LATENCY.is_file() else None,
        seed=42,
    )
    assert "artifact_dir" in out
    assert out["report"]["model_id"] in ("HYP_5", "SPREAD_BLOWOUT_RECOMPRESSION")
    assert Path(out["artifact_dir"]).joinpath("report.md").is_file()
