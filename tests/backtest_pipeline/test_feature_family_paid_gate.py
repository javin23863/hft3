"""Phase 9: feature-family paid-screen gate tests."""

from __future__ import annotations

import json
from pathlib import Path

from backtest_pipeline.src.feature_family_status import (
    evaluate_feature_family_paid_gate,
    load_feature_family_status_manifest,
)
from backtest_pipeline.src.feature_plane import build_feature_plane_payload
from scripts.validate_paid_screen_ready_gate import evaluate_gate

_REPO = Path(__file__).resolve().parents[2]


def _valid_screening_artifact(
    *,
    run_id: str,
    scope: str = "pilot",
    promoted_ids: list[str] | None = None,
    positive_trades: bool = True,
    bar_stub: bool = False,
) -> dict:
    from backtest_pipeline.src.promotion_gate import PromotedCandidate
    from backtest_pipeline.src.vectorbt_adapter import FilterResult, validate_screening_artifact

    promoted_ids = promoted_ids if promoted_ids is not None else ["c1"]
    model_id = "SPREAD_BLOWOUT_RECOMPRESSION"
    symbol = "MES.v.0"
    event_id = "NFP_2019_11_01_TIGHT"
    event_type = "NFP"
    bar_id = (
        "ohlcv_1m_from_npz_or_supplied_array"
        if bar_stub
        else "fs_v1_row_loop_from_feature_store"
    )
    feature_set_id = "fs_v1_pilot_unknown" if bar_stub else "fs_v1"
    vbt_stats = {
        "Total Trades": 12 if positive_trades else 0,
        "Expectancy": 1.0 if positive_trades else None,
        "Max Drawdown [%]": 0.1 if positive_trades else None,
    }
    paid_scope = scope in {"paid_compute", "paid-compute", "all_model", "all_models", "all-models"}
    result = FilterResult(
        promoted=[
            PromotedCandidate(
                candidate_id=candidate_id,
                hypothesis_id=model_id,
                strategy_family=model_id,
                asset_class="CME_FUTURES",
                symbol=symbol,
                timeframe="event_window",
                param_values={"signal_threshold": 0.15, "holding_period_bars": 15},
                vectorbt_run_id=run_id,
                vectorbt_results={
                    **vbt_stats,
                    "base_candidate_metadata": {
                        "model_id": model_id,
                        "symbol": symbol,
                        "event_id": event_id,
                        "event_type": event_type,
                    },
                    "opportunity_type_or_event_type": event_type,
                    "oos_expectancy": 1.0,
                    "wf_consistency": 1.0,
                    "max_drawdown_pct": -1.0,
                    "turnover_mean_pct": 1.0,
                    "num_trades": 12 if positive_trades else 0,
                    "param_stability_score": 1.0,
                    "slippage_sensitivity": 0.0,
                    "net_return": 0.01,
                    "net_pnl": 10.0,
                    "profit_factor": 1.5,
                    "sharpe": 1.0,
                    "sortino": 1.0,
                    "feature_recipe_hash": "abc123",
                    "pilot_gate_evaluation": {"failures": []},
                },
                pass_reason="vectorbt_screen_passed_replay_not_eligible",
            )
            for candidate_id in promoted_ids
        ],
        rejected=[],
        run_id=run_id,
        total_candidates=len(promoted_ids),
        code_commit="test_commit",
        vectorbt_available=True,
        backend="python",
        vectorbt_version="test",
        vectorbt_engine="rust" if paid_scope else "numba",
        engine_parity_status=(
            "rust_runtime_proven"
            if paid_scope
            else "pilot_python_engine_allowed"
        ),
        rust_engine_required_for_scope=paid_scope,
        rust_engine_available=paid_scope,
        vectorbt_engine_runtime_proof=paid_scope,
        license_review="unit_test",
        parameter_space_id="unit_test_parameter_space",
        parameter_space_hash="unit_test_parameter_space_hash",
        max_trials=max(1, len(promoted_ids)),
        trials_run=len(promoted_ids),
        run_budget_id="unit_test_budget",
        max_models=1,
        max_symbols=1,
        max_feature_sets=1,
        max_total_trials=max(1, len(promoted_ids)),
        abort_on_budget_exhaustion=True,
        screening_scope=scope,
        feature_set_id=feature_set_id,
        feature_set_hash="unit_test_feature_set_hash",
        data_manifest_hash="unit_test_data_manifest_hash",
        lake_manifest_hash="hash2",
        events_csv_hash_or_not_applicable="hash1",
        fees_model_id="unit_test_fees",
        slippage_model_id="unit_test_slippage",
        bar_construction_id=bar_id,
        target_event_type_or_null=event_type,
    )
    artifact = result.to_dict()
    validate_screening_artifact(artifact)
    return artifact


