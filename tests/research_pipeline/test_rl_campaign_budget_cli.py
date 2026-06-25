from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_cli_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "plan_rl_gpu_campaign_budget.py"
    spec = importlib.util.spec_from_file_location("plan_rl_gpu_campaign_budget_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_plan_rl_gpu_campaign_budget_cli_writes_pilot_ready_artifact(tmp_path, capsys) -> None:
    cli = _load_cli_module()
    manifest = tmp_path / "feature_manifest.jsonl"
    manifest.write_text(
        "\n".join(
            [
                json.dumps({"symbol": "ES.v.0", "event_id": "EVT_A", "n_rows": 100}),
                json.dumps({"symbol": "NQ.v.0", "event_id": "EVT_A", "n_rows": 200}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "rl_campaign_budget_plan.json"

    code = cli.main(
        [
            "--feature-manifest",
            str(manifest),
            "--vast-credit-usd",
            "12.90",
            "--gpu-hour-rate-usd",
            "1.0956",
            "--budget-reserve-usd",
            "1.00",
            "--out",
            str(out),
        ]
    )

    assert code == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert printed["out"] == str(out)
    assert printed["pilot_selected_rows"] >= 300
    assert printed["measured_throughput_row_basis"] == "manifest_source_rows"
    assert printed["full_training_ready"] is False
    assert printed["estimated_full_inventory_gpu_hours"] is None
    assert printed["estimated_full_inventory_cost_usd"] is None
    assert printed["estimated_full_inventory_covered"] is None
    assert payload["status"] == "pilot_plan_ready_full_training_blocked"
    assert payload["training_started"] is False
    assert payload["npz_payloads_read"] is False
    assert payload["measured_throughput_row_basis"] == "manifest_source_rows"
    assert payload["known_inventory_rows"] == 300
    assert payload["stage_statuses"]["stratified_pilot"]["status"] == "planned"
    assert payload["stage_statuses"]["stratified_pilot"]["selection"]["selected_symbols"] == [
        "ES.v.0",
        "NQ.v.0",
    ]
    assert payload["stage_statuses"]["full_training"]["status"] == "blocked"
    assert "measured_throughput_missing" in payload["stage_statuses"]["full_training"]["failure_reasons"]
    assert "weighted_depth_price" in payload["unsupported_required_features"]


def test_plan_rl_gpu_campaign_budget_cli_derives_supported_full_campaign_budget(
    tmp_path, capsys
) -> None:
    cli = _load_cli_module()
    manifest = tmp_path / "feature_manifest.jsonl"
    manifest.write_text(
        "\n".join(
            [
                json.dumps({"symbol": "ES.v.0", "event_id": "EVT_A", "source_rows": 100}),
                json.dumps({"symbol": "NQ.v.0", "event_id": "EVT_A", "row_summary": {"source_rows": 200}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "supported_budget_plan.json"

    code = cli.main(
        [
            "--feature-manifest",
            str(manifest),
            "--vast-credit-usd",
            "12.90",
            "--gpu-hour-rate-usd",
            "1.0956",
            "--budget-reserve-usd",
            "1.00",
            "--measured-source-rows",
            "600",
            "--measured-duration-seconds",
            "60",
            "--required-feature",
            "spread",
            "--out",
            str(out),
        ]
    )

    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "full_training_plan_ready"
    assert printed["full_training_status"] == "planned"
    assert printed["full_training_ready"] is True
    assert printed["estimated_full_inventory_covered"] is True
    assert payload["measured_throughput_row_basis"] == "manifest_source_rows"
    assert payload["measured_throughput_rows_per_gpu_hour"] == 36000.0
    assert payload["known_inventory_rows"] == 300
    assert payload["unsupported_required_features"] == []
    assert payload["stage_statuses"]["full_training"]["status"] == "planned"


def test_plan_rl_gpu_campaign_budget_cli_uses_effective_manifest_last_wins(
    tmp_path, capsys
) -> None:
    cli = _load_cli_module()
    manifest = tmp_path / "feature_manifest.jsonl"
    manifest.write_text(
        "\n".join(
            [
                json.dumps({"symbol": "ES.v.0", "event_id": "EVT_A", "source_rows": 100}),
                json.dumps({"symbol": "ES.v.0", "event_id": "EVT_A", "source_rows": 250}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "supported_budget_plan.json"

    code = cli.main(
        [
            "--feature-manifest",
            str(manifest),
            "--vast-credit-usd",
            "12.90",
            "--gpu-hour-rate-usd",
            "1.0956",
            "--budget-reserve-usd",
            "1.00",
            "--measured-source-rows",
            "600",
            "--measured-duration-seconds",
            "60",
            "--required-feature",
            "spread",
            "--out",
            str(out),
        ]
    )

    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "full_training_plan_ready"
    assert payload["known_inventory_rows"] == 250
    assert payload["manifest_source_row_fingerprint"]["entry_count"] == 1
    assert payload["manifest_source_row_fingerprint"]["source_row_count"] == 250


def test_plan_rl_gpu_campaign_budget_cli_rejects_unsupported_supported_feature(tmp_path) -> None:
    cli = _load_cli_module()
    manifest = tmp_path / "feature_manifest.jsonl"
    manifest.write_text(
        json.dumps({"symbol": "ES.v.0", "event_id": "EVT_A", "n_rows": 100}) + "\n",
        encoding="utf-8",
    )

    try:
        cli.main(
            [
                "--feature-manifest",
                str(manifest),
                "--vast-credit-usd",
                "12.90",
                "--gpu-hour-rate-usd",
                "1.0956",
                "--supported-feature",
                "vamp",
            ]
        )
    except ValueError as exc:
        assert "fs_v1 RL builder" in str(exc)
    else:  # pragma: no cover - defensive assertion.
        raise AssertionError("unsupported supported-feature override should fail")
