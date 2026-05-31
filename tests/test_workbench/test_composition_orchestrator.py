"""Composition orchestrator phased defensive stack."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from workbench.src.core.composition import CompositionTrace, DefensiveStub, ModelComposition
from workbench.src.registry.composition_orchestrator import CompositionOrchestrator
from workbench.src.registry.model_catalog import resolve_stub_dependencies
from workbench.src.run.run_context import RunContext

REPO = Path(__file__).resolve().parents[2]


def test_pdf_model_11_pulls_pdf_model_4_dependency():
    stubs = [DefensiveStub("HAWKES_TOXIC_FLOW", "during", 2500.0)]
    resolved = resolve_stub_dependencies(stubs, REPO)
    ids = {s.model_id for s in resolved}
    assert "HYBRID_EXECUTION" in ids
    assert "HAWKES_TOXIC_FLOW" in ids


@patch("workbench.src.registry.composition_orchestrator.get_model_by_id")
def test_before_veto_blocks_backtest(mock_get_model):
    mock_primary = MagicMock()
    mock_primary.build_features.return_value = [1]
    mock_primary.generate_signals.return_value = 0.5
    mock_get_model.return_value = mock_primary

    ctx = RunContext(
        repo_root=REPO,
        run_id="test",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        event_id="CPI_2018_01_11_TIGHT",
        npz_path=REPO / "x.npz",
        events=np.array([]),
    )
    ctx.metadata["pdf_bars"] = []

    composition = ModelComposition(
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        defensive_stubs=[DefensiveStub("QUANTUM_SPREAD_DEFENSE", "before", 50.0)],
    )

    orch = CompositionOrchestrator()

    mock_payload = MagicMock(cancel_all_quotes=True)

    def fake_pdf_stub(stub, ctx, phase):
        ctx.metadata.setdefault("pdf_composition_outputs", {})[stub.model_id] = MagicMock(payload=mock_payload)
        return {"cancel_all_quotes": True}, 10.0

    with patch.object(orch, "_run_pdf_stub", side_effect=fake_pdf_stub):
        with patch.object(orch, "_ensure_pdf_context"):
            with patch("workbench.src.registry.composition_orchestrator.get_catalog_entry") as mock_cat:
                mock_cat.return_value = MagicMock(blocks_trade=True)
                result, trace = orch.run(ctx, composition)

    assert trace.trades_vetoed >= 1
    assert trace.signal_adjusted == 0.0
    assert result.num_trades == 0


def test_phase_budget_summary_sums():
    from workbench.src.registry.model_catalog import phase_budget_summary

    comp = ModelComposition(
        "SPREAD_BLOWOUT_RECOMPRESSION",
        [
            DefensiveStub("QUANTUM_SPREAD_DEFENSE", "before", 50.0),
            DefensiveStub("VPIN_TOXICITY", "continuous", 2500.0),
        ],
    )
    totals = phase_budget_summary(comp, REPO)
    assert totals["before"] >= 50.0
    assert totals["continuous"] >= 2500.0


def test_vpin_continuous_scales_once():
    orch = CompositionOrchestrator()
    ctx = RunContext(
        repo_root=REPO,
        run_id="test",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        event_id="CPI_2018_01_11_TIGHT",
        npz_path=REPO / "x.npz",
        events=np.array([]),
    )
    ctx.metadata["pdf_bars"] = []
    stub = DefensiveStub("VPIN_TOXICITY", "continuous", 2500.0)
    trace = CompositionTrace(primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION")
    mock_payload = MagicMock(VPIN_percentile=0.995)
    mock_out = MagicMock(payload=mock_payload)

    with patch.object(orch, "_run_pdf_stub", return_value=({"VPIN_percentile": 0.995}, 1.0)):
        with patch("workbench.src.registry.composition_orchestrator.get_catalog_entry") as mock_cat:
            mock_cat.return_value = MagicMock(blocks_trade=False)
            ctx.metadata["pdf_composition_outputs"] = {"VPIN_TOXICITY": mock_out}
            adjusted, _ = orch._apply_stub(stub, ctx, trace, 1.0, "continuous")
    assert adjusted == pytest.approx(0.5)
