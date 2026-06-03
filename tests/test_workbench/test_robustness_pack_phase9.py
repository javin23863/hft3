from __future__ import annotations

from workbench.src.robustness.pack import REQUIRED_ROBUSTNESS_CHECKS, run_robustness_pack


def _passing_metrics() -> dict:
    return {
        "net_pnl": 100.0,
        "num_trades": 10,
        "expectancy": 10.0,
        "feature_leakage_detected": False,
        "label_leakage_detected": False,
        "timestamp_leakage_detected": False,
        "future_data_leakage_detected": False,
        "parameter_stability_score": 0.90,
        "regime_stability_score": 0.80,
        "max_drawdown": -10.0,
        "tail_loss_p95": -20.0,
        "turnover": 100.0,
        "transaction_cost_sensitivity_pass": True,
        "slippage_sensitivity_pass": True,
        "survives_cpp_execution_delay": True,
        "liquidity_capacity_pass": True,
        "event_window_stability_pass": True,
        "data_resolution_eligible": True,
        "model_combination_attribution_complete": True,
        "model_combination_degradation_pass": True,
        "registry_eligible": True,
        "artifact_complete": True,
        "purged_cv_pass": True,
    }


def _passing_periods() -> list[dict]:
    return [
        {"name": "Discovery", "gate_pass": True},
        {"name": "Confirmation", "gate_pass": True},
        {"name": "Holdout", "gate_pass": True},
        {"name": "Recent holdout", "gate_pass": True},
    ]


def test_phase9_required_robustness_check_registry_has_25_names():
    assert len(REQUIRED_ROBUSTNESS_CHECKS) == 25
    assert len(set(REQUIRED_ROBUSTNESS_CHECKS)) == 25


def test_phase9_pack_emits_all_required_checks_in_order():
    result = run_robustness_pack(
        _passing_metrics,
        [10.0] * 30,
        sweep_count=2,
        campaign_periods=_passing_periods(),
    )

    assert [check.name for check in result.checks] == list(REQUIRED_ROBUSTNESS_CHECKS)
    assert len(result.checks_dict()) == 25
    assert result.passed is True


def test_phase9_missing_required_inputs_are_pending_and_blocking():
    result = run_robustness_pack(
        lambda: {"net_pnl": 100.0, "num_trades": 10, "expectancy": 10.0},
        [10.0] * 10,
        sweep_count=1,
    )

    checks = {check.name: check for check in result.checks}
    assert checks["feature_leakage"].status == "PENDING"
    assert checks["walk_forward_validation"].status == "PENDING"
    assert result.passed is False


def test_phase9_empty_trade_sample_blocks_monte_carlo_checks():
    result = run_robustness_pack(
        _passing_metrics,
        [],
        sweep_count=1,
        campaign_periods=_passing_periods(),
    )

    checks = {check.name: check for check in result.checks}
    assert checks["monte_carlo_sharpe"].status == "PENDING"
    assert checks["monte_carlo_drawdown"].status == "PENDING"
    assert checks["purged_cv"].status == "PENDING"
    assert result.purged_cv == []
    assert result.passed is False


def test_phase9_undersized_trade_sample_keeps_purged_cv_pending():
    result = run_robustness_pack(
        _passing_metrics,
        [10.0] * 4,
        sweep_count=1,
        campaign_periods=_passing_periods(),
    )

    checks = {check.name: check for check in result.checks}
    assert checks["purged_cv"].status == "PENDING"
    assert result.purged_cv == []
    assert result.passed is False


