from __future__ import annotations

import argparse
import importlib.util
import json
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


def _load_vix_manifest_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "build_vix_options_rl_manifest.py"
    spec = importlib.util.spec_from_file_location("build_vix_options_rl_manifest_for_test", path)
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


def _write_vix_feature_file(root: Path, event_id: str = "EVT_A") -> Path:
    path = root / "VIX.OPT" / f"VIX.OPT_{event_id}_features_v1.npz"
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = np.array([100, 200, 300, 400], dtype=np.int64)
    np.savez(
        path,
        ts=ts,
        ts_event_raw=ts - 10,
        ts_recv_raw=ts,
        vix_opt_quote_intensity=np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float64),
        vix_quote_arrival_accel=np.array([0.0, 1.0, 1.0, 1.0], dtype=np.float64),
        vix_opt_spread_stress=np.array([10.0, 11.0, 10.5, 12.0], dtype=np.float64),
        vix_opt_depth_imbalance=np.array([0.1, 0.2, -0.1, 0.3], dtype=np.float64),
        vix_opt_bipower_var=np.array([0.01, 0.02, 0.03, 0.04], dtype=np.float64),
        vix_opt_tsrv=np.array([0.001, 0.002, 0.003, 0.004], dtype=np.float64),
        _attrs_json=np.array(["{'feature_version': 'vixf_v1'}"], dtype=object),
    )
    return path


def _write_bad_vix_timestamp_file(root: Path, event_id: str = "EVT_A") -> Path:
    path = _write_vix_feature_file(root, event_id=event_id)
    ts = np.array([100, 200, 300, 400], dtype=np.int64)
    np.savez(
        path,
        ts=ts,
        ts_event_raw=ts - 10,
        ts_recv_raw=ts + 1,
        vix_opt_quote_intensity=np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float64),
        vix_quote_arrival_accel=np.array([0.0, 1.0, 1.0, 1.0], dtype=np.float64),
        vix_opt_spread_stress=np.array([10.0, 11.0, 10.5, 12.0], dtype=np.float64),
        vix_opt_depth_imbalance=np.array([0.1, 0.2, -0.1, 0.3], dtype=np.float64),
        vix_opt_bipower_var=np.array([0.01, 0.02, 0.03, 0.04], dtype=np.float64),
        vix_opt_tsrv=np.array([0.001, 0.002, 0.003, 0.004], dtype=np.float64),
    )
    return path


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
            source_family="fs_v1_target",
        )


def test_fast_runner_rejects_budget_feature_order_drift() -> None:
    cli = _load_fast_module()
    manifest = {
        ("VIX.OPT", "EVT_A"): {
            "source_rows": 10,
            "store_path": "VIX.OPT_EVT_A_features_v1.npz",
            "source_family": "vix_options_clue",
            "content_hash": "content-a",
            "feature_schema_hash": "schema-a",
        }
    }
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=manifest,
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["vix_opt_quote_intensity", "vix_opt_spread_stress"],
        required_features=["vix_opt_quote_intensity", "vix_opt_spread_stress"],
        measured_throughput_rows_per_gpu_hour=1000,
    )

    with pytest.raises(ValueError, match="required_features"):
        cli._require_budget_ready(
            plan,
            ["vix_opt_spread_stress", "vix_opt_quote_intensity"],
            manifest,
            source_family="vix_options_clue",
        )


