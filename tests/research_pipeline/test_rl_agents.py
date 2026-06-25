from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from research_pipeline.rl_agents import (
    rl_gpu_training_readiness_artifact,
    train_deep_rl_policy_artifact,
    train_or_load_rl_policy_artifact,
    train_rl_policy_artifact,
    validate_rl_deep_policy_artifact,
    validate_rl_features,
    validate_rl_policy_artifact,
    write_rl_deep_policy_artifact,
    write_rl_policy_artifact,
)


def _load_run_rl_gpu_smoke_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_rl_gpu_smoke.py"
    spec = importlib.util.spec_from_file_location("run_rl_gpu_smoke_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_rl_cpu_training_writes_non_promotable_policy_artifact(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "spread": 1.0, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "spread": 1.0, "reward": -0.20},
        {"timestamp_ns": 3, "order_book_imbalance": 0.0, "spread": 2.0, "reward": 0.00},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = train_rl_policy_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance", "spread"],
        device="cpu",
        seed=9,
    )

    validate_rl_policy_artifact(artifact)
    assert artifact["status"] == "trained_research_only"
    assert artifact["promotable"] is False
    assert artifact["device"] == "cpu"
    assert artifact["training_data_receipt"]["sha256"]
    assert artifact["feature_names"] == ["order_book_imbalance", "spread"]
    assert artifact["training_summary"]["reward_metadata"]["reward_units"] == "unknown"
    assert artifact["q_table"]

    written = write_rl_policy_artifact(tmp_path / "rl_policy_artifact.json", artifact)
    assert written.is_file()


def test_rl_cuda_builds_blocked_gpu_handoff_artifact(tmp_path):
    training_path = tmp_path / "rl_rows.json"
    training_path.write_text(
        json.dumps(
            {
                "rows": [
                    {"timestamp_ns": 1, "order_book_imbalance": 0.5, "reward": 0.1},
                    {"timestamp_ns": 2, "order_book_imbalance": -0.2, "reward": -0.1},
                ]
            }
        ),
        encoding="utf-8",
    )

    artifact = train_rl_policy_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        device="cuda",
    )

    validate_rl_policy_artifact(artifact)
    assert artifact["status"] == "blocked"
    assert artifact["gpu_training_required"] is True
    assert artifact["failure_reasons"] == ["cuda_training_requires_gpu_subagent"]
    assert artifact["policy"] == {}


def test_rl_policy_cache_hits_same_inputs(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "spread": 1.0, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "spread": 1.0, "reward": -0.20},
        {"timestamp_ns": 3, "order_book_imbalance": 0.0, "spread": 2.0, "reward": 0.00},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    cache_root = tmp_path / "rl_cache"

    first = train_or_load_rl_policy_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance", "spread"],
        device="cpu",
        seed=9,
        cache_root=cache_root,
    )
    second = train_or_load_rl_policy_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance", "spread"],
        device="cpu",
        seed=9,
        cache_root=cache_root,
    )
    cache_path = cache_root / f"{first['cache_receipt']['cache_key']}.json"
    tampered = json.loads(cache_path.read_text(encoding="utf-8"))
    tampered["seed"] = 999
    cache_path.write_text(json.dumps(tampered), encoding="utf-8")
    repaired = train_or_load_rl_policy_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance", "spread"],
        device="cpu",
        seed=9,
        cache_root=cache_root,
    )
    third = train_or_load_rl_policy_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance", "spread"],
        device="cpu",
        seed=10,
        cache_root=cache_root,
    )

    validate_rl_policy_artifact(first)
    validate_rl_policy_artifact(second)
    validate_rl_policy_artifact(repaired)
    validate_rl_policy_artifact(third)
    assert first["cache_receipt"]["status"] == "miss"
    assert second["cache_receipt"]["status"] == "hit"
    assert repaired["cache_receipt"]["status"] == "miss"
    assert third["cache_receipt"]["status"] == "miss"
    assert first["cache_receipt"]["cache_key"] == second["cache_receipt"]["cache_key"]
    assert repaired["cache_receipt"]["cache_key"] == first["cache_receipt"]["cache_key"]
    assert third["cache_receipt"]["cache_key"] != first["cache_receipt"]["cache_key"]
    assert first["policy"] == second["policy"]
    assert repaired["policy"] == first["policy"]
    assert first["promotable"] is False
    assert second["promotable"] is False
    assert repaired["promotable"] is False
    assert third["promotable"] is False
    assert len(list(cache_root.glob("*.json"))) == 2


