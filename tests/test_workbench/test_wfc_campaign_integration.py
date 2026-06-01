"""Campaign integration with WFC gate blocking promotion."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from workbench.src.core.params import DEFAULT_STRATEGY_PARAMS
from workbench.src.robustness.pack import RobustnessResult
from workbench.src.robustness.wfc.gate import WfcResult
from workbench.src.run.campaign_runner import PeriodResult, _holdout_used_for_tuning, run_campaign

REPO = Path(__file__).resolve().parents[2]


def _cleanup_artifact(result):
    """Remove the artifact directory created by a campaign run."""
    if result and result.artifact_dir:
        ap = Path(result.artifact_dir)
        if ap.exists():
            shutil.rmtree(str(ap), ignore_errors=True)


@patch("workbench.src.run.campaign_runner.run_full_matrix_oos")
@patch("workbench.src.run.campaign_runner.load_wfc_config")
@patch("workbench.src.run.campaign_runner.load_parameter_bounds")
@patch("workbench.src.run.engine.WorkbenchEngine")
@patch("workbench.src.run.campaign_runner.list_campaign_events")
def test_wfc_fail_blocks_promotion(mock_list, MockEngine, mock_bounds, mock_wfc, mock_matrix):
    mock_wfc.return_value = {
        "enabled": True,
        "primary_metric": "sharpe",
        "pearson_min": 0.20,
        "spearman_min": 0.20,
        "correlation_p_value_max": 0.10,
        "min_parameter_combinations": 2,
        "min_walk_forward_folds": 1,
        "min_positive_fold_ratio": 0.0,
        "require_oos_net_profit_positive": False,
        "require_oos_risk_adjusted_positive": False,
        "bootstrap_samples": 10,
        "permutation_samples": 10,
        "outlier_winsor_pct": 0.01,
        "min_oos_trade_count": {"default": 1},
    }
    mock_bounds.return_value = {"signal_threshold": [0.1, 0.2]}
    mock_matrix.return_value = [
        {
            "parameter_hash": "a",
            "fold_id": "D1",
            "is_metrics": {"sharpe": 1.0, "net_return": 1.0, "trade_count": 10},
            "oos_metrics": {"sharpe": -1.0, "net_return": -1.0, "trade_count": 10},
        },
        {
            "parameter_hash": "b",
            "fold_id": "D1",
            "is_metrics": {"sharpe": 2.0, "net_return": 2.0, "trade_count": 10},
            "oos_metrics": {"sharpe": -2.0, "net_return": -2.0, "trade_count": 10},
        },
    ]
    mock_list.return_value = []

    result = run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        dry_run=False,
        allow_partial=True,
        audit_grade=False,
    )
    assert result.status == "FAIL"
    summary_path = Path(result.artifact_dir) / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["promote_candidate"] is False
    assert summary["wfc_status"] in ("FAIL", "ERROR")


@patch("workbench.src.run.campaign_runner.run_full_matrix_oos")
@patch("workbench.src.run.campaign_runner.load_wfc_config")
@patch("workbench.src.run.campaign_runner.load_parameter_bounds")
@patch("workbench.src.run.engine.WorkbenchEngine")
@patch("workbench.src.run.campaign_runner.list_campaign_events")
def test_wfc_pass_negative_plateau_oos_blocks_promotion(
    mock_list, MockEngine, mock_bounds, mock_wfc, mock_matrix
):
    """WFC PASS but selected plateau has negative OOS → promotion blocked."""
    mock_wfc.return_value = {
        "enabled": True,
        "primary_metric": "sharpe",
        "pearson_min": 0.20,
        "spearman_min": 0.20,
        "correlation_p_value_max": 0.10,
        "min_parameter_combinations": 20,
        "min_walk_forward_folds": 2,
        "min_positive_fold_ratio": 0.0,
        "require_oos_net_profit_positive": False,
        "require_oos_risk_adjusted_positive": False,
        "bootstrap_samples": 10,
        "permutation_samples": 10,
        "outlier_winsor_pct": 0.01,
        "min_oos_trade_count": {"default": 1},
    }
    mock_bounds.return_value = {"signal_threshold": [0.05, 0.5]}
    import random
    rng = random.Random(42)
    n = 25
    rows = []
    for fi, fold in enumerate(("D1", "D2", "D3")):
        for i in range(n):
            base_is = 1.0 + float(i) * 0.5
            if i == 0:
                ph = "h0_bad"
                is_v = 15.0
                oos_v = -5.0
            else:
                ph = f"h{i}"
                is_v = base_is + fi * 0.3 + rng.gauss(0, 0.05)
                oos_v = is_v * 0.8 + 0.5 + rng.gauss(0, 0.05)
            rows.append(
                {
                    "parameter_hash": ph,
                    "fold_id": fold,
                    "asset": "MES.v.0",
                    "regime_label": "macro",
                    "params": {"signal_threshold": 0.1},
                    "is_metrics": {"sharpe": is_v, "net_return": is_v, "trade_count": 10},
                    "oos_metrics": {"sharpe": oos_v, "net_return": oos_v, "trade_count": 10},
                }
            )
    mock_matrix.return_value = rows
    mock_list.return_value = []
    result = run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        dry_run=False,
        allow_partial=True,
        audit_grade=False,
    )
    assert result.status == "FAIL"
    summary_path = Path(result.artifact_dir) / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["promote_candidate"] is False
    assert summary["wfc_status"] == "PASS"
    assert any(
        "Selected plateau OOS" in str(r)
        for r in summary.get("wfc", {}).get("rejection_reasons", [])
    )
    _cleanup_artifact(result)


@patch("workbench.src.robustness.pack.run_robustness_pack")
@patch("workbench.src.robustness.wfc.write_wfc_artifacts")
@patch("workbench.src.run.campaign_runner.save_matrix_rows")
@patch("workbench.src.run.campaign_runner.run_full_matrix_oos")
@patch("workbench.src.run.campaign_runner.load_wfc_config")
@patch("workbench.src.run.campaign_runner.load_parameter_bounds")
@patch("workbench.src.run.engine.WorkbenchEngine")
@patch("workbench.src.run.campaign_runner.list_campaign_events")
def test_wfc_oos_fail_skips_period_loop(
    mock_list,
    MockEngine,
    mock_bounds,
    mock_wfc,
    mock_matrix,
    _save_rows,
    _wfc_art,
    mock_robust,
):
    """WFC PASS but plateau OOS negative → skip_periods blocks period loop even with runnable events."""
    from workbench.src.data.event_catalog import EventSpec

    mock_wfc.return_value = {
        "enabled": True,
        "primary_metric": "sharpe",
        "pearson_min": 0.20,
        "spearman_min": 0.20,
        "correlation_p_value_max": 0.10,
        "min_parameter_combinations": 20,
        "min_walk_forward_folds": 2,
        "min_positive_fold_ratio": 0.0,
        "require_oos_net_profit_positive": False,
        "require_oos_risk_adjusted_positive": False,
        "bootstrap_samples": 10,
        "permutation_samples": 10,
        "outlier_winsor_pct": 0.01,
        "min_oos_trade_count": {"default": 1},
    }
    mock_bounds.return_value = {"signal_threshold": [0.05, 0.5]}
    mock_robust.return_value = RobustnessResult(passed=True)
    import random
    rng = random.Random(42)
    n = 25
    rows = []
    for fi, fold in enumerate(("D1", "D2", "D3")):
        for i in range(n):
            base_is = 1.0 + float(i) * 0.5
            if i == 0:
                ph = "h0_bad"
                is_v = 15.0
                oos_v = -5.0
            else:
                ph = f"h{i}"
                is_v = base_is + fi * 0.3 + rng.gauss(0, 0.05)
                oos_v = is_v * 0.8 + 0.5 + rng.gauss(0, 0.05)
            rows.append(
                {
                    "parameter_hash": ph,
                    "fold_id": fold,
                    "asset": "MES.v.0",
                    "regime_label": "macro",
                    "params": {"signal_threshold": 0.1},
                    "is_metrics": {"sharpe": is_v, "net_return": is_v, "trade_count": 10},
                    "oos_metrics": {"sharpe": oos_v, "net_return": oos_v, "trade_count": 10},
                }
            )
    mock_matrix.return_value = rows

    ev = EventSpec(
        event_id="CPI_2018_01_11_TIGHT",
        event_type="CPI",
        release_date="2018-01-11",
        event_context="CPI_TIGHT",
        symbol="MES.v.0",
        npz_path=REPO / "data" / "npz" / "x.npz",
        npz_present=True,
        start_utc=None,
        end_utc=None,
    )
    mock_list.return_value = [ev]
    mock_engine = MagicMock()
    MockEngine.return_value = mock_engine
    mock_engine.run.return_value = {
        "artifact_dir": str(REPO / "artifacts" / "research_cards" / "workbench_runs" / "oostest"),
        "report": {
            "net_pnl": 10.0,
            "num_trades": 5,
            "survives_cpp_execution_delay": True,
        },
    }

    result = run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        dry_run=False,
        allow_partial=False,
        audit_grade=False,
    )
    assert result.status == "FAIL"
    assert len(result.periods) == 0, "Period loop should be skipped when plateau OOS fails"
    summary_path = Path(result.artifact_dir) / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["promote_candidate"] is False
    assert summary["wfc_status"] == "PASS"
    assert any(
        "Selected plateau OOS" in str(r)
        for r in summary.get("wfc", {}).get("rejection_reasons", [])
    )
    _cleanup_artifact(result)


@patch("workbench.src.run.campaign_runner.load_wfc_config")
@patch("workbench.src.run.campaign_runner.load_parameter_bounds")
@patch("workbench.src.run.engine.WorkbenchEngine")
@patch("workbench.src.run.campaign_runner.list_campaign_events")
def test_wfc_skipped_when_no_bounds(mock_list, MockEngine, mock_bounds, mock_wfc):
    mock_wfc.return_value = {"enabled": True, "require_bounds": True}
    mock_bounds.return_value = {}
    mock_list.return_value = []

    result = run_campaign(
        REPO,
        "PDF_MODEL_1",
        "MES.v.0",
        dry_run=False,
        allow_partial=True,
        audit_grade=False,
    )
    summary_path = Path(result.artifact_dir) / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["wfc_status"] == "SKIPPED"
    assert result.status != "FAIL" or summary.get("periods") is not None


@patch("workbench.src.robustness.pack.run_robustness_pack")
@patch("workbench.src.robustness.wfc.write_wfc_artifacts")
@patch("workbench.src.run.campaign_runner.save_matrix_rows")
@patch("workbench.src.run.campaign_runner.select_robust_plateau")
@patch("workbench.src.run.campaign_runner.evaluate_wfc_gate")
@patch("workbench.src.run.campaign_runner.run_full_matrix_oos")
@patch("workbench.src.run.campaign_runner.load_wfc_config")
@patch("workbench.src.run.campaign_runner.load_parameter_bounds")
@patch("workbench.src.run.engine.WorkbenchEngine")
@patch("workbench.src.run.campaign_runner.list_campaign_events")
def test_confirmation_uses_default_params_when_tune_disallowed(
    mock_list,
    MockEngine,
    mock_bounds,
    mock_wfc,
    mock_matrix,
    mock_eval,
    mock_plateau,
    _save_rows,
    _wfc_art,
    mock_robust,
):
    from workbench.src.data.event_catalog import EventSpec

    mock_wfc.return_value = {
        "enabled": True,
        "primary_metric": "sharpe",
        "pearson_min": 0.0,
        "spearman_min": 0.0,
        "correlation_p_value_max": 1.0,
        "min_parameter_combinations": 2,
        "min_walk_forward_folds": 1,
        "min_positive_fold_ratio": 0.0,
        "require_oos_net_profit_positive": False,
        "require_oos_risk_adjusted_positive": False,
        "bootstrap_samples": 10,
        "permutation_samples": 10,
        "outlier_winsor_pct": 0.01,
        "min_oos_trade_count": {"default": 1},
        "folds": [],
    }
    mock_bounds.return_value = {"signal_threshold": [0.05, 0.15]}
    mock_matrix.return_value = [{"parameter_hash": "h1", "fold_id": "D1"}]
    mock_eval.return_value = WfcResult(
        run_id="c",
        model_id="HYP_5",
        wfc_status="PASS",
        n_parameter_combinations=2,
        n_folds=1,
    )
    tuned = {"signal_threshold": 0.05}
    mock_plateau.return_value = tuned
    mock_robust.return_value = RobustnessResult(passed=True)

    ev = EventSpec(
        event_id="CPI_2018_01_11_TIGHT",
        event_type="CPI",
        release_date="2018-01-11",
        event_context="CPI_TIGHT",
        symbol="MES.v.0",
        npz_path=REPO / "data" / "npz" / "x.npz",
        npz_present=True,
        start_utc=None,
        end_utc=None,
    )
    artifact = REPO / "artifacts" / "research_cards" / "workbench_runs" / "tune_test_run"
    artifact.mkdir(parents=True, exist_ok=True)
    run_report = {
        "run_id": "x",
        "artifact_dir": str(artifact),
        "report": {
            "net_pnl": 1.0,
            "num_trades": 2,
            "survives_cpp_execution_delay": True,
        },
    }
    params_by_period: dict[str, dict] = {}
    current_period: list[str] = []

    def _list_events(model_id, period, symbol, repo_root):
        current_period.append(period.name)
        return [ev]

    def _run_event(*args, **kwargs):
        params_by_period[current_period[-1]] = kwargs["strategy_params"]
        return run_report

    mock_list.side_effect = _list_events
    mock_engine = MagicMock()
    MockEngine.return_value = mock_engine
    mock_engine.run.side_effect = _run_event

    run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        allow_partial=True,
        audit_grade=False,
    )

    assert params_by_period["Discovery"] == tuned
    assert params_by_period["Confirmation"] == dict(DEFAULT_STRATEGY_PARAMS)
    assert params_by_period["Holdout"] == dict(DEFAULT_STRATEGY_PARAMS)


def test_holdout_used_for_tuning_false_when_defaults_on_holdout(tmp_path):
    wf_cfg = {"holdout_evaluate_only": ["Holdout"]}
    period_dir = tmp_path / "periods" / "Holdout"
    period_dir.mkdir(parents=True)
    (period_dir / "period_summary.json").write_text(
        json.dumps({"params_used": dict(DEFAULT_STRATEGY_PARAMS)}),
        encoding="utf-8",
    )
    periods = [
        PeriodResult(
            name="Holdout",
            gate_pass=True,
            evaluate_only=True,
            net_pnl=1.0,
            num_trades=1,
            expectancy=1.0,
            events_run=1,
            events_missing=0,
            survives_cpp=True,
        )
    ]
    assert _holdout_used_for_tuning(tmp_path, periods, wf_cfg) is False
