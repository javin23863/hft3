from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest_pipeline.src import hftbacktest_realism as hbt3
from backtest_pipeline.src.vectorbt_adapter import compute_screening_artifact_hash


NATIVE_CPP_LATENCY_EVIDENCE = "reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json"


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _screening_artifact(candidate_id: str = "cand_hbt3") -> dict:
    artifact = {
        "run_id": "vbt_handoff_hbt3",
        "created_at_utc": "2026-06-16T00:00:00+00:00",
        "screening_backend": "vectorbt",
        "vectorbt_version": "1.0.0",
        "vectorbt_engine": "rust",
        "engine_parity_status": "rust_available",
        "rust_engine_required_for_scope": True,
        "rust_engine_available": True,
        "license_review": "pilot_license_review_recorded",
        "screening_scope": "pilot",
        "research_clock": "event_window_pilot",
        "candidate_ids": [candidate_id],
        "candidate_reasons": {candidate_id: "queued_for_vectorbt_screen"},
        "promoted_ids": [candidate_id],
        "promoted_reasons": {candidate_id: "all_gates_passed"},
        "rejected_ids": [],
        "rejected_reasons": {},
        "no_lookahead_signal_shift_proof": "close-derived signals shifted one executable bar",
        "promoted": [
            {
                "candidate_id": candidate_id,
                "hypothesis_id": "HYP_5",
                "model_id": "HYP_5",
                "symbol": "MES",
                "param_values": {"signal_threshold": 0.15},
                "research_clock": "event_window_pilot",
                "opportunity_type_or_event_type": "CPI",
                "parameter_values": {"signal_threshold": 0.15},
                "parameter_values_hash": "sha256:parameter-values",
                "trials_budget_tier": "pilot",
                "in_sample_metrics": {"sharpe": 1.2, "net_pnl": 125.0},
                "out_of_sample_metrics": {"sharpe": 1.0, "net_pnl": 80.0},
                "walk_forward_metrics": {
                    "fold_matrix": [["2018-2020", "2021"], ["2019-2021", "2022"]],
                    "fold_train_test_dates": [
                        {"train": ["2018-01-01", "2020-12-31"], "test": ["2021-01-01", "2021-12-31"]},
                        {"train": ["2019-01-01", "2021-12-31"], "test": ["2022-01-01", "2022-12-31"]},
                    ],
                    "fold_metrics": [{"sharpe": 1.0}, {"sharpe": 1.1}],
                    "walk_forward_efficiency": 0.72,
                    "fold_dispersion": 0.08,
                    "is_oos_gap": 0.12,
                    "oos_decay": 0.18,
                },
                "wfc_metrics": {
                    "metric_in_sample": [1.2, 1.0, 0.9],
                    "metric_out_of_sample": [1.0, 0.86, 0.78],
                    "pearson": 0.64,
                    "spearman": 0.58,
                    "scatter_data": [{"is": 1.2, "oos": 1.0}],
                    "quadrant_counts": {"high_is_high_oos": 2, "high_is_low_oos": 0},
                    "high_is_high_oos_region": {"threshold": 0.8, "count": 2},
                    "rejection_reason": None,
                },
                "surface_stability_metrics": {"plateau_score": 0.81},
                "robustness_gate_scope": "pilot",
                "wfc_status": "pass",
                "dsr_status": "pass",
                "pbo_status": "pass",
                "cscv_status": "pass",
                "robustness_artifact_staleness": "fresh",
                "trade_count": 32,
                "gross_return": 0.042,
                "total_fees": 12.0,
                "total_slippage": 4.0,
                "net_return": 0.031,
                "net_pnl": 80.0,
                "expectancy_per_trade": 2.5,
                "profit_factor": 1.35,
                "sharpe": 1.0,
                "sortino": 1.4,
                "max_drawdown": 0.012,
                "turnover": 7.0,
                "bootstrap_ci_or_not_run": {"status": "pass", "lower": 0.01, "upper": 0.05},
                "dsr_or_not_run": {"status": "pass", "dsr_pass": True, "dsr_cdf": 0.96},
                "pbo_or_not_run": {"status": "pass", "pbo_pass": True, "pbo": 0.12, "maximum_pbo": 0.2},
                "cscv_count_or_not_run": {"status": "pass", "n_partitions": 16, "n_configs": 8},
                "screening_status": "pass",
                "replay_eligibility_status": "eligible",
                "rejection_reason_or_null": None,
            }
        ],
        "rejected": [],
    }
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    return artifact


def _native_probe_fields() -> dict:
    return {
        "native_latency_probe_artifact": NATIVE_CPP_LATENCY_EVIDENCE,
        "native_latency_probe_artifact_hash": f"sha256:{'a' * 64}",
        "native_latency_probe_status": "provided",
        "native_latency_probe_provenance": "hft3_native_cpp_rithmic_latency_probe",
        "native_latency_probe_host": "CHI404",
    }


