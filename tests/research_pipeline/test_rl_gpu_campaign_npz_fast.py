from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from data_system.src.feature_store import feature_index_hash
from features_engine.src.features.feature_index import FEATURE_DIM, FeatureIndex
from research_pipeline.rl_campaign_budget import plan_rl_campaign_budget


def _load_fast_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_rl_gpu_campaign_npz_fast.py"
    spec = importlib.util.spec_from_file_location("run_rl_gpu_campaign_npz_fast_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fixture_x(rows: int) -> np.ndarray:
    x = np.zeros((rows, FEATURE_DIM), dtype=np.float64)
    x[:, FeatureIndex.TOP_1_DEPTH_BID] = 10.0
    x[:, FeatureIndex.TOP_1_DEPTH_ASK] = 8.0
    x[:, FeatureIndex.TOP_5_DEPTH_BID] = 50.0
    x[:, FeatureIndex.TOP_5_DEPTH_ASK] = 45.0
    x[:, FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE] = 0.1
    return x


def test_fast_runner_rejects_stale_budget_plan_manifest() -> None:
    cli = _load_fast_module()
    plan = plan_rl_campaign_budget(
        feature_manifest_rows={("ES.v.0", "EVT_A"): {"source_rows": 100, "store_path": "A.npz"}},
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )

    with pytest.raises(ValueError, match="known_inventory_rows"):
        cli._require_budget_ready(
            plan,
            ["spread"],
            {("ES.v.0", "EVT_A"): {"source_rows": 200, "store_path": "A.npz"}},
        )


def test_fast_runner_rejects_nonmonotonic_timestamp_array() -> None:
    cli = _load_fast_module()
    x = _fixture_x(3)
    x[:, FeatureIndex.MID_PRICE] = [100.0, 100.25, 100.5]
    x[:, FeatureIndex.SPREAD] = 0.25

    with pytest.raises(ValueError, match="ts not monotonic"):
        cli._arrays_from_store(
            ts=np.array([10, 5, 20], dtype=np.int64),
            X=x,
            best_bid=np.array([99.875, 100.125, 100.375], dtype=np.float64),
            best_ask=np.array([100.125, 100.375, 100.625], dtype=np.float64),
            symbol="ES.v.0",
            event_id="EVT_A",
            features=["spread"],
            reward_horizon_rows=1,
            reward_horizon_ns=None,
            feature_latency_ns=0,
            spread_cost_multiplier=0.05,
        )


def test_fast_runner_skips_invalid_quote_row_like_scalar_builder() -> None:
    cli = _load_fast_module()
    x = _fixture_x(3)
    x[:, FeatureIndex.MID_PRICE] = [0.0, 100.0, 100.5]
    x[:, FeatureIndex.SPREAD] = [np.nan, 0.25, 0.25]

    result = cli._arrays_from_store(
        ts=np.array([10, 20, 30], dtype=np.int64),
        X=x,
        best_bid=np.array([-1.0, 99.875, 100.375], dtype=np.float64),
        best_ask=np.array([-1.0, 100.125, 100.625], dtype=np.float64),
        symbol="ES.v.0",
        event_id="EVT_A",
        features=["spread"],
        reward_horizon_rows=1,
        reward_horizon_ns=None,
        feature_latency_ns=0,
        spread_cost_multiplier=0.05,
    )

    assert result["source_rows"] == 3
    assert result["skipped_rows"] == 2
    assert result["x"].shape == (1, 1)
    assert result["timestamp_ns"].tolist() == [20]
    assert result["reward"].tolist() == pytest.approx([0.4875])


def test_fast_runner_launch_gate_records_duration_stop_rule_and_max_events() -> None:
    cli = _load_fast_module()
    args = argparse.Namespace(
        gpu_host="vastai:42474200",
        host_kind="vastai",
        expected_duration_minutes=12,
        stop_rule="stop after summary or 20 minutes",
        operator_approval="approved-vastai-paid-rl-campaign",
        allow_pre_ppo_proxy=True,
        max_events=2,
    )

    gate = cli._require_launch_gate(
        args=args,
        argv=["--max-events", "2"],
        budget_plan_sha256="a" * 64,
        current_os_name="posix",
    )

    assert args.max_events == 2
    assert gate["status"] == "ready_for_paid_gpu_campaign"
    assert gate["expected_duration_minutes"] == 12.0
    assert gate["stop_rule"] == "stop after summary or 20 minutes"
    assert gate["device"] == "cuda"


def test_fast_runner_status_marks_failed_or_truncated_campaigns() -> None:
    cli = _load_fast_module()

    assert cli._campaign_status(failure_count=1, event_inventory_truncated=False) == "partial_failed"
    assert (
        cli._campaign_status(failure_count=0, event_inventory_truncated=True)
        == "partial_event_inventory_truncated"
    )
    assert cli._campaign_status(failure_count=0, event_inventory_truncated=False) == "completed_research_only"

    receipt = cli._symbol_failure_receipt(
        symbol="ES.v.0",
        features=["spread"],
        exc=RuntimeError("boom"),
        budget_plan_sha256="a" * 64,
    )
    assert receipt["status"] == "failed"
    assert receipt["failure_reasons"] == ["boom"]
    assert receipt["budget_plan_sha256"] == "a" * 64


def test_fast_runner_store_authority_rejects_manifest_hash_drift(tmp_path) -> None:
    cli = _load_fast_module()
    store = {
        "feature_index_hash": feature_index_hash(),
        "ts": np.array([1, 2], dtype=np.int64),
    }

    with pytest.raises(ValueError, match="feature manifest schema drift"):
        cli._require_store_authority(
            record={"feature_index_hash": "wrong"},
            store=store,
            store_path=tmp_path / "store.npz",
        )
