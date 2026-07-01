"""HftBacktest-only active pipeline contract.

This module is intentionally independent of the older handoff and realism
layers. The active path here starts with validated HftBacktest event data.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np


PLAN_PATH = "docs/project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md"
UPSTREAM_REPO_URL = "https://github.com/nkaz001/hftbacktest"
UPSTREAM_DOCS_URL = "https://hftbacktest.readthedocs.io/en/latest/"
EXPECTED_EVENT_FIELDS = ("ev", "exch_ts", "local_ts", "px", "qty", "order_id", "ival", "fval")
_FUTURE_DATA_GRACE_NS = 86_400 * 1_000_000_000
_NS_PER_SECOND = 1_000_000_000
_TRADE_EVENT = 2
_ADD_ORDER_EVENT = 10
_CANCEL_ORDER_EVENT = 11
_MODIFY_ORDER_EVENT = 12
_FILL_EVENT = 13
_STRUCTURAL_PAYLOAD_ATTR_BY_CLASS = {
    "BookPressureModel": "book_pressure",
    "CrossAssetLeadLagModel": "cross_asset",
    "VPINToxicityModel": "vpin",
    "HybridExecutionModel": "hybrid",
    "DealerHedgingModel": "dealer",
    "DowYMIndexModel": "dow_ym",
    "TreasuryCTDModel": "treasury",
    "TransferEntropyModel": "transfer_entropy",
    "QuantumSpreadDefenseModel": "quantum_spread",
    "StochasticThermoModel": "thermo",
    "HawkesToxicFlowModel": "hawkes",
}
_STRUCTURAL_SIGNAL_FIELDS_BY_CLASS = {
    "BookPressureModel": ("OFI_zscore", "OFI_smooth", "book_pressure_direction"),
    "CrossAssetLeadLagModel": ("predicted_target_return", "cross_impact_score"),
    "VPINToxicityModel": (),
    "HybridExecutionModel": ("OFI_drift_component",),
    "DealerHedgingModel": ("dealer_hedging_pressure",),
    "DowYMIndexModel": ("constituent_to_YM_signal", "YM_fair_pressure", "synthetic_Dow_pressure"),
    "TreasuryCTDModel": ("futures_basis_signal",),
    "TransferEntropyModel": (),
    "QuantumSpreadDefenseModel": (),
    "StochasticThermoModel": (),
    "HawkesToxicFlowModel": ("reservation_price_skew",),
}
HYPOTHESIS_LIMIT_ORDER_SURFACE_VERSION = "hypothesis_limit_order_event_scan_v2"


class HftBacktestOnlyPipelineError(ValueError):
    """Raised when the active HftBacktest-only contract cannot be satisfied."""


@dataclass(frozen=True)
class HftBacktestOnlyRunConfig:
    run_id: str
    symbol: str
    contract: str
    event_id: str
    normalized_npz: Path
    initial_snapshot: Path
    strategy_id: str
    strategy_params: Mapping[str, Any] = field(default_factory=dict)
    canonical_model_id: str = ""
    legacy_aliases: tuple[str, ...] = field(default_factory=tuple)
    authority_refs: tuple[str, ...] = field(default_factory=tuple)
    tick_size: float = 0.25
    lot_size: float = 1.0
    contract_size: float = 1.0
    maker_fee: float = 0.0
    taker_fee: float = 0.0
    entry_latency_ns: int = 100_000
    response_latency_ns: int = 100_000
    exchange_fill_model: str = "NoPartialFillExchange"
    queue_model: str = "L3FIFOQueueModel"
    fee_model: str = "trading_qty_fee_model"
    latency_model: str = "constant_order_latency"
    roi_lower_bound: float | None = None
    roi_upper_bound: float | None = None
    event_window: Mapping[str, Any] = field(default_factory=dict)

    def run_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "hft3_hftbacktest_only_run_manifest_v1",
            "plan": PLAN_PATH,
            "upstream_repo_url": UPSTREAM_REPO_URL,
            "upstream_docs_url": UPSTREAM_DOCS_URL,
            "run_id": self.run_id,
            "created_at_utc": _utc_now(),
            "hft3_commit": _git_commit(_repo_root_default()),
            "symbol": self.symbol,
            "contract": self.contract,
            "event_id": self.event_id,
            "event_window": dict(self.event_window),
            "normalized_npz": str(self.normalized_npz),
            "initial_snapshot": str(self.initial_snapshot),
            "strategy_id": self.strategy_id,
            "strategy_surface_version": _strategy_surface_version(self.strategy_id),
            "strategy_params": dict(self.strategy_params),
            "canonical_model_id": self.canonical_model_id,
            "legacy_aliases": list(self.legacy_aliases),
            "authority_refs": list(self.authority_refs),
            "tick_size": self.tick_size,
            "lot_size": self.lot_size,
            "contract_size": self.contract_size,
            "fee_model": self.fee_model,
            "maker_fee": self.maker_fee,
            "taker_fee": self.taker_fee,
            "latency_model": self.latency_model,
            "entry_latency_ns": self.entry_latency_ns,
            "response_latency_ns": self.response_latency_ns,
            "exchange_fill_model": self.exchange_fill_model,
            "queue_model": self.queue_model,
            "roi_lower_bound": self.roi_lower_bound,
            "roi_upper_bound": self.roi_upper_bound,
            "active_path": "hftbacktest_only",
            "legacy_screening_dependency": "forbidden_active_path",
        }


@dataclass(frozen=True)
class HftBacktestOnlyPrepareConfig:
    source_npz: Path
    symbol: str
    contract: str
    event_id: str
    trade_date: str
    out_root: Path
    warmup_seconds: int = 30
    output_stem: str | None = None
    source: str = "databento_cme_mbo"
    tick_size: float = 0.25
    lot_size: float = 1.0
    contract_size: float = 1.0
    replay_mode: str = "full_l3_event_replay"
    force_rebuild: bool = False


def prepare_hftbacktest_only_l3_from_lake(config: HftBacktestOnlyPrepareConfig) -> dict[str, Any]:
    """Prepare an existing lake L3 NPZ for the active HftBacktest-only runner."""
    source_npz = Path(config.source_npz)
    if not source_npz.is_file():
        raise HftBacktestOnlyPipelineError(f"source_npz_missing:{source_npz}")
    if config.warmup_seconds <= 0:
        raise HftBacktestOnlyPipelineError("warmup_seconds_must_be_positive")
    if config.replay_mode not in {"full_l3_event_replay", "added_orders_only"}:
        raise HftBacktestOnlyPipelineError(f"unknown_prepare_replay_mode:{config.replay_mode}")

    stem = config.output_stem or f"{_safe_stem(config.event_id)}_warmup_{config.warmup_seconds}s_{config.replay_mode}"
    out_dir = Path(config.out_root) / "prepared" / _safe_stem(config.symbol) / config.trade_date
    normalized_npz = out_dir / f"{stem}_l3.npz"
    initial_snapshot = out_dir / f"{stem}_initial_snapshot.npz"
    manifest_path = out_dir / f"{stem}_manifest.json"
    source_hash = _sha256_file(source_npz)

    if (
        not config.force_rebuild
        and normalized_npz.is_file()
        and initial_snapshot.is_file()
        and manifest_path.is_file()
    ):
        manifest = _load_json(manifest_path)
        if (
            manifest.get("source_npz_sha256") == source_hash
            and int(manifest.get("warmup_seconds", -1)) == int(config.warmup_seconds)
            and manifest.get("replay_mode") == config.replay_mode
            and manifest.get("normalized_npz") == str(normalized_npz)
            and manifest.get("initial_snapshot") == str(initial_snapshot)
        ):
            return {**manifest, "manifest_path": str(manifest_path), "reused_existing": True}

    with np.load(source_npz, allow_pickle=False) as payload:
        if "data" not in payload.files:
            raise HftBacktestOnlyPipelineError("source_npz_missing_data_array")
        events = payload["data"]

    _require_event_fields(events)
    if len(events) <= 0:
        raise HftBacktestOnlyPipelineError("source_event_array_empty")

    start_ts_ns = int(events["exch_ts"].min())
    cutoff_ts_ns = start_ts_ns + int(config.warmup_seconds) * _NS_PER_SECOND
    end_ts_ns = int(events["exch_ts"].max())
    warmup = events[events["exch_ts"] < cutoff_ts_ns]
    replay = events[events["exch_ts"] >= cutoff_ts_ns]

    snapshot = _snapshot_from_warmup(warmup, cutoff_ts_ns)
    if config.replay_mode == "added_orders_only":
        prepared_replay, dropped = _filter_replay_added_orders_only(replay)
        status = "research_smoke_derived_subset"
        caveat = (
            "Derived smoke subset: snapshot supplies depth; replay keeps only order IDs "
            "added after cutoff. Not full-market replay or promotion evidence."
        )
    else:
        prepared_replay = replay.copy()
        dropped = {
            "preexisting_or_unknown_lifecycle": 0,
            "duplicate_add": 0,
            "zero_order_id": 0,
        }
        status = "hbt_full_l3_event_replay_prepared"
        caveat = (
            "Full L3 replay rows are preserved after the warmup cutoff; the initial "
            "snapshot is derived from warmup rows before cutoff."
        )

    _save_npz_atomic(normalized_npz, data=prepared_replay, timestamp_units="nanoseconds")
    _save_npz_atomic(initial_snapshot, data=snapshot, timestamp_units="nanoseconds")

    manifest = {
        "schema_version": "hft3_hbt_only_lake_prepare_v1",
        "status": status,
        "plan": PLAN_PATH,
        "source": config.source,
        "source_npz": str(source_npz),
        "source_npz_sha256": source_hash,
        "source_rows": int(len(events)),
        "warmup_seconds": int(config.warmup_seconds),
        "replay_mode": config.replay_mode,
        "start_ts_ns": start_ts_ns,
        "cutoff_ts_ns": cutoff_ts_ns,
        "end_ts_ns": end_ts_ns,
        "warmup_rows": int(len(warmup)),
        "replay_source_rows": int(len(replay)),
        "snapshot_rows": int(len(snapshot)),
        "replay_rows": int(len(prepared_replay)),
        "dropped_rows": dropped,
        "symbol": config.symbol,
        "contract": config.contract,
        "event_id": config.event_id,
        "trade_date": config.trade_date,
        "normalized_npz": str(normalized_npz),
        "normalized_npz_sha256": _sha256_file(normalized_npz),
        "initial_snapshot": str(initial_snapshot),
        "initial_snapshot_sha256": _sha256_file(initial_snapshot),
        "tick_size": config.tick_size,
        "lot_size": config.lot_size,
        "contract_size": config.contract_size,
        "feed_type": "L3_MBO",
        "timezone": "UTC",
        "validation_status": "pending_hftbacktest_only_validation",
        "timestamp_units": "nanoseconds",
        "caveat": caveat,
    }
    _write_json(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path), "reused_existing": False}


def validate_hftbacktest_only_input(config: HftBacktestOnlyRunConfig) -> dict[str, Any]:
    """Validate active HftBacktest data before any strategy or economics step."""
    reasons: list[str] = []
    data_path = Path(config.normalized_npz)
    snapshot_path = Path(config.initial_snapshot)
    if not data_path.is_file():
        reasons.append("DATA_NPZ_MISSING")
    if not snapshot_path.is_file():
        reasons.append("INITIAL_SNAPSHOT_MISSING")
    else:
        reasons.extend(_snapshot_read_reasons(snapshot_path))

    events: Any = None
    timestamp_units = "unproven"
    dtype_fields: list[str] = []
    dtype_exact_match = False
    row_count = 0
    min_feed_latency_ns: int | None = None
    official_validate_event_order_status = "not_run"
    l2_l3_classification = "unknown"
    event_type_counts: dict[str, int] = {}

    if data_path.is_file():
        try:
            with np.load(data_path, allow_pickle=False) as payload:
                if "data" not in payload.files:
                    reasons.append("DATA_NPZ_MISSING_DATA_ARRAY")
                else:
                    events = payload["data"]
                    row_count = int(len(events))
                    timestamp_units = _timestamp_units_value(
                        payload["timestamp_units"] if "timestamp_units" in payload.files else None
                    )
        except Exception as exc:
            reasons.append(f"DATA_NPZ_READ_FAILED:{type(exc).__name__}")

    if events is not None:
        dtype_fields = list(events.dtype.names or [])
        missing = [field for field in EXPECTED_EVENT_FIELDS if field not in dtype_fields]
        if missing:
            reasons.append("EVENT_DTYPE_INVALID")
        expected_dtype = _expected_event_dtype()
        if expected_dtype is None:
            reasons.append("HFTBACKTEST_EVENT_DTYPE_UNAVAILABLE")
        else:
            dtype_exact_match = bool(events.dtype == expected_dtype)
            if not dtype_exact_match:
                reasons.append("EVENT_DTYPE_INVALID")
        if row_count <= 0:
            reasons.append("EVENT_ARRAY_EMPTY")
        if timestamp_units == "unproven" and _looks_like_epoch_ns(events["exch_ts"]) and _looks_like_epoch_ns(events["local_ts"]):
            timestamp_units = "nanoseconds"
        if timestamp_units != "nanoseconds":
            reasons.append("TIMESTAMP_UNITS_UNPROVEN")

        if not missing:
            local_minus_exch = events["local_ts"].astype("int64") - events["exch_ts"].astype("int64")
            if row_count:
                min_feed_latency_ns = int(local_minus_exch.min())
            if row_count and bool((local_minus_exch < 0).any()):
                reasons.append("NEGATIVE_FEED_LATENCY_UNCORRECTED")
            if (
                row_count
                and _looks_like_epoch_ns(events["exch_ts"])
                and int(events["exch_ts"].max()) > _future_data_cutoff_ns()
            ):
                reasons.append("FUTURE_DATA_AFTER_VALIDATION_CLOCK")

            constants = _event_constants()
            if constants:
                ev = events["ev"].astype("uint64")
                exch_mask = (ev & constants["EXCH_EVENT"]) == constants["EXCH_EVENT"]
                local_mask = (ev & constants["LOCAL_EVENT"]) == constants["LOCAL_EVENT"]
                if bool(exch_mask.any()) and bool((events["exch_ts"][exch_mask][1:] < events["exch_ts"][exch_mask][:-1]).any()):
                    reasons.append("EXCHANGE_ORDER_INVALID")
                if bool(local_mask.any()) and bool((events["local_ts"][local_mask][1:] < events["local_ts"][local_mask][:-1]).any()):
                    reasons.append("LOCAL_ORDER_INVALID")
                l2_l3_classification = _classify_events(events, constants)
                event_type_counts = _event_type_counts(events)
                if l2_l3_classification == "mixed_rejected":
                    reasons.append("L2_L3_MISMATCH")
                elif l2_l3_classification == "unknown_rejected":
                    reasons.append("EVENT_TYPE_UNKNOWN")
                elif l2_l3_classification == "l3_mbo":
                    add_mask = ((events["ev"].astype("int64") & 0xFF) == constants["ADD_ORDER_EVENT"])
                    if bool(add_mask.any()) and bool((events["order_id"][add_mask] == 0).any()):
                        reasons.append("L3_ORDER_ID_MISSING")
            else:
                reasons.append("HFTBACKTEST_EVENT_CONSTANTS_UNAVAILABLE")

            try:
                from hftbacktest.data import validate_event_order
            except Exception as exc:
                official_validate_event_order_status = f"unavailable:{type(exc).__name__}"
                reasons.append("HFTBACKTEST_VALIDATE_EVENT_ORDER_UNAVAILABLE")
            else:
                try:
                    validate_event_order(events)
                    official_validate_event_order_status = "pass"
                except Exception as exc:
                    official_validate_event_order_status = f"fail:{type(exc).__name__}:{exc}"
                    reasons.append("HFTBACKTEST_VALIDATE_EVENT_ORDER_FAILED")

    reasons = list(dict.fromkeys(reasons))
    return {
        "schema_version": "hft3_hftbacktest_only_data_validation_v1",
        "plan": PLAN_PATH,
        "data_path": str(data_path),
        "initial_snapshot": str(snapshot_path),
        "symbol": config.symbol,
        "contract": config.contract,
        "event_id": config.event_id,
        "data_validation_status": "pass" if not reasons else "fail",
        "row_count": row_count,
        "expected_dtype_fields": list(EXPECTED_EVENT_FIELDS),
        "dtype_fields": dtype_fields,
        "dtype_exact_match": dtype_exact_match,
        "timestamp_units": timestamp_units,
        "exchange_order_status": "fail" if "EXCHANGE_ORDER_INVALID" in reasons else "pass",
        "local_order_status": "fail" if "LOCAL_ORDER_INVALID" in reasons else "pass",
        "feed_latency_status": "fail" if "NEGATIVE_FEED_LATENCY_UNCORRECTED" in reasons else "pass",
        "min_feed_latency_ns": min_feed_latency_ns,
        "official_validate_event_order_status": official_validate_event_order_status,
        "l2_l3_classification": l2_l3_classification,
        "event_type_counts": event_type_counts,
        "fail_closed_reasons": reasons,
    }


def run_hftbacktest_only(
    config: HftBacktestOnlyRunConfig,
    *,
    out_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the active HftBacktest-only slice and write plan-shaped artifacts."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = config.run_manifest()
    data_validation = validate_hftbacktest_only_input(config)

    _write_json(out_dir / "run_manifest.json", manifest)
    _write_json(out_dir / "data_manifest.json", _data_manifest(config, data_validation))
    _write_json(out_dir / "hbt_config.json", _hbt_config(config))
    _write_json(out_dir / "strategy_config.json", _strategy_config(config))
    _write_json(out_dir / "normalized_input_manifest.json", _normalized_input_manifest(config, data_validation))
    _write_json(out_dir / "data_validation.json", data_validation)
    _write_json(out_dir / "latency_report.json", _latency_report(config))
    _write_json(out_dir / "fill_quality_report.json", {"status": "not_run", "reason": "awaiting_hftbacktest_run"})
    _write_json(out_dir / "queue_diagnostics.json", {"status": "not_run", "reason": "awaiting_hftbacktest_run"})
    _write_json(out_dir / "robustness_report.json", {"status": "not_run", "reason": "minimal_single_run_slice"})

    if data_validation["data_validation_status"] != "pass":
        _write_audit(out_dir, "data_invalid", data_validation["fail_closed_reasons"])
        return _result(config, out_dir, "data_invalid", data_validation["fail_closed_reasons"])

    if dry_run:
        _write_audit(out_dir, "dry_run", ["dry_run_no_hftbacktest_execution"])
        return _result(config, out_dir, "dry_run", ["dry_run_no_hftbacktest_execution"])

    replay, replay_reasons = _run_minimal_strategy(config)
    _write_json(out_dir / "official_replay.json", replay)
    _write_jsonl(out_dir / "orders.jsonl", replay.get("orders", []))
    _write_jsonl(out_dir / "fills.jsonl", replay.get("fills", []))

    if replay_reasons:
        status = _blocked_run_status(replay_reasons)
        _write_audit(out_dir, status, replay_reasons)
        return _result(config, out_dir, status, replay_reasons)

    recorder_path = _write_recorder_result(out_dir / "recorder_result.npz", replay)
    stats = _stats_summary(config, replay, recorder_path)
    _write_json(out_dir / "stats_summary.json", stats)
    _write_optional_parquet(out_dir / "orders.parquet", replay.get("orders", []))
    _write_optional_parquet(out_dir / "fills.parquet", replay.get("fills", []))
    _write_optional_parquet(out_dir / "equity_curve.parquet", replay.get("equity_curve", []))
    _write_optional_parquet(out_dir / "position_timeseries.parquet", replay.get("position_timeseries", []))
    _write_json(out_dir / "fill_quality_report.json", _fill_quality_report(replay))
    _write_json(out_dir / "queue_diagnostics.json", _queue_diagnostics(config, replay))
    decision = write_promotion_decision(out_dir, stats_summary=stats)
    _write_audit(out_dir, "completed", [])
    return {
        **_result(config, out_dir, "completed", []),
        "stats_summary": stats,
        "promotion_decision": decision,
    }


def write_promotion_decision(out_dir: Path, *, stats_summary: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write post-HBT evaluation only after recorder and stats artifacts exist."""
    out_dir = Path(out_dir)
    recorder_path = out_dir / "recorder_result.npz"
    stats_path = out_dir / "stats_summary.json"
    if not recorder_path.is_file() or not stats_path.is_file():
        raise HftBacktestOnlyPipelineError(
            "promotion_decision_requires_recorder_result_and_stats_summary"
        )
    stats = dict(stats_summary or _load_json(stats_path))
    mechanical_pass = stats.get("mechanical_validity_status") == "pass"
    decision = {
        "schema_version": "hft3_hftbacktest_only_promotion_decision_v1",
        "plan": PLAN_PATH,
        "created_at_utc": _utc_now(),
        "run_id": stats.get("run_id"),
        "canonical_model_id": stats.get("canonical_model_id", ""),
        "decision": "observe" if mechanical_pass else "reject",
        "promotion_allowed": False,
        "mechanical_validity_status": stats.get("mechanical_validity_status"),
        "economic_result_status": stats.get("economic_result_status"),
        "microstructure_realism_status": "not_evaluated_minimal_slice",
        "robustness_status": "not_evaluated_minimal_slice",
        "required_artifacts": {
            "recorder_result": str(recorder_path),
            "stats_summary": str(stats_path),
        },
        "notes": [
            "Generated after HftBacktest output exists.",
            "Minimal active-path slice observes but does not promote without robustness evidence.",
        ],
    }
    _write_json(out_dir / "promotion_decision.json", decision)
    return decision