def _constant_latency_model() -> dict:
    return {
        **_native_probe_fields(),
        "latency_model_family": "ConstantLatency",
        "feed_latency_source": "hftbacktest_event_local_exchange_timestamp_delta",
        "order_entry_latency_source": "hft3_native_cpp_rithmic_latency_probe",
        "order_response_latency_source": "hft3_native_cpp_rithmic_latency_probe",
        "latency_units": "milliseconds",
        "latency_value_or_sample_hash": f"sha256:{'b' * 64}",
        "latency_p50_ms": 3.1,
        "latency_p90_ms": 5.2,
        "latency_p99_ms": 6.256,
        "latency_source_authority": "hft3_native_cpp_latency_probe",
        "latency_proxy_status": "measured",
        "latency_component_mapping": {
            "feed_latency": "event local_ts - exch_ts, in milliseconds",
            "order_entry_latency": "native C++ req_ts to exch_ts, in milliseconds",
            "order_response_latency": "native C++ exch_ts to resp_ts, in milliseconds",
        },
        "feed_latency_ms": 0.24,
        "order_entry_latency_ms": 3.128,
        "order_response_latency_ms": 3.128,
    }


def _valid_l3_fill_queue_model() -> dict:
    return {
        "exchange_model": "NoPartialFillExchange",
        "exchange_model_source": "asset.no_partial_fill_exchange",
        "queue_model": "L3FIFOQueueModel",
        "queue_model_source": "asset.l3_fifo_queue_model",
        "fill_model_scope": "l3_mbo",
        "partial_fill_policy": "no_partial_fill",
        "time_in_force_policy": "post_only_cancel_remaining",
        "maker_fee": -0.0002,
        "taker_fee": 0.0007,
        "tick_size": 0.25,
        "lot_size": 1.0,
        "minimum_order_qty": 1.0,
        "market_impact_mode": "external_charge",
        "market_impact_charge_model": "depth_scaled_external_slippage_charge",
        "market_impact_charge_units": "ticks_per_contract",
        "market_impact_charge_value": 0.25,
        "market_impact_evidence_source": "reports/hftbacktest/market_depth_mes_20260616.json",
        "liquidity_taking_max_depth_ratio": 0.15,
        "orders_intended": 2,
        "orders_submitted": 2,
        "orders_acknowledged": 2,
        "orders_cancelled": 1,
        "fills_count": 1,
        "partial_fills_count": 0,
        "unfilled_count": 1,
        "fill_rate": 0.5,
        "avg_queue_position_or_not_available": 3.0,
    }


def test_valid_l3_fill_queue_model_artifact_passes_and_persists_summary_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hbt3, "_repo_commit", lambda _root: "hft3sha")
    monkeypatch.setattr(hbt3, "_repo_dirty", lambda _root: False)
    monkeypatch.setattr(
        hbt3,
        "detect_hftbacktest_installation",
        lambda: {
            "available": True,
            "python_package_name": "hftbacktest",
            "python_package_version": "2.4.2",
            "installed_module_path": "site-packages/hftbacktest",
        },
    )

    fill_queue = _valid_l3_fill_queue_model()
    reasons = hbt3.validate_hftbacktest_fill_queue_model(fill_queue)
    assert reasons == []

    screening_path = _write_json(tmp_path / "screening_artifact.json", _screening_artifact())
    latency_path = _write_json(tmp_path / "latency_model.json", _constant_latency_model())
    fill_queue_path = _write_json(tmp_path / "fill_queue_model.json", fill_queue)
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt3_test"

    payload = hbt3.write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        latency_model_path=latency_path,
        fill_queue_model_path=fill_queue_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt3_test",
    )

    artifact = json.loads((out_dir / "fill_queue_model.json").read_text(encoding="utf-8"))
    summary = payload["replay_summary"]
    assert artifact["fill_queue_model_status"] == "pass"
    assert summary["exchange_model"] == "NoPartialFillExchange"
    assert summary["queue_model"] == "L3FIFOQueueModel"
    assert summary["market_impact_mode"] == "external_charge"
    assert summary["orders_intended"] == 2
    assert summary["orders_submitted"] == 2
    assert summary["orders_acknowledged"] == 2
    assert summary["orders_cancelled"] == 1
    assert summary["fills_count"] == 1
    assert summary["partial_fills_count"] == 0
    assert summary["unfilled_count"] == 1
    assert summary["fill_rate"] == 0.5
    assert summary["avg_queue_position_or_not_available"] == 3.0
    assert summary["maker_fees"] == -0.0002
    assert summary["taker_fees"] == 0.0007
    assert summary["replay_realism_status"] == "research_only"
    assert artifact["market_impact_charge_model"] == "depth_scaled_external_slippage_charge"
    assert artifact["market_impact_charge_value"] == 0.25
    assert artifact["market_impact_evidence_source"].endswith("market_depth_mes_20260616.json")
    assert artifact["liquidity_taking_max_depth_ratio"] == 0.15


