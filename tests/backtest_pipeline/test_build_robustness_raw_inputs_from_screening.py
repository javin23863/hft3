from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from backtest_pipeline.src.vectorbt_adapter import (
    _parameter_values_hash,
    compute_screening_artifact_hash,
    validate_screening_artifact,
)
from test_apply_robustness_evidence_to_screening import _screening_artifact, _write_json

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_robustness_raw_inputs_from_screening.py"


def _event_ids() -> list[str]:
    return [
        "CPI_2020_01_14_TIGHT",
        "CPI_2020_02_13_TIGHT",
        "CPI_2020_03_11_TIGHT",
        "CPI_2020_04_10_TIGHT",
    ]


def _param_values() -> list[dict[str, Any]]:
    return [
        {
            "signal_threshold": threshold,
            "holding_period_bars": 5,
            "stop_loss_pct": None,
            "take_profit_pct": None,
        }
        for threshold in (0.10, 0.11, 0.12, 0.13)
    ]


def _metric_row(
    template: dict[str, Any],
    *,
    event_id: str,
    params: dict[str, Any],
    index: int,
    promoted: bool,
) -> dict[str, Any]:
    row = copy.deepcopy(template)
    param_hash = _parameter_values_hash(params)
    candidate_id = f"{'prom' if promoted else 'rej'}_{event_id}_{index}"
    net_return = 0.10 + (index * 0.001)
    expectancy = 0.08 + (index * 0.001)
    row.update(
        {
            "candidate_id": candidate_id,
            "model_id": "QUEUE_DEPLETION_TRIGGER",
            "hypothesis_id": "QUEUE_DEPLETION_TRIGGER",
            "symbol": "ES",
            "base_candidate_id": f"QUEUE_DEPLETION_TRIGGER|ES.v.0|{event_id}|10",
            "base_candidate_metadata": {
                "event_id": event_id,
                "target_event_id": event_id,
                "event_type": "CPI",
                "symbol": "ES",
                "research_clock": "scheduled_event",
                "context_set_id": "target_only",
                "allowed_context_set_id": "target_only",
                "feature_recipe_hash": f"recipe_{event_id}",
            },
            "parameter_values": params,
            "param_values": params,
            "parameter_values_hash": param_hash,
            "feature_recipe_hash": f"recipe_{event_id}",
            "trade_count": 80,
            "gross_return": net_return,
            "net_return": net_return,
            "net_pnl": net_return * 10000.0,
            "expectancy_per_trade": expectancy,
            "profit_factor": 1.25 + (index * 0.01),
            "sharpe": 1.0 + (index * 0.05),
            "sortino": 1.1 + (index * 0.05),
            "max_drawdown": -0.01,
            "turnover": 80.0,
            "total_fees": 0.0,
            "total_slippage": 0.0,
        }
    )
    if promoted:
        row["screening_status"] = "pass"
        row["pass_reason"] = "vectorbt_simulated"
        row["rejection_reason_or_null"] = (
            "vbt2_pilot_screen_only_without_real_wfc_dsr_pbo_cscv_pass_evidence:"
            + candidate_id
        )
    else:
        row["screening_status"] = "rejected"
        row["reject_reason"] = "promotion_gate_failed"
        row["rejection_reason_or_null"] = "promotion_gate_failed"
        row["replay_eligibility_status"] = "not_eligible"
        row["vectorbt_results"] = {}
        row["metric_values"] = {
            "base_candidate_id": row["base_candidate_id"],
            "base_candidate_metadata": row["base_candidate_metadata"],
            "parameter_values": params,
            "param_values": params,
            "feature_recipe_hash": row["feature_recipe_hash"],
            "trade_count": row["trade_count"],
            "net_return": row["net_return"],
            "net_pnl": row["net_pnl"],
            "expectancy": row["expectancy_per_trade"],
            "profit_factor": row["profit_factor"],
            "sharpe": row["sharpe"],
            "max_drawdown": row["max_drawdown"],
        }
    return row


def _complete_surface_artifact(*, omit_last_cell: bool = False) -> dict[str, Any]:
    base = _screening_artifact("placeholder_promoted")
    template = base["promoted"][0]
    rows: list[dict[str, Any]] = []
    promoted_row: dict[str, Any] | None = None
    for event_index, event_id in enumerate(_event_ids()):
        for param_index, params in enumerate(_param_values()):
            promoted = event_index == len(_event_ids()) - 1 and param_index == 0
            if omit_last_cell and event_index == len(_event_ids()) - 1 and param_index == len(_param_values()) - 1:
                continue
            row = _metric_row(
                template,
                event_id=event_id,
                params=params,
                index=param_index,
                promoted=promoted,
            )
            if promoted:
                promoted_row = row
            else:
                rows.append(row)
    assert promoted_row is not None
    rejected = rows
    base["promoted"] = [promoted_row]
    base["rejected"] = rejected
    base["promoted_ids"] = [promoted_row["candidate_id"]]
    base["rejected_ids"] = [row["candidate_id"] for row in rejected]
    base["candidate_ids"] = base["promoted_ids"] + base["rejected_ids"]
    base["promoted_reasons"] = {promoted_row["candidate_id"]: "vectorbt_simulated"}
    base["rejected_reasons"] = {
        row["candidate_id"]: str(row["rejection_reason_or_null"]) for row in rejected
    }
    base["candidate_reasons"] = {**base["promoted_reasons"], **base["rejected_reasons"]}
    base["promoted_count"] = len(base["promoted"])
    base["rejected_count"] = len(base["rejected"])
    base["max_trials"] = len(_param_values())
    base["trials_run"] = len(base["candidate_ids"])
    base["max_total_trials"] = len(base["candidate_ids"])
    base["screening_artifact_hash"] = compute_screening_artifact_hash(base)
    validate_screening_artifact(base)
    return base


