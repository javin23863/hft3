"""Matrix runner fold filtering tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_matrix_runner_skips_evaluate_only_folds():
    from workbench.src.optimization.matrix_runner import run_full_matrix_oos

    cfg = {
        "min_parameter_combinations": 2,
        "folds": [
            {
                "id": "D4",
                "name": "holdout",
                "is_start_year": 2018,
                "is_end_year": 2020,
                "oos_start_year": 2023,
                "oos_end_year": 2024,
                "evaluate_only": True,
            },
            {
                "id": "D3",
                "name": "discovery_confirmation",
                "is_start_year": 2018,
                "is_end_year": 2020,
                "oos_start_year": 2021,
                "oos_end_year": 2022,
            },
        ],
    }

    with patch("workbench.src.optimization.matrix_runner.generate_param_grid") as mock_grid:
        mock_grid.return_value = [{"parameter_hash": "h1", "params": {"signal_threshold": 0.1}}]
        with patch("workbench.src.optimization.matrix_runner._events_for_years") as mock_ev:
            ev = MagicMock(event_id="E1", release_date="2018-01-01", npz_present=True)
            mock_ev.return_value = [ev]
            with patch("workbench.src.optimization.matrix_runner._run_events") as mock_run:
                mock_run.return_value = [
                    {"net_pnl": 1.0, "num_trades": 1, "expectancy": 1.0, "event_id": "E1"}
                ]
                with patch("workbench.src.run.engine.WorkbenchEngine"):
                    rows = run_full_matrix_oos(
                        MagicMock(),
                        model_id="HYP_5",
                        symbol="MES.v.0",
                        campaign_id="test",
                        wfc_cfg=cfg,
                    )
    assert rows
    assert all(r["fold_id"] == "D3" for r in rows)
    assert not any(r["fold_id"] == "D4" for r in rows)


def test_matrix_runner_uses_explicit_regime_label():
    from workbench.src.optimization.matrix_runner import run_full_matrix_oos

    cfg = {
        "min_parameter_combinations": 2,
        "folds": [
            {
                "id": "D1",
                "name": "Discovery_2018_to_2019",
                "regime_label": "pre_covid",
                "is_start_year": 2018,
                "is_end_year": 2018,
                "oos_start_year": 2019,
                "oos_end_year": 2019,
            },
        ],
    }

    with patch("workbench.src.optimization.matrix_runner.generate_param_grid") as mock_grid:
        mock_grid.return_value = [{"parameter_hash": "h1", "params": {"signal_threshold": 0.1}}]
        with patch("workbench.src.optimization.matrix_runner._events_for_years") as mock_ev:
            ev = MagicMock(event_id="E1", release_date="2018-01-01", npz_present=True)
            mock_ev.return_value = [ev]
            with patch("workbench.src.optimization.matrix_runner._run_events") as mock_run:
                mock_run.return_value = [
                    {"net_pnl": 1.0, "num_trades": 1, "expectancy": 1.0, "event_id": "E1"}
                ]
                with patch("workbench.src.run.engine.WorkbenchEngine"):
                    rows = run_full_matrix_oos(
                        MagicMock(),
                        model_id="HYP_5",
                        symbol="MES.v.0",
                        campaign_id="test",
                        wfc_cfg=cfg,
                    )
    assert rows[0]["regime_label"] == "pre_covid"
    assert rows[0]["regime_label"] != "Discovery_2018_to_2019"


def test_matrix_runner_forwards_audit_grade_to_engine():
    from workbench.src.optimization.matrix_runner import run_full_matrix_oos

    cfg = {
        "min_parameter_combinations": 2,
        "folds": [
            {
                "id": "D1",
                "regime_label": "pre_covid",
                "is_start_year": 2018,
                "is_end_year": 2018,
                "oos_start_year": 2019,
                "oos_end_year": 2019,
            },
        ],
    }
    engine = MagicMock()
    engine.run.return_value = {
        "report": {"net_pnl": 1.0, "num_trades": 1, "simulated_latency_adjusted_pnl": 1.0}
    }

    with patch("workbench.src.optimization.matrix_runner.generate_param_grid") as mock_grid:
        mock_grid.return_value = [{"parameter_hash": "h1", "params": {"signal_threshold": 0.1}}]
        with patch("workbench.src.optimization.matrix_runner._events_for_years") as mock_ev:
            ev = MagicMock(event_id="E1", release_date="2018-01-01", npz_present=True)
            mock_ev.return_value = [ev]
            with patch("workbench.src.run.engine.WorkbenchEngine", return_value=engine):
                run_full_matrix_oos(
                    MagicMock(),
                    model_id="HYP_5",
                    symbol="MES.v.0",
                    campaign_id="test",
                    wfc_cfg=cfg,
                    audit_grade=False,
                )
    assert engine.run.call_args.kwargs["skip_history_gate"] is True
    assert engine.run.call_args.kwargs["fast_sweep"] is True


def test_matrix_runner_raises_on_missing_npz():
    from workbench.src.optimization.matrix_runner import MatrixFoldDataError, run_full_matrix_oos

    cfg = {
        "min_parameter_combinations": 2,
        "folds": [
            {
                "id": "D3",
                "name": "discovery_confirmation",
                "is_start_year": 2018,
                "is_end_year": 2020,
                "oos_start_year": 2021,
                "oos_end_year": 2022,
            },
        ],
    }

    with patch("workbench.src.optimization.matrix_runner.generate_param_grid") as mock_grid:
        mock_grid.return_value = [{"parameter_hash": "h1", "params": {"signal_threshold": 0.1}}]
        with patch("workbench.src.optimization.matrix_runner._events_for_years") as mock_ev:
            mock_ev.return_value = []
            with patch("workbench.src.run.engine.WorkbenchEngine"):
                with pytest.raises(MatrixFoldDataError, match="Missing IS NPZ"):
                    run_full_matrix_oos(
                        MagicMock(),
                        model_id="HYP_5",
                        symbol="MES.v.0",
                        campaign_id="test",
                        wfc_cfg=cfg,
                    )