def _pilot_artifact(*, recipe_hash: str = "abc123", screening_scope: str = "pilot") -> dict:
    plane = build_feature_plane_payload(
        bar_construction_id="fs_v1_row_loop_from_feature_store",
        feature_set_id="fs_v1",
        feature_set_hash="sha256:test",
        research_clock="scheduled_event",
        screening_scope=screening_scope,
    )
    return {
        "screening_backend": "vectorbt",
        "screening_scope": screening_scope,
        "trials_run": 1,
        "promoted": [
            {
                "candidate_id": "c1",
                "screening_status": "pass",
                "feature_recipe_hash": recipe_hash,
                "vectorbt_results": {
                    "feature_recipe_hash": recipe_hash,
                    "pilot_gate_evaluation": {"failures": []},
                },
            }
        ],
        **plane,
    }


def test_load_feature_family_status_manifest() -> None:
    manifest = load_feature_family_status_manifest(_REPO)
    assert manifest.get("schema_version") == "feature_family_status.v1"
    gate = manifest.get("paid_screen_gate") or {}
    assert gate.get("allowed") is True
    assert "feature_recipe_hash" in (gate.get("required_pilot_fields") or [])


def test_paid_gate_fails_when_manifest_disallows() -> None:
    pilot = _pilot_artifact()
    manifest = load_feature_family_status_manifest(_REPO)
    gate = dict(manifest.get("paid_screen_gate") or {})
    gate["allowed"] = False
    errors, summary = evaluate_feature_family_paid_gate(
        pilot,
        repo_root=_REPO,
        status_manifest={**manifest, "paid_screen_gate": gate},
    )
    assert any(err.startswith("paid_screen_gate_not_allowed:") for err in errors)
    assert summary["paid_screen_gate_allowed"] is False
    assert summary["resolved_fields"]["feature_recipe_hash"] == "abc123"


def test_paid_gate_missing_recipe_hash() -> None:
    pilot = _pilot_artifact(recipe_hash="")
    pilot["promoted"] = [{"candidate_id": "c1", "screening_status": "pass"}]
    errors, summary = evaluate_feature_family_paid_gate(pilot, repo_root=_REPO)
    assert "pilot_missing:feature_recipe_hash" in errors
    assert summary["resolved_fields"]["feature_recipe_hash"] is None


def test_paid_gate_resolves_family_status_fields() -> None:
    pilot = _pilot_artifact()
    _errors, summary = evaluate_feature_family_paid_gate(pilot, repo_root=_REPO)
    resolved = summary["resolved_fields"]
    assert resolved["feature_usage_manifest"] == "present"
    assert resolved["cross_asset_alignment_status"]
    assert resolved["robustness_result"] == "not_run_pilot_scope"
    assert resolved["hftbacktest_handoff_status"] == "recipe_hash_handoff_ready"
    assert resolved["vectorbt_result"] == "screen_pass"