def test_phase9_malformed_and_nonfinite_inputs_fail_closed():
    metrics = _passing_metrics()
    metrics["feature_leakage_detected"] = "False"
    metrics["parameter_stability_score"] = float("inf")
    metrics["regime_stability_score"] = True

    result = run_robustness_pack(
        lambda: metrics,
        [10.0] * 10,
        sweep_count=1,
        campaign_periods=_passing_periods(),
    )

    checks = {check.name: check for check in result.checks}
    assert checks["feature_leakage"].status == "FAIL"
    assert checks["feature_leakage"].reason_code == "ROBUSTNESS_INPUT_MALFORMED"
    assert checks["parameter_stability"].status == "FAIL"
    assert checks["parameter_stability"].reason_code == "ROBUSTNESS_INPUT_MALFORMED"
    assert checks["regime_stability"].status == "FAIL"
    assert checks["regime_stability"].reason_code == "ROBUSTNESS_INPUT_MALFORMED"
    assert result.passed is False

    sample_result = run_robustness_pack(
        _passing_metrics,
        [True] * 30,  # type: ignore[list-item]
        sweep_count=1,
        campaign_periods=_passing_periods(),
    )
    sample_checks = {check.name: check for check in sample_result.checks}
    assert sample_checks["purged_cv"].status == "PENDING"
    assert sample_checks["monte_carlo_sharpe"].status == "PENDING"


def test_phase9_malformed_thresholds_fail_closed_without_raising():
    metrics = _passing_metrics()
    metrics["parameter_stability_min"] = "bad"
    metrics["regime_stability_min"] = True

    result = run_robustness_pack(
        lambda: metrics,
        [10.0] * 10,
        sweep_count=1,
        campaign_periods=_passing_periods(),
    )

    checks = {check.name: check for check in result.checks}
    assert checks["parameter_stability"].status == "FAIL"
    assert checks["parameter_stability"].reason_code == "ROBUSTNESS_INPUT_MALFORMED"
    assert checks["regime_stability"].status == "FAIL"
    assert checks["regime_stability"].reason_code == "ROBUSTNESS_INPUT_MALFORMED"
    assert result.passed is False


def test_phase9_missing_drawdown_and_tail_inputs_are_pending():
    metrics = _passing_metrics()
    metrics.pop("max_drawdown")
    metrics.pop("tail_loss_p95")

    result = run_robustness_pack(
        lambda: metrics,
        [10.0] * 10,
        sweep_count=1,
        campaign_periods=_passing_periods(),
    )

    checks = {check.name: check for check in result.checks}
    assert checks["drawdown_limit"].status == "PENDING"
    assert checks["tail_risk_limit"].status == "PENDING"
    assert result.passed is False


def test_phase9_incomplete_walk_forward_periods_are_pending():
    result = run_robustness_pack(
        _passing_metrics,
        [10.0] * 10,
        sweep_count=1,
        campaign_periods=[{"name": "Discovery", "gate_pass": True}],
    )

    checks = {check.name: check for check in result.checks}
    assert result.walk_forward["status"] == "INCOMPLETE"
    assert checks["walk_forward_validation"].status == "PENDING"
    assert result.passed is False


def test_phase9_empty_campaign_periods_are_pending_evidence():
    result = run_robustness_pack(
        _passing_metrics,
        [10.0] * 10,
        sweep_count=1,
        campaign_periods=[],
    )

    checks = {check.name: check for check in result.checks}
    assert result.walk_forward["status"] == "EMPTY"
    assert checks["walk_forward_validation"].status == "PENDING"
    assert result.passed is False


def test_phase9_purged_cv_pass_requires_generated_folds():
    result = run_robustness_pack(
        _passing_metrics,
        [10.0] * 10,
        n_purged_splits=0,
        sweep_count=1,
        campaign_periods=_passing_periods(),
    )

    checks = {check.name: check for check in result.checks}
    assert result.purged_cv == []
    assert checks["purged_cv"].status == "PENDING"
    assert result.passed is False


def test_phase9_failed_required_check_blocks_pack():
    metrics = _passing_metrics()
    metrics["future_data_leakage_detected"] = True

    result = run_robustness_pack(
        lambda: metrics,
        [10.0] * 10,
        sweep_count=1,
        campaign_periods=_passing_periods(),
    )

    checks = {check.name: check for check in result.checks}
    assert checks["future_data_leakage"].status == "FAIL"
    assert result.passed is False