def test_rl_feature_validation_uses_microstructure_registry():
    assert validate_rl_features(["order_flow_imbalance"]) == ["order_flow_imbalance"]
    assert validate_rl_features(["vix_opt_spread_stress"]) == ["vix_opt_spread_stress"]

    with pytest.raises(ValueError, match="non-PIT or label-like"):
        validate_rl_features(["future_pnl_label"])


def test_rl_training_requires_timestamps_unless_explicitly_allowed(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"order_book_imbalance": 0.5, "reward": 0.10},
        {"order_book_imbalance": -0.5, "reward": -0.20},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    with pytest.raises(ValueError, match="requires a timestamp_ns"):
        train_rl_policy_artifact(
            training_data_path=training_path,
            feature_names=["order_book_imbalance"],
            device="cpu",
        )

    artifact = train_rl_policy_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        device="cpu",
        allow_missing_timestamps=True,
    )
    assert artifact["training_summary"]["train_eval_split"]["chronology_status"] == "missing_timestamp"
    assert artifact["training_summary"]["train_eval_split"]["missing_timestamps_allowed"] is True


def test_rl_training_rejects_mixed_reward_columns(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "return": -0.20},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    with pytest.raises(ValueError, match="mixed rl reward columns"):
        train_rl_policy_artifact(
            training_data_path=training_path,
            feature_names=["order_book_imbalance"],
            device="cpu",
        )


def test_rl_training_allows_duplicate_timestamps(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "reward": 0.10},
        {"timestamp_ns": 1, "order_book_imbalance": -0.5, "reward": -0.20},
        {"timestamp_ns": 2, "order_book_imbalance": 0.0, "reward": 0.00},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = train_rl_policy_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        device="cpu",
    )

    assert artifact["training_summary"]["train_eval_split"]["chronology_status"] == "non_decreasing_timestamp"


def test_rl_training_reports_budget_exhaustion_after_truncation(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": idx, "order_book_imbalance": 0.5, "reward": 0.10}
        for idx in range(1, 6)
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = train_rl_policy_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        device="cpu",
        max_rows=3,
    )

    assert artifact["training_summary"]["source_row_count"] == 5
    assert artifact["training_summary"]["row_count"] == 3
    assert artifact["training_summary"]["training_budget"]["budget_exhausted"] is True


def test_rl_gpu_readiness_preflight_ready_after_runtime_smoke(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "spread": 1.0, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "spread": 1.0, "reward": -0.20},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = rl_gpu_training_readiness_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance", "spread"],
        gpu_host="MSI-RTX3080-smoke",
        command=(
            "python scripts/run_rl_gpu_smoke.py --training-data rl_rows.jsonl "
            "--feature order_book_imbalance --output-dir runtime/rl_gpu_smoke/test"
        ),
        output_dir="runtime/rl_gpu_smoke/test",
        expected_duration_minutes=5,
        stop_rule="stop after one mini-batch smoke checkpoint",
        runtime_probe=lambda: {"cuda_runtime_ok": True, "selected_runtime": "torch", "probes": []},
    )

    assert artifact["schema_version"] == "hft3_rl_gpu_readiness_v1"
    assert artifact["status"] == "ready_for_gpu_training"
    assert artifact["ready_for_gpu_training"] is True
    assert artifact["promotable"] is False
    assert artifact["device"] == "cuda"
    assert artifact["gpu_host"] == "MSI-RTX3080-smoke"
    assert artifact["training_data_receipt"]["sha256"]
    assert artifact["training_summary"]["row_count"] == 2
    assert artifact["training_summary"]["timestamp_field"] == "timestamp_ns"
    assert artifact["failure_reasons"] == []


def test_rl_gpu_readiness_preflight_blocks_missing_required_fields(tmp_path):
    training_path = tmp_path / "missing.jsonl"

    artifact = rl_gpu_training_readiness_artifact(
        training_data_path=training_path,
        feature_names=["future_pnl_label"],
        gpu_host="",
        command="",
        output_dir=".",
        expected_duration_minutes=0,
        stop_rule="",
        device="cpu",
        runtime_probe=lambda: {"cuda_runtime_ok": False, "selected_runtime": None, "probes": []},
    )

    assert artifact["status"] == "blocked_gpu_training_readiness"
    assert artifact["ready_for_gpu_training"] is False
    reasons = artifact["failure_reasons"]
    assert "rl_gpu_readiness_requires_cuda_device" in reasons
    assert any(reason.startswith("invalid_rl_features:") for reason in reasons)
    assert any(reason.startswith("invalid_training_data:") for reason in reasons)
    assert "gpu_host_missing" in reasons
    assert "command_missing" in reasons
    assert "output_dir_must_be_bounded" in reasons
    assert "expected_duration_minutes_must_be_positive" in reasons
    assert "stop_rule_missing" in reasons