def test_paid_gate_resolves_paid_compute_gate_evaluation() -> None:
    paid_compute = _pilot_artifact(screening_scope="paid_compute")
    vectorbt_results = paid_compute["promoted"][0]["vectorbt_results"]
    vectorbt_results.pop("pilot_gate_evaluation", None)
    vectorbt_results["paid_compute_gate_evaluation"] = {
        "scope": "paid_compute",
        "failures": [],
    }

    _errors, summary = evaluate_feature_family_paid_gate(paid_compute, repo_root=_REPO)

    assert summary["resolved_fields"]["vectorbt_result"] == "paid_compute_gate_pass"


def test_ready_gate_includes_feature_family_summary(tmp_path: Path) -> None:
    pilot_path = tmp_path / "pilot.json"
    pilot = _valid_screening_artifact(run_id="pilot")
    pilot_path.write_text(json.dumps(pilot), encoding="utf-8")

    smoke_manifest = tmp_path / "smoke.json"
    unit_dir = tmp_path / "units" / "u1"
    unit_dir.mkdir(parents=True)
    smoke = _valid_screening_artifact(run_id="paid_smoke", scope="all_models")
    (unit_dir / "screening_artifact.json").write_text(json.dumps(smoke), encoding="utf-8")
    smoke_manifest.write_text(
        json.dumps(
            {
                "expected_work_units": 1,
                "completed_work_units": 1,
                "skipped_work_units": 0,
                "failed_work_units": 0,
                "out_dir": str(tmp_path),
                "unit_results": [
                    {
                        "unit_id": "u1",
                        "status": "OK",
                        "screening_artifact_relpath": "units/u1/screening_artifact.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_gate(
        pilot_artifact=pilot_path,
        smoke_manifest=smoke_manifest,
        repo_root=_REPO,
        run_pytest=False,
    )
    assert "feature_family_gate" in result
    assert result["feature_family_gate"]["resolved_fields"]["feature_recipe_hash"] == "abc123"
    assert result["feature_family_gate"]["paid_screen_gate_allowed"] is True
    assert result["ready_for_full_run"] is True
    assert result["smoke_manifest_summary"]["paid_scope_positive_promotions"] == 1
    assert result["smoke_manifest_summary"]["paid_scope_positive_trade_rows"] == 1
    assert not any("paid_screen_gate_not_allowed" in err for err in result["errors"])


def test_ready_gate_rejects_paid_smoke_without_positive_evidence(tmp_path: Path) -> None:
    pilot_path = tmp_path / "pilot.json"
    pilot_path.write_text(
        json.dumps(_valid_screening_artifact(run_id="pilot")),
        encoding="utf-8",
    )
    unit_dir = tmp_path / "units" / "u1"
    unit_dir.mkdir(parents=True)
    smoke = _valid_screening_artifact(
        run_id="paid_smoke_zero",
        scope="paid_compute",
        promoted_ids=[],
        positive_trades=False,
        bar_stub=True,
    )
    (unit_dir / "screening_artifact.json").write_text(json.dumps(smoke), encoding="utf-8")
    smoke_manifest = tmp_path / "smoke.json"
    smoke_manifest.write_text(
        json.dumps(
            {
                "expected_work_units": 1,
                "completed_work_units": 1,
                "skipped_work_units": 0,
                "failed_work_units": 0,
                "out_dir": str(tmp_path),
                "unit_results": [
                    {
                        "unit_id": "u1",
                        "status": "OK",
                        "screening_artifact_relpath": "units/u1/screening_artifact.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_gate(
        pilot_artifact=pilot_path,
        smoke_manifest=smoke_manifest,
        repo_root=_REPO,
        run_pytest=False,
    )

    assert result["ready_for_full_run"] is False
    assert "smoke_manifest:no_paid_scope_positive_promotions" in result["errors"]
    assert "smoke_manifest:no_paid_scope_positive_trade_rows" in result["errors"]
    assert any("paid_scope_bar_stub_research_only" in err for err in result["errors"])
    assert any("paid_scope_npz_bar_fallback" in err for err in result["errors"])
