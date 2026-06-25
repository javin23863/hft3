#!/usr/bin/env python3
"""Run a vectorized full-supported-feature RL GPU campaign from fs_v1 stores.

This is a paid-host execution helper for the current pre-PPO deep-Q proxy lane.
It preserves the existing PIT boundary and reward formula while avoiding the
JSONL row materialization bottleneck used by the smoke-oriented builder.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(_REPO_ROOT / "packages"))
sys.path.insert(0, str(_REPO_ROOT))

from data_system.src.feature_store import feature_index_hash, load_manifest, load_store  # noqa: E402
from features_engine.src.features.feature_index import FeatureIndex  # noqa: E402
from research_pipeline.rl_campaign_budget import plan_rl_campaign_budget  # noqa: E402
from research_pipeline.rl_training_data import (  # noqa: E402
    VIX_OPTIONS_DEFAULT_RL_FEATURES,
    VIX_OPTIONS_DEFAULT_RL_REWARD_COLUMN,
    VIX_OPTIONS_RL_FEATURE_STORE_SCHEMA_VERSION,
    VIX_OPTIONS_RL_REWARD_COST_MODEL,
    VIX_OPTIONS_RL_REWARD_UNITS,
    VIX_OPTIONS_SUPPORTED_RL_FEATURES,
    vix_options_feature_schema_hash,
)

SUPPORTED_FEATURES = (
    "order_book_imbalance",
    "queue_imbalance",
    "order_flow_imbalance",
    "micro_price",
    "spread",
)
VIX_OPTIONS_SYMBOL = "VIX.OPT"
VIX_OPTIONS_SOURCE_FAMILY = "vix_options_clue"
FS_V1_ACTION_SPACE = ("hold", "enter_long", "enter_short")
VIX_OPTIONS_ACTION_SPACE = ("hold", "clue_up", "clue_down")
SCHEMA_VERSION = "hft3_rl_gpu_vectorized_campaign_v1"
PROMOTION_STATUS = "blocked_downstream_validation_required"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--feature-store-root", type=Path, required=True)
    parser.add_argument(
        "--feature-manifest",
        type=Path,
        default=None,
        help="Optional manifest JSONL path; defaults to <feature-store-root>/feature_manifest.jsonl.",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--budget-plan", type=Path, required=True)
    parser.add_argument("--budget-plan-sha256", required=True)
    parser.add_argument("--host-kind", choices=["vastai"], required=True)
    parser.add_argument("--gpu-host", required=True)
    parser.add_argument("--expected-duration-minutes", type=float, required=True)
    parser.add_argument("--stop-rule", required=True)
    parser.add_argument("--operator-approval", required=True)
    parser.add_argument("--allow-pre-ppo-proxy", action="store_true")
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--feature", action="append", default=None)
    parser.add_argument("--reward-horizon-rows", type=int, default=1)
    parser.add_argument("--reward-horizon-ns", type=int, default=None)
    parser.add_argument("--feature-latency-ms", type=float, default=1.0)
    parser.add_argument("--spread-cost-multiplier", type=float, default=0.05)
    parser.add_argument("--vix-reward-column", default=VIX_OPTIONS_DEFAULT_RL_REWARD_COLUMN)
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--eval-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-events", type=int, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan_sha = _sha256_file(args.budget_plan)
    if plan_sha.lower() != args.budget_plan_sha256.lower():
        raise SystemExit("budget plan sha256 mismatch")
    launch_gate = _require_launch_gate(args=args, argv=argv, budget_plan_sha256=plan_sha)
    features = _resolve_campaign_features(symbols=args.symbol, requested=args.feature)
    unsupported = _unsupported_campaign_features(symbols=args.symbol, features=features)
    if unsupported:
        raise SystemExit("unsupported RL campaign features: " + ", ".join(unsupported))
    source_family = _campaign_source_family(args.symbol)
    _require_campaign_manifest_allowed(source_family=source_family, feature_manifest=args.feature_manifest)
    plan = json.loads(args.budget_plan.read_text(encoding="utf-8"))

    import torch

    if not torch.cuda.is_available():
        raise SystemExit("CUDA runtime unavailable")

    started = time.perf_counter()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = _load_campaign_manifest(Path(args.feature_store_root), args.feature_manifest)
    _require_budget_ready(plan, features, manifest, source_family=source_family)
    all_results: list[dict[str, Any]] = []
    for symbol in args.symbol:
        try:
            result = _run_symbol(
                args=args,
                torch=torch,
                manifest=manifest,
                symbol=symbol,
                features=features,
                output_root=output_root / _safe(symbol),
                budget_plan_sha256=plan_sha,
            )
        except Exception as exc:
            result = _symbol_failure_receipt(symbol=symbol, features=features, exc=exc, budget_plan_sha256=plan_sha)
        all_results.append(result)

    failure_count = sum(1 for item in all_results if item.get("status") != "trained_research_only")
    event_inventory_truncated = any(bool(item.get("event_inventory_truncated")) for item in all_results)
    status = _campaign_status(
        failure_count=failure_count,
        event_inventory_truncated=event_inventory_truncated,
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "promotion_status": PROMOTION_STATUS,
        "promotable": False,
        "algorithm_status": "pre_ppo_deep_q_proxy_not_rl5_completion",
        "host_kind": args.host_kind,
        "gpu_host": args.gpu_host,
        "device": "cuda",
        "cuda_device_name": str(torch.cuda.get_device_name(0)),
        "feature_store_root": str(args.feature_store_root),
        "feature_manifest": str(args.feature_manifest or Path(args.feature_store_root) / "feature_manifest.jsonl"),
        "output_root": str(output_root),
        "symbols": list(args.symbol),
        "feature_names": list(features),
        "source_family": source_family,
        "vix_reward_column": (
            str(args.vix_reward_column) if source_family == VIX_OPTIONS_SOURCE_FAMILY else None
        ),
        "max_events": args.max_events,
        "event_inventory_truncated": event_inventory_truncated,
        "launch_gate": launch_gate,
        "budget_plan": str(args.budget_plan),
        "budget_plan_sha256": plan_sha,
        "failure_count": failure_count,
        "duration_seconds": round(time.perf_counter() - started, 6),
        "symbol_results": all_results,
        "decision_time_boundary": (
            _decision_time_boundary(source_family)
        ),
        "receipts": {
            "feature_registry": "features_engine.feature_sets.MICROSTRUCTURE_FEATURE_RECEIPTS",
            "vix_options_feature_registry": "features_engine.feature_sets.VIX_OPTIONS_RL_FEATURE_RECEIPTS",
            "feature_store": "data_system.src.feature_store",
            "rl_execution": "https://www.cis.upenn.edu/~mkearns/KN.html",
        },
    }
    _write_json(output_root / "rl_gpu_vectorized_campaign_summary.json", summary)
    return 0 if status == "completed_research_only" else 2


def _run_symbol(
    *,
    args: argparse.Namespace,
    torch: Any,
    manifest: Mapping[Any, Mapping[str, Any]],
    symbol: str,
    features: Sequence[str],
    output_root: Path,
    budget_plan_sha256: str,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    symbol_started = time.perf_counter()
    build_started = time.perf_counter()
    arrays = _build_symbol_arrays(
        feature_store_root=Path(args.feature_store_root),
        manifest=manifest,
        symbol=symbol,
        features=features,
        reward_horizon_rows=_positive_int(args.reward_horizon_rows, "reward_horizon_rows"),
        reward_horizon_ns=_optional_positive_int(args.reward_horizon_ns, "reward_horizon_ns"),
        feature_latency_ns=int(round(_finite_non_negative(args.feature_latency_ms, "feature_latency_ms") * 1_000_000)),
        spread_cost_multiplier=_finite_non_negative(args.spread_cost_multiplier, "spread_cost_multiplier"),
        max_events=args.max_events,
        vix_reward_column=str(args.vix_reward_column),
    )
    build_seconds = time.perf_counter() - build_started
    train_started = time.perf_counter()
    train = _train_arrays(
        torch=torch,
        x=arrays["x"],
        rewards=arrays["reward"],
        features=features,
        output_root=output_root,
        seed=int(args.seed),
        steps=_positive_int(args.steps, "steps"),
        batch_size=_positive_int(args.batch_size, "batch_size"),
        hidden_dim=_positive_int(args.hidden_dim, "hidden_dim"),
        learning_rate=_positive_float(args.learning_rate, "learning_rate"),
        eval_fraction=_eval_fraction(args.eval_fraction),
        source_row_count=int(arrays["source_row_count"]),
        action_space=arrays["action_space"],
    )
    train_seconds = time.perf_counter() - train_started
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "trained_research_only",
        "promotion_status": PROMOTION_STATUS,
        "promotable": False,
        "algorithm_status": "pre_ppo_deep_q_proxy_not_rl5_completion",
        "symbol": symbol,
        "feature_names": list(features),
        "source_family": arrays["source_family"],
        "row_count": int(arrays["x"].shape[0]),
        "source_row_count": int(arrays["source_row_count"]),
        "skipped_row_count": int(arrays["skipped_row_count"]),
        "event_count": int(arrays["event_count"]),
        "manifest_event_count": int(arrays["manifest_event_count"]),
        "max_events": arrays["max_events"],
        "events_truncated_count": int(arrays["events_truncated_count"]),
        "event_inventory_truncated": bool(arrays["events_truncated_count"]),
        "budget_plan_sha256": budget_plan_sha256,
        "build_seconds": round(build_seconds, 6),
        "build_rows_per_second": round(float(arrays["x"].shape[0]) / build_seconds, 6) if build_seconds else None,
        "train_seconds": round(train_seconds, 6),
        "duration_seconds": round(time.perf_counter() - symbol_started, 6),
        "row_materialization": "in_memory_numpy_arrays_no_jsonl",
        "decision_time_boundary": arrays["decision_time_boundary"],
        "reward_rule": arrays["reward_rule"],
        "sources": arrays["sources"],
        "training_summary": train,
    }
    _write_json(output_root / "rl_gpu_symbol_summary.json", result)
    return result


def _symbol_failure_receipt(
    *,
    symbol: str,
    features: Sequence[str],
    exc: Exception,
    budget_plan_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "failed",
        "promotion_status": PROMOTION_STATUS,
        "promotable": False,
        "algorithm_status": "pre_ppo_deep_q_proxy_not_rl5_completion",
        "symbol": symbol,
        "feature_names": list(features),
        "budget_plan_sha256": budget_plan_sha256,
        "failure_reasons": [str(exc)],
        "row_materialization": "in_memory_numpy_arrays_no_jsonl",
    }


def _campaign_status(*, failure_count: int, event_inventory_truncated: bool) -> str:
    if failure_count:
        return "partial_failed"
    if event_inventory_truncated:
        return "partial_event_inventory_truncated"
    return "completed_research_only"


def _resolve_campaign_features(*, symbols: Sequence[str], requested: Sequence[str] | None) -> tuple[str, ...]:
    source_family = _campaign_source_family(symbols)
    if source_family == "mixed":
        raise SystemExit("mixed fs_v1 target and VIX.OPT clue campaigns must run separately")
    if requested:
        return tuple(str(feature).strip() for feature in requested if str(feature).strip())
    if source_family == VIX_OPTIONS_SOURCE_FAMILY:
        return tuple(VIX_OPTIONS_DEFAULT_RL_FEATURES)
    return tuple(SUPPORTED_FEATURES)


def _unsupported_campaign_features(*, symbols: Sequence[str], features: Sequence[str]) -> list[str]:
    if _campaign_source_family(symbols) == VIX_OPTIONS_SOURCE_FAMILY:
        return sorted(set(features) - set(VIX_OPTIONS_SUPPORTED_RL_FEATURES))
    return sorted(set(features) - set(SUPPORTED_FEATURES))


def _campaign_source_family(symbols: Sequence[str]) -> str:
    has_vix = any(_is_vix_options_symbol(symbol) for symbol in symbols)
    has_target = any(not _is_vix_options_symbol(symbol) for symbol in symbols)
    if has_vix and has_target:
        return "mixed"
    if has_vix:
        return VIX_OPTIONS_SOURCE_FAMILY
    return "fs_v1_target"


def _is_vix_options_symbol(symbol: str) -> bool:
    return str(symbol).upper() == VIX_OPTIONS_SYMBOL


def _decision_time_boundary(source_family: str) -> str:
    if source_family == VIX_OPTIONS_SOURCE_FAMILY:
        return (
            "VIX options clue features use source_timestamp_ns <= decision timestamp minus feature_latency_ns; "
            "future VIX clue delta is used only as the offline reward label and makes no execution claim"
        )
    return (
        "features use source_timestamp_ns <= decision timestamp minus feature_latency_ns; "
        "future mid-price is used only as the offline reward label"
    )


def _load_campaign_manifest(feature_store_root: Path, feature_manifest: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if feature_manifest is None:
        return load_manifest(feature_store_root)
    path = Path(feature_manifest)
    if not path.is_file():
        raise FileNotFoundError(f"feature manifest not found: {path}")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if not isinstance(value, Mapping):
                raise ValueError(f"feature manifest row {line_no} must be an object")
            symbol = str(value.get("symbol") or "").strip()
            event_id = str(value.get("event_id") or "").strip()
            if not symbol or not event_id:
                raise ValueError(f"feature manifest row {line_no} missing symbol/event_id")
            result[(symbol, event_id)] = dict(value)
    return result


def _require_campaign_manifest_allowed(*, source_family: str, feature_manifest: Path | None) -> None:
    if source_family != VIX_OPTIONS_SOURCE_FAMILY and feature_manifest is not None:
        raise SystemExit("custom --feature-manifest is reserved for VIX.OPT clue campaigns")


def _require_vix_options_features(features: Sequence[str]) -> None:
    unsupported = sorted(set(features) - set(VIX_OPTIONS_SUPPORTED_RL_FEATURES))
    if unsupported:
        raise ValueError("unsupported VIX options RL features: " + ", ".join(unsupported))


def _require_vix_options_reward_column(value: str) -> str:
    reward_column = str(value).strip()
    if reward_column not in VIX_OPTIONS_SUPPORTED_RL_FEATURES:
        raise ValueError(f"unsupported VIX options RL reward column: {reward_column!r}")
    return reward_column


def _build_symbol_arrays(
    *,
    feature_store_root: Path,
    manifest: Mapping[Any, Mapping[str, Any]],
    symbol: str,
    features: Sequence[str],
    reward_horizon_rows: int,
    reward_horizon_ns: int | None,
    feature_latency_ns: int,
    spread_cost_multiplier: float,
    max_events: int | None,
    vix_reward_column: str,
) -> dict[str, Any]:
    all_records = [
        (str(record.get("event_id") or key[1]), record)
        for key, record in manifest.items()
        if str(record.get("symbol") or key[0]) == symbol
    ]
    all_records.sort(key=lambda item: item[0])
    manifest_event_count = len(all_records)
    records = all_records
    if max_events is not None:
        records = records[: _positive_int(max_events, "max_events")]
    if not records:
        raise ValueError(f"symbol not found in feature manifest: {symbol}")
    if _is_vix_options_symbol(symbol):
        return _build_vix_options_symbol_arrays(
            feature_store_root=feature_store_root,
            records=records,
            manifest_event_count=manifest_event_count,
            symbol=symbol,
            features=features,
            reward_horizon_rows=reward_horizon_rows,
            reward_horizon_ns=reward_horizon_ns,
            feature_latency_ns=feature_latency_ns,
            max_events=max_events,
            events_truncated_count=max(0, manifest_event_count - len(records)),
            vix_reward_column=vix_reward_column,
        )

    x_parts: list[np.ndarray] = []
    reward_parts: list[np.ndarray] = []
    ts_parts: list[np.ndarray] = []
    source_rows = 0
    skipped_rows = 0
    sources: list[dict[str, Any]] = []
    for event_id, record in records:
        store_path = _store_path(feature_store_root, record)
        store = load_store(store_path)
        _require_store_authority(record=record, store=store, store_path=store_path)
        ts = np.asarray(store["ts"], dtype=np.int64)
        X = np.asarray(store["X"], dtype=np.float64)
        best_bid = np.asarray(store["best_bid"], dtype=np.float64)
        best_ask = np.asarray(store["best_ask"], dtype=np.float64)
        unit = _arrays_from_store(
            ts=ts,
            X=X,
            best_bid=best_bid,
            best_ask=best_ask,
            symbol=symbol,
            event_id=event_id,
            features=features,
            reward_horizon_rows=reward_horizon_rows,
            reward_horizon_ns=reward_horizon_ns,
            feature_latency_ns=feature_latency_ns,
            spread_cost_multiplier=spread_cost_multiplier,
        )
        source_rows += int(unit["source_rows"])
        skipped_rows += int(unit["skipped_rows"])
        if unit["x"].size:
            x_parts.append(unit["x"])
            reward_parts.append(unit["reward"])
            ts_parts.append(unit["timestamp_ns"])
        sources.append(
            {
                "symbol": symbol,
                "event_id": event_id,
                "store_path": str(store_path),
                "store_sha256": _sha256_file(store_path),
                "manifest_record": dict(record),
                "row_summary": {
                    "source_rows": int(unit["source_rows"]),
                    "built_rows": int(unit["x"].shape[0]),
                    "skipped_rows": int(unit["skipped_rows"]),
                },
            }
        )
    if not x_parts:
        raise ValueError(f"no PIT-valid rows built for {symbol}")
    x = np.concatenate(x_parts, axis=0).astype(np.float32, copy=False)
    reward = np.concatenate(reward_parts, axis=0).astype(np.float32, copy=False)
    timestamp_ns = np.concatenate(ts_parts, axis=0)
    order = np.argsort(timestamp_ns, kind="stable")
    return {
        "x": x[order],
        "reward": reward[order],
        "timestamp_ns": timestamp_ns[order],
        "source_row_count": source_rows,
        "skipped_row_count": skipped_rows,
        "event_count": len(records),
        "manifest_event_count": manifest_event_count,
        "max_events": max_events,
        "events_truncated_count": max(0, manifest_event_count - len(records)),
        "sources": sources,
        "source_family": "fs_v1_target",
        "action_space": FS_V1_ACTION_SPACE,
        "reward_rule": {
            "name": "future_mid_minus_decision_mid_minus_spread_cost",
            "reward_units": "price_points",
            "cost_model": "future_mid_delta_minus_spread_multiplier",
            "label_only": True,
        },
        "decision_time_boundary": (
            "source feature index is vectorized searchsorted(ts, timestamp_ns - feature_latency_ns); "
            "source_row_index never exceeds decision_row_index; future mid-price is label-only"
        ),
    }


def _build_vix_options_symbol_arrays(
    *,
    feature_store_root: Path,
    records: Sequence[tuple[str, Mapping[str, Any]]],
    manifest_event_count: int,
    symbol: str,
    features: Sequence[str],
    reward_horizon_rows: int,
    reward_horizon_ns: int | None,
    feature_latency_ns: int,
    max_events: int | None,
    events_truncated_count: int,
    vix_reward_column: str,
) -> dict[str, Any]:
    _require_vix_options_features(features)
    reward_column = _require_vix_options_reward_column(vix_reward_column)
    x_parts: list[np.ndarray] = []
    reward_parts: list[np.ndarray] = []
    ts_parts: list[np.ndarray] = []
    source_rows = 0
    skipped_rows = 0
    sources: list[dict[str, Any]] = []
    for event_id, record in records:
        store_path = _store_path(feature_store_root, record)
        store_sha256 = _sha256_file(store_path)
        store = _load_vix_options_store(store_path)
        _require_vix_options_store_authority(
            record=record,
            store=store,
            store_path=store_path,
            store_sha256=store_sha256,
            features=features,
            reward_column=reward_column,
        )
        unit = _arrays_from_vix_options_store(
            store=store,
            features=features,
            reward_horizon_rows=reward_horizon_rows,
            reward_horizon_ns=reward_horizon_ns,
            feature_latency_ns=feature_latency_ns,
            reward_column=reward_column,
        )
        source_rows += int(unit["source_rows"])
        skipped_rows += int(unit["skipped_rows"])
        if unit["x"].size:
            x_parts.append(unit["x"])
            reward_parts.append(unit["reward"])
            ts_parts.append(unit["timestamp_ns"])
        sources.append(
            {
                "symbol": symbol,
                "event_id": event_id,
                "store_path": str(store_path),
                "store_sha256": store_sha256,
                "source_family": VIX_OPTIONS_SOURCE_FAMILY,
                "manifest_record": dict(record),
                "row_summary": {
                    "source_rows": int(unit["source_rows"]),
                    "built_rows": int(unit["x"].shape[0]),
                    "skipped_rows": int(unit["skipped_rows"]),
                },
            }
        )
    if not x_parts:
        raise ValueError(f"no PIT-valid VIX options clue rows built for {symbol}")
    x = np.concatenate(x_parts, axis=0).astype(np.float32, copy=False)
    reward = np.concatenate(reward_parts, axis=0).astype(np.float32, copy=False)
    timestamp_ns = np.concatenate(ts_parts, axis=0)
    order = np.argsort(timestamp_ns, kind="stable")
    return {
        "x": x[order],
        "reward": reward[order],
        "timestamp_ns": timestamp_ns[order],
        "source_row_count": source_rows,
        "skipped_row_count": skipped_rows,
        "event_count": len(records),
        "manifest_event_count": manifest_event_count,
        "max_events": max_events,
        "events_truncated_count": events_truncated_count,
        "sources": sources,
        "source_family": VIX_OPTIONS_SOURCE_FAMILY,
        "action_space": VIX_OPTIONS_ACTION_SPACE,
        "reward_rule": {
            "name": "future_vix_options_clue_delta",
            "reward_column": reward_column,
            "reward_units": VIX_OPTIONS_RL_REWARD_UNITS,
            "cost_model": VIX_OPTIONS_RL_REWARD_COST_MODEL,
            "label_only": True,
            "execution_claim": False,
        },
        "decision_time_boundary": (
            "VIX options clue features use source_timestamp_ns <= decision timestamp minus feature_latency_ns; "
            "future VIX clue delta is label-only and not an executable instrument reward"
        ),
    }


def _arrays_from_store(
    *,
    ts: np.ndarray,
    X: np.ndarray,
    best_bid: np.ndarray,
    best_ask: np.ndarray,
    symbol: str,
    event_id: str,
    features: Sequence[str],
    reward_horizon_rows: int,
    reward_horizon_ns: int | None,
    feature_latency_ns: int,
    spread_cost_multiplier: float,
) -> dict[str, Any]:
    del symbol, event_id
    n = len(ts)
    if n and not np.all(np.diff(ts) >= 0):
        raise ValueError("feature store ts not monotonic")
    if n < 2 or X.ndim != 2 or X.shape[0] != n:
        return {"x": np.empty((0, len(features)), dtype=np.float32), "reward": np.empty(0), "timestamp_ns": np.empty(0), "source_rows": n, "skipped_rows": n}
    decision_idx = np.arange(n, dtype=np.int64)
    source_idx = np.searchsorted(ts, ts - int(feature_latency_ns), side="right").astype(np.int64) - 1
    source_idx = np.minimum(source_idx, decision_idx)
    if reward_horizon_ns is None:
        future_idx = decision_idx + int(reward_horizon_rows)
    else:
        future_idx = np.searchsorted(ts, ts + int(reward_horizon_ns), side="left").astype(np.int64)
    valid = (source_idx >= 0) & (future_idx >= 0) & (future_idx < n)
    decision_mid = _mid_array(X, best_bid, best_ask, decision_idx)
    future_mid = _mid_array(X, best_bid, best_ask, np.clip(future_idx, 0, n - 1))
    spread = _spread_array(X, best_bid, best_ask, decision_idx)
    valid &= np.isfinite(decision_mid) & np.isfinite(future_mid) & np.isfinite(spread)
    reward = future_mid - decision_mid - float(spread_cost_multiplier) * spread
    valid &= np.isfinite(reward)

    columns: list[np.ndarray] = []
    for feature in features:
        column = _feature_array(feature, X, best_bid, best_ask, source_idx)
        columns.append(column)
        valid &= np.isfinite(column)
    x = np.column_stack(columns)
    valid_count = int(np.count_nonzero(valid))
    skipped = int(n - valid_count)
    return {
        "x": x[valid].astype(np.float32, copy=False),
        "reward": reward[valid].astype(np.float32, copy=False),
        "timestamp_ns": ts[valid],
        "source_rows": n,
        "skipped_rows": skipped,
    }


def _arrays_from_vix_options_store(
    *,
    store: Mapping[str, Any],
    features: Sequence[str],
    reward_horizon_rows: int,
    reward_horizon_ns: int | None,
    feature_latency_ns: int,
    reward_column: str,
) -> dict[str, Any]:
    ts = np.asarray(store.get("ts"), dtype=np.int64)
    n = len(ts)
    if n and not np.all(np.diff(ts) >= 0):
        raise ValueError("VIX options clue store ts not monotonic")
    if n < 2:
        return {
            "x": np.empty((0, len(features)), dtype=np.float32),
            "reward": np.empty(0),
            "timestamp_ns": np.empty(0),
            "source_rows": n,
            "skipped_rows": n,
        }
    decision_idx = np.arange(n, dtype=np.int64)
    source_idx = np.searchsorted(ts, ts - int(feature_latency_ns), side="right").astype(np.int64) - 1
    source_idx = np.minimum(source_idx, decision_idx)
    if reward_horizon_ns is None:
        future_idx = decision_idx + int(reward_horizon_rows)
    else:
        future_idx = np.searchsorted(ts, ts + int(reward_horizon_ns), side="left").astype(np.int64)
    valid = (source_idx >= 0) & (future_idx >= 0) & (future_idx < n)
    ts_event_raw = _vix_required_timestamp_array(store, "ts_event_raw", Path("<in-memory-vix-options-store>"))
    ts_recv_raw = _vix_required_timestamp_array(store, "ts_recv_raw", Path("<in-memory-vix-options-store>"))
    row_causal = (ts_recv_raw <= ts) & (ts_event_raw <= ts_recv_raw)
    source_causal = np.zeros(n, dtype=bool)
    source_valid = source_idx >= 0
    source_causal[source_valid] = row_causal[source_idx[source_valid]]
    valid &= row_causal & source_causal
    decision_clue = np.full(n, np.nan, dtype=np.float64)
    future_clue = np.full(n, np.nan, dtype=np.float64)
    reward_idx = valid.copy()
    if np.any(reward_idx):
        decision_clue[reward_idx] = _vix_options_array(store, reward_column, decision_idx[reward_idx])
        future_clue[reward_idx] = _vix_options_array(store, reward_column, future_idx[reward_idx])
    reward = future_clue - decision_clue
    valid &= np.isfinite(decision_clue) & np.isfinite(future_clue) & np.isfinite(reward)

    columns: list[np.ndarray] = []
    for feature in features:
        column = np.full(n, np.nan, dtype=np.float64)
        feature_idx = valid.copy()
        if np.any(feature_idx):
            column[feature_idx] = _vix_options_array(store, feature, source_idx[feature_idx])
        columns.append(column)
        valid &= np.isfinite(column)
    x = np.column_stack(columns)
    valid_count = int(np.count_nonzero(valid))
    skipped = int(n - valid_count)
    return {
        "x": x[valid].astype(np.float32, copy=False),
        "reward": reward[valid].astype(np.float32, copy=False),
        "timestamp_ns": ts[valid],
        "source_rows": n,
        "skipped_rows": skipped,
    }


def _train_arrays(
    *,
    torch: Any,
    x: np.ndarray,
    rewards: np.ndarray,
    features: Sequence[str],
    output_root: Path,
    seed: int,
    steps: int,
    batch_size: int,
    hidden_dim: int,
    learning_rate: float,
    eval_fraction: float,
    source_row_count: int,
    action_space: Sequence[str],
) -> dict[str, Any]:
    if x.shape[0] < 3:
        raise ValueError("deep RL vectorized training requires at least three rows")
    actions = [str(action) for action in action_space]
    if len(actions) != 3 or any(not action for action in actions):
        raise ValueError("action_space must contain exactly three action labels")
    torch.manual_seed(seed)
    device = torch.device("cuda")
    x_raw = torch.from_numpy(x).to(device=device, dtype=torch.float32)
    reward_t = torch.from_numpy(rewards).to(device=device, dtype=torch.float32)
    target_q = torch.stack([torch.zeros_like(reward_t), reward_t, -reward_t], dim=1)
    train_rows = max(1, int(x.shape[0] * (1.0 - eval_fraction)))
    if train_rows >= x.shape[0]:
        train_rows = x.shape[0] - 1
    mean = torch.mean(x_raw[:train_rows], dim=0)
    std = torch.std(x_raw[:train_rows], dim=0, unbiased=False)
    std = torch.where(std <= 1e-12, torch.ones_like(std), std)
    x_all = (x_raw - mean) / std
    model = torch.nn.Sequential(
        torch.nn.Linear(len(features), hidden_dim),
        torch.nn.ReLU(),
        torch.nn.Linear(hidden_dim, 3),
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    criterion = torch.nn.MSELoss()
    generator = torch.Generator(device=device)
    generator.manual_seed(seed)
    losses: list[float] = []
    for _ in range(steps):
        if train_rows > batch_size:
            idx = torch.randint(0, train_rows, (batch_size,), generator=generator, device=device)
            x_batch = x_all[idx]
            y_batch = target_q[idx]
        else:
            x_batch = x_all[:train_rows]
            y_batch = target_q[:train_rows]
        optimizer.zero_grad(set_to_none=True)
        loss = criterion(model(x_batch), y_batch)
        loss.backward()
        optimizer.step()
        losses.append(float(loss.detach().cpu().item()))
    eval_rows = x.shape[0] - train_rows
    with torch.no_grad():
        eval_pred = model(x_all[train_rows:])
        eval_loss = float(criterion(eval_pred, target_q[train_rows:]).detach().cpu().item())
        action_ids = torch.argmax(eval_pred, dim=1).detach().cpu().numpy()
    checkpoint_path = output_root / "deep_rl_policy_checkpoint.pt"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "schema_version": "hft3_deep_rl_policy_checkpoint_v1",
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "feature_names": list(features),
            "normalizer": {
                name: {"mean": float(mean[i].detach().cpu()), "std": float(std[i].detach().cpu())}
                for i, name in enumerate(features)
            },
            "seed": seed,
            "steps": steps,
            "action_space": actions,
        },
        checkpoint_path,
    )
    return {
        "row_count": int(x.shape[0]),
        "source_row_count": int(source_row_count),
        "algorithm": "offline_deep_q_proxy_mse_vectorized",
        "chronology_status": "non_decreasing_timestamp_sort_applied",
        "train_eval_split": {
            "method": "chronological_prefix_train_suffix_eval",
            "train_rows": int(train_rows),
            "eval_rows": int(eval_rows),
            "random_split": False,
        },
        "training_budget": {
            "steps": steps,
            "batch_size": batch_size,
            "hidden_dim": hidden_dim,
            "learning_rate": learning_rate,
            "budget_exhausted": False,
        },
        "loss_start": losses[0],
        "loss_end": losses[-1],
        "eval_mse": eval_loss,
        "action_space": actions,
        "eval_action_counts": {
            actions[0]: int(np.count_nonzero(action_ids == 0)),
            actions[1]: int(np.count_nonzero(action_ids == 1)),
            actions[2]: int(np.count_nonzero(action_ids == 2)),
        },
        "checkpoint": {
            "path": str(checkpoint_path),
            "sha256": _sha256_file(checkpoint_path),
        },
    }


def _feature_array(feature: str, X: np.ndarray, best_bid: np.ndarray, best_ask: np.ndarray, idx: np.ndarray) -> np.ndarray:
    safe_idx = np.clip(idx, 0, X.shape[0] - 1)
    if feature == "order_flow_imbalance":
        return _slot(X, safe_idx, FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE)
    if feature == "spread":
        return _spread_array(X, best_bid, best_ask, safe_idx)
    if feature == "queue_imbalance":
        return _safe_imbalance(_slot(X, safe_idx, FeatureIndex.TOP_1_DEPTH_BID), _slot(X, safe_idx, FeatureIndex.TOP_1_DEPTH_ASK))
    if feature == "order_book_imbalance":
        bid, ask = _depth_pair_array(X, safe_idx)
        return _safe_imbalance(bid, ask)
    if feature == "micro_price":
        return _micro_price_array(X, best_bid, best_ask, safe_idx)
    raise ValueError(f"unsupported feature {feature!r}")


def _slot(X: np.ndarray, idx: np.ndarray, slot: int) -> np.ndarray:
    slot_idx = int(slot)
    if slot_idx < 0 or slot_idx >= X.shape[1]:
        return np.zeros(idx.shape[0], dtype=np.float64)
    return X[idx, slot_idx].astype(np.float64, copy=False)


def _mid_array(X: np.ndarray, best_bid: np.ndarray, best_ask: np.ndarray, idx: np.ndarray) -> np.ndarray:
    mid = _slot(X, idx, FeatureIndex.MID_PRICE)
    bid = best_bid[idx]
    ask = best_ask[idx]
    fallback = np.where(
        np.isfinite(bid) & np.isfinite(ask) & (bid > 0.0) & (ask > 0.0),
        (bid + ask) / 2.0,
        np.nan,
    )
    return np.where(np.isfinite(mid) & (mid > 0.0), mid, fallback)


def _spread_array(X: np.ndarray, best_bid: np.ndarray, best_ask: np.ndarray, idx: np.ndarray) -> np.ndarray:
    spread = _slot(X, idx, FeatureIndex.SPREAD)
    bid = best_bid[idx]
    ask = best_ask[idx]
    fallback = np.where(
        np.isfinite(bid) & np.isfinite(ask) & (bid > 0.0) & (ask >= bid),
        ask - bid,
        np.nan,
    )
    return np.where(np.isfinite(spread) & (spread >= 0.0), spread, fallback)


def _micro_price_array(X: np.ndarray, best_bid: np.ndarray, best_ask: np.ndarray, idx: np.ndarray) -> np.ndarray:
    bid_qty = _slot(X, idx, FeatureIndex.TOP_1_DEPTH_BID)
    ask_qty = _slot(X, idx, FeatureIndex.TOP_1_DEPTH_ASK)
    denom = bid_qty + ask_qty
    mid = _mid_array(X, best_bid, best_ask, idx)
    bid = best_bid[idx]
    ask = best_ask[idx]
    raw = (bid * ask_qty + ask * bid_qty) / np.where(denom == 0.0, 1.0, denom)
    valid_quote = np.isfinite(bid) & np.isfinite(ask) & (bid > 0.0) & (ask > 0.0)
    return np.where(valid_quote & (denom > 0.0), raw, mid)


def _depth_pair_array(X: np.ndarray, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    bid = np.zeros(idx.shape[0], dtype=np.float64)
    ask = np.zeros(idx.shape[0], dtype=np.float64)
    filled = np.zeros(idx.shape[0], dtype=bool)
    for bid_slot, ask_slot in (
        (FeatureIndex.TOP_5_DEPTH_BID, FeatureIndex.TOP_5_DEPTH_ASK),
        (FeatureIndex.TOP_3_DEPTH_BID, FeatureIndex.TOP_3_DEPTH_ASK),
        (FeatureIndex.TOP_10_DEPTH_BID, FeatureIndex.TOP_10_DEPTH_ASK),
        (FeatureIndex.TOP_1_DEPTH_BID, FeatureIndex.TOP_1_DEPTH_ASK),
    ):
        cand_bid = _slot(X, idx, bid_slot)
        cand_ask = _slot(X, idx, ask_slot)
        use = (~filled) & ((cand_bid + cand_ask) > 0.0)
        bid[use] = cand_bid[use]
        ask[use] = cand_ask[use]
        filled |= use
    return bid, ask


def _safe_imbalance(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    denom = left + right
    out = np.zeros_like(denom, dtype=np.float64)
    return np.divide(left - right, denom, out=out, where=denom != 0.0)


def _vix_options_array(store: Mapping[str, Any], feature: str, idx: np.ndarray) -> np.ndarray:
    if feature not in VIX_OPTIONS_SUPPORTED_RL_FEATURES:
        raise ValueError(f"unsupported VIX options RL feature {feature!r}")
    if feature not in store:
        raise ValueError(f"VIX options clue store missing feature {feature!r}")
    raw = np.asarray(store[feature], dtype=np.float64)
    if raw.ndim != 1:
        raise ValueError(f"VIX options clue feature {feature!r} must be a 1D array")
    idx_array = np.asarray(idx, dtype=np.int64)
    if np.any(idx_array < 0) or np.any(idx_array >= raw.shape[0]):
        raise ValueError(f"VIX options clue feature {feature!r} index out of bounds")
    return raw[idx_array]


def _load_vix_options_store(path: Path) -> dict[str, Any]:
    with np.load(str(path), allow_pickle=False) as arch:
        result: dict[str, Any] = {}
        for key in arch.files:
            if key == "_attrs_json":
                continue
            arr = arch[key]
            if not np.issubdtype(arr.dtype, np.number):
                continue
            result[key] = arr.item() if arr.ndim == 0 else arr
    return result


def _store_path(root: Path, record: Mapping[str, Any]) -> Path:
    raw = str(record.get("store_path") or record.get("path") or "")
    rel = raw.replace("\\", "/")
    path = Path(rel)
    return path if path.is_absolute() else root / path


def _require_store_authority(*, record: Mapping[str, Any], store: Mapping[str, Any], store_path: Path) -> None:
    expected = feature_index_hash()
    stored_hash = str(store.get("feature_index_hash") or "")
    manifest_hash = str(record.get("feature_index_hash") or "")
    if stored_hash != expected:
        raise ValueError(
            f"feature store schema drift: stored={stored_hash!r} expected={expected!r} path={store_path}"
        )
    if manifest_hash and manifest_hash != expected:
        raise ValueError(
            f"feature manifest schema drift: stored={manifest_hash!r} expected={expected!r} path={store_path}"
        )
    ts = np.asarray(store.get("ts"), dtype=np.int64)
    if len(ts) and not np.all(np.diff(ts) >= 0):
        raise ValueError(f"feature store ts not monotonic: {store_path}")


def _require_vix_options_store_authority(
    *,
    record: Mapping[str, Any],
    store: Mapping[str, Any],
    store_path: Path,
    store_sha256: str,
    features: Sequence[str],
    reward_column: str,
) -> None:
    family = str(record.get("source_family") or record.get("feature_family") or "")
    if family != VIX_OPTIONS_SOURCE_FAMILY:
        raise ValueError(
            f"VIX options RL manifest row must declare source_family={VIX_OPTIONS_SOURCE_FAMILY!r}: {store_path}"
        )
    store_schema = str(record.get("store_schema_version") or "")
    if store_schema != VIX_OPTIONS_RL_FEATURE_STORE_SCHEMA_VERSION:
        raise ValueError(
            "VIX options RL manifest row store_schema_version mismatch: "
            f"stored={store_schema!r} expected={VIX_OPTIONS_RL_FEATURE_STORE_SCHEMA_VERSION!r} path={store_path}"
        )
    record_features = [str(name) for name in (record.get("feature_names") or []) if str(name)]
    requested_features = [str(name) for name in features]
    if record_features != requested_features:
        raise ValueError(
            "VIX options RL manifest row feature_names must match requested features exactly and in order: "
            f"manifest={record_features!r} requested={requested_features!r}"
        )
    record_reward = str(record.get("reward_column") or "")
    if record_reward != reward_column:
        raise ValueError(
            f"VIX options RL reward column mismatch: manifest={record_reward!r} requested={reward_column!r}"
        )
    manifest_schema_hash = str(record.get("feature_schema_hash") or "")
    expected_schema_hash = vix_options_feature_schema_hash(
        feature_names=record_features,
        reward_column=record_reward,
    )
    if manifest_schema_hash != expected_schema_hash:
        raise ValueError(
            "VIX options RL feature schema hash mismatch: "
            f"stored={manifest_schema_hash!r} expected={expected_schema_hash!r} path={store_path}"
        )
    expected_file_hash = str(record.get("store_sha256") or record.get("sha256") or record.get("content_hash") or "")
    if not expected_file_hash:
        raise ValueError(f"VIX options RL manifest row missing store_sha256/content_hash: {store_path}")
    if expected_file_hash.lower() != store_sha256.lower():
        raise ValueError(f"VIX options RL store sha256 mismatch: {store_path}")
    ts = np.asarray(store.get("ts"), dtype=np.int64)
    if len(ts) and not np.all(np.diff(ts) >= 0):
        raise ValueError(f"VIX options clue store ts not monotonic: {store_path}")
    ts_event_raw = _vix_required_timestamp_array(store, "ts_event_raw", store_path)
    ts_recv_raw = _vix_required_timestamp_array(store, "ts_recv_raw", store_path)
    if ts_event_raw.shape[0] != ts.shape[0] or ts_recv_raw.shape[0] != ts.shape[0]:
        raise ValueError(f"VIX options clue audit timestamp shape mismatch: {store_path}")
    if not np.all(np.diff(ts_recv_raw) >= 0):
        raise ValueError(f"VIX options clue ts_recv_raw not monotonic: {store_path}")
    causal_row_count = int(np.count_nonzero((ts_recv_raw <= ts) & (ts_event_raw <= ts_recv_raw)))
    if causal_row_count < 2:
        raise ValueError(f"VIX options clue store has fewer than two causal timestamp rows: {store_path}")
    for feature in sorted(set(features) | {reward_column}):
        values = np.asarray(store.get(feature), dtype=np.float64)
        if values.ndim != 1 or values.shape[0] != ts.shape[0]:
            raise ValueError(f"VIX options clue feature {feature!r} shape mismatch: {store_path}")


def _vix_required_timestamp_array(store: Mapping[str, Any], key: str, store_path: Path) -> np.ndarray:
    if key not in store:
        raise ValueError(f"VIX options clue store missing {key}: {store_path}")
    values = np.asarray(store[key], dtype=np.int64)
    if values.ndim != 1:
        raise ValueError(f"VIX options clue {key} must be a 1D array: {store_path}")
    return values


def _require_launch_gate(
    *,
    args: argparse.Namespace,
    argv: Sequence[str] | None,
    budget_plan_sha256: str,
    current_os_name: str | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    gpu_host = str(getattr(args, "gpu_host", "") or "").strip()
    host_kind = str(getattr(args, "host_kind", "") or "").strip().lower()
    stop_rule = str(getattr(args, "stop_rule", "") or "").strip()
    approval = str(getattr(args, "operator_approval", "") or "").strip()
    os_name = current_os_name or os.name

    if host_kind != "vastai":
        blockers.append("host_kind_must_be_vastai")
    if not gpu_host:
        blockers.append("gpu_host_missing")
    if os_name == "nt":
        blockers.append("vastai_campaign_refuses_windows_msi_host")
    try:
        expected_minutes = float(getattr(args, "expected_duration_minutes"))
        if not expected_minutes > 0.0:
            blockers.append("expected_duration_minutes_must_be_positive")
    except (TypeError, ValueError):
        expected_minutes = 0.0
        blockers.append("expected_duration_minutes_must_be_positive")
    if not stop_rule:
        blockers.append("stop_rule_missing")
    if approval != "approved-vastai-paid-rl-campaign":
        blockers.append("operator_approval_token_missing")
    if not bool(getattr(args, "allow_pre_ppo_proxy", False)):
        blockers.append("pre_ppo_proxy_ack_missing")

    gate = {
        "schema_version": "hft3_rl_gpu_vectorized_campaign_launch_gate_v1",
        "status": "ready_for_paid_gpu_campaign" if not blockers else "blocked",
        "failure_reasons": blockers,
        "host_kind": host_kind,
        "gpu_host": gpu_host,
        "device": "cuda",
        "expected_duration_minutes": expected_minutes,
        "stop_rule": stop_rule,
        "budget_plan_sha256": budget_plan_sha256,
        "operator_approval": approval == "approved-vastai-paid-rl-campaign",
        "local_os_name": os_name,
        "command": _command_line(argv),
        "algorithm": "offline_deep_q_proxy_mse_vectorized",
        "ppo_status": "deferred; this campaign is not RL-5 PPO completion",
        "decision_time_boundary": (
            "launch gate only; paid GPU work is allowed only on VastAI with an "
            "approved, bounded, non-local research-only command"
        ),
    }
    if blockers:
        raise ValueError("rl gpu campaign launch gate blocked: " + ", ".join(blockers))
    return gate


def _command_line(argv: Sequence[str] | None) -> str:
    if argv is None:
        return " ".join(sys.argv)
    return " ".join(["run_rl_gpu_campaign_npz_fast.py", *[str(value) for value in argv]])


def _require_budget_ready(
    plan: Mapping[str, Any],
    features: Sequence[str],
    manifest: Mapping[Any, Mapping[str, Any]],
    *,
    source_family: str,
) -> None:
    if plan.get("status") != "full_training_plan_ready":
        raise ValueError("budget plan is not full_training_plan_ready")
    planned_features = [str(name) for name in (plan.get("required_features") or [])]
    campaign_features = [str(name) for name in features]
    if source_family == VIX_OPTIONS_SOURCE_FAMILY:
        feature_mismatch = planned_features != campaign_features
    else:
        feature_mismatch = sorted(planned_features) != sorted(campaign_features)
    if feature_mismatch:
        raise ValueError("budget plan required_features do not match campaign features")
    if plan.get("measured_throughput_row_basis") != "manifest_source_rows":
        raise ValueError("budget plan throughput row basis is not manifest_source_rows")
    actual = plan_rl_campaign_budget(
        feature_manifest_rows=manifest,
        vast_credit_usd=float((plan.get("vast_budget") or {}).get("credit_usd", 0.0)),
        vast_gpu_hour_rate_usd=float((plan.get("vast_budget") or {}).get("gpu_hour_rate_usd", 1.0)),
        budget_reserve_usd=float((plan.get("vast_budget") or {}).get("reserve_usd", 0.0)),
        supported_features=features,
        required_features=features,
        measured_throughput_rows_per_gpu_hour=float(plan.get("measured_throughput_rows_per_gpu_hour") or 1.0),
        measured_throughput_row_basis="manifest_source_rows",
        pilot_target_rows=1,
    )
    if actual.get("known_inventory_rows") != plan.get("known_inventory_rows"):
        raise ValueError("budget plan known_inventory_rows does not match feature manifest")
    if actual.get("manifest_source_row_fingerprint") != plan.get("manifest_source_row_fingerprint"):
        raise ValueError("budget plan manifest_source_row_fingerprint does not match feature manifest")
    if actual.get("status") != "full_training_plan_ready":
        raise ValueError("current feature manifest is not full-training budget-ready")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)


def _positive_int(value: Any, label: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{label} must be positive")
    return parsed


def _optional_positive_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, label)


def _finite_non_negative(value: Any, label: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError(f"{label} must be finite and non-negative")
    return parsed


def _positive_float(value: Any, label: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{label} must be finite and positive")
    return parsed


def _eval_fraction(value: Any) -> float:
    parsed = float(value)
    if not 0.0 < parsed < 1.0:
        raise ValueError("eval_fraction must be between 0 and 1")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