def _run_minimal_strategy(config: HftBacktestOnlyRunConfig) -> tuple[dict[str, Any], list[str]]:
    params = dict(config.strategy_params)
    signal_lookup: Callable[[int | None], float] | None = None
    signal_meta: dict[str, Any] = {}
    if config.strategy_id == "hypothesis_limit_order":
        signal_lookup, signal_meta, setup_reasons = _build_model_signal_lookup(config, params)
        if setup_reasons:
            return _not_run_replay(setup_reasons[0], config), setup_reasons
        side = "BUY"
    else:
        side = str(params.get("side", "BUY")).upper()
    try:
        quantity = _positive_float(params.get("quantity", 1.0), "strategy_params.quantity")
        interval_ns = _positive_int(params.get("interval_ns", 1_000_000_000), "strategy_params.interval_ns")
        entry_scan_steps, max_loop_steps = _strategy_entry_scan_steps(
            config,
            params,
            interval_ns,
            require_event_window=config.strategy_id == "hypothesis_limit_order",
        )
        holding_steps = _strategy_holding_steps(params)
        order_id = _positive_int(params.get("order_id", 9001), "strategy_params.order_id")
        signal_threshold = _positive_float(params.get("signal_threshold", 0.15), "strategy_params.signal_threshold")
    except HftBacktestOnlyPipelineError as exc:
        reason = str(exc)
        return _not_run_replay(reason, config), [reason]
    price_mode = str(params.get("price_mode", "passive_best_bid_or_ask"))
    if side not in {"BUY", "SELL"}:
        return _not_run_replay("invalid_strategy_side", config), ["invalid_strategy_side"]
    if price_mode not in {"passive_best_bid_or_ask", "cross_spread"}:
        return _not_run_replay("invalid_strategy_price_mode", config), ["invalid_strategy_price_mode"]

    try:
        from hftbacktest import BacktestAsset, HashMapMarketDepthBacktest
        from hftbacktest.order import GTC, LIMIT
    except Exception as exc:
        reason = f"hftbacktest_unavailable:{type(exc).__name__}"
        return _not_run_replay(reason, config), [reason]

    try:
        asset = BacktestAsset()
        _call_asset(asset, "data", str(config.normalized_npz))
        _call_asset(asset, "initial_snapshot", str(config.initial_snapshot))
        if hasattr(asset, "linear_asset"):
            _call_asset(asset, "linear_asset", float(config.contract_size))
        _call_asset(asset, "tick_size", float(config.tick_size))
        _call_asset(asset, "lot_size", float(config.lot_size))
        _apply_latency(asset, config)
        _apply_exchange_and_queue(asset, config)
        _call_asset(asset, "trading_qty_fee_model", float(config.maker_fee), float(config.taker_fee))
        if config.roi_lower_bound is not None and hasattr(asset, "roi_lb"):
            _call_asset(asset, "roi_lb", float(config.roi_lower_bound))
        if config.roi_upper_bound is not None and hasattr(asset, "roi_ub"):
            _call_asset(asset, "roi_ub", float(config.roi_upper_bound))
        hbt = HashMapMarketDepthBacktest([asset])
    except Exception as exc:
        reason = f"hftbacktest_asset_config_failed:{type(exc).__name__}:{exc}"
        return _not_run_replay(reason, config), [reason]

    api_calls = [
        "BacktestAsset",
        "BacktestAsset.data",
        "BacktestAsset.initial_snapshot",
        "HashMapMarketDepthBacktest",
    ]
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    submitted = False
    submit_ret = None
    response_ret = None
    cancel_ret = None
    cancel_response_ret = None
    order_snapshot: dict[str, Any] = {}
    last_recorded_exec_qty = 0.0
    submitted_step: int | None = None

    def record_order_state(event_type: str, state: Any, step: int) -> None:
        nonlocal last_recorded_exec_qty, order_snapshot
        active_orders = hbt.orders(0)
        api_calls.append("HashMapMarketDepthBacktest.orders")
        order_obj = _get_order(active_orders, order_id)
        snapshot = _order_snapshot(order_obj)
        if not snapshot:
            return
        order_snapshot = snapshot
        orders.append({"order_id": order_id, "event_type": event_type, "step": step, **snapshot})
        exec_qty = float(snapshot.get("exec_qty") or 0.0)
        if exec_qty <= last_recorded_exec_qty:
            return
        fill_delta = exec_qty - last_recorded_exec_qty
        last_recorded_exec_qty = exec_qty
        fills.append(
            {
                "order_id": order_id,
                "step": step,
                "filled_quantity": fill_delta,
                "cumulative_exec_qty": exec_qty,
                "avg_fill_price": float(snapshot.get("exec_price") or 0.0),
                "fees": _float_field(state, "fee"),
            }
        )

    try:
        for step in range(max_loop_steps):
            ret, api_name = _advance_hbt(hbt, interval_ns)
            api_calls.append(api_name)
            if ret != 0:
                break
            state = _state_values(hbt)
            api_calls.append("HashMapMarketDepthBacktest.state_values")
            positions.append({"step": step, "position": _float_field(state, "position"), "balance": _float_field(state, "balance"), "fee": _float_field(state, "fee")})
            equity.append({"step": step, "net_pnl": _float_field(state, "balance")})
            if submitted:
                record_order_state("ORDER_STATE_AFTER_ELAPSE", state, step)
                _maybe_call(hbt, "clear_inactive_orders", 0)
                api_calls.append("HashMapMarketDepthBacktest.clear_inactive_orders")
                if submitted_step is not None and step - submitted_step >= holding_steps:
                    break
                continue
            signal_value = None
            if signal_lookup is not None:
                if step >= entry_scan_steps:
                    continue
                signal_value = signal_lookup(_hbt_current_timestamp(hbt))
                if abs(signal_value) < signal_threshold:
                    continue
                side = "BUY" if signal_value > 0 else "SELL"
            _maybe_call(hbt, "clear_inactive_orders", 0)
            api_calls.append("HashMapMarketDepthBacktest.clear_inactive_orders")
            depth = hbt.depth(0)
            api_calls.append("HashMapMarketDepthBacktest.depth")
            price = _price_from_depth(depth, side, price_mode)
            if price is None:
                continue
            if side == "BUY":
                submit_ret = int(hbt.submit_buy_order(0, order_id, price, quantity, GTC, LIMIT, False))
                api_calls.append("HashMapMarketDepthBacktest.submit_buy_order")
            else:
                submit_ret = int(hbt.submit_sell_order(0, order_id, price, quantity, GTC, LIMIT, False))
                api_calls.append("HashMapMarketDepthBacktest.submit_sell_order")
            response_ret = int(hbt.wait_order_response(0, order_id, interval_ns))
            api_calls.append("HashMapMarketDepthBacktest.wait_order_response")
            submitted = True
            submitted_step = step
            orders.append(
                {
                    "order_id": order_id,
                    "event_type": "ORDER_SUBMITTED",
                    "step": step,
                    "side": side,
                    "price": price,
                    "quantity": quantity,
                    "signal": signal_value,
                    "submit_return_code": submit_ret,
                    "response_return_code": response_ret,
                }
            )
            state = _state_values(hbt)
            api_calls.append("HashMapMarketDepthBacktest.state_values")
            record_order_state("ORDER_STATE", state, step)
        if submitted and float(order_snapshot.get("leaves_qty") or 0.0) > 0 and hasattr(hbt, "cancel"):
            cancel_step = min(max_loop_steps, int(submitted_step or 0) + holding_steps)
            cancel_ret = int(hbt.cancel(0, order_id, False))
            api_calls.append("HashMapMarketDepthBacktest.cancel")
            cancel_response_ret = int(hbt.wait_order_response(0, order_id, interval_ns))
            api_calls.append("HashMapMarketDepthBacktest.wait_order_response")
            orders.append(
                {
                    "order_id": order_id,
                    "event_type": "ORDER_CANCEL_REQUESTED",
                    "step": cancel_step,
                    "cancel_return_code": cancel_ret,
                    "cancel_response_return_code": cancel_response_ret,
                }
            )
            state = _state_values(hbt)
            api_calls.append("HashMapMarketDepthBacktest.state_values")
            record_order_state("ORDER_STATE_AFTER_CANCEL", state, cancel_step)
        _maybe_call(hbt, "clear_inactive_orders", 0)
        final_state = _state_values(hbt)
    except Exception as exc:
        reason = f"hftbacktest_run_failed:{type(exc).__name__}:{exc}"
        return _not_run_replay(reason, config), [reason]

    reasons: list[str] = []
    no_order_observation = ""
    if not submitted:
        if signal_lookup is not None and signal_meta.get("adapter_status") == "available":
            no_order_observation = "strategy_signal_below_threshold_or_no_directional_order"
            reasons.append("pipeline_blocker:no_hbt_order_submitted")
        else:
            reasons.append("pipeline_blocker:no_hbt_order_submitted")
    if submit_ret not in (0, None):
        reasons.append("order_submit_failed")
    # hftbacktest v2 return codes: 0 = success, 3 = WaitCanceled timeout for passive orders.
    if response_ret not in (0, 3, None):
        reasons.append("order_response_failed")
    if submitted and not order_snapshot:
        reasons.append("order_state_missing")
    gross_pnl = _float_field(final_state, "balance") + _float_field(final_state, "fee")
    net_pnl = _float_field(final_state, "balance")
    fills_count = len(fills)
    orders_intended = 1
    orders_submitted = 1 if submitted and submit_ret == 0 else 0
    orders_acknowledged = 1 if response_ret in (0, 3) else 0
    total_filled_qty = sum(float(row.get("filled_quantity") or 0.0) for row in fills)
    filled_orders = 1 if total_filled_qty > 0 else 0
    fill_rate = filled_orders / orders_submitted if orders_submitted else 0.0
    replay = {
        "schema_version": "hft3_hftbacktest_only_official_replay_v1",
        "official_hftbacktest_replay_status": "pass" if not reasons else "fail",
        "api_calls": list(dict.fromkeys(api_calls)),
        "run_id": config.run_id,
        "canonical_model_id": config.canonical_model_id,
        "symbol": config.symbol,
        "contract": config.contract,
        "event_id": config.event_id,
        "strategy_id": config.strategy_id,
        "strategy_surface_version": _strategy_surface_version(config.strategy_id),
        "strategy_params": params,
        "entry_scan_steps": entry_scan_steps,
        "max_loop_steps": max_loop_steps,
        "holding_period_bars": holding_steps,
        "legacy_aliases": list(config.legacy_aliases),
        "authority_refs": list(config.authority_refs),
        "strategy_adapter_status": signal_meta.get("adapter_status", "smoke_limit_order"),
        "signal_observations": signal_meta.get("signal_observations", 0),
        "signal_min": signal_meta.get("signal_min"),
        "signal_max": signal_meta.get("signal_max"),
        "signal_abs_max": signal_meta.get("signal_abs_max"),
        "signal_source": signal_meta.get("signal_source", "none"),
        "signal_field": signal_meta.get("signal_field", ""),
        "feature_backend": signal_meta.get("feature_backend", "none"),
        "no_order_observation": no_order_observation,
        "orders": orders,
        "fills": fills,
        "position_timeseries": positions,
        "equity_curve": equity,
        "orders_intended": orders_intended,
        "orders_submitted": orders_submitted,
        "orders_acknowledged": orders_acknowledged,
        "orders_cancelled": 1 if cancel_ret == 0 else 0,
        "fills_count": fills_count,
        "partial_fills_count": _partial_fill_count(fills, quantity),
        "unfilled_count": 1 if submitted and total_filled_qty <= 0 else 0,
        "fill_rate": fill_rate,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "total_fees": _float_field(final_state, "fee"),
        "fail_closed_reasons": reasons,
    }
    return replay, reasons


