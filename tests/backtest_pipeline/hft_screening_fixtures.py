"""Shared HftBacktest realism test fixtures (§10 evidence + native hot-path pins)."""
from __future__ import annotations

from typing import Any

from backtest_pipeline.src.feature_plane import build_feature_plane_payload
from backtest_pipeline.src.robustness_bridge import compute_robustness_evidence
from backtest_pipeline.src.vectorbt_adapter import compute_screening_artifact_hash

NATIVE_CPP_LATENCY_EVIDENCE_HASH = f"sha256:{'a' * 64}"
NATIVE_CPP_LATENCY_EVIDENCE_PATH = (
    "reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json"
)
NATIVE_CPP_LATENCY_EVIDENCE = f"{NATIVE_CPP_LATENCY_EVIDENCE_PATH}#{NATIVE_CPP_LATENCY_EVIDENCE_HASH}"

SECTION_10_EVIDENCE_KEYS = (
    "fee_stress_or_not_run",
    "slippage_stress_or_not_run",
    "latency_stress_or_not_run",
    "holm_bh_or_not_run",
    "null_battery_or_not_run",
    "planted_alpha_or_not_run",
    "adversarial_or_not_run",
    "parameter_perturbation_or_not_run",
)

_WALK_FORWARD_METRICS = {
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
}

_WFC_METRICS = {
    "metric_in_sample": [1.2, 1.0, 0.9],
    "metric_out_of_sample": [1.0, 0.86, 0.78],
    "pearson": 0.64,
    "spearman": 0.58,
    "scatter_data": [{"is": 1.2, "oos": 1.0}],
    "quadrant_counts": {"high_is_high_oos": 2, "high_is_low_oos": 0},
    "high_is_high_oos_region": {"threshold": 0.8, "count": 2},
    "rejection_reason": None,
}


def passing_section10_evidence_maps(candidate_id: str = "hbt_fixture") -> dict[str, Any]:
    """§10 robustness maps that pass staleness (from robustness_bridge golden input)."""
    from test_robustness_bridge import _full_passing_input

    result = compute_robustness_evidence(_full_passing_input(), candidate_id=candidate_id)
    return {key: result[key] for key in SECTION_10_EVIDENCE_KEYS}


def replay_eligible_promoted_candidate(
    candidate_id: str,
    *,
    rejection_reason: Any = None,
    wfc_rejection_reason: Any = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Promoted screening row with replay-eligibility fields + §10 evidence."""
    wfc_metrics = dict(_WFC_METRICS)
    if wfc_rejection_reason is not None:
        wfc_metrics["rejection_reason"] = wfc_rejection_reason
    row: dict[str, Any] = {
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
        "walk_forward_metrics": dict(_WALK_FORWARD_METRICS),
        "wfc_metrics": wfc_metrics,
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
        "robustness_evidence_receipt": {
            "schema": "hft3_robustness_evidence_inputs_v1",
            "binding": {"candidate_id": candidate_id},
            "source_evidence": {
                "fixture": "tests/backtest_pipeline/hft_screening_fixtures.py#sha256:"
                + "c" * 64,
            },
            "evidence_entry_hash": "d" * 64,
        },
        "rejection_reason_or_null": rejection_reason,
        **passing_section10_evidence_maps(candidate_id),
    }
    row.update(overrides)
    return row


def screening_artifact_shell(
    run_id: str,
    candidate_id: str,
    *,
    promoted: list[dict[str, Any]] | None = None,
    **artifact_overrides: Any,
) -> dict[str, Any]:
    """Top-level screening artifact with hash for HBT handoff tests."""
    promoted_rows = promoted or [replay_eligible_promoted_candidate(candidate_id)]
    artifact: dict[str, Any] = {
        "run_id": run_id,
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
        "promoted": promoted_rows,
        "rejected": [],
    }
    artifact.update(
        build_feature_plane_payload(
            bar_construction_id=str(artifact_overrides.get("bar_construction_id", "ohlcv_1m_from_npz_or_supplied_array")),
            feature_set_id=str(artifact_overrides.get("feature_set_id", "fs_v1_pilot_unknown")),
            feature_set_hash=str(
                artifact_overrides.get("feature_set_hash", "pilot_requires_feature_manifest_before_screen")
            ),
            research_clock=str(artifact.get("research_clock", "scheduled_event")),
            screening_scope=str(artifact.get("screening_scope", "pilot")),
        )
    )
    artifact.update(artifact_overrides)
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    return artifact


def native_probe_latency_fields() -> dict[str, Any]:
    """Latency artifact fields pointing at hash-backed CHI404 native probe evidence."""
    return {
        "native_latency_probe_artifact": NATIVE_CPP_LATENCY_EVIDENCE,
        "native_latency_probe_artifact_hash": NATIVE_CPP_LATENCY_EVIDENCE_HASH,
        "native_latency_probe_status": "provided",
        "native_latency_probe_provenance": "hft3_native_cpp_rithmic_latency_probe",
        "native_latency_probe_host": "CHI404",
    }
