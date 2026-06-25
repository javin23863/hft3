"""Point-in-time RL training row builder for research-only GPU training."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from backtest_pipeline.src.fs_v1_screen_path import resolve_fs_v1_screen_context
from backtest_pipeline.src.hft_campaign._hashing import sha256_file, sha256_hex
from backtest_pipeline.src.hft_campaign.artifacts import write_json_atomic, write_jsonl_atomic
from data_system.src.feature_store import feature_index_hash, load_manifest
from features_engine.feature_sets import VIX_OPTIONS_RL_FEATURE_RECEIPTS
from features_engine.src.features.feature_index import FEATURE_NAME_TO_INDEX, FeatureIndex

from research_pipeline.rl_agents import (
    PROMOTION_BLOCKED_STATUS,
    validate_rl_features,
)

RL_TRAINING_DATA_SCHEMA_VERSION = "hft3_rl_training_data_v1"
RL_TRAINING_ROWS_FILENAME = "rl_training_rows.jsonl"
RL_TRAINING_MANIFEST_FILENAME = "rl_training_manifest.json"
RL_REWARD_UNITS = "price_points"
RL_REWARD_COST_MODEL = "future_mid_delta_minus_spread_multiplier"
VIX_OPTIONS_RL_FEATURE_STORE_SCHEMA_VERSION = "hft3_vix_options_rl_clue_store_v1"
VIX_OPTIONS_RL_REWARD_UNITS = "vix_options_clue_delta"
VIX_OPTIONS_RL_REWARD_COST_MODEL = "future_vix_options_clue_delta_no_execution_cost"
VIX_OPTIONS_DEFAULT_RL_REWARD_COLUMN = "vix_opt_spread_stress"
VIX_OPTIONS_DEFAULT_RL_FEATURES = (
    "vix_opt_quote_intensity",
    "vix_quote_arrival_accel",
    "vix_opt_spread_stress",
    "vix_opt_depth_imbalance",
    "vix_opt_bipower_var",
    "vix_opt_tsrv",
)
_VIX_FEATURES = VIX_OPTIONS_RL_FEATURE_RECEIPTS.get("features", {})
VIX_OPTIONS_SUPPORTED_RL_FEATURES = frozenset(_VIX_FEATURES if isinstance(_VIX_FEATURES, Mapping) else {})
FEATURE_STORE_SUPPORTED_RL_FEATURES = frozenset(
    {
        "order_book_imbalance",
        "queue_imbalance",
        "order_flow_imbalance",
        "micro_price",
        "spread",
    }
)
GPU_SUPPORTED_RL_FEATURES = FEATURE_STORE_SUPPORTED_RL_FEATURES | VIX_OPTIONS_SUPPORTED_RL_FEATURES


@dataclass(frozen=True)
class RlTrainingDataBuildResult:
    rows_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


def build_rl_training_data(
    *,
    repo_root: Path,
    feature_store_root: Path,
    symbol: str,
    event_ids: Sequence[str],
    feature_names: Sequence[str],
    output_dir: Path,
    reward_horizon_rows: int = 1,
    reward_horizon_ns: int | None = None,
    feature_latency_ms: float = 1.0,
    spread_cost_multiplier: float = 0.05,
    max_rows: int | None = None,
) -> RlTrainingDataBuildResult:
    """Build PIT-visible RL rows from existing feature-store NPZ artifacts.

    Features come from the latest feature-store row visible at
    ``decision_timestamp - feature_latency_ms``. The reward is the future
    mid-price move and is only written as the label column.
    """
    started = datetime.now(timezone.utc)
    repo_root = Path(repo_root)
    feature_store_root = Path(feature_store_root)
    output_dir = Path(output_dir)
    features = validate_rl_features(feature_names)
    _validate_feature_store_supported_features(features)
    events = [str(event_id).strip() for event_id in event_ids if str(event_id).strip()]
    if not events:
        raise ValueError("rl training data requires at least one event_id")
    horizon_rows = _positive_int(reward_horizon_rows, "reward_horizon_rows")
    horizon_ns = _optional_positive_int(reward_horizon_ns, "reward_horizon_ns")
    latency_ms = _finite_non_negative(feature_latency_ms, "feature_latency_ms")
    cost_multiplier = _finite_non_negative(spread_cost_multiplier, "spread_cost_multiplier")
    row_limit = _optional_positive_int(max_rows, "max_rows")

    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    skipped_units: list[dict[str, str]] = []
    manifest = load_manifest(feature_store_root)
    feature_hash = feature_index_hash()
    latency_ns = int(round(latency_ms * 1_000_000))

    for event_id in events:
        ctx = resolve_fs_v1_screen_context(
            repo_root=repo_root,
            event_id=event_id,
            symbol=symbol,
            feature_store_root_override=feature_store_root,
            feature_latency_ms=latency_ms,
        )
        if ctx is None:
            skipped_units.append({"symbol": symbol, "event_id": event_id, "reason": "feature_store_unavailable"})
            continue
        unit_rows, unit_summary = _rows_from_context(
            ctx.store,
            symbol=ctx.symbol,
            event_id=event_id,
            feature_names=features,
            reward_horizon_rows=horizon_rows,
            reward_horizon_ns=horizon_ns,
            latency_ns=latency_ns,
            spread_cost_multiplier=cost_multiplier,
        )
        if unit_rows:
            rows.extend(unit_rows)
        record = manifest.get((ctx.symbol, event_id), manifest.get((symbol, event_id), {}))
        sources.append(
            {
                "symbol": ctx.symbol,
                "event_id": event_id,
                "store_path": str(ctx.store_path),
                "store_sha256": sha256_file(ctx.store_path),
                "manifest_record": dict(record),
                "content_hash": str(record.get("content_hash") or ctx.content_hash or ""),
                "manifest_hash": ctx.manifest_hash,
                "feature_index_hash": str(record.get("feature_index_hash") or feature_hash),
                "row_summary": unit_summary,
            }
        )

    rows.sort(key=lambda row: (int(row["timestamp_ns"]), str(row["symbol"]), str(row["event_id"])))
    if row_limit is not None and len(rows) > row_limit:
        rows = rows[:row_limit]
    _require_non_decreasing_rows(rows)
    if len(rows) < 2:
        raise ValueError("rl training data requires at least two PIT-valid rows")
    for row_idx, row in enumerate(rows):
        row["row_sequence"] = row_idx

    output_dir.mkdir(parents=True, exist_ok=True)
    rows_path = output_dir / RL_TRAINING_ROWS_FILENAME
    write_jsonl_atomic(rows_path, rows)
    split = _chronological_split_counts(len(rows))
    manifest_payload = {
        "schema_version": RL_TRAINING_DATA_SCHEMA_VERSION,
        "status": "built_research_only",
        "promotion_status": PROMOTION_BLOCKED_STATUS,
        "promotable": False,
        "created_at_utc": started.isoformat(),
        "builder": {
            "module": "research_pipeline.rl_training_data",
            "sha256": sha256_file(Path(__file__)),
        },
        "repo_root": str(repo_root),
        "feature_store_root": str(feature_store_root),
        "symbol": str(symbol),
        "event_ids": events,
        "feature_names": features,
        "row_count": len(rows),
        "row_limit": row_limit,
        "rows_path": str(rows_path),
        "rows_sha256": sha256_file(rows_path),
        "feature_index_hash": feature_hash,
        "reward_rule": {
            "name": "future_mid_minus_decision_mid_minus_spread_cost",
            "reward_units": RL_REWARD_UNITS,
            "horizon_rows": horizon_rows,
            "horizon_ns": horizon_ns,
            "cost_model": {
                "name": RL_REWARD_COST_MODEL,
                "spread_units": RL_REWARD_UNITS,
                "spread_cost_multiplier": cost_multiplier,
                "spread_cost_column": "spread_cost_price_points",
            },
            "reward_column": "reward",
            "label_only": True,
        },
        "decision_time_boundary": (
            "feature values use source_timestamp_ns <= timestamp_ns - feature_latency_ns; "
            "future mid-price is written only to the reward label"
        ),
        "feature_latency_ms": latency_ms,
        "feature_latency_ns": latency_ns,
        "train_validation_split": split,
        "sources": sources,
        "skipped_units": skipped_units,
        "artifact_hash": "",
        "receipts": {
            "rl_microstructure": "https://www.cis.upenn.edu/~mkearns/KN.html",
            "feature_registry": "features_engine.feature_sets.MICROSTRUCTURE_FEATURE_RECEIPTS",
            "feature_store": "data_system.src.feature_store",
        },
    }
    manifest_payload["artifact_hash"] = sha256_hex(
        {k: v for k, v in manifest_payload.items() if k != "artifact_hash"}
    )
    manifest_path = output_dir / RL_TRAINING_MANIFEST_FILENAME
    write_json_atomic(manifest_path, manifest_payload)
    return RlTrainingDataBuildResult(
        rows_path=rows_path,
        manifest_path=manifest_path,
        manifest=manifest_payload,
    )


def _rows_from_context(
    store: Mapping[str, Any],
    *,
    symbol: str,
    event_id: str,
    feature_names: Sequence[str],
    reward_horizon_rows: int,
    reward_horizon_ns: int | None,
    latency_ns: int,
    spread_cost_multiplier: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ts = np.asarray(store.get("ts"), dtype=np.int64)
    X = np.asarray(store.get("X"), dtype=np.float64)
    if len(ts) < 2 or X.ndim != 2 or X.shape[0] != len(ts):
        return [], {"source_rows": int(len(ts)), "built_rows": 0, "skipped_rows": len(ts), "reason": "invalid_store_shape"}
    rows: list[dict[str, Any]] = []
    skipped = 0
    for decision_idx, decision_ts in enumerate(ts):
        source_idx = int(np.searchsorted(ts, int(decision_ts) - latency_ns, side="right")) - 1
        source_idx = min(source_idx, decision_idx)
        if source_idx < 0:
            skipped += 1
            continue
        future_idx = _future_index(ts, decision_idx, reward_horizon_rows, reward_horizon_ns)
        if future_idx is None:
            skipped += 1
            continue
        try:
            decision_mid = _mid_at(store, X, decision_idx)
            future_mid = _mid_at(store, X, future_idx)
            spread = max(0.0, _spread_at(store, X, decision_idx))
            future_mid_delta = float(future_mid - decision_mid)
            spread_cost = float(spread_cost_multiplier * spread)
            row = {
                "timestamp_ns": int(decision_ts),
                "decision_row_index": int(decision_idx),
                "source_row_index": int(source_idx),
                "source_timestamp_ns": int(ts[source_idx]),
                "feature_latency_ns": int(decision_ts) - int(ts[source_idx]),
                "symbol": str(symbol),
                "event_id": str(event_id),
                "reward": float(future_mid_delta - spread_cost),
                "reward_units": RL_REWARD_UNITS,
                "reward_cost_model": RL_REWARD_COST_MODEL,
                "reward_horizon_rows": int(future_idx - decision_idx),
                "future_mid_delta": future_mid_delta,
                "spread_cost_price_points": spread_cost,
                "spread_cost_multiplier": float(spread_cost_multiplier),
                "decision_mid": decision_mid,
                "future_mid": future_mid,
                "spread_at_decision": spread,
            }
            for feature in feature_names:
                row[feature] = _feature_value(feature, store, X, source_idx)
            rows.append(row)
        except ValueError:
            skipped += 1
    return rows, {"source_rows": int(len(ts)), "built_rows": len(rows), "skipped_rows": skipped}


def _future_index(
    ts: np.ndarray,
    decision_idx: int,
    reward_horizon_rows: int,
    reward_horizon_ns: int | None,
) -> int | None:
    if reward_horizon_ns is not None:
        target_ts = int(ts[decision_idx]) + reward_horizon_ns
        idx = int(np.searchsorted(ts, target_ts, side="left"))
    else:
        idx = decision_idx + reward_horizon_rows
    return idx if 0 <= idx < len(ts) else None


def _feature_value(feature: str, store: Mapping[str, Any], X: np.ndarray, idx: int) -> float:
    if feature == "order_flow_imbalance":
        return _finite(_x_slot(X, idx, FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE), feature)
    if feature == "spread":
        return _finite(_spread_at(store, X, idx), feature)
    if feature == "queue_imbalance":
        return _safe_imbalance(
            _x_slot(X, idx, FeatureIndex.TOP_1_DEPTH_BID),
            _x_slot(X, idx, FeatureIndex.TOP_1_DEPTH_ASK),
        )
    if feature == "order_book_imbalance":
        bid, ask = _depth_pair(X, idx)
        return _safe_imbalance(bid, ask)
    if feature == "micro_price":
        return _micro_price(store, X, idx)
    if feature in FEATURE_NAME_TO_INDEX:
        return _finite(_x_slot(X, idx, FEATURE_NAME_TO_INDEX[feature]), feature)
    raise ValueError(f"unsupported rl feature {feature!r}")


def _depth_pair(X: np.ndarray, idx: int) -> tuple[float, float]:
    for bid_slot, ask_slot in (
        (FeatureIndex.TOP_5_DEPTH_BID, FeatureIndex.TOP_5_DEPTH_ASK),
        (FeatureIndex.TOP_3_DEPTH_BID, FeatureIndex.TOP_3_DEPTH_ASK),
        (FeatureIndex.TOP_10_DEPTH_BID, FeatureIndex.TOP_10_DEPTH_ASK),
        (FeatureIndex.TOP_1_DEPTH_BID, FeatureIndex.TOP_1_DEPTH_ASK),
    ):
        bid = _x_slot(X, idx, bid_slot)
        ask = _x_slot(X, idx, ask_slot)
        if bid + ask > 0.0:
            return bid, ask
    return 0.0, 0.0


def _mid_at(store: Mapping[str, Any], X: np.ndarray, idx: int) -> float:
    mid = _x_slot(X, idx, FeatureIndex.MID_PRICE)
    if math.isfinite(mid) and mid > 0.0:
        return mid
    bid = _array_value(store.get("best_bid"), idx, "best_bid")
    ask = _array_value(store.get("best_ask"), idx, "best_ask")
    if bid > 0.0 and ask > 0.0:
        return (bid + ask) / 2.0
    raise ValueError("missing finite mid price")


def _spread_at(store: Mapping[str, Any], X: np.ndarray, idx: int) -> float:
    spread = _x_slot(X, idx, FeatureIndex.SPREAD)
    if math.isfinite(spread) and spread >= 0.0:
        return spread
    bid = _array_value(store.get("best_bid"), idx, "best_bid")
    ask = _array_value(store.get("best_ask"), idx, "best_ask")
    if bid > 0.0 and ask >= bid:
        return ask - bid
    raise ValueError("missing finite spread")


def _micro_price(store: Mapping[str, Any], X: np.ndarray, idx: int) -> float:
    bid = _array_value(store.get("best_bid"), idx, "best_bid")
    ask = _array_value(store.get("best_ask"), idx, "best_ask")
    bid_qty = _x_slot(X, idx, FeatureIndex.TOP_1_DEPTH_BID)
    ask_qty = _x_slot(X, idx, FeatureIndex.TOP_1_DEPTH_ASK)
    denom = bid_qty + ask_qty
    if bid <= 0.0 or ask <= 0.0 or denom <= 0.0:
        return _mid_at(store, X, idx)
    return _finite((bid * ask_qty + ask * bid_qty) / denom, "micro_price")


def _x_slot(X: np.ndarray, idx: int, slot: int) -> float:
    slot_idx = int(slot)
    if slot_idx < 0 or slot_idx >= X.shape[1]:
        return 0.0
    return _finite(float(X[idx, slot_idx]), f"feature_slot_{slot_idx}")


def _array_value(value: Any, idx: int, label: str) -> float:
    if value is None:
        raise ValueError(f"{label} missing")
    arr = np.asarray(value, dtype=np.float64)
    if idx >= len(arr):
        raise ValueError(f"{label} too short")
    return _finite(float(arr[idx]), label)


def _safe_imbalance(left: float, right: float) -> float:
    left = _finite(left, "imbalance_left")
    right = _finite(right, "imbalance_right")
    denom = left + right
    if denom <= 0.0:
        return 0.0
    return (left - right) / denom


def _finite(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite numeric")
    return parsed


def _finite_non_negative(value: Any, label: str) -> float:
    parsed = _finite(value, label)
    if parsed < 0.0:
        raise ValueError(f"{label} must be non-negative")
    return parsed


def _positive_int(value: Any, label: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{label} must be positive")
    return parsed


def _optional_positive_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, label)


def _validate_feature_store_supported_features(feature_names: Sequence[str]) -> None:
    unsupported = [name for name in feature_names if name not in FEATURE_STORE_SUPPORTED_RL_FEATURES]
    if unsupported:
        raise ValueError(
            "fs_v1 rl training builder cannot faithfully compute these registry features: "
            + ", ".join(unsupported)
            + ". Use raw depth/trade windows or add source columns before enabling them."
        )


def vix_options_feature_schema_hash(
    *,
    feature_names: Sequence[str] | None = None,
    reward_column: str = VIX_OPTIONS_DEFAULT_RL_REWARD_COLUMN,
) -> str:
    features = tuple(feature_names or VIX_OPTIONS_DEFAULT_RL_FEATURES)
    unknown = sorted(set(features) - set(VIX_OPTIONS_SUPPORTED_RL_FEATURES))
    if unknown:
        raise ValueError("unknown VIX options RL features: " + ", ".join(unknown))
    reward = str(reward_column).strip()
    if reward not in VIX_OPTIONS_SUPPORTED_RL_FEATURES:
        raise ValueError(f"unknown VIX options RL reward column: {reward!r}")
    return sha256_hex(
        {
            "schema_version": VIX_OPTIONS_RL_FEATURE_STORE_SCHEMA_VERSION,
            "feature_family": "vix_options_clue",
            "feature_names": list(features),
            "reward_column": reward,
            "reward_units": VIX_OPTIONS_RL_REWARD_UNITS,
            "reward_cost_model": VIX_OPTIONS_RL_REWARD_COST_MODEL,
        }
    )


def _require_non_decreasing_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    previous: int | None = None
    for idx, row in enumerate(rows):
        timestamp = int(row["timestamp_ns"])
        if previous is not None and timestamp < previous:
            raise ValueError(f"rl training rows must be non-decreasing at row {idx}")
        source_ts = int(row["source_timestamp_ns"])
        if source_ts > timestamp:
            raise ValueError(f"rl training row {idx} has future source timestamp")
        if int(row.get("source_row_index", 0)) > int(row.get("decision_row_index", 0)):
            raise ValueError(f"rl training row {idx} has future source row index")
        previous = timestamp


def _chronological_split_counts(row_count: int) -> dict[str, Any]:
    train_rows = max(1, int(row_count * 0.8))
    if train_rows >= row_count:
        train_rows = row_count - 1
    return {
        "method": "chronological_prefix_train_suffix_validation",
        "train_rows": train_rows,
        "validation_rows": row_count - train_rows,
        "random_split": False,
    }


__all__ = [
    "RL_TRAINING_DATA_SCHEMA_VERSION",
    "RL_TRAINING_MANIFEST_FILENAME",
    "RL_TRAINING_ROWS_FILENAME",
    "RL_REWARD_COST_MODEL",
    "RL_REWARD_UNITS",
    "FEATURE_STORE_SUPPORTED_RL_FEATURES",
    "GPU_SUPPORTED_RL_FEATURES",
    "VIX_OPTIONS_DEFAULT_RL_FEATURES",
    "VIX_OPTIONS_DEFAULT_RL_REWARD_COLUMN",
    "VIX_OPTIONS_RL_FEATURE_STORE_SCHEMA_VERSION",
    "VIX_OPTIONS_RL_REWARD_COST_MODEL",
    "VIX_OPTIONS_RL_REWARD_UNITS",
    "VIX_OPTIONS_SUPPORTED_RL_FEATURES",
    "RlTrainingDataBuildResult",
    "build_rl_training_data",
    "vix_options_feature_schema_hash",
]