def _build_model_signal_lookup(
    config: HftBacktestOnlyRunConfig,
    params: Mapping[str, Any],
) -> tuple[Callable[[int | None], float] | None, dict[str, Any], list[str]]:
    model_id = str(params.get("model_id") or config.canonical_model_id or "").strip()
    if not model_id:
        return None, {}, ["authority_missing:canonical_model_id_missing"]
    if config.canonical_model_id and model_id != config.canonical_model_id:
        return None, {}, ["authority_missing:canonical_model_id_mismatch"]
    try:
        adapter_kind, adapter, class_name = _canonical_signal_adapter(model_id)
        if adapter_kind == "structural":
            return _build_structural_signal_lookup(config, adapter, class_name)
        from features_engine.src.features.npz_feed import iter_mbo_events, load_npz_events
        from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline

        raw_events = load_npz_events(str(config.normalized_npz))
        pipeline = MarketStatePipeline(
            tick_size=float(config.tick_size),
            latency_ms=float(config.entry_latency_ns) / 1_000_000.0,
        )
        observations: list[tuple[int, float]] = []
        for event in iter_mbo_events(raw_events):
            state = pipeline.process_event(event)
            observations.append((int(event.timestamp_ns), float(adapter.evaluate(state))))
    except HftBacktestOnlyPipelineError as exc:
        return None, {}, [str(exc)]
    except Exception as exc:
        reason = f"pipeline_blocker:feature_surface_mismatch:{type(exc).__name__}:{exc}"
        return None, {}, [reason]
    if not observations:
        return None, {}, ["pipeline_blocker:feature_surface_mismatch:no_signal_observations"]

    observations.sort(key=lambda item: item[0])
    cursor = {"index": 0, "last_signal": 0.0}

    def lookup(timestamp_ns: int | None) -> float:
        if timestamp_ns is None:
            index = int(cursor["index"])
            if index < len(observations):
                cursor["last_signal"] = observations[index][1]
                cursor["index"] = index + 1
            return float(cursor["last_signal"])
        index = int(cursor["index"])
        while index < len(observations) and observations[index][0] <= int(timestamp_ns):
            cursor["last_signal"] = observations[index][1]
            index += 1
        cursor["index"] = index
        return float(cursor["last_signal"])

    return lookup, {
        "adapter_status": "available",
        "signal_observations": len(observations),
        **_signal_stats(observations),
        "signal_source": "hbt_normalized_mbo_market_state_pipeline",
        "signal_field": "hypothesis.evaluate",
        "feature_backend": getattr(pipeline, "feature_backend", "unknown"),
    }, []