def test_rl_gpu_readiness_preflight_rejects_unbounded_output_dirs(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "reward": -0.20},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    for output_dir in ("..", "../outside", tmp_path.resolve()):
        artifact = rl_gpu_training_readiness_artifact(
            training_data_path=training_path,
            feature_names=["order_book_imbalance"],
            gpu_host="MSI-RTX3080-smoke",
            command=(
                "python scripts/run_rl_gpu_smoke.py --training-data rl_rows.jsonl "
                "--output-dir runtime/rl_gpu_smoke/test"
            ),
            output_dir=output_dir,
            expected_duration_minutes=5,
            stop_rule="stop after one mini-batch smoke checkpoint",
            runtime_probe=lambda: {"cuda_runtime_ok": True, "selected_runtime": "torch", "probes": []},
        )
        assert "output_dir_must_be_bounded" in artifact["failure_reasons"]


def test_rl_gpu_readiness_preflight_blocks_when_runtime_smoke_required(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "reward": -0.20},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = rl_gpu_training_readiness_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        gpu_host="MSI-RTX3080-smoke",
        command=(
            "python scripts/run_rl_gpu_smoke.py --training-data rl_rows.jsonl "
            "--output-dir runtime/rl_gpu_smoke/test"
        ),
        output_dir="runtime/rl_gpu_smoke/test",
        expected_duration_minutes=5,
        stop_rule="stop after one mini-batch smoke checkpoint",
        runtime_probe=lambda: {"cuda_runtime_ok": False, "selected_runtime": None, "probes": []},
    )

    assert artifact["status"] == "blocked_gpu_training_readiness"
    assert artifact["ready_for_gpu_training"] is False
    assert artifact["runtime_smoke_required"] is True
    assert artifact["failure_reasons"] == ["cuda_runtime_smoke_failed"]


def test_rl_gpu_readiness_blocks_when_runtime_smoke_is_skipped(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "reward": -0.20},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = rl_gpu_training_readiness_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        gpu_host="H100-planned",
        command=(
            "python scripts/train_deep_rl_policy.py --training-data rl_rows.jsonl "
            "--output-dir runtime/rl/deep --resume-checkpoint runtime/rl/deep/checkpoint.pt"
        ),
        output_dir="runtime/rl/deep",
        expected_duration_minutes=5,
        stop_rule="stop after trainer checkpoint",
        require_runtime_smoke=False,
        runtime_probe=lambda: {"cuda_runtime_ok": False, "selected_runtime": None, "probes": []},
    )

    assert artifact["status"] == "blocked_gpu_training_readiness"
    assert artifact["ready_for_gpu_training"] is False
    assert artifact["runtime_smoke_required"] is False
    assert artifact["runtime_smoke"]["skipped"] is True
    assert artifact["failure_reasons"] == ["cuda_runtime_smoke_required_for_ready"]


def test_rl_gpu_readiness_preflight_validates_training_rows(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 2, "order_book_imbalance": 0.5, "reward": 0.10},
        {"timestamp_ns": 1, "reward": -0.20},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = rl_gpu_training_readiness_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        gpu_host="MSI-RTX3080-smoke",
        command=(
            "python scripts/run_rl_gpu_smoke.py --training-data rl_rows.jsonl "
            "--output-dir runtime/rl_gpu_smoke/test"
        ),
        output_dir="runtime/rl_gpu_smoke/test",
        expected_duration_minutes=5,
        stop_rule="stop after one mini-batch smoke checkpoint",
        runtime_probe=lambda: {"cuda_runtime_ok": True, "selected_runtime": "torch", "probes": []},
    )

    assert artifact["status"] == "blocked_gpu_training_readiness"
    assert artifact["ready_for_gpu_training"] is False
    assert any(reason.startswith("invalid_training_data:") for reason in artifact["failure_reasons"])


