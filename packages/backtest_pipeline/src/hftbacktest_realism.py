"""HftBacktest source-lock and fail-closed realism artifact contract."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from backtest_pipeline.src.fee_model import FeeModel
from backtest_pipeline.src.research_clock import research_clock_validation_errors
from backtest_pipeline.src.vectorbt_adapter import compute_screening_artifact_hash

UPSTREAM_REPO_URL = "https://github.com/nkaz001/hftbacktest"
UPSTREAM_DOCS_URL = "https://hftbacktest.readthedocs.io/en/latest/index.html"
DOCS_PAGES_USED = [
    UPSTREAM_DOCS_URL,
    "https://hftbacktest.readthedocs.io/en/latest/data.html",
    "https://hftbacktest.readthedocs.io/en/latest/reference/data_validation.html",
    "https://hftbacktest.readthedocs.io/en/latest/reference/backtester.html",
    "https://hftbacktest.readthedocs.io/en/latest/latency_models.html",
    "https://hftbacktest.readthedocs.io/en/latest/order_fill.html",
    "https://hftbacktest.readthedocs.io/en/latest/tutorials/Level-3%20Backtesting.html",
]
DEFAULT_ADAPTER_FILES = [
    "packages/backtest_pipeline/src/hftbacktest_realism.py",
    "packages/backtest_pipeline/src/hft_backtest_builder.py",
    "packages/execution/adapters/hftbacktest_simulated_exchange.py",
]
DEFAULT_API_SURFACE_USED = [
    "hftbacktest.types.event_dtype",
    "hftbacktest.types.EXCH_EVENT",
    "hftbacktest.types.LOCAL_EVENT",
    "BacktestAsset",
    "HashMapMarketDepthBacktest",
    "asset.data",
    "asset.constant_order_latency",
    "asset.intp_order_latency",
    "Backtester.feed_latency",
    "Backtester.order_latency",
    "asset.no_partial_fill_exchange",
    "asset.partial_fill_exchange",
    "asset.l3_fifo_queue_model",
    "asset.log_prob_queue_model",
    "asset.log_prob_queue_model2",
    "asset.power_prob_queue_model",
    "asset.power_prob_queue_model2",
    "asset.power_prob_queue_model3",
    "asset.risk_adverse_queue_model",
    "asset.tick_size",
    "asset.lot_size",
    "asset.trading_qty_fee_model",
    "hftbacktest.data.validate_event_order",
    "HashMapMarketDepthBacktest.wait_next_feed",
    "HashMapMarketDepthBacktest.submit_buy_order",
    "HashMapMarketDepthBacktest.submit_sell_order",
    "HashMapMarketDepthBacktest.wait_order_response",
    "HashMapMarketDepthBacktest.orders",
    "HashMapMarketDepthBacktest.state_values",
    "HashMapMarketDepthBacktest.cancel",
    "HashMapMarketDepthBacktest.clear_inactive_orders",
]
EXPECTED_EVENT_DTYPE_FIELDS = ("ev", "exch_ts", "local_ts", "px", "qty", "order_id", "ival", "fval")
HFTBACKTEST_DATA_VALIDATION_DOCS = [
    "https://hftbacktest.readthedocs.io/en/latest/data.html#validation",
    "https://hftbacktest.readthedocs.io/en/latest/reference/data_validation.html",
]
DATA_VALIDATION_FAIL_STATUSES = {
    "EVENT_DTYPE_INVALID",
    "EXCHANGE_ORDER_INVALID",
    "LOCAL_ORDER_INVALID",
    "NEGATIVE_FEED_LATENCY_UNCORRECTED",
    "L3_ORDER_ID_MISSING",
    "L2_L3_MISMATCH",
    "EVENT_ARRAY_EMPTY",
    "EVENT_TYPE_UNKNOWN",
    "ORPHAN_L3_EVENTS_UNACCOUNTED",
    "HFTBACKTEST_VALIDATE_EVENT_ORDER_FAILED",
    "DATA_NPZ_MISSING_DATA_ARRAY",
    "DATA_NPZ_READ_FAILED",
    "HFTBACKTEST_DATA_VALIDATION_UNAVAILABLE",
    "TIMESTAMP_UNITS_UNPROVEN",
}
NON_REPLAY_ONLY_REASONS = {
    "data_npz_path_missing_hbt1_not_run",
    "data_npz_path_missing_hbt4_not_run",
    "fill_queue_model_path_missing",
    "hbt0_source_lock_only_replay_not_run",
    "hbt4_order_intent_missing",
    "official_replay_not_run",
    "latency_model_path_missing",
}
SCREENING_ARTIFACT_REQUIRED_FIELDS = (
    "screening_artifact_hash",
    "screening_backend",
    "vectorbt_version",
    "vectorbt_engine",
    "engine_parity_status",
    "rust_engine_required_for_scope",
    "rust_engine_available",
    "license_review",
    "candidate_ids",
    "promoted_ids",
    "rejected_ids",
    "candidate_reasons",
    "promoted_reasons",
    "rejected_reasons",
    "no_lookahead_signal_shift_proof",
)
REPLAY_ELIGIBILITY_REQUIRED_FIELDS = (
    "candidate_id",
    "model_id",
    "symbol",
    "research_clock",
    "opportunity_type_or_event_type",
    "parameter_values",
    "parameter_values_hash",
    "trials_budget_tier",
    "in_sample_metrics",
    "out_of_sample_metrics",
    "walk_forward_metrics",
    "wfc_metrics",
    "surface_stability_metrics",
    "robustness_gate_scope",
    "wfc_status",
    "dsr_status",
    "pbo_status",
    "cscv_status",
    "robustness_artifact_staleness",
    "trade_count",
    "gross_return",
    "total_fees",
    "total_slippage",
    "net_return",
    "net_pnl",
    "expectancy_per_trade",
    "profit_factor",
    "sharpe",
    "sortino",
    "max_drawdown",
    "turnover",
    "bootstrap_ci_or_not_run",
    "dsr_or_not_run",
    "pbo_or_not_run",
    "cscv_count_or_not_run",
    "fee_stress_or_not_run",
    "slippage_stress_or_not_run",
    "latency_stress_or_not_run",
    "holm_bh_or_not_run",
    "null_battery_or_not_run",
    "planted_alpha_or_not_run",
    "adversarial_or_not_run",
    "parameter_perturbation_or_not_run",
    "screening_status",
    "replay_eligibility_status",
    "rejection_reason_or_null",
)
REPLAY_ELIGIBILITY_PASS_STATUS_FIELDS = ("wfc_status", "dsr_status", "pbo_status", "cscv_status")
REPLAY_ELIGIBILITY_REQUIRED_MAPPING_FIELDS = (
    "in_sample_metrics",
    "out_of_sample_metrics",
    "walk_forward_metrics",
    "wfc_metrics",
    "surface_stability_metrics",
)
WALK_FORWARD_REQUIRED_EVIDENCE_FIELDS = (
    "fold_matrix",
    "fold_train_test_dates",
    "fold_metrics",
    "walk_forward_efficiency",
    "fold_dispersion",
    "is_oos_gap",
    "oos_decay",
)
WFC_REQUIRED_EVIDENCE_FIELDS = (
    "metric_in_sample",
    "metric_out_of_sample",
    "pearson",
    "spearman",
    "scatter_data",
    "quadrant_counts",
    "high_is_high_oos_region",
    "rejection_reason",
)
REPLAY_ELIGIBILITY_NOT_RUN_EVIDENCE_FIELDS = (
    "bootstrap_ci_or_not_run",
    "dsr_or_not_run",
    "pbo_or_not_run",
    "cscv_count_or_not_run",
    "fee_stress_or_not_run",
    "slippage_stress_or_not_run",
    "latency_stress_or_not_run",
    "holm_bh_or_not_run",
    "null_battery_or_not_run",
    "planted_alpha_or_not_run",
    "adversarial_or_not_run",
    "parameter_perturbation_or_not_run",
)
REPLAY_SUMMARY_STATUSES = {
    "pass",
    "fail",
    "research_only",
    "hftbacktest_unavailable",
    "data_invalid",
    "latency_proxy_only",
    "market_impact_not_modeled",
    "accelerated_not_certifying",
}
HBT5_OBSERVATION_REQUIRED_METRIC_FIELDS = (
    "fill_rate",
    "latency_p50_ms",
    "latency_p90_ms",
    "latency_p99_ms",
    "total_fees",
    "total_slippage",
    "adverse_selection_markout",
    "spread_capture_or_cost",
)
HBT5_OBSERVATION_REQUIRED_ORDER_STATE_FIELDS = (
    "orders_intended",
    "orders_submitted",
    "orders_acknowledged",
    "orders_cancelled",
    "fills_count",
    "partial_fills_count",
    "unfilled_count",
)
LATENCY_MODEL_REQUIRED_FIELDS = (
    "latency_model_family",
    "feed_latency_source",
    "order_entry_latency_source",
    "order_response_latency_source",
    "latency_units",
    "latency_value_or_sample_hash",
    "latency_p50_ms",
    "latency_p90_ms",
    "latency_p99_ms",
    "latency_source_authority",
    "latency_proxy_status",
    "latency_component_mapping",
    "native_latency_probe_artifact",
    "native_latency_probe_status",
)
LATENCY_MODEL_ALLOWED_FAMILIES = {
    "ConstantLatency",
    "IntpOrderLatency",
    "FeedLatency",
    "Custom",
}
LATENCY_SAMPLE_SCHEMA_FIELDS = ("req_ts", "exch_ts", "resp_ts", "_padding")
LATENCY_NATIVE_PROBE_OK_STATUSES = {"provided", "pass", "valid"}
LATENCY_COMPONENT_MAPPING_FIELDS = ("feed_latency", "order_entry_latency", "order_response_latency")
LATENCY_MEASURED_FAMILIES = {"ConstantLatency", "IntpOrderLatency", "Custom"}
LATENCY_NATIVE_PROBE_REQUIRED_FIELDS = (
    "native_latency_probe_artifact_hash",
    "native_latency_probe_provenance",
    "native_latency_probe_host",
)
FILL_QUEUE_MODEL_REQUIRED_FIELDS = (
    "exchange_model",
    "queue_model",
    "queue_model_source",
    "fill_model_scope",
    "partial_fill_policy",
    "time_in_force_policy",
    "maker_fee",
    "taker_fee",
    "tick_size",
    "lot_size",
    "minimum_order_qty",
    "market_impact_mode",
)
FILL_QUEUE_EXCHANGE_PARTIAL_POLICY = {
    "NoPartialFillExchange": "no_partial_fill",
    "PartialFillExchange": "partial_fill",
}
FILL_QUEUE_MODEL_SOURCES_BY_MODEL = {
    "L3FIFOQueueModel": {"asset.l3_fifo_queue_model"},
    "RiskAverseQueueModel": {"asset.risk_adverse_queue_model"},
    "LogProbQueueModel": {"asset.log_prob_queue_model"},
    "LogProbQueueModel2": {"asset.log_prob_queue_model2"},
    "PowerProbQueueModel": {"asset.power_prob_queue_model"},
    "PowerProbQueueModel2": {"asset.power_prob_queue_model2"},
    "PowerProbQueueModel3": {"asset.power_prob_queue_model3"},
}
L2_PROBABILITY_QUEUE_MODELS = {
    "RiskAverseQueueModel",
    "LogProbQueueModel",
    "LogProbQueueModel2",
    "PowerProbQueueModel",
    "PowerProbQueueModel2",
    "PowerProbQueueModel3",
}
OFFICIAL_REPLAY_EXCHANGE_MODEL = "NoPartialFillExchange"
OFFICIAL_REPLAY_PARTIAL_FILL_POLICY = "no_partial_fill"
OFFICIAL_REPLAY_TIME_IN_FORCE_POLICY = "post_only_cancel_remaining"
OFFICIAL_REPLAY_QUEUE_MODELS_BY_SCOPE = {
    "l2_mbp": {"LogProbQueueModel2"},
    "l3_mbo": {"L3FIFOQueueModel"},
}
OFFICIAL_REPLAY_FEE_ABS_TOL = 1e-12
FILL_MODEL_SCOPES = {"l3_mbo", "l2_mbp", "l3_to_l2_comparison", "comparison"}
MARKET_IMPACT_MODES = {"not_modeled", "external_charge", "rejected"}
MARKET_IMPACT_MODE_ALIASES = {"not_modelled": "not_modeled"}
FILL_QUEUE_ORDER_STATE_FIELDS = (
    "orders_intended",
    "orders_submitted",
    "orders_acknowledged",
    "orders_cancelled",
    "fills_count",
    "partial_fills_count",
    "unfilled_count",
)
MARKET_IMPACT_EXTERNAL_CHARGE_REQUIRED_FIELDS = (
    "market_impact_charge_model",
    "market_impact_charge_units",
    "market_impact_charge_value",
    "market_impact_evidence_source",
    "liquidity_taking_max_depth_ratio",
)
FILL_QUEUE_COMPARISON_REQUIRED_FIELDS = (
    "comparison_reference_artifact",
    "comparison_reference_artifact_hash",
    "comparison_reference_scope",
    "comparison_metric",
)
OFFICIAL_REPLAY_API_CALLS = (
    "BacktestAsset",
    "HashMapMarketDepthBacktest",
    "HashMapMarketDepthBacktest.wait_next_feed",
    "HashMapMarketDepthBacktest.submit_buy_order",
    "HashMapMarketDepthBacktest.submit_sell_order",
    "HashMapMarketDepthBacktest.wait_order_response",
    "HashMapMarketDepthBacktest.orders",
    "HashMapMarketDepthBacktest.state_values",
    "HashMapMarketDepthBacktest.cancel",
    "HashMapMarketDepthBacktest.clear_inactive_orders",
)
HBT4_INTENT_REQUIRED_FIELDS = ("side", "quantity", "price_mode", "max_feed_steps")
HBT4_PRICE_MODES = {"passive_best_bid_or_ask", "marketable_touch"}
RUST_REQUIRED_SCREENING_SCOPES = {
    "screen",
    "broad",
    "broad-screen",
    "broad_screen",
    "refine",
    "all-model",
    "all-models",
    "all_model",
    "all_models",
    "paid",
    "paid-compute",
    "paid_compute",
}
NATIVE_CPP_HOT_PATH_EVIDENCE_TOKENS = (
    "rithmic_latency_probe",
    "reports/latency_baselines/",
)
SOURCE_LOCK_REQUIRED_FIELDS = (
    "upstream_repo_url",
    "upstream_commit_sha_or_tag",
    "upstream_ref_verification_status",
    "upstream_ref_verified_against",
    "upstream_docs_url",
    "docs_pages_used",
    "python_package_name",
    "python_package_version",
    "rust_crate_version_or_not_used",
    "installed_module_path",
    "source_lock_created_at_utc",
    "hft3_commit",
    "hft3_adapter_files",
    "api_surface_used",
    "known_doc_repo_discrepancies",
    "license_review",
    "native_hot_path_required",
    "native_hot_path_evidence",
    "native_hot_path_status",
)
REPLAY_SUMMARY_REQUIRED_FIELDS = (
    "run_id",
    "created_at_utc",
    "hft3_commit",
    "screening_artifact_hash",
    "candidate_id",
    "model_id",
    "symbol",
    "research_clock",
    "event_or_session_scope",
    "hftbacktest_source_lock_hash",
    "data_validation_status",
    "latency_model_family",
    "exchange_model",
    "queue_model",
    "queue_model_source",
    "fill_model_scope",
    "partial_fill_policy",
    "time_in_force_policy",
    "accelerated_mode",
    "accuracy_tradeoff_declared",
    "queue_position_modeled",
    "order_response_latency_modeled",
    "full_replay_comparison_hash_or_not_run",
    "certification_allowed",
    "market_impact_mode",
    "orders_intended",
    "orders_submitted",
    "orders_acknowledged",
    "orders_cancelled",
    "fills_count",
    "partial_fills_count",
    "unfilled_count",
    "fill_rate",
    "avg_queue_position_or_not_available",
    "latency_p50_ms",
    "latency_p90_ms",
    "latency_p99_ms",
    "tick_size",
    "lot_size",
    "minimum_order_qty",
    "maker_fees",
    "taker_fees",
    "gross_pnl",
    "net_pnl",
    "execution_adjusted_expectancy",
    "max_drawdown",
    "adverse_selection_markout",
    "spread_capture_or_cost",
    "official_hftbacktest_replay_status",
    "official_replay_artifact_hash",
    "discrepancy_comparison_status",
    "discrepancy_comparison_artifact_hash",
    "certification_feedback_status",
    "replay_realism_status",
    "fail_closed_reasons",
)
L3_EVENT_TYPES = {
    10: "ADD_ORDER_EVENT",
    11: "CANCEL_ORDER_EVENT",
    12: "MODIFY_ORDER_EVENT",
    13: "FILL_EVENT",
}
L2_EVENT_TYPES = {
    1: "DEPTH_EVENT",
    # Per Codex round-3 P1: TRADE_EVENT (type 2) can appear in real CME MBO/L3
    # captures alongside ADD/CANCEL/MODIFY/FILL events. Listing it as L2-only
    # causes valid L3 feeds with trades to be rejected as L2_L3_MISMATCH.
    # TRADE_EVENT is now treated as L3-compatible (not L2-exclusive).
    3: "DEPTH_CLEAR_EVENT",
}
L3_ORPHAN_EVENT_TYPES = {11, 12, 13}


class HftBacktestRealismArtifactError(ValueError):
    """Raised when a HftBacktest realism artifact violates the HBT contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def _sha256_payload(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _optional_list_arg(value: list[str] | None, *, field: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise HftBacktestRealismArtifactError(f"{field} must be a list")
    return list(value)


def _hash_without_keys(value: Any, excluded: set[str]) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _hash_without_keys(item, excluded)
            for key, item in value.items()
            if key not in excluded
        }
    if isinstance(value, list):
        return [_hash_without_keys(item, excluded) for item in value]
    return value


def _is_nonnegative_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_positive_number(value: Any) -> bool:
    return _is_number(value) and value > 0


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalize_market_impact_mode(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    return MARKET_IMPACT_MODE_ALIASES.get(normalized, normalized)


def _latency_model_schema_value(latency_model: Mapping[str, Any]) -> list[str] | None:
    schema = latency_model.get("latency_sample_schema")
    if schema is None:
        schema = latency_model.get("sample_schema")
    if schema is None:
        schema = latency_model.get("latency_sample_fields")
    if schema is None:
        schema = latency_model.get("schema")
    if schema is None:
        return None
    if not isinstance(schema, list):
        raise HftBacktestRealismArtifactError("latency_sample_schema must be a list")
    return [str(field) for field in schema]


def _is_sha256_digest(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(char in "0123456789abcdefABCDEF" for char in digest)


def _is_raw_sha256_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdefABCDEF" for char in value
    )


def _contains_sha256_digest(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    marker = "sha256:"
    start = value.lower().find(marker)
    if start < 0:
        return False
    digest = value[start + len(marker) : start + len(marker) + 64]
    return len(digest) == 64 and all(char in "0123456789abcdefABCDEF" for char in digest)


def _validate_latency_component_mapping(latency_model: Mapping[str, Any]) -> list[str]:
    mapping = latency_model.get("latency_component_mapping")
    if not isinstance(mapping, Mapping):
        return ["invalid_latency_component_mapping"]
    reasons: list[str] = []
    for field in LATENCY_COMPONENT_MAPPING_FIELDS:
        if not isinstance(mapping.get(field), str) or not mapping.get(field, "").strip():
            reasons.append(f"missing_latency_component_mapping:{field}")
    return reasons


def _validate_native_latency_probe_evidence(latency_model: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    for field in LATENCY_NATIVE_PROBE_REQUIRED_FIELDS:
        if not isinstance(latency_model.get(field), str) or not latency_model.get(field, "").strip():
            reasons.append(f"missing_native_latency_probe_field:{field}")
    if not _is_sha256_digest(latency_model.get("native_latency_probe_artifact_hash")):
        reasons.append("invalid_native_latency_probe_artifact_hash")
    provenance = str(latency_model.get("native_latency_probe_provenance", "")).lower()
    if "hft3" not in provenance or "native_cpp" not in provenance or "rithmic_latency_probe" not in provenance:
        reasons.append("invalid_native_latency_probe_provenance")
    if str(latency_model.get("native_latency_probe_host", "")).lower() != "chi404":
        reasons.append("invalid_native_latency_probe_host")
    authority = str(latency_model.get("latency_source_authority", "")).lower()
    if "native_cpp" not in authority or "latency_probe" not in authority:
        reasons.append("invalid_native_latency_source_authority")
    return reasons


def _looks_like_native_cpp_hot_path_evidence(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.replace("\\", "/").lower()
    return any(token in normalized for token in NATIVE_CPP_HOT_PATH_EVIDENCE_TOKENS)


def _source_lock_has_hash_backed_native_hot_path_evidence(lock: Mapping[str, Any]) -> bool:
    evidence = lock.get("native_hot_path_evidence")
    if not isinstance(evidence, list) or not evidence:
        return False
    return all(
        _looks_like_native_cpp_hot_path_evidence(item) and _contains_sha256_digest(item)
        for item in evidence
    )


def _upstream_ref_verification_status(upstream_ref: str | None, package_version: str) -> str:
    if not upstream_ref:
        return "missing_upstream_ref"
    if package_version in ("", "unavailable", None):
        return "installed_package_version_unavailable"
    allowed_refs = {str(package_version), f"v{package_version}"}
    return "package_version_match" if upstream_ref in allowed_refs else "unverified_ref_package_version_mismatch"


def validate_hftbacktest_latency_model(latency_model: Mapping[str, Any]) -> list[str]:
    """Return fail-closed reasons for a HftBacktest latency artifact."""
    reasons: list[str] = []
    for field in LATENCY_MODEL_REQUIRED_FIELDS:
        if field not in latency_model or latency_model[field] in ("", None):
            reasons.append(f"missing_latency_model_field:{field}")

    family = latency_model.get("latency_model_family")
    if family not in LATENCY_MODEL_ALLOWED_FAMILIES:
        reasons.append("invalid_latency_model_family")

    for field in (
        "feed_latency_source",
        "order_entry_latency_source",
        "order_response_latency_source",
        "latency_units",
        "latency_value_or_sample_hash",
        "latency_source_authority",
    ):
        if not isinstance(latency_model.get(field), str) or not latency_model.get(field, "").strip():
            reasons.append(f"invalid_latency_model_field:{field}")

    proxy_status = latency_model.get("latency_proxy_status")
    if not isinstance(proxy_status, str) or not proxy_status.strip():
        reasons.append("invalid_latency_model_field:latency_proxy_status")

    probe_status = latency_model.get("native_latency_probe_status")
    if not isinstance(latency_model.get("native_latency_probe_artifact"), str) or not latency_model.get(
        "native_latency_probe_artifact", ""
    ).strip():
        reasons.append("invalid_latency_model_field:native_latency_probe_artifact")
    if not isinstance(probe_status, str) or not probe_status.strip():
        reasons.append("invalid_latency_model_field:native_latency_probe_status")

    if latency_model.get("latency_units") != "milliseconds":
        reasons.append("latency_units_must_be_milliseconds")
    if not _is_sha256_digest(latency_model.get("latency_value_or_sample_hash")):
        reasons.append("invalid_latency_value_or_sample_hash")
    reasons.extend(_validate_latency_component_mapping(latency_model))

    if family == "ConstantLatency":
        if not _is_nonnegative_number(latency_model.get("feed_latency_ms")):
            reasons.append("invalid_constant_feed_latency_ms")
        if not _is_nonnegative_number(latency_model.get("order_entry_latency_ms")):
            reasons.append("invalid_constant_latency_entry_ms")
        if not _is_nonnegative_number(latency_model.get("order_response_latency_ms")):
            reasons.append("invalid_constant_latency_response_ms")
        if proxy_status == "proxy_only":
            reasons.append("constant_latency_cannot_be_proxy_only")

    if family == "IntpOrderLatency":
        if not isinstance(latency_model.get("latency_sample_artifact"), str) or not latency_model.get(
            "latency_sample_artifact", ""
        ).strip():
            reasons.append("missing_latency_sample_artifact")
        row_count = latency_model.get("latency_sample_row_count")
        if not isinstance(row_count, int) or row_count <= 0:
            reasons.append("invalid_latency_sample_row_count")
        if not isinstance(latency_model.get("interpolation_method"), str) or not latency_model.get(
            "interpolation_method", ""
        ).strip():
            reasons.append("missing_interpolation_method")
        try:
            sample_schema = _latency_model_schema_value(latency_model)
        except HftBacktestRealismArtifactError:
            reasons.append("invalid_latency_sample_schema")
        else:
            if not sample_schema or any(field not in sample_schema for field in LATENCY_SAMPLE_SCHEMA_FIELDS):
                reasons.append("invalid_latency_sample_schema")

    if family == "FeedLatency":
        if proxy_status != "proxy_only":
            reasons.append("feed_latency_must_be_proxy_only")
        if probe_status not in {"not_run", "not_required", "not_applicable", "proxy_only"}:
            reasons.append("feed_latency_probe_status_invalid")
        if not isinstance(latency_model.get("order_latency_unavailable_reason"), str) or not latency_model.get(
            "order_latency_unavailable_reason", ""
        ).strip():
            reasons.append("missing_order_latency_unavailable_reason")

    if family in LATENCY_MEASURED_FAMILIES:
        if proxy_status != "measured":
            reasons.append("measured_latency_proxy_status_must_be_measured")
        if probe_status not in LATENCY_NATIVE_PROBE_OK_STATUSES:
            reasons.append("invalid_native_latency_probe_evidence")
        reasons.extend(_validate_native_latency_probe_evidence(latency_model))

    for field in ("latency_p50_ms", "latency_p90_ms", "latency_p99_ms"):
        value = latency_model.get(field)
        if not _is_nonnegative_number(value):
            reasons.append(f"invalid_latency_percentile_field:{field}")
    if all(_is_nonnegative_number(latency_model.get(field)) for field in ("latency_p50_ms", "latency_p90_ms", "latency_p99_ms")):
        if not latency_model["latency_p50_ms"] <= latency_model["latency_p90_ms"] <= latency_model["latency_p99_ms"]:
            reasons.append("invalid_latency_percentile_order")

    if family == "FeedLatency" and not reasons:
        # FeedLatency remains proxy-only/non-certifying by contract.
        reasons.append("latency_proxy_only")

    return list(dict.fromkeys(reasons))


def _load_latency_model_artifact(latency_model_path: Path | None) -> tuple[dict[str, Any], list[str]]:
    if latency_model_path is None:
        artifact = {
            "latency_model_status": "not_run",
            "latency_model_family": "not_run",
            "feed_latency_source": "not_run",
            "order_entry_latency_source": "not_run",
            "order_response_latency_source": "not_run",
            "latency_units": "not_run",
            "latency_value_or_sample_hash": "not_run",
            "latency_p50_ms": None,
            "latency_p90_ms": None,
            "latency_p99_ms": None,
            "latency_source_authority": "not_run",
            "latency_proxy_status": "not_run",
            "native_latency_probe_artifact": "not_run",
            "native_latency_probe_status": "not_run",
        }
        return artifact, ["latency_model_path_missing"]

    try:
        latency_model = _load_json(latency_model_path)
    except Exception as exc:
        artifact = {
            "latency_model_status": "fail",
            "latency_model_family": "not_run",
            "feed_latency_source": "not_run",
            "order_entry_latency_source": "not_run",
            "order_response_latency_source": "not_run",
            "latency_units": "not_run",
            "latency_value_or_sample_hash": "not_run",
            "latency_p50_ms": None,
            "latency_p90_ms": None,
            "latency_p99_ms": None,
            "latency_source_authority": "not_run",
            "latency_proxy_status": "not_run",
            "native_latency_probe_artifact": "not_run",
            "native_latency_probe_status": "not_run",
            "latency_model_path": str(latency_model_path),
        }
        return artifact, [f"latency_model_read_failed:{type(exc).__name__}"]

    reasons = validate_hftbacktest_latency_model(latency_model)
    artifact = dict(latency_model)
    artifact["latency_model_path"] = str(latency_model_path)
    artifact["latency_model_status"] = (
        "proxy_only" if reasons == ["latency_proxy_only"] else "pass" if not reasons else "fail"
    )
    return artifact, reasons


def validate_hftbacktest_fill_queue_model(fill_queue_model: Mapping[str, Any]) -> list[str]:
    """Return fail-closed reasons for HftBacktest exchange/queue/fill assumptions."""
    reasons: list[str] = []
    for field in FILL_QUEUE_MODEL_REQUIRED_FIELDS:
        if field not in fill_queue_model or fill_queue_model[field] in ("", None):
            reasons.append(f"missing_fill_queue_model_field:{field}")

    for field in (
        "exchange_model",
        "queue_model",
        "queue_model_source",
        "fill_model_scope",
        "partial_fill_policy",
        "time_in_force_policy",
        "market_impact_mode",
    ):
        if not _nonempty_str(fill_queue_model.get(field)):
            reasons.append(f"invalid_fill_queue_model_field:{field}")

    exchange_model = fill_queue_model.get("exchange_model")
    partial_fill_policy = fill_queue_model.get("partial_fill_policy")
    if exchange_model not in FILL_QUEUE_EXCHANGE_PARTIAL_POLICY:
        reasons.append("invalid_exchange_model")
    elif partial_fill_policy != FILL_QUEUE_EXCHANGE_PARTIAL_POLICY[exchange_model]:
        reasons.append("exchange_partial_fill_policy_mismatch")

    queue_model = fill_queue_model.get("queue_model")
    queue_model_source = fill_queue_model.get("queue_model_source")
    if queue_model not in FILL_QUEUE_MODEL_SOURCES_BY_MODEL:
        reasons.append("invalid_queue_model")
    elif queue_model_source not in FILL_QUEUE_MODEL_SOURCES_BY_MODEL[queue_model]:
        reasons.append("queue_model_source_mismatch")

    fill_model_scope = fill_queue_model.get("fill_model_scope")
    if fill_model_scope not in FILL_MODEL_SCOPES:
        reasons.append("invalid_fill_model_scope")
    elif fill_model_scope == "l3_mbo" and queue_model in L2_PROBABILITY_QUEUE_MODELS:
        reasons.append("l3_scope_requires_l3_queue_model")
    elif fill_model_scope == "l2_mbp" and queue_model == "L3FIFOQueueModel":
        reasons.append("l2_scope_cannot_use_l3_queue_model")
    if fill_model_scope in {"l3_to_l2_comparison", "comparison"}:
        for field in FILL_QUEUE_COMPARISON_REQUIRED_FIELDS:
            if not _nonempty_str(fill_queue_model.get(field)):
                reasons.append(f"missing_fill_queue_comparison_field:{field}")
        if not _is_sha256_digest(fill_queue_model.get("comparison_reference_artifact_hash")):
            reasons.append("invalid_comparison_reference_artifact_hash")

    for field in ("maker_fee", "taker_fee"):
        if not _is_number(fill_queue_model.get(field)):
            reasons.append(f"invalid_fill_queue_numeric_field:{field}")
    for field in ("tick_size", "lot_size", "minimum_order_qty"):
        if not _is_positive_number(fill_queue_model.get(field)):
            reasons.append(f"invalid_fill_queue_positive_field:{field}")

    for field in FILL_QUEUE_ORDER_STATE_FIELDS:
        if field in fill_queue_model:
            value = fill_queue_model.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                reasons.append(f"invalid_fill_queue_order_state_field:{field}")
    if "fill_rate" in fill_queue_model:
        fill_rate = fill_queue_model.get("fill_rate")
        if not _is_number(fill_rate) or not 0 <= fill_rate <= 1:
            reasons.append("invalid_fill_queue_fill_rate")
    if "avg_queue_position_or_not_available" in fill_queue_model:
        queue_position = fill_queue_model.get("avg_queue_position_or_not_available")
        if not (
            _is_nonnegative_number(queue_position)
            or _nonempty_str(queue_position)
        ):
            reasons.append("invalid_avg_queue_position_or_not_available")

    market_impact_mode = _normalize_market_impact_mode(fill_queue_model.get("market_impact_mode"))
    if market_impact_mode not in MARKET_IMPACT_MODES:
        reasons.append("invalid_market_impact_mode")
    elif market_impact_mode == "not_modeled":
        reasons.append("market_impact_not_modeled")
    elif market_impact_mode == "external_charge":
        for field in MARKET_IMPACT_EXTERNAL_CHARGE_REQUIRED_FIELDS:
            if field in {"market_impact_charge_value", "liquidity_taking_max_depth_ratio"}:
                continue
            if not _nonempty_str(fill_queue_model.get(field)):
                reasons.append(f"missing_market_impact_external_charge_field:{field}")
        charge_value = fill_queue_model.get("market_impact_charge_value")
        if not _is_nonnegative_number(charge_value):
            reasons.append("invalid_market_impact_charge_value")
        depth_ratio = fill_queue_model.get("liquidity_taking_max_depth_ratio")
        if not _is_number(depth_ratio) or not 0 < depth_ratio <= 1:
            reasons.append("invalid_liquidity_taking_max_depth_ratio")

    return list(dict.fromkeys(reasons))


def validate_official_hbt4_replay_contract(
    fill_queue_model: Mapping[str, Any],
    selected_candidate: Mapping[str, Any],
) -> list[str]:
    """Return reasons when the declared contract is not what the official runner executes."""
    reasons: list[str] = []
    if fill_queue_model.get("exchange_model") != OFFICIAL_REPLAY_EXCHANGE_MODEL:
        reasons.append("official_replay_unsupported_exchange_model")
    if fill_queue_model.get("partial_fill_policy") != OFFICIAL_REPLAY_PARTIAL_FILL_POLICY:
        reasons.append("official_replay_unsupported_partial_fill_policy")
    if fill_queue_model.get("time_in_force_policy") != OFFICIAL_REPLAY_TIME_IN_FORCE_POLICY:
        reasons.append("official_replay_unsupported_time_in_force_policy")

    minimum_order_qty = fill_queue_model.get("minimum_order_qty")
    # Per Codex round-3 P2: honor official_replay_order_intent alias.
    intent_obj = (
        selected_candidate.get("hbt4_order_intent")
        or selected_candidate.get("official_replay_order_intent")
    )
    intent_qty = intent_obj.get("quantity") if isinstance(intent_obj, Mapping) else None
    if not _is_positive_number(minimum_order_qty):
        reasons.append("official_replay_invalid_minimum_order_qty")
    elif not _is_positive_number(intent_qty) or float(intent_qty) < float(minimum_order_qty):
        reasons.append("official_replay_order_qty_below_minimum")

    fill_model_scope = fill_queue_model.get("fill_model_scope")
    queue_model = fill_queue_model.get("queue_model")
    supported_queue_models = OFFICIAL_REPLAY_QUEUE_MODELS_BY_SCOPE.get(str(fill_model_scope))
    if supported_queue_models is None:
        reasons.append("official_replay_unsupported_fill_model_scope")
    elif queue_model not in supported_queue_models:
        reasons.append("official_replay_unsupported_queue_model")
    elif fill_queue_model.get("queue_model_source") not in FILL_QUEUE_MODEL_SOURCES_BY_MODEL.get(str(queue_model), set()):
        reasons.append("official_replay_unsupported_queue_model_source")

    product = str(selected_candidate.get("symbol") or "MES").split(".")[0]
    builder_fee = FeeModel(product=product).get_fee_per_contract()
    fee_reasons = []
    for field in ("maker_fee", "taker_fee"):
        fee = fill_queue_model.get(field)
        if not _is_number(fee) or not math.isclose(
            float(fee),
            builder_fee,
            rel_tol=0.0,
            abs_tol=OFFICIAL_REPLAY_FEE_ABS_TOL,
        ):
            fee_reasons.append(field)
    if fee_reasons:
        reasons.append("official_replay_unsupported_fee_model")

    return list(dict.fromkeys(reasons))


def _official_replay_builder_queue_model_type(fill_queue_model: Mapping[str, Any]) -> str:
    # Per Codex round-3 P1: when fill_model_scope == "l3_mbo", use the
    # declared queue model (should be L3FIFOQueueModel), not LogProbQueueModel2.
    # The contract validator accepts L3FIFOQueueModel for L3 scope; the builder
    # must honor the declared model, not silently switch it.
    declared = str(fill_queue_model.get("queue_model") or "LogProbQueueModel2")
    return declared


def _load_fill_queue_model_artifact(fill_queue_model_path: Path | None) -> tuple[dict[str, Any], list[str]]:
    if fill_queue_model_path is None:
        artifact = {
            "fill_queue_model_status": "not_run",
            "exchange_model": "not_run",
            "queue_model": "not_run",
            "queue_model_source": "not_run",
            "fill_model_scope": "not_run",
            "partial_fill_policy": "not_run",
            "time_in_force_policy": "not_run",
            "maker_fee": None,
            "taker_fee": None,
            "tick_size": None,
            "lot_size": None,
            "minimum_order_qty": None,
            "market_impact_mode": "not_run",
        }
        return artifact, ["fill_queue_model_path_missing"]

    try:
        fill_queue_model = _load_json(fill_queue_model_path)
    except Exception as exc:
        artifact = {
            "fill_queue_model_status": "fail",
            "exchange_model": "not_run",
            "queue_model": "not_run",
            "queue_model_source": "not_run",
            "fill_model_scope": "not_run",
            "partial_fill_policy": "not_run",
            "time_in_force_policy": "not_run",
            "maker_fee": None,
            "taker_fee": None,
            "tick_size": None,
            "lot_size": None,
            "minimum_order_qty": None,
            "market_impact_mode": "not_run",
            "fill_queue_model_path": str(fill_queue_model_path),
        }
        return artifact, [f"fill_queue_model_read_failed:{type(exc).__name__}"]

    reasons = validate_hftbacktest_fill_queue_model(fill_queue_model)
    artifact = dict(fill_queue_model)
    artifact["market_impact_mode"] = _normalize_market_impact_mode(artifact.get("market_impact_mode"))
    artifact["fill_queue_model_path"] = str(fill_queue_model_path)
    artifact["fill_queue_model_status"] = (
        "market_impact_not_modeled"
        if reasons == ["market_impact_not_modeled"]
        else "pass" if not reasons else "fail"
    )
    return artifact, reasons


def _selected_hbt4_order_intent(selected_candidate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    intent = selected_candidate.get("hbt4_order_intent")
    if intent is None:
        intent = selected_candidate.get("official_replay_order_intent")
    return intent if isinstance(intent, Mapping) else None


def validate_hbt4_order_intent(intent: Mapping[str, Any] | None) -> list[str]:
    """Validate the explicit order intent used for the minimal official HBT replay."""
    if intent is None:
        return ["hbt4_order_intent_missing"]
    reasons: list[str] = []
    for field in HBT4_INTENT_REQUIRED_FIELDS:
        if field not in intent or intent[field] in ("", None):
            reasons.append(f"missing_hbt4_order_intent_field:{field}")
    side = str(intent.get("side", "")).upper()
    if side not in {"BUY", "SELL"}:
        reasons.append("invalid_hbt4_order_side")
    if not _is_positive_number(intent.get("quantity")):
        reasons.append("invalid_hbt4_order_quantity")
    if intent.get("price_mode") not in HBT4_PRICE_MODES:
        reasons.append("invalid_hbt4_price_mode")
    max_feed_steps = intent.get("max_feed_steps")
    if not isinstance(max_feed_steps, int) or isinstance(max_feed_steps, bool) or max_feed_steps <= 0:
        reasons.append("invalid_hbt4_max_feed_steps")
    if "order_id" in intent:
        order_id = intent.get("order_id")
        if not isinstance(order_id, int) or isinstance(order_id, bool) or order_id <= 0:
            reasons.append("invalid_hbt4_order_id")
    return list(dict.fromkeys(reasons))


def _official_replay_not_run_artifact(
    *,
    reason: str,
    selected_id: str,
    selected_candidate: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    return (
        {
            "official_hftbacktest_replay_status": "not_run",
            "candidate_id": selected_id,
            "model_id": selected_candidate.get("hypothesis_id") or selected_candidate.get("model_id") or "",
            "api_calls": [],
            "accelerated_mode": False,
            "orders": [],
            "fills": [],
            "markouts": [],
            "discrepancies": [{"reason": reason}],
            "orders_intended": 0,
            "orders_submitted": 0,
            "orders_acknowledged": 0,
            "orders_cancelled": 0,
            "fills_count": 0,
            "partial_fills_count": 0,
            "unfilled_count": 0,
            "fill_rate": 0.0,
            "gross_pnl": None,
            "net_pnl": None,
            "execution_adjusted_expectancy": None,
            "max_drawdown": None,
            "adverse_selection_markout": None,
            "spread_capture_or_cost": None,
        },
        [reason, "official_replay_not_run"],
    )


def _field_value(obj: Any, field: str, default: Any) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _float_field(obj: Any, field: str, default: float = 0.0) -> float:
    try:
        return float(_field_value(obj, field, default))
    except (TypeError, ValueError):
        return default


def _int_field(obj: Any, field: str, default: int = 0) -> int:
    try:
        return int(_field_value(obj, field, default))
    except (TypeError, ValueError):
        return default


def _hbt_order_snapshot(order: Any) -> dict[str, Any]:
    return {
        "status": _int_field(order, "status", -1),
        "qty": _float_field(order, "qty"),
        "leaves_qty": _float_field(order, "leaves_qty"),
        "exec_qty": _float_field(order, "exec_qty"),
        "price": _float_field(order, "price"),
        "exec_price": _float_field(order, "exec_price"),
        "exch_timestamp": _int_field(order, "exch_timestamp"),
        "local_timestamp": _int_field(order, "local_timestamp"),
    }


def _price_from_mode(hbt: Any, side: str, price_mode: str) -> float | None:
    depth = hbt.depth(0)
    best_bid = float(depth.best_bid)
    best_ask = float(depth.best_ask)
    if math.isnan(best_bid) or math.isnan(best_ask) or best_bid <= 0 or best_ask <= 0:
        return None
    tick_size = float(getattr(depth, "tick_size", 0.0) or 0.0)
    if price_mode == "passive_best_bid_or_ask":
        return best_bid if side == "BUY" else best_ask
    if side == "BUY":
        return best_ask + tick_size
    return best_bid - tick_size


def run_minimal_official_hftbacktest_replay(
    *,
    data_npz_path: Path | None,
    selected_id: str,
    selected_candidate: Mapping[str, Any],
    fill_queue_model: Mapping[str, Any],
    latency_model: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Run one explicit, non-accelerated official HftBacktest replay."""
    if data_npz_path is None:
        return _official_replay_not_run_artifact(
            reason="data_npz_path_missing_hbt4_not_run",
            selected_id=selected_id,
            selected_candidate=selected_candidate,
        )
    intent = _selected_hbt4_order_intent(selected_candidate)
    intent_reasons = validate_hbt4_order_intent(intent)
    if intent_reasons:
        artifact, reasons = _official_replay_not_run_artifact(
            reason=intent_reasons[0],
            selected_id=selected_id,
            selected_candidate=selected_candidate,
        )
        artifact["order_intent_validation_reasons"] = intent_reasons
        return artifact, list(dict.fromkeys(intent_reasons + reasons))

    contract_reasons = validate_official_hbt4_replay_contract(fill_queue_model, selected_candidate)
    if contract_reasons:
        artifact, reasons = _official_replay_not_run_artifact(
            reason=contract_reasons[0],
            selected_id=selected_id,
            selected_candidate=selected_candidate,
        )
        artifact["official_replay_contract_validation_reasons"] = contract_reasons
        return artifact, list(dict.fromkeys(contract_reasons + reasons))

    try:
        from hftbacktest.order import GTC, LIMIT
        from backtest_pipeline.src.hft_backtest_builder import build_hftbacktest
    except Exception as exc:
        return _official_replay_not_run_artifact(
            reason=f"official_replay_import_failed:{type(exc).__name__}",
            selected_id=selected_id,
            selected_candidate=selected_candidate,
        )

    try:
        # Per Codex round-3 P2: preserve explicit zero order-entry latency.
        # The `or` chain treats 0.0 as falsy and falls back to latency_p50_ms;
        # use explicit None checks instead so 0.0 is honored.
        _oe_latency = latency_model.get("order_entry_latency_ms")
        if _oe_latency is None:
            _oe_latency = latency_model.get("latency_p50_ms")
        if _oe_latency is None:
            _oe_latency = 0.0
        latency_ms = float(_oe_latency)
        hbt = build_hftbacktest(
            str(data_npz_path),
            latency_ms=latency_ms,
            queue_model_type=_official_replay_builder_queue_model_type(fill_queue_model),
            tick_size=float(fill_queue_model.get("tick_size") or 0.25),
            lot_size=float(fill_queue_model.get("lot_size") or 1.0),
            product=str(selected_candidate.get("symbol") or "MES").split(".")[0],
            force_l3=(fill_queue_model.get("fill_model_scope") == "l3_mbo"),
        )
        side = str(intent["side"]).upper()
        max_feed_steps = int(intent["max_feed_steps"])
        price = None
        feeds_seen = 0
        observed_api_calls = ["BacktestAsset", "HashMapMarketDepthBacktest"]
        for _ in range(max_feed_steps):
            ret = int(hbt.wait_next_feed(False, 1_000_000_000))
            if "wait_next_feed" not in observed_api_calls:
                observed_api_calls.extend(("HashMapMarketDepthBacktest.wait_next_feed", "wait_next_feed"))
            if ret == 1:
                break
            feeds_seen += 1
            price = _price_from_mode(hbt, side, str(intent["price_mode"]))
            if "depth" not in observed_api_calls:
                observed_api_calls.extend(("HashMapMarketDepthBacktest.depth", "depth"))
            if price is not None:
                break
        if price is None:
            artifact, reasons = _official_replay_not_run_artifact(
                reason="official_replay_book_not_live",
                selected_id=selected_id,
                selected_candidate=selected_candidate,
            )
            artifact["feed_steps_before_order"] = feeds_seen
            return artifact, reasons

        order_id = int(intent.get("order_id", 9001))
        qty = float(intent["quantity"])
        if side == "BUY":
            submit_ret = int(hbt.submit_buy_order(0, order_id, price, qty, GTC, LIMIT, False))
            observed_api_calls.extend(("HashMapMarketDepthBacktest.submit_buy_order", "submit_buy_or_sell_order"))
        else:
            submit_ret = int(hbt.submit_sell_order(0, order_id, price, qty, GTC, LIMIT, False))
            observed_api_calls.extend(("HashMapMarketDepthBacktest.submit_sell_order", "submit_buy_or_sell_order"))
        response_ret = int(hbt.wait_order_response(0, order_id, 1_000_000_000))
        observed_api_calls.extend(("HashMapMarketDepthBacktest.wait_order_response", "wait_order_response"))
        orders = hbt.orders(0)
        observed_api_calls.extend(("HashMapMarketDepthBacktest.orders", "orders"))
        state = hbt.state_values(0)
        observed_api_calls.extend(("HashMapMarketDepthBacktest.state_values", "state_values"))

        order_obj = orders.get(order_id)
        order_snapshot = _hbt_order_snapshot(order_obj) if order_obj is not None else {}
        order_events = [
            {
                "order_id": order_id,
                "event_type": "ORDER_SUBMITTED",
                "side": side,
                "price": price,
                "quantity": qty,
                "submit_return_code": submit_ret,
                "response_return_code": response_ret,
            }
        ]
        if order_snapshot:
            order_events.append(
                {
                    "order_id": order_id,
                    "event_type": "ORDER_STATE",
                    **order_snapshot,
                }
            )
        fills = []
        exec_qty = float(order_snapshot.get("exec_qty") or 0.0)
        if exec_qty > 0:
            fills.append(
                {
                    "order_id": order_id,
                    "filled_quantity": exec_qty,
                    "avg_fill_price": float(order_snapshot.get("exec_price") or 0.0),
                    "fees": _float_field(state, "fee"),
                }
            )
        cancel_ret = None
        if order_obj is not None and float(order_snapshot.get("leaves_qty") or 0.0) > 0:
            cancel_ret = int(hbt.cancel(0, order_id, False))
            observed_api_calls.extend(("HashMapMarketDepthBacktest.cancel", "cancel"))
            hbt.wait_order_response(0, order_id, 1_000_000_000)
            order_events.append(
                {
                    "order_id": order_id,
                    "event_type": "ORDER_CANCEL_REQUESTED",
                    "cancel_return_code": cancel_ret,
                }
            )
        hbt.clear_inactive_orders(0)
        observed_api_calls.extend(("HashMapMarketDepthBacktest.clear_inactive_orders", "clear_inactive_orders"))
        final_state = hbt.state_values(0)
        gross_pnl = _float_field(final_state, "balance")
        fees = _float_field(final_state, "fee")
        net_pnl = gross_pnl - fees
        # Per Codex round-3 P1: subtract declared market-impact charges from
        # replay PnL when market_impact_mode == "external_charge". Without
        # this, net_pnl overstates execution-adjusted expectancy by the
        # declared market-impact cost.
        market_impact_charge = fill_queue_model.get("market_impact_charge_value")
        if (
            _normalize_market_impact_mode(fill_queue_model.get("market_impact_mode"))
            == "external_charge"
            and _is_positive_number(market_impact_charge)
        ):
            net_pnl = net_pnl - float(market_impact_charge)
        orders_submitted = 1 if submit_ret == 0 else 0
        orders_acknowledged = 1 if response_ret in (0, 3) else 0
        fills_count = len(fills)
        partial_fills_count = 1 if 0 < exec_qty < qty else 0
        unfilled_count = 1 if exec_qty <= 0 else 0
        replay_reasons = []
        if submit_ret != 0:
            replay_reasons.append("official_replay_submit_failed")
        if response_ret not in (0, 3):
            replay_reasons.append("official_replay_order_response_failed")
        if order_obj is None:
            replay_reasons.append("official_replay_order_state_missing")
        official_replay_status = "pass" if not replay_reasons else "fail"
        artifact = {
            "official_hftbacktest_replay_status": official_replay_status,
            "candidate_id": selected_id,
            "model_id": selected_candidate.get("hypothesis_id") or selected_candidate.get("model_id") or "",
            "api_calls": list(dict.fromkeys(observed_api_calls)),
            "api_surface_declared": list(OFFICIAL_REPLAY_API_CALLS),
            "accelerated_mode": False,
            "order_intent": dict(intent),
            "price_used": price,
            "feed_steps_before_order": feeds_seen,
            "orders": order_events,
            "fills": fills,
            "markouts": [],
            "discrepancies": [],
            "orders_intended": 1,
            "orders_submitted": orders_submitted,
            "orders_acknowledged": orders_acknowledged,
            "orders_cancelled": 1 if cancel_ret == 0 else 0,
            "fills_count": fills_count,
            "partial_fills_count": partial_fills_count,
            "unfilled_count": unfilled_count,
            "fill_rate": fills_count / max(orders_submitted, 1),
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            # Per Codex round-3 P2: persist replay fees for HBT5 comparison.
            "total_fees": fees,
            "fees": fees,
            "fee_total": fees,
            "execution_adjusted_expectancy": net_pnl,
            "max_drawdown": 0.0,
            "adverse_selection_markout": None,
            "spread_capture_or_cost": None,
        }
        return artifact, replay_reasons
    except Exception as exc:
        return _official_replay_not_run_artifact(
            reason=f"official_replay_run_failed:{type(exc).__name__}",
            selected_id=selected_id,
            selected_candidate=selected_candidate,
        )


def compute_hftbacktest_source_lock_hash(lock: Mapping[str, Any]) -> str:
    """Hash a source lock excluding identity/time/hash fields."""
    return _sha256_payload(
        _hash_without_keys(lock, {"source_lock_hash", "source_lock_created_at_utc"})
    )


def compute_replay_summary_hash(summary: Mapping[str, Any]) -> str:
    """Hash replay summary content excluding identity/time/hash fields."""
    return _sha256_payload(
        _hash_without_keys(summary, {"replay_summary_hash", "created_at_utc"})
    )


def _repo_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
    except Exception:
        return "unknown"


def _repo_dirty(repo_root: Path) -> bool:
    try:
        status = subprocess.check_output(
            ["git", "status", "--short"],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return True
    return bool(status.strip())


def detect_hftbacktest_installation() -> dict[str, Any]:
    """Return installed HftBacktest package facts without importing heavy modules."""
    spec = importlib.util.find_spec("hftbacktest")
    package_version = "unavailable"
    try:
        package_version = importlib.metadata.version("hftbacktest")
    except importlib.metadata.PackageNotFoundError:
        pass
    return {
        "available": spec is not None and package_version != "unavailable",
        "python_package_name": "hftbacktest",
        "python_package_version": package_version,
        "installed_module_path": str(spec.origin) if spec and spec.origin else "unavailable",
    }


def _event_constants() -> dict[str, int]:
    try:
        from hftbacktest import types as hbt_types
    except Exception as exc:
        raise HftBacktestRealismArtifactError(
            f"hftbacktest.types unavailable: {type(exc).__name__}: {exc}"
        ) from exc
    return {
        "EXCH_EVENT": int(hbt_types.EXCH_EVENT),
        "LOCAL_EVENT": int(hbt_types.LOCAL_EVENT),
        "ADD_ORDER_EVENT": int(hbt_types.ADD_ORDER_EVENT),
        "DEPTH_EVENT": int(hbt_types.DEPTH_EVENT),
        "CANCEL_ORDER_EVENT": int(hbt_types.CANCEL_ORDER_EVENT),
        "MODIFY_ORDER_EVENT": int(hbt_types.MODIFY_ORDER_EVENT),
        "FILL_EVENT": int(hbt_types.FILL_EVENT),
    }


def _expected_event_dtype() -> Any:
    try:
        from hftbacktest.types import event_dtype
    except Exception as exc:
        raise HftBacktestRealismArtifactError(
            f"hftbacktest event_dtype unavailable: {type(exc).__name__}: {exc}"
        ) from exc
    return event_dtype


def _event_type_counts(events: Any) -> dict[str, int]:
    ev_types = (events["ev"].astype("int64")) & 0xFF
    counts: dict[str, int] = {}
    for value in ev_types:
        key = str(int(value))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _timestamp_units_value(value: Any) -> str:
    if value is None:
        return "unproven"
    try:
        value = value.item()
    except Exception:
        pass
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    return text if text else "unproven"


def _timestamp_units_proven_nanoseconds(timestamp_units: Any) -> tuple[bool, str]:
    observed = _timestamp_units_value(timestamp_units)
    return observed == "nanoseconds", observed


def classify_hftbacktest_events(events: Any) -> str:
    """Classify a HftBacktest event array as L2, L3, or mixed."""
    ev_types = set(int(value) for value in ((events["ev"].astype("int64")) & 0xFF))
    if not ev_types:
        return "empty_rejected"
    has_l3 = bool(ev_types & set(L3_EVENT_TYPES))
    has_l2 = bool(ev_types & set(L2_EVENT_TYPES))
    has_unknown = bool(ev_types - set(L3_EVENT_TYPES) - set(L2_EVENT_TYPES))
    if has_unknown:
        return "unknown_rejected"
    if has_l3 and has_l2:
        return "mixed_rejected"
    if has_l3:
        return "l3_mbo"
    return "l2_mbp"


def count_l3_orphans(events: Any) -> tuple[int, list[int]]:
    """Count L3 CANCEL/MODIFY/FILL events whose order_id has no prior ADD."""
    constants = _event_constants()
    add_event = constants["ADD_ORDER_EVENT"]
    orphan_types = {
        constants["CANCEL_ORDER_EVENT"],
        constants["MODIFY_ORDER_EVENT"],
        constants["FILL_EVENT"],
    }
    ev_types = (events["ev"].astype("int64")) & 0xFF
    seen: set[int] = set()
    orphan_ids: list[int] = []
    for index, ev_type in enumerate(ev_types):
        order_id = int(events[index]["order_id"])
        if int(ev_type) == add_event:
            if order_id:
                seen.add(order_id)
        elif int(ev_type) in orphan_types and order_id not in seen:
            orphan_ids.append(order_id)
    return len(orphan_ids), orphan_ids[:20]


def _validate_event_dtype(events: Any) -> tuple[bool, list[str], list[str]]:
    try:
        names = list(events.dtype.names or [])
    except AttributeError:
        return False, [], list(EXPECTED_EVENT_DTYPE_FIELDS)
    missing = [field for field in EXPECTED_EVENT_DTYPE_FIELDS if field not in names]
    if missing:
        return False, names, missing
    try:
        expected = _expected_event_dtype()
        exact = bool(events.dtype == expected)
    except HftBacktestRealismArtifactError:
        exact = False
    return exact, names, missing


def validate_hftbacktest_event_array(
    events: Any,
    *,
    data_path: str | None = None,
    timestamp_units: Any = None,
) -> dict[str, Any]:
    """Validate HftBacktest event data before replay, without correcting it."""
    reasons: list[str] = []
    timestamp_units_ok, timestamp_units_observed = _timestamp_units_proven_nanoseconds(timestamp_units)
    if not timestamp_units_ok:
        reasons.append("TIMESTAMP_UNITS_UNPROVEN")
    try:
        constants = _event_constants()
    except HftBacktestRealismArtifactError:
        constants = {}
        reasons.append("HFTBACKTEST_DATA_VALIDATION_UNAVAILABLE")

    dtype_exact, dtype_fields, missing_dtype_fields = _validate_event_dtype(events)
    if not dtype_exact or missing_dtype_fields:
        reasons.append("EVENT_DTYPE_INVALID")

    row_count = int(len(events))
    classification = "unknown"
    event_counts: dict[str, int] = {}
    orphan_count = 0
    orphan_sample: list[int] = []
    min_feed_latency_ns: int | None = None

    if not missing_dtype_fields:
        classification = classify_hftbacktest_events(events)
        event_counts = _event_type_counts(events)
        if classification == "mixed_rejected":
            reasons.append("L2_L3_MISMATCH")
        elif classification == "empty_rejected":
            reasons.append("EVENT_ARRAY_EMPTY")
        elif classification == "unknown_rejected":
            reasons.append("EVENT_TYPE_UNKNOWN")

        local_minus_exch = events["local_ts"].astype("int64") - events["exch_ts"].astype("int64")
        if row_count:
            min_feed_latency_ns = int(local_minus_exch.min())
        if row_count and bool((local_minus_exch < 0).any()):
            reasons.append("NEGATIVE_FEED_LATENCY_UNCORRECTED")

        if constants:
            exch_mask = (events["ev"].astype("uint64") & constants["EXCH_EVENT"]) == constants["EXCH_EVENT"]
            local_mask = (events["ev"].astype("uint64") & constants["LOCAL_EVENT"]) == constants["LOCAL_EVENT"]
            if bool(exch_mask.any()) and bool((events["exch_ts"][exch_mask][1:] < events["exch_ts"][exch_mask][:-1]).any()):
                reasons.append("EXCHANGE_ORDER_INVALID")
            if bool(local_mask.any()) and bool((events["local_ts"][local_mask][1:] < events["local_ts"][local_mask][:-1]).any()):
                reasons.append("LOCAL_ORDER_INVALID")

            if classification == "l3_mbo":
                l3_type_mask = (((events["ev"].astype("int64")) & 0xFF) == constants["ADD_ORDER_EVENT"])
                if bool(l3_type_mask.any()) and bool((events["order_id"][l3_type_mask] == 0).any()):
                    reasons.append("L3_ORDER_ID_MISSING")
                orphan_count, orphan_sample = count_l3_orphans(events)
                if orphan_count:
                    reasons.append("ORPHAN_L3_EVENTS_UNACCOUNTED")

        try:
            from hftbacktest.data import validate_event_order
        except Exception as exc:
            official_status = f"unavailable:{type(exc).__name__}:{exc}"
            reasons.append("HFTBACKTEST_VALIDATE_EVENT_ORDER_UNAVAILABLE")
        else:
            try:
                validate_event_order(events)
                official_status = "pass"
            except Exception as exc:
                official_status = f"fail:{type(exc).__name__}:{exc}"
                reasons.append("HFTBACKTEST_VALIDATE_EVENT_ORDER_FAILED")
    else:
        official_status = "not_run_dtype_invalid"

    reasons = list(dict.fromkeys(reasons))
    return {
        "data_path": data_path,
        "data_validation_status": "pass" if not reasons else "fail",
        "docs_pages_used": HFTBACKTEST_DATA_VALIDATION_DOCS,
        "row_count": row_count,
        "expected_dtype_fields": list(EXPECTED_EVENT_DTYPE_FIELDS),
        "dtype_fields": dtype_fields,
        "dtype_exact_match": dtype_exact,
        "missing_dtype_fields": missing_dtype_fields,
        "timestamp_units": timestamp_units_observed,
        "exchange_order_status": "fail" if "EXCHANGE_ORDER_INVALID" in reasons else "pass",
        "local_order_status": "fail" if "LOCAL_ORDER_INVALID" in reasons else "pass",
        "feed_latency_status": "fail" if "NEGATIVE_FEED_LATENCY_UNCORRECTED" in reasons else "pass",
        "min_feed_latency_ns": min_feed_latency_ns,
        "official_validate_event_order_status": official_status,
        "l2_l3_classification": classification,
        "event_type_counts": event_counts,
        "orphan_l3_event_count": orphan_count,
        "orphan_l3_order_id_sample": orphan_sample,
        "fail_closed_reasons": reasons,
    }


def validate_hftbacktest_data_path(data_path: Path) -> dict[str, Any]:
    """Load a HftBacktest NPZ data file and write a fail-closed validation artifact."""
    try:
        import numpy as np

        with np.load(data_path, allow_pickle=False) as payload:
            timestamp_units = payload["timestamp_units"] if "timestamp_units" in payload.files else None
            if "data" not in payload.files:
                return {
                    "data_path": str(data_path),
                    "data_validation_status": "fail",
                    "docs_pages_used": HFTBACKTEST_DATA_VALIDATION_DOCS,
                    "row_count": 0,
                    "expected_dtype_fields": list(EXPECTED_EVENT_DTYPE_FIELDS),
                    "dtype_fields": [],
                    "dtype_exact_match": False,
                    "missing_dtype_fields": list(EXPECTED_EVENT_DTYPE_FIELDS),
                    "timestamp_units": _timestamp_units_value(timestamp_units),
                    "exchange_order_status": "not_run",
                    "local_order_status": "not_run",
                    "feed_latency_status": "not_run",
                    "min_feed_latency_ns": None,
                    "official_validate_event_order_status": "not_run_missing_data_array",
                    "l2_l3_classification": "unknown",
                    "event_type_counts": {},
                    "orphan_l3_event_count": 0,
                    "orphan_l3_order_id_sample": [],
                    "fail_closed_reasons": ["DATA_NPZ_MISSING_DATA_ARRAY"],
                }
            events = payload["data"]
    except Exception as exc:
        return {
            "data_path": str(data_path),
            "data_validation_status": "fail",
            "docs_pages_used": HFTBACKTEST_DATA_VALIDATION_DOCS,
            "row_count": 0,
            "expected_dtype_fields": list(EXPECTED_EVENT_DTYPE_FIELDS),
            "dtype_fields": [],
            "dtype_exact_match": False,
            "missing_dtype_fields": list(EXPECTED_EVENT_DTYPE_FIELDS),
            "timestamp_units": "unproven",
            "exchange_order_status": "not_run",
            "local_order_status": "not_run",
            "feed_latency_status": "not_run",
            "min_feed_latency_ns": None,
            "official_validate_event_order_status": f"not_run_read_failed:{type(exc).__name__}",
            "l2_l3_classification": "unknown",
            "event_type_counts": {},
            "orphan_l3_event_count": 0,
            "orphan_l3_order_id_sample": [],
            "fail_closed_reasons": ["DATA_NPZ_READ_FAILED"],
        }
    return validate_hftbacktest_event_array(
        events,
        data_path=str(data_path),
        timestamp_units=timestamp_units,
    )


def _not_run_data_validation(data_path: Path | None) -> dict[str, Any]:
    return {
        "data_path": str(data_path) if data_path else None,
        "data_validation_status": "not_run",
        "docs_pages_used": HFTBACKTEST_DATA_VALIDATION_DOCS,
        "row_count": 0,
        "expected_dtype_fields": list(EXPECTED_EVENT_DTYPE_FIELDS),
        "dtype_fields": [],
        "dtype_exact_match": False,
        "missing_dtype_fields": [],
        "timestamp_units": "unproven",
        "exchange_order_status": "not_run",
        "local_order_status": "not_run",
        "feed_latency_status": "not_run",
        "min_feed_latency_ns": None,
        "official_validate_event_order_status": "not_run_no_data_npz",
        "l2_l3_classification": "unknown",
        "event_type_counts": {},
        "orphan_l3_event_count": 0,
        "orphan_l3_order_id_sample": [],
        "fail_closed_reasons": ["data_npz_path_missing_hbt1_not_run"],
    }


def build_hftbacktest_source_lock(
    *,
    repo_root: Path,
    upstream_ref: str | None = None,
    native_hot_path_evidence: list[str] | None = None,
    native_hot_path_status: str | None = None,
    hft3_adapter_files: list[str] | None = None,
    docs_pages_used: list[str] | None = None,
    known_doc_repo_discrepancies: list[str] | None = None,
    license_review: str = "license_review_pending_before_execution_realism_pass",
    rust_crate_version_or_not_used: str = "not_used_by_python_hbt0",
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the HBT-0 source lock required before any replay result."""
    install = detect_hftbacktest_installation()
    evidence = _optional_list_arg(
        native_hot_path_evidence,
        field="native_hot_path_evidence",
    ) or []
    docs = _optional_list_arg(docs_pages_used, field="docs_pages_used") or DOCS_PAGES_USED
    adapter_files = (
        _optional_list_arg(hft3_adapter_files, field="hft3_adapter_files")
        or DEFAULT_ADAPTER_FILES
    )
    discrepancies = (
        _optional_list_arg(
            known_doc_repo_discrepancies,
            field="known_doc_repo_discrepancies",
        )
        or []
    )
    hot_path_status = native_hot_path_status or (
        "provided" if evidence else "missing_required_native_cpp_hot_path_evidence"
    )
    lock: dict[str, Any] = {
        "upstream_repo_url": UPSTREAM_REPO_URL,
        "upstream_commit_sha_or_tag": upstream_ref or "",
        "upstream_ref_verification_status": _upstream_ref_verification_status(
            upstream_ref,
            str(install["python_package_version"]),
        ),
        "upstream_ref_verified_against": "installed_python_package_version",
        "upstream_docs_url": UPSTREAM_DOCS_URL,
        "docs_pages_used": list(docs),
        "python_package_name": install["python_package_name"],
        "python_package_version": install["python_package_version"],
        "rust_crate_version_or_not_used": rust_crate_version_or_not_used,
        "installed_module_path": install["installed_module_path"],
        "source_lock_created_at_utc": created_at_utc or _utc_now(),
        "hft3_commit": _repo_commit(repo_root),
        "hft3_worktree_dirty": _repo_dirty(repo_root),
        "hft3_adapter_files": list(adapter_files),
        "api_surface_used": list(DEFAULT_API_SURFACE_USED),
        "known_doc_repo_discrepancies": list(discrepancies),
        "license_review": license_review,
        "native_hot_path_required": True,
        "native_hot_path_evidence": evidence,
        "native_hot_path_status": hot_path_status,
        "hftbacktest_available": bool(install["available"]),
    }
    lock["source_lock_hash"] = compute_hftbacktest_source_lock_hash(lock)
    return lock


def validate_hftbacktest_source_lock(lock: Mapping[str, Any]) -> list[str]:
    """Return fail-closed source-lock validation reasons."""
    reasons: list[str] = []
    for field in SOURCE_LOCK_REQUIRED_FIELDS:
        if field not in lock or lock[field] in ("", None):
            reasons.append(f"missing_source_lock_field:{field}")
        elif field in {"docs_pages_used", "hft3_adapter_files", "api_surface_used"}:
            if not isinstance(lock[field], list):
                reasons.append(f"source_lock_field_not_list:{field}")
            elif not lock[field]:
                reasons.append(f"empty_source_lock_field:{field}")
    if lock.get("upstream_repo_url") != UPSTREAM_REPO_URL:
        reasons.append("source_lock_upstream_repo_mismatch")
    if lock.get("upstream_docs_url") != UPSTREAM_DOCS_URL:
        reasons.append("source_lock_docs_url_mismatch")
    if lock.get("python_package_name") != "hftbacktest":
        reasons.append("source_lock_package_name_mismatch")
    if lock.get("upstream_ref_verification_status") != "package_version_match":
        reasons.append("source_lock_upstream_ref_unverified")
    if lock.get("upstream_ref_verified_against") != "installed_python_package_version":
        reasons.append("source_lock_upstream_ref_verification_basis_invalid")
    if lock.get("python_package_version") in ("", None, "unavailable"):
        reasons.append("hftbacktest_unavailable")
    if lock.get("installed_module_path") in ("", None, "unavailable"):
        reasons.append("hftbacktest_module_path_unavailable")
    if lock.get("native_hot_path_required") is not True:
        reasons.append("native_hot_path_required_must_be_true")
    if not lock.get("native_hot_path_evidence"):
        reasons.append("native_cpp_hot_path_evidence_missing")
    elif lock.get("native_hot_path_status") != "provided":
        reasons.append("native_cpp_hot_path_evidence_missing")
    elif not all(_looks_like_native_cpp_hot_path_evidence(item) for item in lock.get("native_hot_path_evidence", [])):
        reasons.append("native_cpp_hot_path_evidence_unrecognized")
    if "source_lock_hash" in lock:
        expected = compute_hftbacktest_source_lock_hash(lock)
        if lock["source_lock_hash"] != expected:
            reasons.append("source_lock_hash_mismatch")
    return reasons


def validate_replay_summary(
    summary: Mapping[str, Any],
    *,
    source_lock: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return replay-summary validation reasons, including PASS source-lock refusal."""
    reasons: list[str] = []
    for field in REPLAY_SUMMARY_REQUIRED_FIELDS:
        if field not in summary:
            reasons.append(f"missing_replay_summary_field:{field}")
    status = summary.get("replay_realism_status")
    if status not in REPLAY_SUMMARY_STATUSES:
        reasons.append("invalid_replay_realism_status")
    if summary.get("accelerated_mode") is not False:
        reasons.append("accelerated_mode_cannot_certify_hbt0")
    if summary.get("certification_allowed") is True:
        if summary.get("accelerated_mode") is not False:
            reasons.append("certification_allowed_requires_non_accelerated_mode")
        if summary.get("official_hftbacktest_replay_status") != "pass":
            reasons.append("certification_allowed_requires_official_hftbacktest_replay")
        comparison_hash = summary.get("full_replay_comparison_hash_or_not_run")
        if comparison_hash == "not_run" or not (
            _is_sha256_digest(comparison_hash) or _is_raw_sha256_digest(comparison_hash)
        ):
            reasons.append("certification_allowed_requires_full_replay_comparison_hash")
    elif summary.get("certification_allowed") is not False:
        reasons.append("invalid_certification_allowed")
    for field in ("accuracy_tradeoff_declared", "queue_position_modeled", "order_response_latency_modeled"):
        if field in summary and not isinstance(summary.get(field), bool):
            reasons.append(f"invalid_replay_summary_field:{field}")
    if "full_replay_comparison_hash_or_not_run" in summary:
        comparison_hash = summary.get("full_replay_comparison_hash_or_not_run")
        if comparison_hash != "not_run" and not (
            _is_sha256_digest(comparison_hash) or _is_raw_sha256_digest(comparison_hash)
        ):
            reasons.append("invalid_full_replay_comparison_hash_or_not_run")

    if status == "pass":
        if summary.get("certification_allowed") is not True:
            reasons.append("pass_requires_certification_allowed")
        if summary.get("official_hftbacktest_replay_status") != "pass":
            reasons.append("hbt0_pass_status_forbidden")
            reasons.append("pass_requires_official_hftbacktest_replay")
        if not summary.get("official_replay_artifact_hash"):
            reasons.append("pass_requires_official_replay_artifact_hash")
        if summary.get("fail_closed_reasons"):
            reasons.append("pass_artifact_has_fail_closed_reasons")
        if source_lock is None:
            reasons.append("pass_artifact_missing_source_lock")
        else:
            lock_reasons = validate_hftbacktest_source_lock(source_lock)
            reasons.extend(f"pass_source_lock_invalid:{reason}" for reason in lock_reasons)
            if summary.get("hftbacktest_source_lock_hash") != source_lock.get("source_lock_hash"):
                reasons.append("pass_source_lock_hash_mismatch")
            if (
                summary.get("official_hftbacktest_replay_status") == "pass"
                and not _source_lock_has_hash_backed_native_hot_path_evidence(source_lock)
            ):
                reasons.append("pass_requires_hash_backed_native_cpp_hot_path_evidence")
    return reasons


def _replay_status_from_fail_reasons(
    fail_reasons: list[str],
    data_validation: Mapping[str, Any],
) -> str:
    """Map fail-closed reasons into the most honest top-level replay status."""
    if "accelerated_mode_cannot_certify_hbt0" in fail_reasons:
        return "accelerated_not_certifying"
    if "hftbacktest_unavailable" in fail_reasons:
        return "hftbacktest_unavailable"
    if "HFTBACKTEST_VALIDATE_EVENT_ORDER_UNAVAILABLE" in fail_reasons:
        return "fail"
    critical_reasons = [
        reason
        for reason in fail_reasons
        if reason not in NON_REPLAY_ONLY_REASONS
        and reason != "latency_proxy_only"
        and reason != "market_impact_not_modeled"
        and not reason.startswith("DATA_")
        and reason not in DATA_VALIDATION_FAIL_STATUSES
    ]
    if critical_reasons:
        return "fail"
    if data_validation.get("data_validation_status") == "fail":
        return "data_invalid"
    if "latency_proxy_only" in fail_reasons:
        return "latency_proxy_only"
    if "market_impact_not_modeled" in fail_reasons:
        return "market_impact_not_modeled"
    return "research_only"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(dict(row), sort_keys=True, default=str) + "\n" for row in rows),
        encoding="utf-8",
    )


def _summary_metric(
    official_replay: Mapping[str, Any],
    fill_queue_model: Mapping[str, Any],
    field: str,
    default: Any,
) -> Any:
    if official_replay.get("official_hftbacktest_replay_status") == "pass":
        return official_replay.get(field, default)
    return fill_queue_model.get(field, default)


def _metric_delta(
    replay_value: Any,
    observed_value: Any,
    *,
    tolerance: float = 1e-9,
) -> tuple[float | None, bool | None]:
    if not _is_number(replay_value) or not _is_number(observed_value):
        return None, None
    delta = round(float(replay_value) - float(observed_value), 12)
    return delta, abs(delta) <= tolerance


def _first_numeric(payload: Mapping[str, Any], fields: tuple[str, ...]) -> float | None:
    for field in fields:
        value = payload.get(field)
        if _is_number(value):
            return float(value)
    return None


def _order_state_mismatches(
    replay_metrics: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for field in (
        "orders_submitted",
        "orders_acknowledged",
        "orders_cancelled",
        "fills_count",
        "partial_fills_count",
        "unfilled_count",
    ):
        if field not in observation:
            continue
        replay_value = replay_metrics.get(field)
        observed_value = observation.get(field)
        if replay_value != observed_value:
            mismatches.append({
                "field": field,
                "replay": replay_value,
                "observed": observed_value,
            })
    return mismatches


def _observation_metrics(observation: Mapping[str, Any]) -> Mapping[str, Any]:
    metrics = observation.get("metrics")
    return metrics if isinstance(metrics, Mapping) else {}


def _observation_order_state(observation: Mapping[str, Any]) -> Mapping[str, Any]:
    order_state = observation.get("order_state")
    return order_state if isinstance(order_state, Mapping) else {}


def _selected_observation_identity(selected_candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": selected_candidate.get("candidate_id"),
        "model_id": selected_candidate.get("model_id") or selected_candidate.get("hypothesis_id"),
        "symbol": selected_candidate.get("symbol"),
        "parameter_values_hash": selected_candidate.get("parameter_values_hash"),
    }


def _validate_observation_artifact_schema(
    observation: Mapping[str, Any],
    selected_candidate: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    expected_identity = _selected_observation_identity(selected_candidate)
    for field, expected_value in expected_identity.items():
        observed_value = observation.get(field)
        if observed_value in (None, ""):
            reasons.append(f"hbt5_observation_identity_missing:{field}")
        elif expected_value not in (None, "") and observed_value != expected_value:
            reasons.append(f"hbt5_observation_identity_mismatch:{field}")

    observed_params = observation.get("parameter_values")
    selected_params = selected_candidate.get("parameter_values")
    if observed_params is not None and selected_params is not None and observed_params != selected_params:
        reasons.append("hbt5_observation_identity_mismatch:parameter_values")

    metrics = observation.get("metrics")
    if not isinstance(metrics, Mapping):
        reasons.append("hbt5_observation_metrics_missing_or_malformed")
    else:
        for field in HBT5_OBSERVATION_REQUIRED_METRIC_FIELDS:
            if field not in metrics:
                reasons.append(f"hbt5_observation_metric_missing:{field}")
            elif not _is_number(metrics.get(field)):
                reasons.append(f"hbt5_observation_metric_not_numeric:{field}")

    order_state = observation.get("order_state")
    if not isinstance(order_state, Mapping):
        reasons.append("hbt5_observation_order_state_missing_or_malformed")
    else:
        for field in HBT5_OBSERVATION_REQUIRED_ORDER_STATE_FIELDS:
            if field not in order_state:
                reasons.append(f"hbt5_observation_order_state_missing:{field}")
            elif not isinstance(order_state.get(field), int) or isinstance(order_state.get(field), bool):
                reasons.append(f"hbt5_observation_order_state_not_integer:{field}")
    return reasons


def _append_metric_discrepancy(
    discrepancies: list[dict[str, Any]],
    reasons: list[str],
    *,
    field: str,
    replay_value: Any,
    observed_value: Any,
    reason: str,
) -> None:
    delta, match = _metric_delta(replay_value, observed_value)
    if match is False:
        discrepancies.append({
            "field": field,
            "replay": replay_value,
            "observed": observed_value,
            "delta": delta,
        })
        reasons.append(reason)
    elif match is None and _is_number(observed_value):
        discrepancies.append({
            "field": field,
            "replay": replay_value,
            "observed": observed_value,
            "delta": None,
            "reason": "replay_metric_missing",
        })
        reasons.append(reason)


def build_discrepancy_comparison_artifact(
    *,
    observation_artifact_path: Path | None,
    official_replay: Mapping[str, Any],
    latency_model: Mapping[str, Any],
    fill_queue_model: Mapping[str, Any],
    selected_candidate: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Compare replay metrics to an offline paper/live observation artifact."""

    if observation_artifact_path is None:
        return (
            {
                "comparison_status": "not_run",
                "reason": "observation_artifact_missing",
                "observation_artifact_path": None,
                "observation_artifact_hash": None,
                "fill_rate_delta": None,
                "latency_delta_ms": {},
                "fee_delta": None,
                "slippage_delta": None,
                "markout_delta": None,
                "order_state_mismatches": [],
                "discrepancies": [],
                "discrepancy_reasons": ["hbt5_discrepancy_comparison_not_run"],
                "fail_closed_reasons": [],
                "certification_feedback_status": "blocked_missing_observation",
                "parameter_tuning_allowed": False,
                "hidden_parameter_mutations": [],
            },
            [],
        )

    try:
        observation_text = observation_artifact_path.read_text(encoding="utf-8")
        observation = json.loads(observation_text)
    except Exception as exc:
        return (
            {
                "comparison_status": "fail",
                "reason": "observation_artifact_malformed",
                "observation_artifact_path": str(observation_artifact_path),
                "observation_artifact_hash": None,
                "fill_rate_delta": None,
                "latency_delta_ms": {},
                "fee_delta": None,
                "slippage_delta": None,
                "markout_delta": None,
                "order_state_mismatches": [],
                "discrepancies": [],
                "discrepancy_reasons": ["hbt5_observation_artifact_malformed"],
                "fail_closed_reasons": ["hbt5_observation_artifact_malformed"],
                "certification_feedback_status": "blocked_malformed_observation",
                "parameter_tuning_allowed": False,
                "hidden_parameter_mutations": [],
            },
            ["hbt5_observation_artifact_malformed"],
        )

    if not isinstance(observation, Mapping):
        return (
            {
                "comparison_status": "fail",
                "reason": "observation_artifact_malformed",
                "observation_artifact_path": str(observation_artifact_path),
                "observation_artifact_hash": _sha256_payload(observation),
                "fill_rate_delta": None,
                "latency_delta_ms": {},
                "fee_delta": None,
                "slippage_delta": None,
                "markout_delta": None,
                "order_state_mismatches": [],
                "discrepancies": [],
                "discrepancy_reasons": ["hbt5_observation_artifact_malformed"],
                "fail_closed_reasons": ["hbt5_observation_artifact_malformed"],
                "certification_feedback_status": "blocked_malformed_observation",
                "parameter_tuning_allowed": False,
                "hidden_parameter_mutations": [],
            },
            ["hbt5_observation_artifact_malformed"],
        )

    schema_reasons = _validate_observation_artifact_schema(observation, selected_candidate)
    if schema_reasons:
        return (
            {
                "comparison_status": "fail",
                "reason": "observation_artifact_malformed",
                "observation_artifact_path": str(observation_artifact_path),
                "observation_artifact_hash": _sha256_payload(dict(observation)),
                "fill_rate_delta": None,
                "latency_delta_ms": {},
                "fee_delta": None,
                "slippage_delta": None,
                "markout_delta": None,
                "order_state_mismatches": [],
                "discrepancies": [],
                "discrepancy_reasons": schema_reasons,
                "fail_closed_reasons": schema_reasons,
                "certification_feedback_status": "blocked_malformed_observation",
                "parameter_tuning_allowed": False,
                "hidden_parameter_mutations": [],
            },
            schema_reasons,
        )

    metrics = _observation_metrics(observation)
    order_state = _observation_order_state(observation)
    replay_metrics = {
        "fill_rate": official_replay.get("fill_rate", fill_queue_model.get("fill_rate")),
        "latency_p50_ms": latency_model.get("latency_p50_ms"),
        "latency_p90_ms": latency_model.get("latency_p90_ms"),
        "latency_p99_ms": latency_model.get("latency_p99_ms"),
        "fees": _first_numeric(official_replay, ("total_fees", "fees", "fee_total")),
        "slippage": (
            _first_numeric(official_replay, ("slippage", "slippage_cost", "spread_capture_or_cost"))
            or _first_numeric(fill_queue_model, ("total_slippage", "slippage", "market_impact_charge_value"))
        ),
        "markout": _first_numeric(
            official_replay,
            ("markout", "adverse_selection_markout", "adverse_selection_markout_ticks"),
        ),
        "orders_submitted": official_replay.get("orders_submitted", fill_queue_model.get("orders_submitted")),
        "orders_acknowledged": official_replay.get("orders_acknowledged", fill_queue_model.get("orders_acknowledged")),
        "orders_cancelled": official_replay.get("orders_cancelled", fill_queue_model.get("orders_cancelled")),
        "fills_count": official_replay.get("fills_count", fill_queue_model.get("fills_count")),
        "partial_fills_count": official_replay.get("partial_fills_count", fill_queue_model.get("partial_fills_count")),
        "unfilled_count": official_replay.get("unfilled_count", fill_queue_model.get("unfilled_count")),
    }

    discrepancy_reasons: list[str] = []
    discrepancies: list[dict[str, Any]] = []
    fill_rate_delta, fill_rate_match = _metric_delta(
        replay_metrics.get("fill_rate"),
        metrics.get("fill_rate"),
    )
    if fill_rate_match is False:
        discrepancies.append({
            "field": "fill_rate",
            "replay": replay_metrics.get("fill_rate"),
            "observed": metrics.get("fill_rate"),
            "delta": fill_rate_delta,
        })
        discrepancy_reasons.append("fill_rate_delta_nonzero")

    latency_delta_ms: dict[str, float | None] = {}
    for field in ("latency_p50_ms", "latency_p90_ms", "latency_p99_ms"):
        delta, match = _metric_delta(replay_metrics.get(field), metrics.get(field))
        latency_delta_ms[field] = delta
        if match is False:
            discrepancies.append({
                "field": field,
                "replay": replay_metrics.get(field),
                "observed": metrics.get(field),
                "delta": delta,
            })
            discrepancy_reasons.append(f"{field}_delta_nonzero")

    observed_fee = _first_numeric(metrics, ("total_fees", "fees", "fee_total", "maker_fees", "taker_fees"))
    fee_delta, fee_match = _metric_delta(replay_metrics.get("fees"), observed_fee)
    if fee_match is False:
        discrepancies.append({
            "field": "total_fees",
            "replay": replay_metrics.get("fees"),
            "observed": observed_fee,
            "delta": fee_delta,
        })
        discrepancy_reasons.append("fee_delta_nonzero")
    elif fee_match is None and _is_number(observed_fee) and float(observed_fee) != 0.0:
        discrepancies.append({
            "field": "total_fees",
            "replay": replay_metrics.get("fees"),
            "observed": observed_fee,
            "delta": None,
            "reason": "replay_metric_missing",
        })
        discrepancy_reasons.append("fee_delta_nonzero")

    observed_slippage = _first_numeric(
        metrics,
        ("total_slippage", "slippage", "slippage_cost", "spread_capture_or_cost"),
    )
    slippage_delta, _slippage_match = _metric_delta(replay_metrics.get("slippage"), observed_slippage)
    _append_metric_discrepancy(
        discrepancies,
        discrepancy_reasons,
        field="total_slippage",
        replay_value=replay_metrics.get("slippage"),
        observed_value=observed_slippage,
        reason="slippage_delta_nonzero",
    )

    observed_markout = _first_numeric(
        metrics,
        ("adverse_selection_markout", "markout", "adverse_selection_markout_ticks"),
    )
    markout_delta, markout_match = _metric_delta(
        replay_metrics.get("markout"),
        observed_markout,
    )
    if markout_match is False:
        discrepancies.append({
            "field": "adverse_selection_markout",
            "replay": replay_metrics.get("markout"),
            "observed": observed_markout,
            "delta": markout_delta,
        })
        discrepancy_reasons.append("markout_delta_nonzero")
    elif markout_match is None and _is_number(observed_markout) and float(observed_markout) != 0.0:
        discrepancies.append({
            "field": "adverse_selection_markout",
            "replay": replay_metrics.get("markout"),
            "observed": observed_markout,
            "delta": None,
            "reason": "replay_metric_missing",
        })
        discrepancy_reasons.append("markout_delta_nonzero")

    order_state_mismatches = _order_state_mismatches(replay_metrics, order_state)
    if order_state_mismatches:
        discrepancies.extend(
            {
                "field": f"order_state.{item['field']}",
                "replay": item["replay"],
                "observed": item["observed"],
            }
            for item in order_state_mismatches
        )
        discrepancy_reasons.append("order_state_mismatch")

    comparison_status = "pass" if not discrepancy_reasons else "fail"
    certification_feedback_status = (
        "ready"
        if comparison_status == "pass"
        else "blocked_discrepancy"
    )
    return (
        {
            "comparison_status": comparison_status,
            "reason": None if comparison_status == "pass" else "observation_replay_discrepancy",
            "observation_artifact_path": str(observation_artifact_path),
            "observation_artifact_hash": _sha256_payload(dict(observation)),
            "observation_source": observation.get("observation_source", "not_recorded"),
            "fill_rate_delta": fill_rate_delta,
            "latency_delta_ms": latency_delta_ms,
            "fee_delta": fee_delta,
            "slippage_delta": slippage_delta,
            "markout_delta": markout_delta,
            "order_state_mismatches": order_state_mismatches,
            "discrepancies": discrepancies,
            "discrepancy_reasons": discrepancy_reasons,
            "fail_closed_reasons": (
                ["hbt5_discrepancy_comparison_failed"] if discrepancy_reasons else []
            ),
            "certification_feedback_status": certification_feedback_status,
            "parameter_tuning_allowed": False,
            "hidden_parameter_mutations": [],
            "parameter_mutation_status": "not_allowed_observation_artifact_is_measurement_only",
        },
        ["hbt5_discrepancy_comparison_failed"] if discrepancy_reasons else [],
    )


def _screening_hash(screening_artifact: Mapping[str, Any]) -> str:
    value = screening_artifact.get("screening_artifact_hash")
    return str(value) if value else ""


def _validate_screening_artifact_hash(screening_artifact: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    for field in SCREENING_ARTIFACT_REQUIRED_FIELDS:
        if field not in screening_artifact or screening_artifact[field] in ("", None):
            reasons.append(f"missing_screening_artifact_field:{field}")
    if screening_artifact.get("screening_backend") != "vectorbt":
        reasons.append("screening_artifact_backend_not_vectorbt")
    scope = str(screening_artifact.get("screening_scope", "")).strip().lower()
    rust_required = (
        screening_artifact.get("rust_engine_required_for_scope") is True
        or scope in RUST_REQUIRED_SCREENING_SCOPES
    )
    if (
        rust_required
        and screening_artifact.get("vectorbt_engine") != "rust"
    ):
        reasons.append("screening_artifact_required_rust_engine_missing")
    if (
        rust_required
        and screening_artifact.get("rust_engine_available") is not True
    ):
        reasons.append("screening_artifact_required_rust_engine_unavailable")
    if screening_artifact.get("vectorbt_engine") != "rust":
        reasons.append("screening_artifact_hbt_pass_requires_rust_vectorbt")
    if screening_artifact.get("rust_engine_available") is not True:
        reasons.append("screening_artifact_hbt_pass_requires_rust_engine_available")
    for list_field in ("candidate_ids", "promoted_ids", "rejected_ids"):
        if list_field in screening_artifact and not isinstance(screening_artifact[list_field], list):
            reasons.append(f"screening_artifact_field_not_list:{list_field}")
    for mapping_field in ("candidate_reasons", "promoted_reasons", "rejected_reasons"):
        if mapping_field in screening_artifact and not isinstance(screening_artifact[mapping_field], Mapping):
            reasons.append(f"screening_artifact_field_not_mapping:{mapping_field}")

    expected = compute_screening_artifact_hash(screening_artifact)
    observed = screening_artifact.get("screening_artifact_hash")
    if not observed:
        reasons.append("screening_artifact_hash_missing")
        return reasons
    if observed and observed != expected:
        reasons.append("screening_artifact_hash_mismatch")
    return reasons


def _status_text(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _replay_ineligible_reason(reason: str) -> str:
    return f"screening_artifact_replay_ineligible:{reason}"


def _is_missing_candidate_field(value: Any) -> bool:
    return value is None or value == ""


def _is_not_run_evidence(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, str):
        return _status_text(value).startswith("not_run")
    if isinstance(value, Mapping):
        status = _status_text(value.get("status"))
        return value.get("not_run") is True or status.startswith("not_run")
    return False


def _numeric_mapping_value(payload: Mapping[str, Any], fields: tuple[str, ...]) -> float | None:
    for field in fields:
        value = payload.get(field)
        if _is_number(value):
            return float(value)
    return None


def validate_candidate_replay_eligibility(candidate: Mapping[str, Any]) -> list[str]:
    """Validate VectorBT-to-HftBacktest handoff evidence for one promoted candidate."""

    reasons: list[str] = []
    for field in REPLAY_ELIGIBILITY_REQUIRED_FIELDS:
        if field == "rejection_reason_or_null":
            if field not in candidate:
                reasons.append(_replay_ineligible_reason(f"missing_field:{field}"))
            continue
        if field not in candidate or _is_missing_candidate_field(candidate.get(field)):
            reasons.append(_replay_ineligible_reason(f"missing_field:{field}"))

    if "research_clock" in candidate and not _is_missing_candidate_field(candidate.get("research_clock")):
        for clock_error in research_clock_validation_errors(
            candidate["research_clock"],
            context="candidate.research_clock",
        ):
            reasons.append(_replay_ineligible_reason(clock_error))

    for field in REPLAY_ELIGIBILITY_REQUIRED_MAPPING_FIELDS:
        value = candidate.get(field)
        if field in candidate and (not isinstance(value, Mapping) or not value):
            reasons.append(_replay_ineligible_reason(f"malformed_or_empty_mapping:{field}"))

    walk_forward_metrics = candidate.get("walk_forward_metrics")
    if isinstance(walk_forward_metrics, Mapping):
        for field in WALK_FORWARD_REQUIRED_EVIDENCE_FIELDS:
            if field not in walk_forward_metrics or _is_missing_candidate_field(walk_forward_metrics.get(field)):
                reasons.append(_replay_ineligible_reason(f"walk_forward_metrics_missing:{field}"))

    wfc_metrics = candidate.get("wfc_metrics")
    if isinstance(wfc_metrics, Mapping):
        for field in WFC_REQUIRED_EVIDENCE_FIELDS:
            if field not in wfc_metrics:
                reasons.append(_replay_ineligible_reason(f"wfc_metrics_missing:{field}"))
            elif field != "rejection_reason" and _is_missing_candidate_field(wfc_metrics.get(field)):
                reasons.append(_replay_ineligible_reason(f"wfc_metrics_missing:{field}"))

    for field in REPLAY_ELIGIBILITY_PASS_STATUS_FIELDS:
        if field in candidate and _status_text(candidate.get(field)) != "pass":
            reasons.append(_replay_ineligible_reason(f"{field}_not_pass"))

    if "screening_status" in candidate and _status_text(candidate.get("screening_status")) != "pass":
        reasons.append(_replay_ineligible_reason("screening_status_not_pass"))
    if "replay_eligibility_status" in candidate and _status_text(candidate.get("replay_eligibility_status")) != "eligible":
        reasons.append(_replay_ineligible_reason("replay_eligibility_status_not_eligible"))
    if "robustness_artifact_staleness" in candidate and _status_text(candidate.get("robustness_artifact_staleness")) != "fresh":
        reasons.append(_replay_ineligible_reason("robustness_artifact_stale_or_invalid"))
    if candidate.get("rejection_reason_or_null") not in (None, "", "null"):
        reasons.append(_replay_ineligible_reason("candidate_has_rejection_reason"))

    for field in REPLAY_ELIGIBILITY_NOT_RUN_EVIDENCE_FIELDS:
        value = candidate.get(field)
        if field in candidate and _is_not_run_evidence(value):
            reasons.append(_replay_ineligible_reason(f"{field}_not_run"))
        # Per Codex round-3 P2: also reject §10 maps with status "fail".
        if isinstance(value, Mapping):
            map_status = _status_text(value.get("status"))
            if map_status == "fail":
                reasons.append(_replay_ineligible_reason(f"{field}_status_fail"))

    dsr_evidence = candidate.get("dsr_or_not_run")
    if "dsr_or_not_run" in candidate and not _is_not_run_evidence(dsr_evidence):
        if not isinstance(dsr_evidence, Mapping):
            reasons.append(_replay_ineligible_reason("dsr_evidence_malformed"))
        elif "dsr_pass" not in dsr_evidence:
            reasons.append(_replay_ineligible_reason("dsr_evidence_missing:dsr_pass"))
        elif dsr_evidence.get("dsr_pass") is not True:
            reasons.append(_replay_ineligible_reason("dsr_evidence_not_pass"))
    if isinstance(dsr_evidence, Mapping):
        dsr_cdf = _numeric_mapping_value(dsr_evidence, ("dsr_cdf", "deflated_sharpe_ratio_cdf"))
        if dsr_cdf is None:
            reasons.append(_replay_ineligible_reason("dsr_evidence_missing:dsr_cdf"))
        elif dsr_cdf < 0.95:
            reasons.append(_replay_ineligible_reason("dsr_cdf_below_0_95"))
        if "status" in dsr_evidence and _status_text(dsr_evidence.get("status")) != "pass":
            reasons.append(_replay_ineligible_reason("dsr_evidence_status_not_pass"))

    pbo_evidence = candidate.get("pbo_or_not_run")
    if "pbo_or_not_run" in candidate and not _is_not_run_evidence(pbo_evidence):
        if not isinstance(pbo_evidence, Mapping):
            reasons.append(_replay_ineligible_reason("pbo_evidence_malformed"))
        elif "pbo_pass" not in pbo_evidence:
            reasons.append(_replay_ineligible_reason("pbo_evidence_missing:pbo_pass"))
        elif pbo_evidence.get("pbo_pass") is not True:
            reasons.append(_replay_ineligible_reason("pbo_evidence_not_pass"))
    if isinstance(pbo_evidence, Mapping):
        pbo_value = _numeric_mapping_value(
            pbo_evidence,
            ("pbo", "probability_of_backtest_overfitting", "pbo_probability"),
        )
        maximum_pbo = _numeric_mapping_value(
            pbo_evidence,
            ("maximum_pbo", "pbo_threshold", "threshold"),
        )
        if pbo_value is None:
            reasons.append(_replay_ineligible_reason("pbo_evidence_missing:pbo"))
        elif not math.isfinite(pbo_value):
            reasons.append(_replay_ineligible_reason("pbo_evidence_not_finite"))
        if maximum_pbo is None:
            reasons.append(_replay_ineligible_reason("pbo_evidence_missing:maximum_pbo"))
        elif not math.isfinite(maximum_pbo):
            reasons.append(_replay_ineligible_reason("pbo_threshold_not_finite"))
        if pbo_value is not None and maximum_pbo is not None and pbo_value > maximum_pbo:
            reasons.append(_replay_ineligible_reason("pbo_evidence_above_threshold"))
        if "status" in pbo_evidence and _status_text(pbo_evidence.get("status")) != "pass":
            reasons.append(_replay_ineligible_reason("pbo_evidence_status_not_pass"))

    cscv_evidence = candidate.get("cscv_count_or_not_run")
    if "cscv_count_or_not_run" in candidate and not _is_not_run_evidence(cscv_evidence):
        if isinstance(cscv_evidence, Mapping):
            partitions = _numeric_mapping_value(cscv_evidence, ("n_partitions", "partitions", "partition_count"))
            configs = _numeric_mapping_value(cscv_evidence, ("n_configs", "configs", "config_count"))
            if partitions is None or partitions <= 0:
                reasons.append(_replay_ineligible_reason("cscv_partition_count_missing_or_not_positive"))
            if configs is not None and configs < 2:
                reasons.append(_replay_ineligible_reason("cscv_config_count_below_2"))
            if "status" in cscv_evidence and _status_text(cscv_evidence.get("status")) != "pass":
                reasons.append(_replay_ineligible_reason("cscv_evidence_status_not_pass"))
        elif _is_number(cscv_evidence):
            if float(cscv_evidence) <= 0:
                reasons.append(_replay_ineligible_reason("cscv_count_not_positive"))
        else:
            reasons.append(_replay_ineligible_reason("cscv_count_malformed"))

    return list(dict.fromkeys(reasons))


def _candidate_from_screening(
    screening_artifact: Mapping[str, Any],
    candidate_id: str | None,
) -> tuple[str, dict[str, Any], list[str]]:
    promoted_ids = [str(value) for value in screening_artifact.get("promoted_ids") or []]
    selected_id = candidate_id or (promoted_ids[0] if promoted_ids else "")
    reasons: list[str] = []
    if not promoted_ids:
        reasons.append("screening_artifact_has_no_promoted_candidate")
    if not selected_id:
        reasons.append("screening_candidate_id_missing")
    elif selected_id not in promoted_ids:
        reasons.append("candidate_id_not_promoted_by_screening_artifact")

    promoted_rows = screening_artifact.get("promoted") or []
    for row in promoted_rows:
        if isinstance(row, Mapping) and str(row.get("candidate_id")) == selected_id:
            selected_candidate = dict(row)
            reasons.extend(validate_candidate_replay_eligibility(selected_candidate))
            return selected_id, selected_candidate, reasons
    if selected_id:
        reasons.append("candidate_metadata_missing_from_screening_artifact")
    return selected_id, {}, reasons


def write_hftbacktest_realism_artifacts(
    *,
    repo_root: Path,
    out_dir: Path,
    screening_artifact_path: Path | None = None,
    data_npz_path: Path | None = None,
    latency_model_path: Path | None = None,
    fill_queue_model_path: Path | None = None,
    observation_artifact_path: Path | None = None,
    candidate_id: str | None = None,
    upstream_ref: str | None = None,
    native_hot_path_evidence: list[str] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Write source lock, data validation, input manifest, and fail-closed summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    latency_model, latency_reasons = _load_latency_model_artifact(latency_model_path)
    latency_model_path_out = out_dir / "latency_model.json"
    latency_model_path_out.write_text(json.dumps(latency_model, indent=2) + "\n", encoding="utf-8")
    fill_queue_model, fill_queue_reasons = _load_fill_queue_model_artifact(fill_queue_model_path)
    fill_queue_model_path_out = out_dir / "fill_queue_model.json"
    fill_queue_model_path_out.write_text(
        json.dumps(fill_queue_model, indent=2) + "\n",
        encoding="utf-8",
    )
    lock = build_hftbacktest_source_lock(
        repo_root=repo_root,
        upstream_ref=upstream_ref,
        native_hot_path_evidence=native_hot_path_evidence,
    )
    lock_path = out_dir / "hftbacktest_source_lock.json"
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    screening_artifact: dict[str, Any] = {}
    screening_reasons: list[str] = []
    if screening_artifact_path is None:
        screening_reasons.append("screening_artifact_missing")
    else:
        try:
            screening_artifact = _load_json(screening_artifact_path)
            screening_reasons.extend(_validate_screening_artifact_hash(screening_artifact))
        except Exception as exc:
            screening_reasons.append(f"screening_artifact_read_failed:{type(exc).__name__}")
    selected_id, selected_candidate, candidate_reasons = _candidate_from_screening(
        screening_artifact,
        candidate_id,
    )
    screening_reasons.extend(candidate_reasons)

    data_validation = (
        validate_hftbacktest_data_path(data_npz_path)
        if data_npz_path is not None
        else _not_run_data_validation(data_npz_path)
    )
    (out_dir / "data_validation.json").write_text(
        json.dumps(data_validation, indent=2) + "\n",
        encoding="utf-8",
    )

    input_manifest = {
        "run_id": run_id or out_dir.name,
        "screening_artifact_path": str(screening_artifact_path) if screening_artifact_path else None,
        "data_npz_path": str(data_npz_path) if data_npz_path else None,
        "latency_model_path": str(latency_model_path) if latency_model_path else None,
        "fill_queue_model_path": str(fill_queue_model_path) if fill_queue_model_path else None,
        "observation_artifact_path": str(observation_artifact_path) if observation_artifact_path else None,
        "screening_artifact_hash": _screening_hash(screening_artifact) if screening_artifact else "",
        "candidate_id": selected_id,
        "candidate_metadata": selected_candidate,
        "source_lock_path": str(lock_path),
        "source_lock_hash": lock["source_lock_hash"],
    }
    (out_dir / "input_manifest.json").write_text(
        json.dumps(input_manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    official_replay, official_replay_reasons = run_minimal_official_hftbacktest_replay(
        data_npz_path=data_npz_path,
        selected_id=selected_id,
        selected_candidate=selected_candidate,
        fill_queue_model=fill_queue_model,
        latency_model=latency_model,
    )
    discrepancy_comparison, discrepancy_reasons = build_discrepancy_comparison_artifact(
        observation_artifact_path=observation_artifact_path,
        official_replay=official_replay,
        latency_model=latency_model,
        fill_queue_model=fill_queue_model,
        selected_candidate=selected_candidate,
    )
    replay_discrepancies = list(official_replay.get("discrepancies") or [])
    replay_discrepancies.extend(
        {"source": "hbt5_discrepancy_comparison", "reason": reason}
        for reason in discrepancy_comparison.get("discrepancy_reasons") or []
    )
    official_replay["discrepancies"] = replay_discrepancies
    official_replay["official_replay_artifact_hash"] = _sha256_payload(official_replay)
    official_replay_path = out_dir / "official_replay.json"
    official_replay_path.write_text(
        json.dumps(official_replay, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_jsonl(out_dir / "orders.jsonl", list(official_replay.get("orders") or []))
    _write_jsonl(out_dir / "fills.jsonl", list(official_replay.get("fills") or []))
    _write_jsonl(out_dir / "markouts.jsonl", list(official_replay.get("markouts") or []))
    (out_dir / "discrepancies.json").write_text(
        json.dumps(official_replay.get("discrepancies") or [], indent=2) + "\n",
        encoding="utf-8",
    )
    discrepancy_comparison["discrepancy_comparison_artifact_hash"] = _sha256_payload(discrepancy_comparison)
    discrepancy_comparison_path = out_dir / "discrepancy_comparison.json"
    discrepancy_comparison_path.write_text(
        json.dumps(discrepancy_comparison, indent=2) + "\n",
        encoding="utf-8",
    )

    fail_reasons = (
        validate_hftbacktest_source_lock(lock)
        + screening_reasons
        + list(data_validation.get("fail_closed_reasons") or [])
        + latency_reasons
        + fill_queue_reasons
        + official_replay_reasons
        + discrepancy_reasons
    )
    if data_npz_path is None:
        fail_reasons.append("hbt0_source_lock_only_replay_not_run")
    if (
        official_replay.get("official_hftbacktest_replay_status") != "pass"
        and "official_replay_not_run" not in fail_reasons
    ):
        fail_reasons.append("official_replay_not_run")
    fail_reasons = list(dict.fromkeys(fail_reasons))
    status = (
        "pass"
        if official_replay.get("official_hftbacktest_replay_status") == "pass" and not fail_reasons
        else _replay_status_from_fail_reasons(fail_reasons, data_validation)
    )
    accelerated_mode = official_replay.get("accelerated_mode", False)
    official_replay_passed = official_replay.get("official_hftbacktest_replay_status") == "pass"
    certification_allowed = official_replay_passed and accelerated_mode is False and not fail_reasons
    full_replay_hash_or_not_run = (
        official_replay["official_replay_artifact_hash"] if official_replay_passed else "not_run"
    )
    summary = {
        "run_id": input_manifest["run_id"],
        "created_at_utc": _utc_now(),
        "hft3_commit": lock["hft3_commit"],
        "screening_artifact_hash": input_manifest["screening_artifact_hash"],
        "candidate_id": selected_id,
        "model_id": selected_candidate.get("hypothesis_id") or selected_candidate.get("model_id") or "",
        "symbol": selected_candidate.get("symbol") or "",
        "research_clock": (
            selected_candidate.get("research_clock")
            or screening_artifact.get("research_clock")
            or ""
        ),
        "event_or_session_scope": screening_artifact.get("event_id") or "not_run_hbt0",
        "hftbacktest_source_lock_hash": lock["source_lock_hash"],
        "data_validation_status": data_validation["data_validation_status"],
        "latency_model_family": latency_model.get("latency_model_family", "not_run"),
        "exchange_model": fill_queue_model.get("exchange_model", "not_run"),
        "queue_model": fill_queue_model.get("queue_model", "not_run"),
        "queue_model_source": fill_queue_model.get("queue_model_source", "not_run"),
        "fill_model_scope": fill_queue_model.get("fill_model_scope", "not_run"),
        "partial_fill_policy": fill_queue_model.get("partial_fill_policy", "not_run"),
        "time_in_force_policy": fill_queue_model.get("time_in_force_policy", "not_run"),
        "accelerated_mode": accelerated_mode,
        "accuracy_tradeoff_declared": bool(official_replay.get("accuracy_tradeoff_declared", False)),
        "queue_position_modeled": bool(
            official_replay_passed and fill_queue_model.get("queue_model", "not_run") != "not_run"
        ),
        "order_response_latency_modeled": bool(
            official_replay_passed
            and latency_model.get("order_response_latency_source") not in (None, "", "not_run")
        ),
        "full_replay_comparison_hash_or_not_run": full_replay_hash_or_not_run,
        "certification_allowed": certification_allowed,
        "market_impact_mode": fill_queue_model.get("market_impact_mode", "not_run"),
        "orders_intended": _summary_metric(official_replay, fill_queue_model, "orders_intended", 0),
        "orders_submitted": _summary_metric(official_replay, fill_queue_model, "orders_submitted", 0),
        "orders_acknowledged": _summary_metric(official_replay, fill_queue_model, "orders_acknowledged", 0),
        "orders_cancelled": _summary_metric(official_replay, fill_queue_model, "orders_cancelled", 0),
        "fills_count": _summary_metric(official_replay, fill_queue_model, "fills_count", 0),
        "partial_fills_count": _summary_metric(official_replay, fill_queue_model, "partial_fills_count", 0),
        "unfilled_count": _summary_metric(official_replay, fill_queue_model, "unfilled_count", 0),
        "fill_rate": _summary_metric(official_replay, fill_queue_model, "fill_rate", 0.0),
        "avg_queue_position_or_not_available": fill_queue_model.get(
            "avg_queue_position_or_not_available",
            "not_available_hbt0",
        ),
        "latency_p50_ms": latency_model.get("latency_p50_ms"),
        "latency_p90_ms": latency_model.get("latency_p90_ms"),
        "latency_p99_ms": latency_model.get("latency_p99_ms"),
        "tick_size": fill_queue_model.get("tick_size"),
        "lot_size": fill_queue_model.get("lot_size"),
        "minimum_order_qty": fill_queue_model.get("minimum_order_qty"),
        "maker_fees": fill_queue_model.get("maker_fee"),
        "taker_fees": fill_queue_model.get("taker_fee"),
        "gross_pnl": official_replay.get("gross_pnl"),
        "net_pnl": official_replay.get("net_pnl"),
        "execution_adjusted_expectancy": official_replay.get("execution_adjusted_expectancy"),
        "max_drawdown": None,
        "adverse_selection_markout": official_replay.get("adverse_selection_markout"),
        "spread_capture_or_cost": official_replay.get("spread_capture_or_cost"),
        "official_hftbacktest_replay_status": official_replay.get("official_hftbacktest_replay_status", "not_run"),
        "official_replay_artifact_hash": official_replay["official_replay_artifact_hash"],
        "discrepancy_comparison_status": discrepancy_comparison["comparison_status"],
        "discrepancy_comparison_artifact_hash": discrepancy_comparison["discrepancy_comparison_artifact_hash"],
        "certification_feedback_status": discrepancy_comparison["certification_feedback_status"],
        "replay_realism_status": status,
        "fail_closed_reasons": fail_reasons,
    }
    summary["replay_summary_hash"] = compute_replay_summary_hash(summary)
    summary_reasons = validate_replay_summary(summary, source_lock=lock)
    if summary_reasons:
        summary["fail_closed_reasons"] = list(dict.fromkeys(fail_reasons + summary_reasons))
        summary["replay_realism_status"] = _replay_status_from_fail_reasons(
            list(summary["fail_closed_reasons"]),
            data_validation,
        )
        summary["certification_allowed"] = False
        summary["replay_summary_hash"] = compute_replay_summary_hash(summary)
    summary_path = out_dir / "replay_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return {
        "source_lock": lock,
        "latency_model": latency_model,
        "fill_queue_model": fill_queue_model,
        "official_replay": official_replay,
        "input_manifest": input_manifest,
        "replay_summary": summary,
        "source_lock_path": str(lock_path),
        "latency_model_path": str(latency_model_path_out),
        "fill_queue_model_path": str(fill_queue_model_path_out),
        "official_replay_path": str(official_replay_path),
        "orders_path": str(out_dir / "orders.jsonl"),
        "fills_path": str(out_dir / "fills.jsonl"),
        "markouts_path": str(out_dir / "markouts.jsonl"),
        "discrepancies_path": str(out_dir / "discrepancies.json"),
        "discrepancy_comparison_path": str(discrepancy_comparison_path),
        "replay_summary_path": str(summary_path),
    }


def write_hbt0_artifacts(**kwargs: Any) -> dict[str, Any]:
    """Backward-compatible name for the source-lock/data-validation gate writer."""
    return write_hftbacktest_realism_artifacts(**kwargs)