def _build_structural_signal_lookup(
    config: HftBacktestOnlyRunConfig,
    _adapter: Any,
    class_name: str,
) -> tuple[Callable[[int | None], float] | None, dict[str, Any], list[str]]:
    if not _STRUCTURAL_SIGNAL_FIELDS_BY_CLASS.get(class_name):
        return None, {}, ["pipeline_blocker:missing_uniform_hbt_adapter"]
    try:
        from features_engine.src.features.feature_index import FEATURE_DIM
        from features_engine.src.features.npz_feed import iter_mbo_events, load_npz_events
        from features_engine.src.pipeline.structural_integration import StructuralModelIntegrator

        raw_events = load_npz_events(str(config.normalized_npz))
        integrator = StructuralModelIntegrator(
            tick_size=float(config.tick_size),
            target_asset=str(config.symbol),
        )
        observations: list[tuple[int, float]] = []
        signal_fields: set[str] = set()
        for event in iter_mbo_events(raw_events):
            snapshot = integrator.integrate(event, np.zeros(FEATURE_DIM, dtype=np.float64))
            payload = _structural_payload_for_class(snapshot, class_name)
            signal_value, signal_field = _structural_payload_signal(class_name, payload)
            if signal_field:
                signal_fields.add(signal_field)
            observations.append((int(event.timestamp_ns), signal_value))
    except HftBacktestOnlyPipelineError as exc:
        return None, {}, [str(exc)]
    except Exception as exc:
        reason = f"pipeline_blocker:feature_surface_mismatch:{type(exc).__name__}:{exc}"
        return None, {}, [reason]
    if not observations:
        return None, {}, ["pipeline_blocker:feature_surface_mismatch:no_signal_observations"]

    observations.sort(key=lambda item: item[0])
    cursor = {"index": 0, "last_signal": 0.0}

    def lookup(timestamp_ns: int | None) -> float:
        if timestamp_ns is None:
            index = int(cursor["index"])
            if index < len(observations):
                cursor["last_signal"] = observations[index][1]
                cursor["index"] = index + 1
            return float(cursor["last_signal"])
        index = int(cursor["index"])
        while index < len(observations) and observations[index][0] <= int(timestamp_ns):
            cursor["last_signal"] = observations[index][1]
            index += 1
        cursor["index"] = index
        return float(cursor["last_signal"])

    return lookup, {
        "adapter_status": "available",
        "signal_observations": len(observations),
        **_signal_stats(observations),
        "signal_source": "hbt_normalized_mbo_structural_integrator",
        "signal_field": ",".join(sorted(signal_fields)),
        "feature_backend": "structural_integrator_python",
    }, []


