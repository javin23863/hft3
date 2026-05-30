"""CPI event end-to-end workbench smoke."""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NPZ = REPO / "data" / "npz" / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
LATENCY = REPO / "runtime" / "latency_reports" / "latency_summary.json"


@pytest.mark.skipif(not NPZ.is_file(), reason="CPI NPZ not present locally")
def test_workbench_hyp5_cpi():
    from workbench.src.run.engine import WorkbenchEngine

    engine = WorkbenchEngine(REPO)
    out = engine.run(
        "HYP_5",
        "CPI_2024_09_11_TIGHT",
        chi404_summary=LATENCY if LATENCY.is_file() else None,
        seed=42,
    )
    assert "artifact_dir" in out
    assert out["report"]["model_id"] == "HYP_5"
    assert Path(out["artifact_dir"]).joinpath("report.md").is_file()
