from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from backtest_pipeline.src import hftbacktest_realism as hbt5

sys.path.append(str(Path(__file__).resolve().parent))

from test_hftbacktest_realism_hbt4 import (  # noqa: E402
    NATIVE_CPP_LATENCY_EVIDENCE,
    _constant_latency_model,
    _valid_l2_fill_queue_model,
    _write_json,
    _write_valid_inputs,
    hbt4_contract,
)


def _write_valid_hbt5_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    return _write_valid_inputs(tmp_path, include_intent=True)


def _write_observation_artifact(
    path: Path,
    *,
    overrides: dict[str, object] | None = None,
) -> Path:
    payload: dict[str, object] = {
        "artifact_type": "hbt5_offline_observation_comparison",
        "schema_version": 1,
        "candidate_id": "cand_hbt4",
        "model_id": "HYP_5",
        "symbol": "MES",
        "source": "offline_cme_mbo_replay_observation",
        "metrics": {
            "fill_rate": 0.0,
            "latency_p50_ms": 3.1,
            "latency_p90_ms": 5.2,
            "latency_p99_ms": 6.256,
            "maker_fees": _valid_l2_fill_queue_model()["maker_fee"],
            "taker_fees": _valid_l2_fill_queue_model()["taker_fee"],
            "total_fees": 0.0,
            "total_slippage": 0.25,
            "adverse_selection_markout": 0.0,
            "spread_capture_or_cost": 0.0,
        },
        "order_state": {
            "orders_intended": 1,
            "orders_submitted": 1,
            "orders_acknowledged": 1,
            "orders_cancelled": 1,
            "fills_count": 0,
            "partial_fills_count": 0,
            "unfilled_count": 1,
        },
        "parameter_values": {"signal_threshold": 0.15},
        "parameter_values_hash": "sha256:parameter-values",
    }
    if overrides:
        payload = copy.deepcopy(payload)
        for key, value in overrides.items():
            if key == "metrics" and isinstance(value, dict):
                metrics = dict(payload["metrics"])  # type: ignore[arg-type]
                metrics.update(value)
                payload["metrics"] = metrics
            elif key == "order_state" and isinstance(value, dict):
                order_state = dict(payload["order_state"])  # type: ignore[arg-type]
                order_state.update(value)
                payload["order_state"] = order_state
            else:
                payload[key] = value
    return _write_json(path, payload)


def _run_hbt5(
    tmp_path: Path,
    *,
    observation_artifact_path: Path | None,
) -> tuple[Path, dict]:
    screening_path, data_path, latency_path, fill_queue_path = _write_valid_hbt5_inputs(tmp_path)
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt5"

    payload = hbt5.write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        data_npz_path=data_path,
        latency_model_path=latency_path,
        fill_queue_model_path=fill_queue_path,
        observation_artifact_path=observation_artifact_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt5",
    )
    return out_dir, payload


