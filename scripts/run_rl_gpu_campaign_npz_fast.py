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

SUPPORTED_FEATURES = (
    "order_book_imbalance",
    "queue_imbalance",
    "order_flow_imbalance",
    "micro_price",
    "spread",
)
SCHEMA_VERSION = "hft3_rl_gpu_vectorized_campaign_v1"
PROMOTION_STATUS = "blocked_downstream_validation_required"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--feature-store-root", type=Path, required=True)
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
    features = tuple(args.feature or SUPPORTED_FEATURES)
    unsupported = sorted(set(features) - set(SUPPORTED_FEATURES))
    if unsupported:
        raise SystemExit("unsupported fs_v1 RL features: " + ", ".join(unsupported))
    plan = json.loads(args.budget_plan.read_text(encoding="utf-8"))

    import torch

    if not torch.cuda.is_available():
        raise SystemExit("CUDA runtime unavailable")

    started = time.perf_counter()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(Path(args.feature_store_root))
    _require_budget_ready(plan, features, manifest)
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
        "output_root": str(output_root),
        "symbols": list(args.symbol),
        "feature_names": list(features),
        "max_events": args.max_events,
        "event_inventory_truncated": event_inventory_truncated,
        "launch_gate": launch_gate,
        "budget_plan": str(args.budget_plan),
        "budget_plan_sha256": plan_sha,
        "failure_count": failure_count,
        "duration_seconds": round(time.perf_counter() - started, 6),
        "symbol_results": all_results,
        "decision_time_boundary": (
            "features use source_timestamp_ns <= decision timestamp minus feature_latency_ns; "
            "future mid-price is used only as the offline reward label"
        ),
        "receipts": {
            "feature_registry": "features_engine.feature_sets.MICROSTRUCTURE_FEATURE_RECEIPTS",
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
        "decision_time_boundary": (
            "source feature index is vectorized searchsorted(ts, timestamp_ns - feature_latency_ns); "
            "source_row_index never exceeds decision_row_index"
        ),
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
) -> dict[str, Any]:
    if x.shape[0] < 3:
        raise ValueError("deep RL vectorized training requires at least three rows")
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
            "action_space": ["hold", "enter_long", "enter_short"],
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
        "action_space": ["hold", "enter_long", "enter_short"],
        "eval_action_counts": {
            "hold": int(np.count_nonzero(action_ids == 0)),
            "enter_long": int(np.count_nonzero(action_ids == 1)),
            "enter_short": int(np.count_nonzero(action_ids == 2)),
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
) -> None:
    if plan.get("status") != "full_training_plan_ready":
        raise ValueError("budget plan is not full_training_plan_ready")
    if set(plan.get("required_features") or []) != set(features):
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