def _structural_payload_for_class(snapshot: Any, class_name: str) -> Any:
    attr = _STRUCTURAL_PAYLOAD_ATTR_BY_CLASS.get(class_name)
    if not attr:
        raise HftBacktestOnlyPipelineError("pipeline_blocker:missing_uniform_hbt_adapter")
    return getattr(snapshot, attr, None)


def _structural_payload_signal(class_name: str, payload: Any) -> tuple[float, str]:
    if payload is None:
        return 0.0, ""
    for field_name in _STRUCTURAL_SIGNAL_FIELDS_BY_CLASS.get(class_name, ()):
        value = getattr(payload, field_name, 0.0)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric) and abs(numeric) > 1e-12:
            return math.tanh(numeric), field_name
    return 0.0, ""


def _canonical_signal_adapter(canonical_model_id: str) -> tuple[str, Any, str]:
    class_name = _canonical_registry_class_name(canonical_model_id)
    import_errors: list[str] = []

    try:
        from features_engine.src.hypotheses.registry import get_active_hypotheses
    except Exception as exc:
        import_errors.append(f"hypotheses:{type(exc).__name__}")
    else:
        by_class = {hyp.__class__.__name__: hyp for hyp in get_active_hypotheses()}
        hypothesis = by_class.get(class_name)
        if hypothesis is not None:
            return "hypothesis", hypothesis, class_name

    try:
        from features_engine.src.structural_models.registry import get_structural_models
    except Exception as exc:
        import_errors.append(f"structural_models:{type(exc).__name__}")
    else:
        by_class = {model.__class__.__name__: model for model in get_structural_models()}
        structural_model = by_class.get(class_name)
        if structural_model is not None:
            return "structural", structural_model, class_name

    if import_errors:
        joined = ",".join(import_errors)
        raise HftBacktestOnlyPipelineError(
            f"pipeline_blocker:missing_uniform_hbt_adapter:import_failed:{joined}"
        )
    raise HftBacktestOnlyPipelineError("pipeline_blocker:missing_uniform_hbt_adapter")


def uniform_hbt_order_adapter_status(canonical_model_id: str) -> str:
    """Return adapter availability from the active HBT runner, not registry metadata."""
    try:
        adapter_kind, _adapter, class_name = _canonical_signal_adapter(canonical_model_id)
    except HftBacktestOnlyPipelineError as exc:
        if "feature_surface_mismatch" in str(exc):
            return "feature_surface_mismatch"
        return "missing_uniform_hbt_adapter"
    if adapter_kind == "structural":
        if class_name not in _STRUCTURAL_PAYLOAD_ATTR_BY_CLASS:
            return "missing_uniform_hbt_adapter"
        if not _STRUCTURAL_SIGNAL_FIELDS_BY_CLASS.get(class_name):
            return "missing_uniform_hbt_adapter"
    return "available"


