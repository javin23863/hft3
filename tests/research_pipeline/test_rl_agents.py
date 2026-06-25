from __future__ import annotations

import json

import pytest

from research_pipeline.rl_agents import (
    train_or_load_rl_policy_artifact,
    train_rl_policy_artifact,
    validate_rl_features,
    validate_rl_policy_artifact,
    write_rl_policy_artifact,
)


def test_rl_cpu_training_writes_non_promotable_policy_artifact(tmp_path):
    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"order_book_imbalance": 0.5, "spread": 1.0, "reward": 0.10},
        {"order_book_imbalance": -0.5, "spread": 1.0, "reward": -0.20},
        {"order_book_imbalance": 0.0, "spread": 2.0, "reward": 0.00},
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
    assert artifact["q_table"]

    written = write_rl_policy_artifact(tmp_path / "rl_policy_artifact.json", artifact)
    assert written.is_file()


def test_rl_cuda_builds_blocked_gpu_handoff_artifact(tmp_path):
    training_path = tmp_path / "rl_rows.json"
    training_path.write_text(
        json.dumps(
            {
                "rows": [
                    {"order_book_imbalance": 0.5, "reward": 0.1},
                    {"order_book_imbalance": -0.2, "reward": -0.1},
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
        {"order_book_imbalance": 0.5, "spread": 1.0, "reward": 0.10},
        {"order_book_imbalance": -0.5, "spread": 1.0, "reward": -0.20},
        {"order_book_imbalance": 0.0, "spread": 2.0, "reward": 0.00},
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

    with pytest.raises(ValueError, match="non-PIT or label-like"):
        validate_rl_features(["future_pnl_label"])
