"""HftBacktest-only active pipeline contract.

This module is intentionally independent from ``vectorbt_adapter`` and
``hftbacktest_realism``.  The older realism layer remains available for
historical VectorBT handoff artifacts; the active path here starts from
validated HftBacktest event data.
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
from typing import Any, Mapping

import numpy as np


PLAN_PATH = "docs/project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md"
UPSTREAM_REPO_URL = "https://github.com/nkaz001/hftbacktest"
UPSTREAM_DOCS_URL = "https://hftbacktest.readthedocs.io/en/latest/"
EXPECTED_EVENT_FIELDS = ("ev", "exch_ts", "local_ts", "px", "qty", "order_id", "ival", "fval")
_FUTURE_DATA_GRACE_NS = 86_400 * 1_000_000_000


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
            "strategy_params": dict(self.strategy_params),
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
            "vectorbt_dependency": "forbidden_active_path",
        }


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
        _write_audit(out_dir, "hftbacktest_run_failed", replay_reasons)
        return _result(config, out_dir, "hftbacktest_run_failed", replay_reasons)

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
    side = str(params.get("side", "BUY")).upper()
    try:
        quantity = _positive_float(params.get("quantity", 1.0), "strategy_params.quantity")
        max_steps = _positive_int(params.get("max_steps", params.get("max_feed_steps", 3)), "strategy_params.max_steps")
        interval_ns = _positive_int(params.get("interval_ns", 1_000_000_000), "strategy_params.interval_ns")
        order_id = _positive_int(params.get("order_id", 9001), "strategy_params.order_id")
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
    order_snapshot: dict[str, Any] = {}

    try:
        for step in range(max_steps):
            ret, api_name = _advance_hbt(hbt, interval_ns)
            api_calls.append(api_name)
            if ret != 0:
                break
            _maybe_call(hbt, "clear_inactive_orders", 0)
            api_calls.append("HashMapMarketDepthBacktest.clear_inactive_orders")
            depth = hbt.depth(0)
            api_calls.append("HashMapMarketDepthBacktest.depth")
            state = _state_values(hbt)
            api_calls.append("HashMapMarketDepthBacktest.state_values")
            positions.append({"step": step, "position": _float_field(state, "position"), "balance": _float_field(state, "balance"), "fee": _float_field(state, "fee")})
            equity.append({"step": step, "net_pnl": _float_field(state, "balance")})
            if submitted:
                continue
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
            orders.append(
                {
                    "order_id": order_id,
                    "event_type": "ORDER_SUBMITTED",
                    "side": side,
                    "price": price,
                    "quantity": quantity,
                    "submit_return_code": submit_ret,
                    "response_return_code": response_ret,
                }
            )
            active_orders = hbt.orders(0)
            api_calls.append("HashMapMarketDepthBacktest.orders")
            order_obj = active_orders.get(order_id) if isinstance(active_orders, Mapping) else None
            order_snapshot = _order_snapshot(order_obj)
            if order_snapshot:
                orders.append({"order_id": order_id, "event_type": "ORDER_STATE", **order_snapshot})
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
        if submitted and float(order_snapshot.get("leaves_qty") or 0.0) > 0 and hasattr(hbt, "cancel"):
            cancel_ret = int(hbt.cancel(0, order_id, False))
            api_calls.append("HashMapMarketDepthBacktest.cancel")
            hbt.wait_order_response(0, order_id, interval_ns)
            api_calls.append("HashMapMarketDepthBacktest.wait_order_response")
            orders.append({"order_id": order_id, "event_type": "ORDER_CANCEL_REQUESTED", "cancel_return_code": cancel_ret})
        _maybe_call(hbt, "clear_inactive_orders", 0)
        final_state = _state_values(hbt)
    except Exception as exc:
        reason = f"hftbacktest_run_failed:{type(exc).__name__}:{exc}"
        return _not_run_replay(reason, config), [reason]

    reasons: list[str] = []
    if not submitted:
        reasons.append("strategy_submitted_no_orders")
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
    filled_orders = 1 if fills_count else 0
    fill_rate = filled_orders / orders_submitted if orders_submitted else 0.0
    replay = {
        "schema_version": "hft3_hftbacktest_only_official_replay_v1",
        "official_hftbacktest_replay_status": "pass" if not reasons else "fail",
        "api_calls": list(dict.fromkeys(api_calls)),
        "run_id": config.run_id,
        "symbol": config.symbol,
        "contract": config.contract,
        "event_id": config.event_id,
        "strategy_id": config.strategy_id,
        "strategy_params": params,
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
        "unfilled_count": 1 if submitted and not fills else 0,
        "fill_rate": fill_rate,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "total_fees": _float_field(final_state, "fee"),
        "fail_closed_reasons": reasons,
    }
    return replay, reasons


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
    return {
        "schema_version": "hft3_hftbacktest_only_strategy_config_v1",
        "strategy_id": config.strategy_id,
        "strategy_params": dict(config.strategy_params),
        "loop_semantics": "while hbt.elapse(interval_ns) == 0 when available",
        "pit_safe_signal_status": "strategy_params_only_minimal_slice",
    }


def _normalized_input_manifest(config: HftBacktestOnlyRunConfig, validation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "hft3_hftbacktest_only_normalized_input_manifest_v1",
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
        "symbol": config.symbol,
        "contract": config.contract,
        "event_id": config.event_id,
        "strategy_id": config.strategy_id,
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
    np.savez_compressed(
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
            path.with_suffix(path.suffix + ".fallback.json"),
            {"status": "fallback_jsonl", "fallback": str(fallback), "reason": f"{type(exc).__name__}:{exc}"},
        )


def _result(config: HftBacktestOnlyRunConfig, out_dir: Path, status: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "run_id": config.run_id,
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
        "symbol": config.symbol,
        "contract": config.contract,
        "event_id": config.event_id,
        "strategy_id": config.strategy_id,
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
        "gross_pnl": None,
        "net_pnl": None,
        "fail_closed_reasons": [reason],
    }


def _write_audit(out_dir: Path, status: str, reasons: list[str]) -> None:
    lines = [
        "# HftBacktest-Only Run Audit",
        "",
        f"- status: {status}",
        f"- plan: {PLAN_PATH}",
        f"- vectorbt_dependency: forbidden_active_path",
        f"- fail_closed_reasons: {', '.join(reasons) if reasons else 'none'}",
        "",
    ]
    (Path(out_dir) / "audit.md").write_text("\n".join(lines), encoding="utf-8")


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
    l2_types = {constants["DEPTH_EVENT"], constants.get("TRADE_EVENT", 2)}
    has_l3 = bool(ev_types & l3_types)
    has_l2 = bool(ev_types & l2_types)
    has_unknown = bool(ev_types - l3_types - l2_types)
    if has_unknown:
        return "unknown_rejected"
    if has_l2 and has_l3:
        return "mixed_rejected"
    return "l3_mbo" if has_l3 else "l2_mbp"


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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
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
