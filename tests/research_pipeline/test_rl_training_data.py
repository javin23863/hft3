from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from data_system.src.feature_store import feature_index_hash, store_path
from research_pipeline.rl_training_data import build_rl_training_data


def _write_store(
    root: Path,
    symbol: str,
    event_id: str,
    ts: np.ndarray,
    mids: np.ndarray,
    *,
    agg_values: np.ndarray | None = None,
) -> Path:
    path = store_path(root, symbol, event_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    X = np.zeros((len(ts), 64), dtype=np.float64)
    X[:, 0] = agg_values if agg_values is not None else np.arange(len(ts), dtype=np.float64) * 0.1 - 0.5
    X[:, 5] = 10.0
    X[:, 6] = 5.0
    X[:, 9] = 40.0
    X[:, 10] = 20.0
    X[:, 15] = 0.25
    X[:, 40] = mids
    np.savez_compressed(
        str(path),
        ts=ts,
        X=X,
        best_bid=mids - 0.125,
        best_ask=mids + 0.125,
        bbo_valid=np.ones(len(ts), dtype=np.bool_),
        feature_index_hash=np.array(feature_index_hash()),
    )
    manifest = root / "feature_manifest.jsonl"
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "symbol": symbol,
                    "event_id": event_id,
                    "store_path": str(path.relative_to(root)),
                    "content_hash": f"content-{event_id}",
                    "feature_index_hash": feature_index_hash(),
                    "n_rows": int(len(ts)),
                }
            )
            + "\n"
        )
    return path


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_rl_training_data_builder_writes_pit_rows_and_manifest(tmp_path):
    root = tmp_path / "features"
    symbol = "MES.v.0"
    event_id = "EVT_A"
    ts = np.array([100, 200, 300, 400, 500], dtype=np.int64)
    mids = np.array([5000.0, 5001.0, 5000.5, 5002.0, 5001.5], dtype=np.float64)
    _write_store(root, symbol, event_id, ts, mids)

    result = build_rl_training_data(
        repo_root=tmp_path,
        feature_store_root=root,
        symbol=symbol,
        event_ids=[event_id],
        feature_names=["order_flow_imbalance", "spread", "queue_imbalance"],
        output_dir=tmp_path / "rl",
        feature_latency_ms=0.0,
        spread_cost_multiplier=0.05,
    )

    rows = _read_jsonl(result.rows_path)
    assert result.manifest_path.is_file()
    assert result.manifest["schema_version"] == "hft3_rl_training_data_v1"
    assert result.manifest["promotable"] is False
    assert result.manifest["row_count"] == 4
    assert result.manifest["rows_sha256"]
    assert result.manifest["sources"][0]["content_hash"] == "content-EVT_A"
    assert result.manifest["reward_rule"]["reward_units"] == "price_points"
    assert result.manifest["reward_rule"]["cost_model"]["name"] == "future_mid_delta_minus_spread_multiplier"
    assert rows[0]["timestamp_ns"] == 100
    assert rows[0]["source_timestamp_ns"] <= rows[0]["timestamp_ns"]
    assert rows[0]["spread"] == 0.25
    assert rows[0]["queue_imbalance"] == pytest.approx((10.0 - 5.0) / 15.0)
    assert rows[0]["reward"] == pytest.approx(1.0 - 0.05 * 0.25)
    assert rows[0]["reward_units"] == "price_points"
    assert rows[0]["reward_cost_model"] == "future_mid_delta_minus_spread_multiplier"
    assert rows[0]["spread_cost_price_points"] == pytest.approx(0.05 * 0.25)


def test_rl_training_data_builder_prefix_is_stable_when_future_truncated(tmp_path):
    root_full = tmp_path / "features_full"
    root_trunc = tmp_path / "features_trunc"
    symbol = "MES.v.0"
    event_id = "EVT_A"
    full_ts = np.array([100, 200, 300, 400, 500], dtype=np.int64)
    full_mids = np.array([5000.0, 5001.0, 5000.5, 5002.0, 5001.5], dtype=np.float64)
    _write_store(root_full, symbol, event_id, full_ts, full_mids)
    _write_store(root_trunc, symbol, event_id, full_ts[:-1], full_mids[:-1])

    full = build_rl_training_data(
        repo_root=tmp_path,
        feature_store_root=root_full,
        symbol=symbol,
        event_ids=[event_id],
        feature_names=["order_flow_imbalance", "spread"],
        output_dir=tmp_path / "full",
        feature_latency_ms=0.0,
    )
    trunc = build_rl_training_data(
        repo_root=tmp_path,
        feature_store_root=root_trunc,
        symbol=symbol,
        event_ids=[event_id],
        feature_names=["order_flow_imbalance", "spread"],
        output_dir=tmp_path / "trunc",
        feature_latency_ms=0.0,
    )

    full_rows = _read_jsonl(full.rows_path)
    trunc_rows = _read_jsonl(trunc.rows_path)
    assert full_rows[: len(trunc_rows)] == trunc_rows