def test_fast_runner_allows_fs_budget_feature_order_compatibility() -> None:
    cli = _load_fast_module()
    manifest = {("ES.v.0", "EVT_A"): {"source_rows": 100, "store_path": "A.npz"}}
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=manifest,
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["micro_price", "spread"],
        required_features=["micro_price", "spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )

    cli._require_budget_ready(
        plan,
        ["spread", "micro_price"],
        manifest,
        source_family="fs_v1_target",
    )


def test_fast_runner_rejects_duplicate_fs_budget_features() -> None:
    cli = _load_fast_module()
    manifest = {("ES.v.0", "EVT_A"): {"source_rows": 100, "store_path": "A.npz"}}
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=manifest,
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )

    with pytest.raises(ValueError, match="required_features"):
        cli._require_budget_ready(
            plan,
            ["spread", "spread"],
            manifest,
            source_family="fs_v1_target",
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


def test_fast_runner_rejects_mixed_fs_and_vix_campaign() -> None:
    cli = _load_fast_module()

    with pytest.raises(SystemExit, match="must run separately"):
        cli._resolve_campaign_features(symbols=["ES.v.0", "VIX.OPT"], requested=None)


def test_fast_runner_rejects_custom_manifest_for_fs_v1_campaign(tmp_path) -> None:
    cli = _load_fast_module()

    with pytest.raises(SystemExit, match="reserved for VIX.OPT"):
        cli._require_campaign_manifest_allowed(
            source_family="fs_v1_target",
            feature_manifest=tmp_path / "feature_manifest.jsonl",
        )


def test_vix_options_rl_manifest_builder_writes_schema_bound_rows(tmp_path) -> None:
    builder = _load_vix_manifest_module()
    root = tmp_path / "features"
    _write_vix_feature_file(root)
    out = tmp_path / "vix_options_rl_manifest.jsonl"

    assert (
        builder.main(
            [
                "--feature-store-root",
                str(root),
                "--out",
                str(out),
                "--feature",
                "vix_opt_spread_stress",
            ]
        )
        == 0
    )

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "VIX.OPT"
    assert row["event_id"] == "EVT_A"
    assert row["source_family"] == "vix_options_clue"
    assert row["store_schema_version"] == "hft3_vix_options_rl_clue_store_v1"
    assert row["source_rows"] == 4
    assert row["feature_schema_hash"]
    assert row["store_sha256"] == row["content_hash"]
    assert row["timestamp_bounds"]["ts_recv_raw_lte_ts"] is True
    assert row["timestamp_bounds"]["ts_event_raw_lte_ts_recv_raw"] is True
    assert row["reward_rule"]["execution_claim"] is False


def test_vix_options_rl_manifest_builder_rejects_noncausal_raw_timestamps(tmp_path) -> None:
    builder = _load_vix_manifest_module()
    root = tmp_path / "features"
    _write_bad_vix_timestamp_file(root)

    with pytest.raises(ValueError, match="fewer than two causal timestamp rows"):
        builder.main(["--feature-store-root", str(root), "--out", str(tmp_path / "manifest.jsonl"), "--strict"])


def test_fast_runner_rejects_vix_manifest_without_file_hash(tmp_path) -> None:
    cli = _load_fast_module()
    builder = _load_vix_manifest_module()
    root = tmp_path / "features"
    store_path = _write_vix_feature_file(root)
    out = tmp_path / "vix_options_rl_manifest.jsonl"
    assert (
        builder.main(
            [
                "--feature-store-root",
                str(root),
                "--out",
                str(out),
                "--feature",
                "vix_opt_spread_stress",
            ]
        )
        == 0
    )
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    row.pop("content_hash", None)
    row.pop("store_sha256", None)
    store = cli._load_vix_options_store(store_path)

    with pytest.raises(ValueError, match="missing store_sha256"):
        cli._require_vix_options_store_authority(
            record=row,
            store=store,
            store_path=store_path,
            store_sha256=cli._sha256_file(store_path),
            features=["vix_opt_spread_stress"],
            reward_column="vix_opt_spread_stress",
        )


def test_fast_runner_builds_vix_options_clue_arrays_from_manifest(tmp_path) -> None:
    cli = _load_fast_module()
    builder = _load_vix_manifest_module()
    root = tmp_path / "features"
    _write_vix_feature_file(root)
    out = tmp_path / "vix_options_rl_manifest.jsonl"
    assert (
        builder.main(
            [
                "--feature-store-root",
                str(root),
                "--out",
                str(out),
                "--feature",
                "vix_opt_quote_intensity",
                "--feature",
                "vix_opt_spread_stress",
            ]
        )
        == 0
    )
    manifest = cli._load_campaign_manifest(root, out)

    result = cli._build_symbol_arrays(
        feature_store_root=root,
        manifest=manifest,
        symbol="VIX.OPT",
        features=["vix_opt_quote_intensity", "vix_opt_spread_stress"],
        reward_horizon_rows=1,
        reward_horizon_ns=None,
        feature_latency_ns=0,
        spread_cost_multiplier=0.05,
        max_events=None,
        vix_reward_column="vix_opt_spread_stress",
    )

    assert result["source_family"] == "vix_options_clue"
    assert result["action_space"] == ("hold", "clue_up", "clue_down")
    assert result["reward_rule"]["execution_claim"] is False
    assert result["source_row_count"] == 4
    assert result["skipped_row_count"] == 1
    assert result["x"].shape == (3, 2)
    assert result["x"][:, 0].tolist() == pytest.approx([1.0, 2.0, 3.0])
    assert result["x"][:, 1].tolist() == pytest.approx([10.0, 11.0, 10.5])
    assert result["reward"].tolist() == pytest.approx([1.0, -0.5, 1.5])


def test_fast_runner_rejects_vix_manifest_feature_order_drift(tmp_path) -> None:
    cli = _load_fast_module()
    builder = _load_vix_manifest_module()
    root = tmp_path / "features"
    store_path = _write_vix_feature_file(root)
    out = tmp_path / "vix_options_rl_manifest.jsonl"
    assert (
        builder.main(
            [
                "--feature-store-root",
                str(root),
                "--out",
                str(out),
                "--feature",
                "vix_opt_quote_intensity",
                "--feature",
                "vix_opt_spread_stress",
            ]
        )
        == 0
    )
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    store = cli._load_vix_options_store(store_path)

    with pytest.raises(ValueError, match="exactly and in order"):
        cli._require_vix_options_store_authority(
            record=row,
            store=store,
            store_path=store_path,
            store_sha256=cli._sha256_file(store_path),
            features=["vix_opt_spread_stress", "vix_opt_quote_intensity"],
            reward_column="vix_opt_spread_stress",
        )


def test_fast_runner_masks_noncausal_vix_source_row_under_latency() -> None:
    cli = _load_fast_module()
    ts = np.array([100, 200, 300, 400], dtype=np.int64)
    store = {
        "ts": ts,
        "ts_event_raw": np.array([90, 190, 290, 390], dtype=np.int64),
        "ts_recv_raw": np.array([150, 200, 300, 400], dtype=np.int64),
        "vix_opt_quote_intensity": np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float64),
        "vix_opt_spread_stress": np.array([10.0, 11.0, 10.5, 12.0], dtype=np.float64),
    }

    result = cli._arrays_from_vix_options_store(
        store=store,
        features=["vix_opt_quote_intensity"],
        reward_horizon_rows=1,
        reward_horizon_ns=None,
        feature_latency_ns=100,
        reward_column="vix_opt_spread_stress",
    )

    assert result["source_rows"] == 4
    assert result["skipped_rows"] == 3
    assert result["timestamp_ns"].tolist() == [300]
    assert result["x"][:, 0].tolist() == pytest.approx([2.0])
    assert result["reward"].tolist() == pytest.approx([1.5])


def test_vix_options_array_rejects_out_of_bounds_index() -> None:
    cli = _load_fast_module()
    store = {"vix_opt_spread_stress": np.array([10.0, 11.0], dtype=np.float64)}

    with pytest.raises(ValueError, match="index out of bounds"):
        cli._vix_options_array(store, "vix_opt_spread_stress", np.array([-1], dtype=np.int64))

    with pytest.raises(ValueError, match="index out of bounds"):
        cli._vix_options_array(store, "vix_opt_spread_stress", np.array([2], dtype=np.int64))