@pytest.mark.parametrize(
    "field,value,expected_reason",
    [
        (
            "market_impact_charge_model",
            None,
            "missing_market_impact_external_charge_field:market_impact_charge_model",
        ),
        (
            "market_impact_charge_units",
            None,
            "missing_market_impact_external_charge_field:market_impact_charge_units",
        ),
        (
            "market_impact_evidence_source",
            None,
            "missing_market_impact_external_charge_field:market_impact_evidence_source",
        ),
        ("market_impact_charge_value", -0.01, "invalid_market_impact_charge_value"),
        ("liquidity_taking_max_depth_ratio", 1.5, "invalid_liquidity_taking_max_depth_ratio"),
    ],
)
def test_external_charge_requires_explicit_market_impact_charge_and_depth_evidence(
    field: str,
    value: object,
    expected_reason: str,
) -> None:
    model = _valid_l3_fill_queue_model()
    if value is None:
        model.pop(field)
    else:
        model[field] = value

    reasons = hbt3.validate_hftbacktest_fill_queue_model(model)

    assert expected_reason in reasons


@pytest.mark.parametrize(
    "queue_model,queue_model_source,comparison_mode",
    [
        ("LogProbQueueModel2", "asset.log_prob_queue_model2", False),
        ("RiskAverseQueueModel", "asset.risk_adverse_queue_model", False),
        ("PowerProbQueueModel", "asset.power_prob_queue_model", False),
    ],
)
def test_l3_scope_rejects_l2_probability_queue_without_explicit_comparison(
    queue_model: str,
    queue_model_source: str,
    comparison_mode: bool,
) -> None:
    model = {
        **_valid_l3_fill_queue_model(),
        "queue_model": queue_model,
        "queue_model_source": queue_model_source,
        "l3_to_l2_comparison_mode": comparison_mode,
    }

    reasons = hbt3.validate_hftbacktest_fill_queue_model(model)

    assert "l3_scope_requires_l3_queue_model" in reasons


def test_l3_scope_allows_l2_probability_queue_for_explicit_comparison() -> None:
    model = {
        **_valid_l3_fill_queue_model(),
        "queue_model": "LogProbQueueModel2",
        "queue_model_source": "asset.log_prob_queue_model2",
        "fill_model_scope": "l3_to_l2_comparison",
        "comparison_reference_artifact": "reports/hftbacktest/l3_l2_pair_mes_20260616.json",
        "comparison_reference_artifact_hash": f"sha256:{'e' * 64}",
        "comparison_reference_scope": "paired_l3_mbo_to_l2_mbp",
        "comparison_metric": "fill_rate_and_queue_position_delta",
    }

    reasons = hbt3.validate_hftbacktest_fill_queue_model(model)

    assert reasons == []


def test_l3_to_l2_comparison_with_l2_probability_queue_requires_pair_evidence_and_hash() -> None:
    model = {
        **_valid_l3_fill_queue_model(),
        "queue_model": "LogProbQueueModel2",
        "queue_model_source": "asset.log_prob_queue_model2",
        "fill_model_scope": "l3_to_l2_comparison",
    }

    reasons = hbt3.validate_hftbacktest_fill_queue_model(model)

    assert "missing_fill_queue_comparison_field:comparison_reference_artifact" in reasons
    assert "missing_fill_queue_comparison_field:comparison_reference_artifact_hash" in reasons
    assert "invalid_comparison_reference_artifact_hash" in reasons


@pytest.mark.parametrize(
    "exchange_model,partial_fill_policy,expected_reason",
    [
        ("NoPartialFillExchange", "partial_fill", "exchange_partial_fill_policy_mismatch"),
        ("PartialFillExchange", "no_partial_fill", "exchange_partial_fill_policy_mismatch"),
    ],
)
def test_exchange_model_and_partial_fill_policy_mismatch_fails(
    exchange_model: str,
    partial_fill_policy: str,
    expected_reason: str,
) -> None:
    model = {
        **_valid_l3_fill_queue_model(),
        "exchange_model": exchange_model,
        "partial_fill_policy": partial_fill_policy,
    }

    reasons = hbt3.validate_hftbacktest_fill_queue_model(model)

    assert expected_reason in reasons