def test_rl_training_data_builder_allows_duplicate_global_timestamps(tmp_path):
    root = tmp_path / "features"
    symbol = "MES.v.0"
    ts = np.array([100, 200, 300], dtype=np.int64)
    mids = np.array([5000.0, 5001.0, 5002.0], dtype=np.float64)
    _write_store(root, symbol, "EVT_A", ts, mids)
    _write_store(root, symbol, "EVT_B", ts, mids)

    result = build_rl_training_data(
        repo_root=tmp_path,
        feature_store_root=root,
        symbol=symbol,
        event_ids=["EVT_A", "EVT_B"],
        feature_names=["order_flow_imbalance"],
        output_dir=tmp_path / "rl",
        feature_latency_ms=0.0,
    )

    rows = _read_jsonl(result.rows_path)
    assert [row["row_sequence"] for row in rows] == list(range(len(rows)))
    assert rows[0]["timestamp_ns"] == rows[1]["timestamp_ns"]


def test_rl_training_data_builder_same_timestamp_source_clamps_to_decision_row(tmp_path):
    root = tmp_path / "features"
    symbol = "MES.v.0"
    _write_store(
        root,
        symbol,
        "EVT_A",
        np.array([100, 100, 200, 300], dtype=np.int64),
        np.array([5000.0, 5001.0, 5002.0, 5003.0], dtype=np.float64),
        agg_values=np.array([0.1, 0.9, 0.2, 0.3], dtype=np.float64),
    )

    result = build_rl_training_data(
        repo_root=tmp_path,
        feature_store_root=root,
        symbol=symbol,
        event_ids=["EVT_A"],
        feature_names=["order_flow_imbalance"],
        output_dir=tmp_path / "rl",
        feature_latency_ms=0.0,
    )

    rows = _read_jsonl(result.rows_path)
    assert rows[0]["decision_row_index"] == 0
    assert rows[0]["source_row_index"] == 0
    assert rows[0]["order_flow_imbalance"] == pytest.approx(0.1)
    assert rows[1]["decision_row_index"] == 1
    assert rows[1]["source_row_index"] == 1
    assert rows[1]["order_flow_imbalance"] == pytest.approx(0.9)


def test_rl_training_data_builder_rejects_leaky_feature_names(tmp_path):
    root = tmp_path / "features"
    symbol = "MES.v.0"
    _write_store(
        root,
        symbol,
        "EVT_A",
        np.array([100, 200, 300], dtype=np.int64),
        np.array([5000.0, 5001.0, 5002.0], dtype=np.float64),
    )

    with pytest.raises(ValueError, match="non-PIT or label-like"):
        build_rl_training_data(
            repo_root=tmp_path,
            feature_store_root=root,
            symbol=symbol,
            event_ids=["EVT_A"],
            feature_names=["future_pnl_label"],
            output_dir=tmp_path / "rl",
            feature_latency_ms=0.0,
        )


def test_rl_training_data_builder_rejects_features_not_faithful_in_fs_v1(tmp_path):
    root = tmp_path / "features"
    symbol = "MES.v.0"
    _write_store(
        root,
        symbol,
        "EVT_A",
        np.array([100, 200, 300], dtype=np.int64),
        np.array([5000.0, 5001.0, 5002.0], dtype=np.float64),
    )

    with pytest.raises(ValueError, match="cannot faithfully compute"):
        build_rl_training_data(
            repo_root=tmp_path,
            feature_store_root=root,
            symbol=symbol,
            event_ids=["EVT_A"],
            feature_names=["weighted_depth_price"],
            output_dir=tmp_path / "rl",
            feature_latency_ms=0.0,
        )