def test_rl_gpu_readiness_preflight_rejects_non_torch_runtime_probe(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "reward": -0.20},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = rl_gpu_training_readiness_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        gpu_host="MSI-RTX3080-smoke",
        command=(
            "python scripts/run_rl_gpu_smoke.py --training-data rl_rows.jsonl "
            "--output-dir runtime/rl_gpu_smoke/test"
        ),
        output_dir="runtime/rl_gpu_smoke/test",
        expected_duration_minutes=5,
        stop_rule="stop after one mini-batch smoke checkpoint",
        runtime_probe=lambda: {"cuda_runtime_ok": True, "selected_runtime": "cupy", "probes": []},
    )

    assert artifact["status"] == "blocked_gpu_training_readiness"
    assert artifact["failure_reasons"] == ["cuda_runtime_smoke_failed"]


def test_rl_gpu_readiness_preflight_requires_smoke_command_output_dir(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "reward": -0.20},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = rl_gpu_training_readiness_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        gpu_host="MSI-RTX3080-smoke",
        command="python scripts/run_rl_gpu_smoke.py --training-data rl_rows.jsonl",
        output_dir="runtime/rl_gpu_smoke/test",
        expected_duration_minutes=5,
        stop_rule="stop after one mini-batch smoke checkpoint",
        runtime_probe=lambda: {"cuda_runtime_ok": True, "selected_runtime": "torch", "probes": []},
    )

    assert artifact["status"] == "blocked_gpu_training_readiness"
    assert artifact["failure_reasons"] == ["command_output_dir_mismatch"]


def test_rl_gpu_readiness_rejects_vague_checkpoint_command(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "reward": -0.20},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = rl_gpu_training_readiness_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        gpu_host="H100-planned",
        command="python arbitrary_trainer.py --checkpoint somewhere.pt",
        output_dir="runtime/rl/deep",
        expected_duration_minutes=5,
        stop_rule="stop after trainer checkpoint",
        runtime_probe=lambda: {"cuda_runtime_ok": True, "selected_runtime": "torch", "probes": []},
    )

    assert artifact["status"] == "blocked_gpu_training_readiness"
    assert artifact["failure_reasons"] == ["command_must_be_resumable_or_bounded_smoke"]


def test_rl_gpu_readiness_rejects_chained_spoofed_command(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "reward": -0.20},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = rl_gpu_training_readiness_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        gpu_host="H100-planned",
        command=(
            "python scripts/run_rl_gpu_smoke.py --training-data rows.jsonl "
            "--output-dir runtime/rl_gpu_smoke/test && python unreviewed.py"
        ),
        output_dir="runtime/rl_gpu_smoke/test",
        expected_duration_minutes=5,
        stop_rule="stop after smoke checkpoint",
        runtime_probe=lambda: {"cuda_runtime_ok": True, "selected_runtime": "torch", "probes": []},
    )

    assert artifact["status"] == "blocked_gpu_training_readiness"
    assert artifact["failure_reasons"] == ["command_must_be_resumable_or_bounded_smoke"]


def test_rl_gpu_smoke_chronology_allows_duplicate_timestamps():
    smoke = _load_run_rl_gpu_smoke_module()

    field = smoke._chronology_field(
        [
            {"timestamp_ns": 1, "order_book_imbalance": 0.1, "reward": 0.1},
            {"timestamp_ns": 1, "order_book_imbalance": 0.2, "reward": 0.2},
            {"timestamp_ns": 2, "order_book_imbalance": 0.3, "reward": 0.3},
        ]
    )

    assert field == "timestamp_ns"