def _run_script(tmp_path: Path, artifact: dict[str, Any], *extra: str) -> subprocess.CompletedProcess[str]:
    screening_path = tmp_path / "screening_artifact.json"
    out_path = tmp_path / "raw_robustness_inputs.json"
    _write_json(screening_path, artifact)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--source-root",
            str(tmp_path),
            "--out",
            str(out_path),
            "--folds",
            "3",
            "--min-events",
            "4",
            "--min-parameter-combinations",
            "4",
            *extra,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_builds_raw_inputs_from_complete_screening_surface(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact()
    result = _run_script(
        tmp_path,
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "ok"
    assert receipt["packaged_count"] == 1

    payload = json.loads((tmp_path / "raw_robustness_inputs.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "hft3_robustness_raw_inputs_v1"
    assert payload["feature_recipe_hash_policy"] == "event_specific_hash_bound_per_candidate"
    candidate_id = receipt["packaged_candidate_ids"][0]
    entry = payload["candidates"][candidate_id]
    raw = entry["robustness_input"]
    assert len(raw["per_event_expectancies"]) == 4
    assert raw["n_trials"] == 4
    assert len(raw["cscv_matrix"]) == 4
    assert len(raw["wfc_rows"]) == 12
    assert entry["surface_stability_metrics"]["status"] == "pass"
    assert entry["source_evidence"]["screening_artifact"]["path"] == "screening_artifact.json"
    assert all(not item.startswith("rej_") for item in payload["candidates"])


def test_accepts_surface_rows_without_replay_net_pnl(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact()
    not_run_pnl = {
        "status": "not_run",
        "reason": "candidate_rejected_before_replay:promotion_gate_failed",
    }
    for row in artifact["rejected"]:
        row["net_pnl"] = not_run_pnl
        metric_values = row.get("metric_values")
        assert isinstance(metric_values, dict)
        metric_values.pop("net_pnl", None)
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    validate_screening_artifact(artifact)

    result = _run_script(
        tmp_path,
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "ok"
    assert receipt["packaged_count"] == 1


def test_accepts_surface_rows_from_official_vbt_stats(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact()
    not_run_reason = "candidate_rejected_before_replay:vectorbt_stats_missing_gate_fields"
    not_run_metric = {"status": "not_run", "reason": not_run_reason}
    for row in artifact["rejected"]:
        stats = {
            "Total Trades": row["trade_count"],
            "Expectancy": row["expectancy_per_trade"],
            "Total Return [%]": row["net_return"] * 100.0,
            "Max Drawdown [%]": None,
            "Sharpe Ratio": row["sharpe"],
            "Profit Factor": "inf",
        }
        row["net_return"] = not_run_metric
        row["net_pnl"] = not_run_metric
        row["expectancy_per_trade"] = not_run_metric
        row["profit_factor"] = not_run_metric
        row["sharpe"] = not_run_metric
        row["max_drawdown"] = not_run_metric
        row["trade_count"] = not_run_metric
        row["metric_values"] = {
            "base_candidate_id": row["base_candidate_id"],
            "base_candidate_metadata": row["base_candidate_metadata"],
            "parameter_values": row["parameter_values"],
            "param_values": row["param_values"],
            "feature_recipe_hash": row["feature_recipe_hash"],
            "vbt_stats": stats,
            "gate_metric_authority": "official_vectorbt_portfolio_stats",
        }
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    validate_screening_artifact(artifact)

    result = _run_script(
        tmp_path,
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "ok"
    assert receipt["packaged_count"] == 1


def test_missing_stress_args_fails_closed_without_output(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact()
    result = _run_script(tmp_path, artifact)

    assert result.returncode != 0
    assert "stress_decomposition_missing" in result.stderr
    assert not (tmp_path / "raw_robustness_inputs.json").exists()


def test_incomplete_surface_fails_closed_without_output(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact(omit_last_cell=True)
    result = _run_script(
        tmp_path,
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
    )

    assert result.returncode != 0
    assert "incomplete_event_parameter_surface" in result.stderr
    assert not (tmp_path / "raw_robustness_inputs.json").exists()


def test_min_completeness_packages_complete_parameter_subset(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact(omit_last_cell=True)
    result = _run_script(
        tmp_path,
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
        "--min-completeness",
        "0.9",
        "--min-parameter-combinations",
        "3",
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "ok"
    assert receipt["packaged_count"] == 1
    payload = json.loads((tmp_path / "raw_robustness_inputs.json").read_text(encoding="utf-8"))
    candidate_id = receipt["packaged_candidate_ids"][0]
    assert payload["candidates"][candidate_id]["robustness_input"]["n_trials"] == 3


def test_unknown_symbol_fails_closed_without_output(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact()
    for row in [*artifact["promoted"], *artifact["rejected"]]:
        row["symbol"] = "unknown"
        metadata = row.get("base_candidate_metadata")
        if isinstance(metadata, dict):
            metadata.pop("symbol", None)
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    validate_screening_artifact(artifact)

    result = _run_script(
        tmp_path,
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
    )

    assert result.returncode != 0
    assert "family_key_missing" in result.stderr
    assert not (tmp_path / "raw_robustness_inputs.json").exists()
