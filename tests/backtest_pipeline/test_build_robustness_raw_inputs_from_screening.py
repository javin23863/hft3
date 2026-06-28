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
from scripts.build_robustness_raw_inputs_from_screening import _extract_measured_row
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


def _row_event_id(row: dict[str, Any]) -> str:
    metadata = row.get("base_candidate_metadata")
    assert isinstance(metadata, dict)
    return str(metadata.get("event_id") or metadata.get("target_event_id"))


def _write_event_unit_artifacts(root: Path, artifact: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    reason_by_id = dict(artifact.get("candidate_reasons", {}))
    for event_id in _event_ids():
        unit = copy.deepcopy(artifact)
        unit["promoted"] = [
            row for row in artifact["promoted"] if _row_event_id(row) == event_id
        ]
        unit["rejected"] = [
            row for row in artifact["rejected"] if _row_event_id(row) == event_id
        ]
        unit["promoted_ids"] = [row["candidate_id"] for row in unit["promoted"]]
        unit["rejected_ids"] = [row["candidate_id"] for row in unit["rejected"]]
        unit["candidate_ids"] = unit["promoted_ids"] + unit["rejected_ids"]
        unit["promoted_reasons"] = {
            candidate_id: reason_by_id.get(candidate_id, "vectorbt_simulated")
            for candidate_id in unit["promoted_ids"]
        }
        unit["rejected_reasons"] = {
            candidate_id: reason_by_id.get(candidate_id, "promotion_gate_failed")
            for candidate_id in unit["rejected_ids"]
        }
        unit["candidate_reasons"] = {
            **unit["promoted_reasons"],
            **unit["rejected_reasons"],
        }
        unit["promoted_count"] = len(unit["promoted"])
        unit["rejected_count"] = len(unit["rejected"])
        unit["trials_run"] = len(unit["candidate_ids"])
        unit["max_total_trials"] = len(unit["candidate_ids"])
        unit["screening_artifact_hash"] = compute_screening_artifact_hash(unit)
        validate_screening_artifact(unit)
        unit_path = root / event_id / "screening_artifact.json"
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(unit_path, unit)
    return root


def _rewrite_metric(row: dict[str, Any], *, net_return: float, expectancy: float) -> None:
    row["gross_return"] = net_return
    row["net_return"] = net_return
    row["net_pnl"] = net_return * 10000.0
    row["expectancy_per_trade"] = expectancy
    row["sharpe"] = 1.0 if net_return > 0 else -1.0
    row["profit_factor"] = 1.25 if net_return > 0 else 0.75
    row["max_drawdown"] = -0.01
    row["trade_count"] = 80
    metric_values = row.get("metric_values")
    if isinstance(metric_values, dict):
        metric_values["net_return"] = row["net_return"]
        metric_values["net_pnl"] = row["net_pnl"]
        metric_values["expectancy"] = row["expectancy_per_trade"]
        metric_values["profit_factor"] = row["profit_factor"]
        metric_values["sharpe"] = row["sharpe"]
        metric_values["max_drawdown"] = row["max_drawdown"]
        metric_values["trade_count"] = row["trade_count"]


def _first_event_fail_artifact() -> dict[str, Any]:
    artifact = _complete_surface_artifact()
    first_event_id = _event_ids()[0]
    for row in [*artifact["promoted"], *artifact["rejected"]]:
        metadata = row.get("base_candidate_metadata")
        if not isinstance(metadata, dict) or metadata.get("event_id") != first_event_id:
            continue
        threshold = float(row["parameter_values"]["signal_threshold"])
        if threshold == 0.10:
            _rewrite_metric(row, net_return=1.0, expectancy=0.5)
        else:
            _rewrite_metric(row, net_return=-0.1, expectancy=-0.05)
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    validate_screening_artifact(artifact)
    return artifact


def _run_script(tmp_path: Path, artifact: dict[str, Any], *extra: str) -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def _run_dir_script(tmp_path: Path, artifact_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    out_path = tmp_path / "raw_robustness_inputs.json"
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--screening-artifact-dir",
            str(artifact_dir),
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


def test_current_first_event_surface_policy_reproduces_default_payload(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact()
    args = ("--fee-per-rt", "0.001", "--tick-value", "0.01")
    default_result = _run_script(tmp_path / "default", artifact, *args)
    explicit_result = _run_script(
        tmp_path / "explicit",
        artifact,
        *args,
        "--surface-policy",
        "current_first_event",
    )

    assert default_result.returncode == 0, default_result.stderr
    assert explicit_result.returncode == 0, explicit_result.stderr
    default_payload = json.loads(
        (tmp_path / "default" / "raw_robustness_inputs.json").read_text(encoding="utf-8")
    )
    explicit_payload = json.loads(
        (tmp_path / "explicit" / "raw_robustness_inputs.json").read_text(encoding="utf-8")
    )
    assert explicit_payload == default_payload


def test_screening_artifact_dir_is_diagnostic_only_even_for_current_policy(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact()
    unit_dir = _write_event_unit_artifacts(tmp_path / "units", artifact)
    report_path = tmp_path / "sensitivity.json"
    result = _run_dir_script(
        tmp_path,
        unit_dir,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
        "--sensitivity-report-out",
        str(report_path),
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "diagnostic_only"
    assert receipt["source_mode"] == "screening_artifact_dir"
    assert receipt["packaged_count"] == 0
    assert not (tmp_path / "raw_robustness_inputs.json").exists()
    assert report["screening_artifact"] == "units"
    assert report["screening_artifact_source"] == "unit_artifact_directory"
    assert report["unit_artifact_count"] == len(_event_ids())
    assert report["unit_artifact_set_hash"] == report["screening_artifact_hash"]
    assert report["summary"]["vectorbt_promoted_count"] == 1
    assert report["summary"]["candidates_passing_current_first_event"] == 1
    assert report["summary"]["packaged_count"] == 0
    assert report["assembler_diagnostics"]["candidate_skip_counts"] == {
        "diagnostic_only_screening_artifact_dir:current_first_event": 1
    }
    assert receipt["skipped"]["candidate_skip_counts"] == {
        "diagnostic_only_screening_artifact_dir:current_first_event": 1
    }


def test_screening_artifact_dir_empty_fails_closed_without_output(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty_units"
    empty_dir.mkdir()
    result = _run_dir_script(tmp_path, empty_dir)

    assert result.returncode != 0
    assert "screening_artifact_dir_empty" in result.stderr
    assert not (tmp_path / "raw_robustness_inputs.json").exists()


def test_first_event_fail_can_pass_pooled_train_event_policy_in_report_only(tmp_path: Path) -> None:
    artifact = _first_event_fail_artifact()
    report_path = tmp_path / "pooled" / "sensitivity.json"
    baseline = _run_script(
        tmp_path / "baseline",
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
    )
    pooled = _run_script(
        tmp_path / "pooled",
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
        "--surface-policy",
        "pooled_train_events",
        "--sensitivity-report-out",
        str(report_path),
    )

    assert baseline.returncode != 0
    assert "surface_stability_metrics_not_replay_ready" in baseline.stderr
    assert not (tmp_path / "baseline" / "raw_robustness_inputs.json").exists()
    assert pooled.returncode == 0, pooled.stderr
    receipt = json.loads(pooled.stdout)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "diagnostic_only"
    assert receipt["packaged_count"] == 0
    assert not (tmp_path / "pooled" / "raw_robustness_inputs.json").exists()
    assert report["summary"]["candidates_passing_pooled_train_events"] == 1
    assert report["attrition"]["selected_policy_packaged_candidates"] == 0


def test_median_event_policy_report_fields(tmp_path: Path) -> None:
    artifact = _first_event_fail_artifact()
    report_path = tmp_path / "robustness_bridge_sensitivity_report.json"
    result = _run_script(
        tmp_path,
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
        "--surface-policy",
        "median_event_surface",
        "--sensitivity-report-out",
        str(report_path),
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    family = report["families"][0]
    promoted_id = artifact["promoted"][0]["candidate_id"]
    assert receipt["status"] == "diagnostic_only"
    assert not (tmp_path / "raw_robustness_inputs.json").exists()
    assert report["schema"] == "hft3_robustness_bridge_sensitivity_report_v1"
    assert report["selected_surface_policy"] == "median_event_surface"
    assert family["model_family"]["model_id"] == "QUEUE_DEPLETION_TRIGGER"
    assert family["vectorbt_promoted_count"] == 1
    assert family["event_count"] == 4
    assert family["usable_event_count"] == 4
    assert family["rejected_event_count"] == 0
    assert family["rejected_events"] == []
    assert family["surface_training_event_count"] == 3
    assert family["surface_training_event_ids"] == _event_ids()[:3]
    assert family["parameter_cell_count"] == 16
    assert family["event_0_id"] == _event_ids()[0]
    assert family["current_first_event_pass"] is False
    assert family["median_event_surface_pass"] is True
    assert family["candidates_passing_median_event_surface"] == 1
    assert "median_plateau_score" in family["median_event_surface_metrics"]
    assert "downside_plateau_score" in family["median_event_surface_metrics"]
    assert family["candidates_rejected_by_current_but_passed_by_corrected_policy"] == [
        promoted_id
    ]
    assert report["summary"]["candidates_rejected_by_current_but_passed_by_corrected_policy"] == [
        promoted_id
    ]
    assert report["summary"]["hftbacktest_eligible_candidates"] == 0
    assert report["summary"]["packaged_count"] == 0
    assert report["summary"]["min_packaged"] == 1
    assert report["summary"]["packaging_eligible_family_count"] == 1
    assert report["assembler_diagnostics"]["row_skip_counts"] == {}
    assert report["assembler_diagnostics"]["candidate_skip_counts"] == {
        "diagnostic_only_surface_policy:median_event_surface": 1
    }
    assert report["attrition"]["families_with_enough_events_cells_trades_data"] == 1
    assert report["attrition"]["selected_policy_packaged_candidates"] == 0


def test_corrected_policy_does_not_write_replay_eligibility_or_receipt(tmp_path: Path) -> None:
    artifact = _first_event_fail_artifact()
    original_promoted = copy.deepcopy(artifact["promoted"][0])
    report_path = tmp_path / "sensitivity.json"
    result = _run_script(
        tmp_path,
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
        "--surface-policy",
        "pooled_train_events",
        "--sensitivity-report-out",
        str(report_path),
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "diagnostic_only"
    assert receipt["packaged_count"] == 0
    assert not (tmp_path / "raw_robustness_inputs.json").exists()
    persisted_artifact = json.loads((tmp_path / "screening_artifact.json").read_text(encoding="utf-8"))
    assert persisted_artifact["promoted"][0] == original_promoted
    assert persisted_artifact["promoted"][0].get("replay_eligibility_status") != "eligible"
    assert persisted_artifact["promoted"][0].get("robustness_evidence_receipt") == (
        original_promoted.get("robustness_evidence_receipt")
    )
    assert report["attrition"]["hftbacktest_eligible_candidates"] == 0


def test_sensitivity_report_is_written_when_selected_policy_fails(tmp_path: Path) -> None:
    artifact = _first_event_fail_artifact()
    report_path = tmp_path / "sensitivity.json"
    result = _run_script(
        tmp_path,
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
        "--sensitivity-report-out",
        str(report_path),
    )

    assert result.returncode != 0
    assert "surface_stability_metrics_not_replay_ready" in result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    promoted_id = artifact["promoted"][0]["candidate_id"]
    assert report["summary"]["packaged_count"] == 0
    assert report["summary"]["candidates_passing_current_first_event"] == 0
    assert report["summary"]["candidates_passing_pooled_train_events"] == 1
    assert report["assembler_diagnostics"]["candidate_skip_counts"] == {
        "family_surface_not_accepted": 1
    }
    assert report["summary"]["candidates_rejected_by_current_but_passed_by_corrected_policy"] == [
        promoted_id
    ]
    assert report["attrition"]["selected_policy_packaged_candidates"] == 0


def test_sensitivity_report_records_missing_surface_event_rejections(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact(omit_last_cell=True)
    report_path = tmp_path / "sensitivity.json"
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
        "--sensitivity-report-out",
        str(report_path),
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    family = report["families"][0]
    assert family["rejected_event_count"] == 1
    assert family["rejected_events"] == [
        {
            "event_id": _event_ids()[-1],
            "event_date": "2020-04-10",
            "reasons": ["missing_surface"],
            "missing_parameter_cell_count": 1,
            "insufficient_trade_cell_count": 0,
        }
    ]


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


def test_accepts_net_return_pct_as_measured_return(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact()
    not_run_metric = {
        "status": "not_run",
        "reason": "candidate_rejected_before_replay:return_fraction_not_recorded",
    }
    for row in [*artifact["promoted"], *artifact["rejected"]]:
        net_return = row["net_return"]
        row["net_return"] = not_run_metric
        metric_values = row.setdefault("metric_values", {})
        assert isinstance(metric_values, dict)
        metric_values.pop("net_return", None)
        metric_values["net_return_pct"] = net_return * 100.0
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


def test_extracts_zero_trade_official_vbt_stats_as_measured_cell() -> None:
    template = _screening_artifact("placeholder_promoted")["promoted"][0]
    row = _metric_row(
        template,
        event_id=_event_ids()[0],
        params=_param_values()[0],
        index=0,
        promoted=False,
    )
    not_run_reason = "candidate_rejected_before_replay:vectorbt_stats_missing_gate_fields"
    not_run_metric = {"status": "not_run", "reason": not_run_reason}
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
        "vbt_stats": {
            "Total Trades": 0,
            "Expectancy": None,
            "Total Return [%]": 0.0,
            "Max Drawdown [%]": None,
            "Sharpe Ratio": "inf",
            "Profit Factor": None,
        },
        "gate_metric_authority": "official_vectorbt_portfolio_stats",
    }

    measured, reason = _extract_measured_row(row)

    assert reason is None
    assert measured is not None
    assert measured.trade_count == 0
    assert measured.net_return == 0.0
    assert measured.expectancy == 0.0
    assert measured.sharpe == 0.0
    assert measured.max_drawdown == 0.0


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


def test_failure_writes_compact_diagnostics_when_requested(tmp_path: Path) -> None:
    artifact = _complete_surface_artifact(omit_last_cell=True)
    diagnostics = tmp_path / "raw_robustness_diagnostics.json"
    result = _run_script(
        tmp_path,
        artifact,
        "--fee-per-rt",
        "0.001",
        "--tick-value",
        "0.01",
        "--diagnostics-out",
        str(diagnostics),
    )

    assert result.returncode != 0
    assert not (tmp_path / "raw_robustness_inputs.json").exists()
    payload = json.loads(diagnostics.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["reason"] == "raw_input_count_below_min"
    assert payload["packaged_count"] == 0
    assert payload["family_skip_counts"]
    assert "family_skips={" not in result.stderr


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