def test_rl_gpu_smoke_artifact_records_checkpoint_sha256(tmp_path, monkeypatch):
    smoke = _load_run_rl_gpu_smoke_module()
    training_path = tmp_path / "rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.1, "reward": 0.1},
        {"timestamp_ns": 1, "order_book_imbalance": 0.2, "reward": 0.2},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    class _FakeLoss:
        def backward(self):
            return None

        def detach(self):
            return self

        def cpu(self):
            return self

        def item(self):
            return 0.25

    class _FakeLinear:
        def __init__(self, *_args):
            pass

        def to(self, _device):
            return self

        def __call__(self, _x):
            return object()

        def parameters(self):
            return []

        def state_dict(self):
            return {"weight": 1}

    class _FakeAdam:
        def __init__(self, *_args, **_kwargs):
            pass

        def zero_grad(self, **_kwargs):
            return None

        def step(self):
            return None

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True, get_device_name=lambda _idx: "fake-h100"),
        manual_seed=lambda _seed: None,
        device=lambda value: value,
        tensor=lambda *_args, **_kwargs: object(),
        float32=object(),
        long=object(),
        nn=SimpleNamespace(Linear=_FakeLinear, CrossEntropyLoss=lambda: lambda _pred, _target: _FakeLoss()),
        optim=SimpleNamespace(Adam=_FakeAdam),
        save=lambda _payload, path: Path(path).write_bytes(b"checkpoint"),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    artifact = smoke.run_smoke(
        training_data_path=training_path,
        feature_names=["order_book_imbalance"],
        output_dir=tmp_path / "out",
        steps=1,
        seed=1,
        max_rows=8,
    )

    checkpoint_path = Path(artifact["checkpoint_path"])
    assert artifact["checkpoint_sha256"] == smoke._sha256_file(checkpoint_path)


def test_deep_rl_trainer_writes_non_promotable_artifact_or_runtime_block(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "spread": 1.0, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "spread": 1.0, "reward": -0.20},
        {"timestamp_ns": 3, "order_book_imbalance": 0.0, "spread": 2.0, "reward": 0.00},
        {"timestamp_ns": 4, "order_book_imbalance": 0.3, "spread": 1.5, "reward": 0.05},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = train_deep_rl_policy_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance", "spread"],
        output_dir=tmp_path / "deep",
        device="cpu",
        seed=7,
        steps=2,
        batch_size=2,
        hidden_dim=4,
    )

    validate_rl_deep_policy_artifact(artifact)
    assert artifact["schema_version"] == "hft3_deep_rl_policy_artifact_v1"
    assert artifact["promotable"] is False
    assert artifact["device"] == "cpu"
    assert artifact["training_data_receipt"]["sha256"]
    if artifact["status"] == "trained_research_only":
        assert artifact["checkpoint"]["sha256"]
        assert artifact["training_summary"]["train_eval_split"]["random_split"] is False
        assert artifact["training_summary"]["action_space"] == ["hold", "enter_long", "enter_short"]
        assert artifact["training_summary"]["reward_metadata"]["reward_units"] == "unknown"
    else:
        assert artifact["status"] == "blocked"
        assert artifact["failure_reasons"]

    written = write_rl_deep_policy_artifact(tmp_path / "deep_artifact.json", artifact)
    assert written.is_file()


def test_deep_rl_resume_rejects_checkpoint_schema_mismatch(tmp_path):
    torch = pytest.importorskip("torch")
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "spread": 1.0, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "spread": 1.0, "reward": -0.20},
        {"timestamp_ns": 3, "order_book_imbalance": 0.0, "spread": 2.0, "reward": 0.00},
        {"timestamp_ns": 4, "order_book_imbalance": 0.3, "spread": 1.5, "reward": 0.05},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    checkpoint_path = tmp_path / "bad_checkpoint.pt"
    torch.save({"schema_version": "wrong", "model_state_dict": {}}, checkpoint_path)

    with pytest.raises(ValueError, match="checkpoint schema mismatch"):
        train_deep_rl_policy_artifact(
            training_data_path=training_path,
            feature_names=["order_book_imbalance", "spread"],
            output_dir=tmp_path / "deep",
            device="cpu",
            seed=7,
            steps=1,
            batch_size=2,
            hidden_dim=4,
            resume_checkpoint=checkpoint_path,
        )


def test_deep_rl_resume_missing_checkpoint_blocks(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "spread": 1.0, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "spread": 1.0, "reward": -0.20},
        {"timestamp_ns": 3, "order_book_imbalance": 0.0, "spread": 2.0, "reward": 0.00},
        {"timestamp_ns": 4, "order_book_imbalance": 0.3, "spread": 1.5, "reward": 0.05},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    artifact = train_deep_rl_policy_artifact(
        training_data_path=training_path,
        feature_names=["order_book_imbalance", "spread"],
        output_dir=tmp_path / "deep",
        device="cpu",
        seed=7,
        steps=1,
        batch_size=2,
        hidden_dim=4,
        resume_checkpoint=tmp_path / "missing.pt",
    )

    assert artifact["status"] == "blocked"
    assert artifact["failure_reasons"] == ["resume_checkpoint_missing"]