def _canonical_registry_class_name(canonical_model_id: str) -> str:
    try:
        from features_engine.src.model_registry import legacy_to_slug, load_model_registry, resolve_model_id
    except Exception as exc:
        raise HftBacktestOnlyPipelineError(
            f"pipeline_blocker:missing_uniform_hbt_adapter:import_failed:{type(exc).__name__}"
        ) from exc
    if canonical_model_id in legacy_to_slug():
        raise HftBacktestOnlyPipelineError("authority_missing:canonical_model_id_required")
    try:
        resolved = resolve_model_id(canonical_model_id)
    except KeyError as exc:
        raise HftBacktestOnlyPipelineError("authority_missing:canonical_model_id_unknown") from exc
    if resolved != canonical_model_id:
        raise HftBacktestOnlyPipelineError("authority_missing:canonical_model_id_required")
    entry = (load_model_registry().get("models") or {}).get(resolved) or {}
    class_name = str(entry.get("class") or "").strip()
    if not class_name:
        raise HftBacktestOnlyPipelineError("authority_missing:model_registry_class_missing")
    return class_name


def _hbt_current_timestamp(hbt: Any) -> int | None:
    value = getattr(hbt, "current_timestamp", None)
    if callable(value):
        value = value()
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _apply_latency(asset: Any, config: HftBacktestOnlyRunConfig) -> None:
    if config.latency_model != "constant_order_latency":
        raise HftBacktestOnlyPipelineError("only_constant_order_latency_wired_in_minimal_slice")
    if hasattr(asset, "constant_order_latency"):
        asset.constant_order_latency(int(config.entry_latency_ns), int(config.response_latency_ns))
    elif hasattr(asset, "constant_latency"):
        asset.constant_latency(int(config.entry_latency_ns), int(config.response_latency_ns))
    else:
        raise HftBacktestOnlyPipelineError("hftbacktest_constant_latency_api_missing")


def _apply_exchange_and_queue(asset: Any, config: HftBacktestOnlyRunConfig) -> None:
    if config.exchange_fill_model == "NoPartialFillExchange":
        _call_asset(asset, "no_partial_fill_exchange")
    elif config.exchange_fill_model == "PartialFillExchange":
        _call_asset(asset, "partial_fill_exchange")
    else:
        raise HftBacktestOnlyPipelineError(f"unsupported_exchange_fill_model:{config.exchange_fill_model}")
    if config.queue_model == "L3FIFOQueueModel":
        _call_asset(asset, "l3_fifo_queue_model")
    elif config.queue_model == "LogProbQueueModel2":
        _call_asset(asset, "log_prob_queue_model2")
    else:
        raise HftBacktestOnlyPipelineError(f"unsupported_queue_model:{config.queue_model}")


def _data_manifest(config: HftBacktestOnlyRunConfig, validation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "hft3_hftbacktest_only_data_manifest_v1",
        "canonical_model_id": config.canonical_model_id,
        "symbol": config.symbol,
        "contract": config.contract,
        "event_id": config.event_id,
        "source": "hftbacktest_normalized_event_data",
        "normalized_npz": str(config.normalized_npz),
        "normalized_npz_sha256": _sha256_file(config.normalized_npz) if Path(config.normalized_npz).is_file() else "",
        "initial_snapshot": str(config.initial_snapshot),
        "initial_snapshot_sha256": _sha256_file(config.initial_snapshot) if Path(config.initial_snapshot).is_file() else "",
        "tick_size": config.tick_size,
        "lot_size": config.lot_size,
        "contract_size": config.contract_size,
        "feed_type": validation.get("l2_l3_classification"),
        "timezone": "UTC",
        "validation_status": validation.get("data_validation_status"),
    }


def _hbt_config(config: HftBacktestOnlyRunConfig) -> dict[str, Any]:
    return {
        "schema_version": "hft3_hftbacktest_only_hbt_config_v1",
        "latency_model": config.latency_model,
        "entry_latency_ns": config.entry_latency_ns,
        "response_latency_ns": config.response_latency_ns,
        "exchange_fill_model": config.exchange_fill_model,
        "queue_model": config.queue_model,
        "fee_model": config.fee_model,
        "maker_fee": config.maker_fee,
        "taker_fee": config.taker_fee,
        "tick_size": config.tick_size,
        "lot_size": config.lot_size,
        "contract_size": config.contract_size,
        "roi_lower_bound": config.roi_lower_bound,
        "roi_upper_bound": config.roi_upper_bound,
    }


def _strategy_config(config: HftBacktestOnlyRunConfig) -> dict[str, Any]:
    pit_safe_signal_status = (
        "hbt_normalized_mbo_market_state_pipeline"
        if config.strategy_id == "hypothesis_limit_order"
        else "strategy_params_only_minimal_slice"
    )
    return {
        "schema_version": "hft3_hftbacktest_only_strategy_config_v1",
        "strategy_id": config.strategy_id,
        "strategy_surface_version": _strategy_surface_version(config.strategy_id),
        "strategy_params": dict(config.strategy_params),
        "canonical_model_id": config.canonical_model_id,
        "legacy_aliases": list(config.legacy_aliases),
        "authority_refs": list(config.authority_refs),
        "loop_semantics": "while hbt.elapse(interval_ns) == 0 when available",
        "pit_safe_signal_status": pit_safe_signal_status,
    }


def _normalized_input_manifest(config: HftBacktestOnlyRunConfig, validation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "hft3_hftbacktest_only_normalized_input_manifest_v1",
        "canonical_model_id": config.canonical_model_id,
        "normalized_npz": str(config.normalized_npz),
        "initial_snapshot": str(config.initial_snapshot),
        "dtype_fields": validation.get("dtype_fields", []),
        "timestamp_units": validation.get("timestamp_units"),
        "row_count": validation.get("row_count"),
        "validation_status": validation.get("data_validation_status"),
    }


def _latency_report(config: HftBacktestOnlyRunConfig) -> dict[str, Any]:
    return {
        "schema_version": "hft3_hftbacktest_only_latency_report_v1",
        "latency_model": config.latency_model,
        "feed_latency_source": "event_local_ts_minus_exch_ts",
        "order_entry_latency_ns": config.entry_latency_ns,
        "order_response_latency_ns": config.response_latency_ns,
        "latency_components_separate": True,
        "latency_sensitivity_ns": [50_000, 100_000, 250_000, 500_000, 1_000_000],
    }


def _stats_summary(config: HftBacktestOnlyRunConfig, replay: Mapping[str, Any], recorder_path: Path) -> dict[str, Any]:
    mechanical_pass = replay.get("official_hftbacktest_replay_status") == "pass"
    net_pnl = replay.get("net_pnl")
    return {
        "schema_version": "hft3_hftbacktest_only_stats_summary_v1",
        "run_id": config.run_id,
        "canonical_model_id": config.canonical_model_id,
        "symbol": config.symbol,
        "contract": config.contract,
        "event_id": config.event_id,
        "strategy_id": config.strategy_id,
        "strategy_surface_version": _strategy_surface_version(config.strategy_id),
        "recorder_result": str(recorder_path),
        "mechanical_validity_status": "pass" if mechanical_pass else "fail",
        "economic_result_status": "pass" if isinstance(net_pnl, (int, float)) and net_pnl > 0 else "observe",
        "orders_submitted": replay.get("orders_submitted", 0),
        "orders_acknowledged": replay.get("orders_acknowledged", 0),
        "orders_cancelled": replay.get("orders_cancelled", 0),
        "fills_count": replay.get("fills_count", 0),
        "partial_fills_count": replay.get("partial_fills_count", 0),
        "unfilled_count": replay.get("unfilled_count", 0),
        "fill_rate": replay.get("fill_rate", 0.0),
        "gross_pnl": replay.get("gross_pnl"),
        "net_pnl": replay.get("net_pnl"),
        "fail_closed_reasons": list(replay.get("fail_closed_reasons") or []),
    }


def _write_recorder_result(path: Path, replay: Mapping[str, Any]) -> Path:
    orders = replay.get("orders") or []
    fills = replay.get("fills") or []
    order_ids = [int(row.get("order_id", 0)) for row in orders if isinstance(row, Mapping)]
    fill_qty = [float(row.get("filled_quantity", 0.0)) for row in fills if isinstance(row, Mapping)]
    _save_npz_atomic(
        path,
        order_ids=np.array(order_ids, dtype=np.int64),
        fill_quantities=np.array(fill_qty, dtype=np.float64),
        net_pnl=np.array([float(replay.get("net_pnl") or 0.0)], dtype=np.float64),
        gross_pnl=np.array([float(replay.get("gross_pnl") or 0.0)], dtype=np.float64),
    )
    return path


