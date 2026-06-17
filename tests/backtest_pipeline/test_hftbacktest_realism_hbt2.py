from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest_pipeline.src import hftbacktest_realism as hbt2
from backtest_pipeline.src.hftbacktest_realism import (
    validate_hftbacktest_latency_model,
    write_hftbacktest_realism_artifacts,
)
from backtest_pipeline.src.vectorbt_adapter import compute_screening_artifact_hash


def _screening_artifact(candidate_id: str = "cand_hbt2") -> dict:
    artifact = {
        "run_id": "vbt_handoff_hbt2",
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


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _native_probe_fields() -> dict:
    return {
        "native_latency_probe_artifact": "reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json",
        "native_latency_probe_artifact_hash": f"sha256:{'a' * 64}",
        "native_latency_probe_status": "provided",
        "native_latency_probe_provenance": "hft3_native_cpp_rithmic_latency_probe",
        "native_latency_probe_host": "CHI404",
    }


def _base_latency_fields() -> dict:
    return {
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
    }


def _constant_latency_model() -> dict:
    model = {
        **_base_latency_fields(),
        **_native_probe_fields(),
        "latency_model_family": "ConstantLatency",
        "feed_latency_ms": 0.24,
        "order_entry_latency_ms": 3.128,
        "order_response_latency_ms": 3.128,
    }
    return model


def _feed_latency_model() -> dict:
    model = {
        **_base_latency_fields(),
        "latency_model_family": "FeedLatency",
        "order_entry_latency_source": "generated_from_feed_latency_proxy",
        "order_response_latency_source": "generated_from_feed_latency_proxy",
        "latency_source_authority": "hftbacktest_feed_latency_proxy",
        "latency_proxy_status": "proxy_only",
        "native_latency_probe_artifact": "not_run",
        "native_latency_probe_status": "not_run",
        "order_latency_unavailable_reason": "native order-latency samples not available for this research-only pass",
    }
    return model


def _intp_latency_model() -> dict:
    model = {
        **_base_latency_fields(),
        **_native_probe_fields(),
        "latency_model_family": "IntpOrderLatency",
        "latency_sample_artifact": "data/latency_baselines/2026-06-11/order_ack_campaign.jsonl",
        "latency_sample_row_count": 1002,
        "latency_sample_schema": ["req_ts", "exch_ts", "resp_ts", "_padding"],
        "interpolation_method": "hftbacktest.IntpOrderLatency",
    }
    return model


def test_constant_latency_requires_three_component_measured_native_contract() -> None:
    assert validate_hftbacktest_latency_model(_constant_latency_model()) == []


def test_constant_latency_rejects_missing_feed_component_and_bad_native_provenance() -> None:
    model = _constant_latency_model()
    model.pop("feed_latency_ms")
    model["native_latency_probe_artifact_hash"] = "sha256:not-a-digest"
    model["native_latency_probe_host"] = "workstation"
    model["native_latency_probe_provenance"] = "python_runtime_probe"

    reasons = validate_hftbacktest_latency_model(model)

    assert "invalid_constant_feed_latency_ms" in reasons
    assert "invalid_native_latency_probe_artifact_hash" in reasons
    assert "invalid_native_latency_probe_host" in reasons
    assert "invalid_native_latency_probe_provenance" in reasons


def test_measured_latency_rejects_non_measured_proxy_status_and_bad_value_hash() -> None:
    model = _constant_latency_model()
    model["latency_proxy_status"] = "synthetic"
    model["latency_value_or_sample_hash"] = "not-a-sha256"

    reasons = validate_hftbacktest_latency_model(model)

    assert "measured_latency_proxy_status_must_be_measured" in reasons
    assert "invalid_latency_value_or_sample_hash" in reasons


def test_latency_percentile_units_and_order_are_fail_closed() -> None:
    model = _constant_latency_model()
    model["latency_units"] = "microseconds"
    model["latency_p50_ms"] = 9.0
    model["latency_p99_ms"] = 6.256

    reasons = validate_hftbacktest_latency_model(model)

    assert "latency_units_must_be_milliseconds" in reasons
    assert "invalid_latency_percentile_order" in reasons


def test_interpolated_order_latency_requires_official_sample_schema() -> None:
    assert validate_hftbacktest_latency_model(_intp_latency_model()) == []

    model = _intp_latency_model()
    model["latency_sample_schema"] = ["req_ts", "resp_ts"]
    model["latency_sample_row_count"] = 0

    reasons = validate_hftbacktest_latency_model(model)

    assert "invalid_latency_sample_schema" in reasons
    assert "invalid_latency_sample_row_count" in reasons


def test_feed_latency_is_proxy_only_not_generic_fail() -> None:
    reasons = validate_hftbacktest_latency_model(_feed_latency_model())
    assert reasons == ["latency_proxy_only"]

    status = hbt2._replay_status_from_fail_reasons(
        ["hbt0_source_lock_only_replay_not_run", "latency_proxy_only"],
        {"data_validation_status": "not_run"},
    )
    assert status == "latency_proxy_only"


def test_feed_latency_requires_explicit_order_latency_unavailability_reason() -> None:
    model = _feed_latency_model()
    model.pop("order_latency_unavailable_reason")

    reasons = validate_hftbacktest_latency_model(model)

    assert "missing_order_latency_unavailable_reason" in reasons
    assert "latency_proxy_only" not in reasons


def test_missing_latency_model_remains_not_run_research_only() -> None:
    status = hbt2._replay_status_from_fail_reasons(
        ["hbt0_source_lock_only_replay_not_run", "data_npz_path_missing_hbt1_not_run", "latency_model_path_missing"],
        {"data_validation_status": "not_run"},
    )

    assert status == "research_only"


def test_write_artifacts_persists_latency_model_and_summary_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hbt2, "_repo_commit", lambda _root: "hft3sha")
    monkeypatch.setattr(hbt2, "_repo_dirty", lambda _root: False)
    monkeypatch.setattr(
        hbt2,
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
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt2_test"

    payload = write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        latency_model_path=latency_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=["reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json"],
        run_id="hbt2_test",
    )

    latency_artifact = json.loads((out_dir / "latency_model.json").read_text(encoding="utf-8"))
    summary = payload["replay_summary"]

    assert latency_artifact["latency_model_status"] == "pass"
    assert summary["latency_model_family"] == "ConstantLatency"
    assert summary["latency_p99_ms"] == 6.256
    assert "latency_model_path_missing" not in summary["fail_closed_reasons"]
    assert summary["replay_realism_status"] == "research_only"
