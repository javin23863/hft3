from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from research_pipeline.rl_campaign_budget import plan_rl_campaign_budget


def _load_cli_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_rl_gpu_campaign.py"
    spec = importlib.util.spec_from_file_location("run_rl_gpu_campaign_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_rl_gpu_campaign_groups_manifest_events_by_symbol() -> None:
    cli = _load_cli_module()
    grouped = cli._event_ids_by_symbol(
        {
            ("ES.v.0", "EVT_B"): {"n_rows": 10},
            ("ES.v.0", "EVT_A"): {"n_rows": 10},
            ("NQ.v.0", "EVT_A"): {"symbol": "NQ.v.0", "event_id": "EVT_A"},
        },
        selected_symbols=None,
    )

    assert grouped == {
        "ES.v.0": ["EVT_A", "EVT_B"],
        "NQ.v.0": ["EVT_A"],
    }


def test_rl_gpu_campaign_rejects_missing_selected_symbol() -> None:
    cli = _load_cli_module()

    with pytest.raises(ValueError, match="selected symbols not found"):
        cli._event_ids_by_symbol(
            {("ES.v.0", "EVT_A"): {"n_rows": 10}},
            selected_symbols=["NQ.v.0"],
        )


def test_rl_gpu_campaign_defaults_worker_count_to_symbol_and_cpu_cap(monkeypatch) -> None:
    cli = _load_cli_module()
    monkeypatch.setattr(cli.os, "cpu_count", lambda: 32)

    assert cli._build_worker_count(requested=0, symbol_count=7) == 7
    assert cli._build_worker_count(requested=2, symbol_count=7) == 2
    assert cli._build_worker_count(requested=100, symbol_count=7) == 7


def test_rl_gpu_campaign_rejects_unsupported_feature() -> None:
    cli = _load_cli_module()

    with pytest.raises(ValueError, match="fs_v1 RL builder"):
        cli._validated_features(["spread", "vamp"])


def test_rl_gpu_campaign_budget_ready_gate(tmp_path) -> None:
    cli = _load_cli_module()
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
    plan_path = tmp_path / "budget.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    loaded = cli._load_budget_plan(plan_path)
    cli._require_budget_ready(loaded, ["spread"], manifest)

    with pytest.raises(ValueError, match="required_features"):
        cli._require_budget_ready(loaded, ["queue_imbalance"], manifest)

    loaded["status"] = "pilot_plan_ready_full_training_blocked"
    with pytest.raises(ValueError, match="not full_training_plan_ready"):
        cli._require_budget_ready(loaded, ["spread"], manifest)


def test_rl_gpu_campaign_requires_reviewed_budget_plan_sha(tmp_path) -> None:
    cli = _load_cli_module()
    plan_path = tmp_path / "budget.json"
    plan_path.write_text('{"status": "full_training_plan_ready"}', encoding="utf-8")
    expected = hashlib.sha256(plan_path.read_bytes()).hexdigest()

    assert cli._require_budget_plan_sha(plan_path, expected.upper()) == expected
    with pytest.raises(ValueError, match="sha256 does not match"):
        cli._require_budget_plan_sha(plan_path, "0" * 64)


def test_rl_gpu_campaign_launch_gate_requires_vast_approval() -> None:
    cli = _load_cli_module()
    args = cli.argparse.Namespace(
        gpu_host="vastai:42465841",
        host_kind="vastai",
        expected_duration_minutes=45,
        stop_rule="stop after summary or sixty minutes",
        operator_approval="approved-vastai-paid-rl-campaign",
        allow_pre_ppo_proxy=True,
        device="cuda",
    )

    gate = cli._require_launch_gate(
        args=args,
        argv=["--output-root", "/workspace/hft3/runtime/rl"],
        budget_plan_sha256="a" * 64,
        current_os_name="posix",
    )

    assert gate["status"] == "ready_for_paid_gpu_campaign"
    assert gate["host_kind"] == "vastai"
    assert gate["operator_approval"] is True
    assert gate["ppo_status"] == "deferred; this campaign is not RL-5 PPO completion"


def test_rl_gpu_campaign_launch_gate_blocks_windows_and_missing_ack() -> None:
    cli = _load_cli_module()
    args = cli.argparse.Namespace(
        gpu_host="vastai:42465841",
        host_kind="vastai",
        expected_duration_minutes=45,
        stop_rule="stop after summary",
        operator_approval="",
        allow_pre_ppo_proxy=False,
        device="cuda",
    )

    with pytest.raises(ValueError, match="vastai_campaign_refuses_windows_msi_host"):
        cli._require_launch_gate(
            args=args,
            argv=[],
            budget_plan_sha256="a" * 64,
            current_os_name="nt",
        )


def test_rl_gpu_campaign_budget_ready_gate_rejects_stale_manifest() -> None:
    cli = _load_cli_module()
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


def test_rl_gpu_campaign_train_symbol_safe_returns_failure_receipt(monkeypatch, tmp_path) -> None:
    cli = _load_cli_module()

    def boom(**_kwargs):
        raise RuntimeError("train exploded")

    monkeypatch.setattr(cli, "_train_symbol", boom)
    receipt = cli._train_symbol_safe(
        rows_path=tmp_path / "rows.jsonl",
        output_dir=tmp_path / "policy",
        feature_names=["spread"],
        device="cuda",
        seed=42,
        max_rows=100,
        steps=1,
        batch_size=16,
        hidden_dim=8,
        learning_rate=0.001,
        eval_fraction=0.2,
    )

    assert receipt["status"] == "failed"
    assert receipt["failure_reasons"] == ["train exploded"]
    assert receipt["max_rows"] == 100
    assert receipt["budget_exhausted"] is False


def test_rl_gpu_campaign_train_symbol_receipt_keeps_cap_metadata(monkeypatch, tmp_path) -> None:
    cli = _load_cli_module()

    def fake_train(**kwargs):
        max_rows = kwargs["max_rows"]
        return {
            "status": "trained_research_only",
            "failure_reasons": [],
            "checkpoint": {"path": "checkpoint.pt"},
            "training_summary": {
                "row_count": max_rows,
                "source_row_count": max_rows + 2,
                "max_rows": max_rows,
                "training_budget": {"budget_exhausted": True},
                "eval_mse": 0.25,
            },
        }

    monkeypatch.setattr(cli, "train_deep_rl_policy_artifact", fake_train)
    monkeypatch.setattr(cli, "write_rl_deep_policy_artifact", lambda *_args, **_kwargs: None)

    receipt = cli._train_symbol(
        rows_path=tmp_path / "rows.jsonl",
        output_dir=tmp_path / "policy",
        feature_names=["spread"],
        device="cuda",
        seed=42,
        max_rows=3,
        steps=1,
        batch_size=16,
        hidden_dim=8,
        learning_rate=0.001,
        eval_fraction=0.2,
        resume_checkpoint=None,
    )

    assert receipt["status"] == "trained_research_only"
    assert receipt["row_count"] == 3
    assert receipt["source_row_count"] == 5
    assert receipt["max_rows"] == 3
    assert receipt["budget_exhausted"] is True


def test_rl_gpu_campaign_budget_receipt_marks_capped_symbols() -> None:
    cli = _load_cli_module()

    budget = cli._campaign_training_budget_receipt(
        [
            {
                "symbol": "ES.v.0",
                "train": {
                    "status": "trained_research_only",
                    "max_rows": 50_000_000,
                    "budget_exhausted": True,
                },
            },
            {
                "symbol": "NQ.v.0",
                "train": {
                    "status": "trained_research_only",
                    "max_rows": 50_000_000,
                    "budget_exhausted": False,
                },
            },
        ],
        max_rows=50_000_000,
    )

    assert budget == {
        "max_rows": 50_000_000,
        "budget_exhausted": True,
        "any_symbol_capped": True,
        "capped_symbol_count": 1,
        "capped_symbols": ["ES.v.0"],
    }
    assert cli._campaign_status(failure_count=0, budget_exhausted=True) == "partial_budget_exhausted"
    assert cli._campaign_status(failure_count=0, budget_exhausted=False) == "completed"