@pytest.mark.parametrize(
    "field,value,expected_reason",
    [
        ("tick_size", 0.0, "invalid_fill_queue_positive_field:tick_size"),
        ("tick_size", -0.25, "invalid_fill_queue_positive_field:tick_size"),
        ("lot_size", 0.0, "invalid_fill_queue_positive_field:lot_size"),
        ("lot_size", -1.0, "invalid_fill_queue_positive_field:lot_size"),
        ("minimum_order_qty", 0.0, "invalid_fill_queue_positive_field:minimum_order_qty"),
        ("minimum_order_qty", -1.0, "invalid_fill_queue_positive_field:minimum_order_qty"),
    ],
)
def test_invalid_tick_lot_and_minimum_order_qty_fail(
    field: str,
    value: float,
    expected_reason: str,
) -> None:
    model = {**_valid_l3_fill_queue_model(), field: value}

    reasons = hbt3.validate_hftbacktest_fill_queue_model(model)

    assert expected_reason in reasons


@pytest.mark.parametrize("spelling", ["not_modelled", "not_modeled"])
def test_market_impact_not_modelled_or_not_modeled_is_labeled_not_silently_green(
    spelling: str,
) -> None:
    model = {**_valid_l3_fill_queue_model(), "market_impact_mode": spelling}

    reasons = hbt3.validate_hftbacktest_fill_queue_model(model)

    assert reasons == ["market_impact_not_modeled"]
    status = hbt3._replay_status_from_fail_reasons(
        ["hbt0_source_lock_only_replay_not_run", "market_impact_not_modeled"],
        {"data_validation_status": "not_run"},
    )
    assert status == "market_impact_not_modeled"


def test_missing_fill_queue_model_path_remains_research_only_not_certifying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hbt3, "_repo_commit", lambda _root: "hft3sha")
    monkeypatch.setattr(hbt3, "_repo_dirty", lambda _root: False)
    monkeypatch.setattr(
        hbt3,
        "detect_hftbacktest_installation",
        lambda: {
            "available": True,
            "python_package_name": "hftbacktest",
            "python_package_version": "2.4.2",
            "installed_module_path": "site-packages/hftbacktest",
        },
    )

    screening_path = _write_json(tmp_path / "screening_artifact.json", _screening_artifact())
    latency_path = _write_json(tmp_path / "latency_model.json", _constant_latency_model())
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt3_missing_fill_queue"

    payload = hbt3.write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        latency_model_path=latency_path,
        fill_queue_model_path=None,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt3_missing_fill_queue",
    )

    summary = payload["replay_summary"]
    assert summary["exchange_model"] == "not_run"
    assert summary["queue_model"] == "not_run"
    assert summary["market_impact_mode"] == "not_run"
    assert summary["replay_realism_status"] == "research_only"
    assert "fill_queue_model_path_missing" in summary["fail_closed_reasons"]
    assert "pass" != summary["replay_realism_status"]


@pytest.mark.parametrize("screening_scope", ["screen", "broad"])
def test_broad_non_rust_vectorbt_screening_artifact_fails_closed_through_hbt3_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    screening_scope: str,
) -> None:
    monkeypatch.setattr(hbt3, "_repo_commit", lambda _root: "hft3sha")
    monkeypatch.setattr(hbt3, "_repo_dirty", lambda _root: False)
    monkeypatch.setattr(
        hbt3,
        "detect_hftbacktest_installation",
        lambda: {
            "available": True,
            "python_package_name": "hftbacktest",
            "python_package_version": "2.4.2",
            "installed_module_path": "site-packages/hftbacktest",
        },
    )

    artifact = _screening_artifact()
    artifact["screening_scope"] = screening_scope
    artifact["rust_engine_required_for_scope"] = False
    artifact["vectorbt_engine"] = "numba"
    artifact["rust_engine_available"] = False
    artifact["engine_parity_status"] = "rust_engine_required_unavailable_fail_closed"
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    screening_path = _write_json(tmp_path / "screening_artifact.json", artifact)
    latency_path = _write_json(tmp_path / "latency_model.json", _constant_latency_model())
    fill_queue_path = _write_json(tmp_path / "fill_queue_model.json", _valid_l3_fill_queue_model())

    payload = hbt3.write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=tmp_path / "out",
        screening_artifact_path=screening_path,
        latency_model_path=latency_path,
        fill_queue_model_path=fill_queue_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id=f"hbt3_{screening_scope}_non_rust_screening",
    )

    reasons = payload["replay_summary"]["fail_closed_reasons"]
    assert "screening_artifact_required_rust_engine_missing" in reasons
    assert "screening_artifact_required_rust_engine_unavailable" in reasons
    assert payload["replay_summary"]["replay_realism_status"] == "fail"