def _fill_quality_report(replay: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "hft3_hftbacktest_only_fill_quality_report_v1",
        "orders_submitted": replay.get("orders_submitted", 0),
        "fills_count": replay.get("fills_count", 0),
        "partial_fills_count": replay.get("partial_fills_count", 0),
        "unfilled_count": replay.get("unfilled_count", 0),
        "fill_rate": replay.get("fill_rate", 0.0),
        "maker_fill_ratio": "not_computed_minimal_slice",
        "taker_fill_ratio": "not_computed_minimal_slice",
        "adverse_selection_after_fill": "not_computed_minimal_slice",
    }


def _queue_diagnostics(config: HftBacktestOnlyRunConfig, replay: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "hft3_hftbacktest_only_queue_diagnostics_v1",
        "queue_model": config.queue_model,
        "exchange_fill_model": config.exchange_fill_model,
        "queue_wait_time": "not_computed_minimal_slice",
        "quote_lifetime": "not_computed_minimal_slice",
        "orders_cancelled": replay.get("orders_cancelled", 0),
    }


def _write_optional_parquet(path: Path, rows: Any) -> None:
    try:
        import pandas as pd

        pd.DataFrame(list(rows or [])).to_parquet(path, index=False)
    except Exception as exc:
        fallback = path.with_suffix(".jsonl")
        _write_jsonl(fallback, list(rows or []))
        _write_json(
            _path_with_added_suffix(path, ".fallback.json"),
            {"status": "fallback_jsonl", "fallback": str(fallback), "reason": f"{type(exc).__name__}:{exc}"},
        )


def _blocked_run_status(reasons: list[str]) -> str:
    for reason in reasons:
        if reason.startswith("pipeline_blocker:"):
            return "pipeline_blocker"
        if reason.startswith("authority_missing"):
            return "authority_missing"
        if reason.startswith("data_blocker:"):
            return "data_blocker"
    return "hftbacktest_run_failed"


def _result(config: HftBacktestOnlyRunConfig, out_dir: Path, status: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "run_id": config.run_id,
        "canonical_model_id": config.canonical_model_id,
        "artifact_dir": str(out_dir),
        "status": status,
        "fail_closed_reasons": list(reasons),
        "plan": PLAN_PATH,
    }


def _not_run_replay(reason: str, config: HftBacktestOnlyRunConfig) -> dict[str, Any]:
    return {
        "schema_version": "hft3_hftbacktest_only_official_replay_v1",
        "official_hftbacktest_replay_status": "not_run",
        "run_id": config.run_id,
        "canonical_model_id": config.canonical_model_id,
        "symbol": config.symbol,
        "contract": config.contract,
        "event_id": config.event_id,
        "strategy_id": config.strategy_id,
        "strategy_surface_version": _strategy_surface_version(config.strategy_id),
        "strategy_adapter_status": (
            "missing_uniform_hbt_adapter"
            if "missing_uniform_hbt_adapter" in reason
            else "not_run"
        ),
        "signal_observations": 0,
        "signal_min": None,
        "signal_max": None,
        "signal_abs_max": None,
        "signal_source": "none",
        "legacy_aliases": list(config.legacy_aliases),
        "authority_refs": list(config.authority_refs),
        "orders": [],
        "fills": [],
        "position_timeseries": [],
        "equity_curve": [],
        "orders_intended": 0,
        "orders_submitted": 0,
        "orders_acknowledged": 0,
        "orders_cancelled": 0,
        "fills_count": 0,
        "partial_fills_count": 0,
        "unfilled_count": 0,
        "fill_rate": 0.0,
        "gross_pnl": 0.0,
        "net_pnl": 0.0,
        "fail_closed_reasons": [reason],
    }


def _write_audit(out_dir: Path, status: str, reasons: list[str]) -> None:
    lines = [
        "# HftBacktest-Only Run Audit",
        "",
        f"- status: {status}",
        f"- plan: {PLAN_PATH}",
        f"- legacy_screening_dependency: forbidden_active_path",
        f"- fail_closed_reasons: {', '.join(reasons) if reasons else 'none'}",
        "",
    ]
    (Path(out_dir) / "audit.md").write_text("\n".join(lines), encoding="utf-8")


def _require_event_fields(events: Any) -> None:
    dtype_fields = list(events.dtype.names or [])
    missing = [field for field in EXPECTED_EVENT_FIELDS if field not in dtype_fields]
    if missing:
        raise HftBacktestOnlyPipelineError(f"source_event_dtype_missing:{','.join(missing)}")


def _snapshot_from_warmup(warmup: np.ndarray, cutoff_ts_ns: int) -> np.ndarray:
    active: dict[int, Any] = {}
    for row in warmup:
        event_type = _event_type(row)
        order_id = int(row["order_id"])
        if order_id <= 0:
            continue
        if event_type == _ADD_ORDER_EVENT:
            active[order_id] = row.copy()
        elif event_type == _MODIFY_ORDER_EVENT and order_id in active:
            active[order_id] = row.copy()
        elif event_type == _TRADE_EVENT and order_id in active:
            _reduce_active_order(active, order_id, row)
        elif event_type in {_CANCEL_ORDER_EVENT, _FILL_EVENT}:
            active.pop(order_id, None)

    snapshot = np.zeros(len(active), dtype=warmup.dtype)
    for index, row in enumerate(active.values()):
        snapshot[index] = row
        snapshot[index]["ev"] = _replace_event_type(snapshot[index]["ev"], _ADD_ORDER_EVENT)
        snapshot[index]["exch_ts"] = cutoff_ts_ns - 1
        snapshot[index]["local_ts"] = cutoff_ts_ns - 1
    return snapshot


def _filter_replay_added_orders_only(replay: np.ndarray) -> tuple[np.ndarray, dict[str, int]]:
    active_quantities: dict[int, float] = {}
    keep = np.zeros(len(replay), dtype=bool)
    dropped = {
        "preexisting_or_unknown_lifecycle": 0,
        "duplicate_add": 0,
        "zero_order_id": 0,
    }
    for index, row in enumerate(replay):
        event_type = _event_type(row)
        order_id = int(row["order_id"])
        if event_type == _ADD_ORDER_EVENT:
            if order_id <= 0:
                dropped["zero_order_id"] += 1
            elif order_id in active_quantities:
                dropped["duplicate_add"] += 1
            else:
                active_quantities[order_id] = _row_quantity(row)
                keep[index] = True
        elif event_type == _MODIFY_ORDER_EVENT:
            if order_id in active_quantities:
                active_quantities[order_id] = _row_quantity(row)
                keep[index] = True
            else:
                dropped["preexisting_or_unknown_lifecycle"] += 1
        elif event_type == _TRADE_EVENT:
            if order_id in active_quantities:
                keep[index] = True
                _reduce_active_quantity(active_quantities, order_id, row)
            else:
                dropped["preexisting_or_unknown_lifecycle"] += 1
        elif event_type in {_CANCEL_ORDER_EVENT, _FILL_EVENT}:
            if order_id in active_quantities:
                keep[index] = True
                active_quantities.pop(order_id, None)
            else:
                dropped["preexisting_or_unknown_lifecycle"] += 1
        else:
            keep[index] = True
    return replay[keep], dropped


def _event_type(row: Any) -> int:
    return int(row["ev"]) & 0xFF


def _row_quantity(row: Any) -> float:
    try:
        return max(0.0, float(row["qty"]))
    except (TypeError, ValueError):
        return 0.0


def _reduce_active_order(active: dict[int, Any], order_id: int, row: Any) -> None:
    active_row = active.get(order_id)
    if active_row is None:
        return
    remaining = _row_quantity(active_row) - _row_quantity(row)
    if remaining <= 0.0:
        active.pop(order_id, None)
        return
    updated = active_row.copy()
    updated["qty"] = remaining
    active[order_id] = updated


def _reduce_active_quantity(active_quantities: dict[int, float], order_id: int, row: Any) -> None:
    remaining = active_quantities.get(order_id, 0.0) - _row_quantity(row)
    if remaining <= 0.0:
        active_quantities.pop(order_id, None)
    else:
        active_quantities[order_id] = remaining


def _replace_event_type(value: Any, event_type: int) -> int:
    return (int(value) & ~0xFF) | int(event_type)


def _safe_stem(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value).strip())
    return safe.strip("._-") or "hbt"