def test_hbt5_no_observation_writes_not_run_comparison_and_blocks_feedback(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    out_dir, payload = _run_hbt5(tmp_path, observation_artifact_path=None)

    comparison_path = out_dir / "discrepancy_comparison.json"
    assert comparison_path.is_file()
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    summary = payload["replay_summary"]

    assert comparison["comparison_status"] == "not_run"
    assert comparison["certification_feedback_status"] == "blocked_missing_observation"
    assert "hbt5_discrepancy_comparison_not_run" in comparison["discrepancy_reasons"]
    assert "hbt5_discrepancy_comparison_not_run" not in summary["fail_closed_reasons"]
    assert summary["replay_realism_status"] == "pass"
    assert "submit_buy_or_sell_order" in hbt4_contract


def test_hbt5_malformed_observation_json_fails_closed(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    observation_path = tmp_path / "malformed_observation.json"
    observation_path.write_text("{not-json", encoding="utf-8")

    out_dir, payload = _run_hbt5(tmp_path, observation_artifact_path=observation_path)

    comparison = json.loads((out_dir / "discrepancy_comparison.json").read_text(encoding="utf-8"))
    summary = payload["replay_summary"]

    assert comparison["comparison_status"] == "fail"
    assert comparison["reason"] == "observation_artifact_malformed"
    assert "hbt5_observation_artifact_malformed" in comparison["fail_closed_reasons"]
    assert "hbt5_observation_artifact_malformed" in summary["fail_closed_reasons"]
    assert summary["replay_realism_status"] != "pass"
    assert "submit_buy_or_sell_order" in hbt4_contract


def test_hbt5_matching_observation_passes_ready_without_hidden_parameter_mutations(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    observation_path = _write_observation_artifact(tmp_path / "matching_observation.json")

    out_dir, payload = _run_hbt5(tmp_path, observation_artifact_path=observation_path)

    comparison = json.loads((out_dir / "discrepancy_comparison.json").read_text(encoding="utf-8"))
    input_manifest = json.loads((out_dir / "input_manifest.json").read_text(encoding="utf-8"))
    summary = payload["replay_summary"]

    assert comparison["comparison_status"] == "pass"
    assert comparison["certification_feedback_status"] == "ready"
    assert comparison["discrepancies"] == []
    assert comparison["hidden_parameter_mutations"] == []
    assert input_manifest["candidate_metadata"]["parameter_values"] == {"signal_threshold": 0.15}
    assert "parameter_mutations" not in input_manifest["candidate_metadata"]
    assert "hidden_parameter_mutation" not in summary["fail_closed_reasons"]
    assert "hbt5_discrepancy_comparison_not_run" not in summary["fail_closed_reasons"]
    assert summary["replay_realism_status"] == "pass"
    assert "submit_buy_or_sell_order" in hbt4_contract


def test_hbt5_wrong_candidate_observation_fails_closed(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    observation_path = _write_observation_artifact(
        tmp_path / "wrong_candidate_observation.json",
        overrides={
            "candidate_id": "other_candidate",
            "parameter_values_hash": "sha256:retuned-parameters",
        },
    )

    out_dir, payload = _run_hbt5(tmp_path, observation_artifact_path=observation_path)

    comparison = json.loads((out_dir / "discrepancy_comparison.json").read_text(encoding="utf-8"))
    summary = payload["replay_summary"]

    assert comparison["comparison_status"] == "fail"
    assert "hbt5_observation_identity_mismatch:candidate_id" in comparison["fail_closed_reasons"]
    assert "hbt5_observation_identity_mismatch:parameter_values_hash" in summary["fail_closed_reasons"]
    assert summary["replay_realism_status"] != "pass"
    assert "submit_buy_or_sell_order" in hbt4_contract


def test_hbt5_missing_nested_observation_sections_fail_closed(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    observation_path = _write_json(
        tmp_path / "missing_nested_observation.json",
        {
            "candidate_id": "cand_hbt4",
            "model_id": "HYP_5",
            "symbol": "MES",
            "parameter_values_hash": "sha256:parameter-values",
        },
    )

    out_dir, payload = _run_hbt5(tmp_path, observation_artifact_path=observation_path)

    comparison = json.loads((out_dir / "discrepancy_comparison.json").read_text(encoding="utf-8"))
    summary = payload["replay_summary"]

    assert comparison["comparison_status"] == "fail"
    assert "hbt5_observation_metrics_missing_or_malformed" in comparison["fail_closed_reasons"]
    assert "hbt5_observation_order_state_missing_or_malformed" in summary["fail_closed_reasons"]
    assert summary["replay_realism_status"] != "pass"
    assert "submit_buy_or_sell_order" in hbt4_contract


def test_hbt5_mismatching_observation_records_discrepancies_and_blocks_certification(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    observation_path = _write_observation_artifact(
        tmp_path / "mismatching_observation.json",
        overrides={
            "metrics": {
                "fill_rate": 1.0,
                "latency_p99_ms": _constant_latency_model()["latency_p99_ms"] + 10.0,
                "total_fees": 9.5,
                "total_slippage": 3.0,
                "adverse_selection_markout": -2.25,
            },
            "order_state": {
                "orders_cancelled": 0,
                "fills_count": 1,
                "unfilled_count": 0,
            },
        },
    )

    out_dir, payload = _run_hbt5(tmp_path, observation_artifact_path=observation_path)

    comparison = json.loads((out_dir / "discrepancy_comparison.json").read_text(encoding="utf-8"))
    summary = payload["replay_summary"]
    discrepancy_fields = {entry["field"] for entry in comparison["discrepancies"]}

    assert comparison["comparison_status"] == "fail"
    assert comparison["certification_feedback_status"] == "blocked_discrepancy"
    assert {
        "fill_rate",
        "latency_p99_ms",
        "total_fees",
        "total_slippage",
        "adverse_selection_markout",
        "order_state.orders_cancelled",
        "order_state.fills_count",
        "order_state.unfilled_count",
    }.issubset(discrepancy_fields)
    assert "hbt5_discrepancy_comparison_failed" in summary["fail_closed_reasons"]
    assert summary["replay_realism_status"] != "pass"
    assert "submit_buy_or_sell_order" in hbt4_contract