def _save_npz_atomic(path: Path, **payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path_with_added_suffix(path, ".tmp")
    with tmp.open("wb") as handle:
        np.savez_compressed(handle, **payload)
    os.replace(tmp, path)


def _path_with_added_suffix(path: Path, suffix: str) -> Path:
    path = Path(path)
    return path.with_name(path.name + suffix)


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


def _snapshot_read_reasons(path: Path) -> list[str]:
    try:
        with np.load(path, allow_pickle=False) as payload:
            if "data" not in payload.files:
                return ["INITIAL_SNAPSHOT_MISSING_DATA_ARRAY"]
    except Exception as exc:
        return [f"INITIAL_SNAPSHOT_READ_FAILED:{type(exc).__name__}"]
    return []


def _expected_event_dtype() -> Any | None:
    try:
        from hftbacktest.types import event_dtype

        return event_dtype
    except Exception:
        return None


def _event_constants() -> dict[str, int]:
    try:
        from hftbacktest import types as hbt_types

        return {
            "EXCH_EVENT": int(hbt_types.EXCH_EVENT),
            "LOCAL_EVENT": int(hbt_types.LOCAL_EVENT),
            "ADD_ORDER_EVENT": int(hbt_types.ADD_ORDER_EVENT),
            "DEPTH_EVENT": int(hbt_types.DEPTH_EVENT),
            "TRADE_EVENT": int(getattr(hbt_types, "TRADE_EVENT", 2)),
            "CANCEL_ORDER_EVENT": int(hbt_types.CANCEL_ORDER_EVENT),
            "MODIFY_ORDER_EVENT": int(hbt_types.MODIFY_ORDER_EVENT),
            "FILL_EVENT": int(hbt_types.FILL_EVENT),
        }
    except Exception:
        return {}


def _classify_events(events: Any, constants: Mapping[str, int]) -> str:
    ev_types = set(int(value) for value in ((events["ev"].astype("int64")) & 0xFF))
    if not ev_types:
        return "empty_rejected"
    l3_types = {
        constants["ADD_ORDER_EVENT"],
        constants["CANCEL_ORDER_EVENT"],
        constants["MODIFY_ORDER_EVENT"],
        constants["FILL_EVENT"],
    }
    trade_type = constants.get("TRADE_EVENT", 2)
    l2_depth_types = {constants["DEPTH_EVENT"]}
    neutral_types = {trade_type}
    has_l3 = bool(ev_types & l3_types)
    has_depth = bool(ev_types & l2_depth_types)
    has_unknown = bool(ev_types - l3_types - l2_depth_types - neutral_types)
    if has_unknown:
        return "unknown_rejected"
    if has_depth and has_l3:
        return "mixed_rejected"
    if has_l3:
        return "l3_mbo"
    return "l2_mbp" if has_depth or trade_type in ev_types else "unknown_rejected"


def _event_type_counts(events: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in ((events["ev"].astype("int64")) & 0xFF):
        key = str(int(value))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _looks_like_epoch_ns(values: Any) -> bool:
    try:
        max_value = int(values.max())
    except Exception:
        return False
    return max_value > 1_000_000_000_000_000_000


def _future_data_cutoff_ns() -> int:
    return time.time_ns() + _FUTURE_DATA_GRACE_NS


def _advance_hbt(hbt: Any, interval_ns: int) -> tuple[int, str]:
    if hasattr(hbt, "elapse"):
        return int(hbt.elapse(interval_ns)), "HashMapMarketDepthBacktest.elapse"
    return int(hbt.wait_next_feed(False, interval_ns)), "HashMapMarketDepthBacktest.wait_next_feed"


def _state_values(hbt: Any) -> Any:
    if hasattr(hbt, "state_values"):
        return hbt.state_values(0)
    return {"position": 0.0, "balance": 0.0, "fee": 0.0}


def _price_from_depth(depth: Any, side: str, price_mode: str) -> float | None:
    best_bid = _float_field(depth, "best_bid", math.nan)
    best_ask = _float_field(depth, "best_ask", math.nan)
    if math.isnan(best_bid) or math.isnan(best_ask) or best_bid <= 0 or best_ask <= 0:
        return None
    tick = _float_field(depth, "tick_size", 0.0)
    if price_mode == "passive_best_bid_or_ask":
        return best_bid if side == "BUY" else best_ask
    return best_ask + tick if side == "BUY" else best_bid - tick


def _order_snapshot(order: Any) -> dict[str, Any]:
    if order is None:
        return {}
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


def _get_order(orders: Any, order_id: int) -> Any:
    getter = getattr(orders, "get", None)
    if callable(getter):
        return getter(order_id)
    if isinstance(orders, Mapping):
        return orders.get(order_id)
    return None


def _field_value(obj: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(field_name, default)
    return getattr(obj, field_name, default)


def _float_field(obj: Any, field_name: str, default: float = 0.0) -> float:
    try:
        return float(_field_value(obj, field_name, default))
    except (TypeError, ValueError):
        return default


def _int_field(obj: Any, field_name: str, default: int = 0) -> int:
    try:
        return int(_field_value(obj, field_name, default))
    except (TypeError, ValueError):
        return default


def _partial_fill_count(fills: list[Mapping[str, Any]], quantity: float) -> int:
    total = sum(float(row.get("filled_quantity") or 0.0) for row in fills)
    return 1 if 0.0 < total < quantity else 0


def _call_asset(asset: Any, method_name: str, *args: Any) -> None:
    method = getattr(asset, method_name, None)
    if method is None:
        raise HftBacktestOnlyPipelineError(f"hftbacktest_asset_api_missing:{method_name}")
    method(*args)


def _maybe_call(obj: Any, method_name: str, *args: Any) -> None:
    method = getattr(obj, method_name, None)
    if method is not None:
        method(*args)


def _positive_float(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise HftBacktestOnlyPipelineError(f"{name} must be positive") from exc
    if parsed <= 0.0:
        raise HftBacktestOnlyPipelineError(f"{name} must be positive")
    return parsed


def _strategy_surface_version(strategy_id: str) -> str:
    if strategy_id == "hypothesis_limit_order":
        return HYPOTHESIS_LIMIT_ORDER_SURFACE_VERSION
    return "smoke_limit_order_single_order_v1"


def _strategy_entry_scan_steps(
    config: HftBacktestOnlyRunConfig,
    params: Mapping[str, Any],
    interval_ns: int,
    *,
    require_event_window: bool = False,
) -> tuple[int, int]:
    if "max_steps" in params:
        steps = _positive_int(params.get("max_steps"), "strategy_params.max_steps")
        return steps, steps
    if "max_feed_steps" in params:
        steps = _positive_int(params.get("max_feed_steps"), "strategy_params.max_feed_steps")
        return steps, steps
    cutoff_ts = _int_mapping_value(config.event_window, "cutoff_ts_ns")
    end_ts = _int_mapping_value(config.event_window, "end_ts_ns")
    if cutoff_ts is None:
        cutoff_ts = _int_mapping_value(config.event_window, "start_ts_ns")
    if cutoff_ts is not None and end_ts is not None and end_ts > cutoff_ts:
        entry_steps = max(1, int(math.ceil((end_ts - cutoff_ts) / float(interval_ns))))
    elif require_event_window:
        raise HftBacktestOnlyPipelineError("pipeline_blocker:event_window_required_for_entry_scan")
    else:
        entry_steps = 3
    return entry_steps, entry_steps + _strategy_holding_steps(params)


def _strategy_holding_steps(params: Mapping[str, Any]) -> int:
    if "holding_period_bars" in params:
        return _positive_int(params.get("holding_period_bars"), "strategy_params.holding_period_bars")
    return 3


def _int_mapping_value(payload: Mapping[str, Any], key: str) -> int | None:
    try:
        value = payload.get(key)
    except AttributeError:
        return None
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _signal_stats(observations: list[tuple[int, float]]) -> dict[str, float | None]:
    values: list[float] = []
    for _timestamp, value in observations:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            values.append(numeric)
    if not values:
        return {"signal_min": None, "signal_max": None, "signal_abs_max": None}
    return {
        "signal_min": min(values),
        "signal_max": max(values),
        "signal_abs_max": max(abs(value) for value in values),
    }


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise HftBacktestOnlyPipelineError(f"{name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HftBacktestOnlyPipelineError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise HftBacktestOnlyPipelineError(f"{name} must be a positive integer")
    return parsed


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path_with_added_suffix(path, ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path_with_added_suffix(path, ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), default=str) + "\n")
    os.replace(tmp, path)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
    except Exception:
        return "unknown"


def _repo_root_default() -> Path:
    return Path(__file__).resolve().parents[3]
