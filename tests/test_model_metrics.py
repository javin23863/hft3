from __future__ import annotations

import json
from pathlib import Path

import pytest

from hft3.validation.model_metrics import build_post_robustness_scorecard
from model_metrics.backfill import backfill_model_metrics, generate_bundle_for_run_dir, run_inputs_from_run_dir
from model_metrics.envelope import generate_behavior_envelope
from model_metrics.registry import calculate_metric_values
from model_metrics.schemas import ModelRuntimeObservation, strict_json_dumps
from trade_manager.model_behavior import ModelBehaviorRuleEngine
from workbench.src.run.campaign_runner import record_sim_shadow


def _inputs() -> dict:
    returns = [10.0, -2.0, 4.0, 8.0, -1.0, 3.0, 5.0, -2.5, 7.0, 6.0]
    return {
        "context": {
            "model_id": "FUTURES_MODEL_A",
            "model_version": "v1",
            "run_id": "RUN_A",
            "campaign_id": "CAMP_A",
            "asset_class": "FUTURES",
            "symbol": "ES",
            "robustness_run_id": "ROB_A",
        },
        "created_at": "2026-06-04T00:00:00+00:00",
        "source_artifact_ids": ["run/status.json"],
        "returns": returns,
        "trades": [
            {"realized_pnl": value, "gross_pnl": value + 0.25, "fill_status": "FILLED", "slippage_bps": 1.0}
            for value in returns
        ],
        "execution": {
            "fill_rate": 1.0,
            "latency_order_to_ack": 1.5,
            "alpha_half_life": 10.0,
            "tick_to_send_us": 950.0,
            "decision_to_send_us": 125.0,
            "send_to_ack_us": 1500.0,
        },
        "latency_operating_envelope": {
            "offensive": {
                "operating_band": "sub_millisecond_loop",
                "placement": {
                    "tick_to_send_us": {"p99": 900.0, "p99_9": 950.0},
                    "decision_to_send_us": {"p99": 100.0, "p99_9": 125.0},
                },
                "opportunity_window_compatible": {"100": False, "1000": True},
            },
            "defensive": {
                "placement": {
                    "cancel_to_send_us": {"p99": 150.0},
                    "replace_to_send_us": {"p99": 175.0},
                },
                "confirmation": {
                    "cancel_to_ack_us": {"p99": 2500.0},
                    "replace_to_ack_us": {"p99": 3000.0},
                },
            },
            "external_confirmation": {
                "modeled_as_async_state_confirmation": True,
                "confirmation": {"send_to_ack_us": {"p99": 1500.0}},
            },
            "pending_state_risk": {
                "max_pending_orders": 3,
                "max_pending_quantity": 2.0,
                "max_pending_notional": 0.0,
                "stale_pending_timeout_us": 500000.0,
                "stale_state_risk": "managed",
            },
            "competitor_speed_sensitivity": {"tested": True, "equal_speed_viable": True},
            "execution_path_audit": {
                "run_id": "lataudit-pass",
                "status": "pass",
                "primary_kpi": "tick_to_send_us",
                "tick_to_send_p99_9_us": 950.0,
                "failure_count": 0,
                "warning_count": 0,
            },
        },
        "robustness": {
            "folds": [
                {"return": 5.0, "sharpe": 1.1, "max_drawdown": -2.0},
                {"return": 7.0, "sharpe": 1.3, "max_drawdown": -2.5},
                {"return": 8.0, "sharpe": 1.2, "max_drawdown": -1.5},
            ],
            "walk_forward_efficiency": 0.8,
            "deflated_sharpe_ratio": 0.5,
            "PBO": 0.05,
            "cost_sensitivity_score": 1.0,
            "slippage_sensitivity_score": 1.0,
        },
        "portfolio": {"correlation_to_existing_models": 0.1, "marginal_sharpe_contribution": 0.2},
        "prediction": {"IC": 0.08, "Brier_score": 0.18, "expected_calibration_error": 0.04},
        "feature_training_domain_bounds": {"basis_zscore": [-3.0, 3.0]},
        "approved_regime_ids": ["NORMAL"],
        "blocked_regime_ids": ["HALT"],
    }


def test_metric_registry_is_deterministic_and_marks_missing_inputs() -> None:
    first = [metric.to_dict() for metric in calculate_metric_values(_inputs())]
    second = [metric.to_dict() for metric in calculate_metric_values(_inputs())]

    assert first == second
    assert json.loads(strict_json_dumps(first))
    by_name = {row["metric_name"]: row for row in first}
    assert by_name["net_return"]["metric_value"] == pytest.approx(37.5)
    assert by_name["slippage_bps"]["metric_value"] == pytest.approx(1.0)
    assert by_name["tick_to_send_us"]["metric_value"] == pytest.approx(950.0)
    assert by_name["tick_to_send_us"]["metric_unit"] == "us"
    assert by_name["send_to_ack_us"]["metric_value"] == pytest.approx(1500.0)
    assert by_name["send_to_ack_us"]["metric_unit"] == "us"
    assert by_name["queue_position_decay"]["status"] == "unavailable"
    assert "required input not observed" in by_name["queue_position_decay"]["errors"][0]


def test_scorecard_and_behavior_envelope_are_asset_class_neutral() -> None:
    scorecard = build_post_robustness_scorecard(_inputs())
    envelope = generate_behavior_envelope(_inputs(), scorecard)

    assert scorecard.model_id == "FUTURES_MODEL_A"
    assert scorecard.asset_class == "FUTURES"
    assert scorecard.grade in {"A", "B", "C", "D", "F"}
    assert scorecard.weighted_score > 0
    assert envelope.model_id == scorecard.model_id
    assert envelope.feature_training_domain_bounds["basis_zscore"] == (-3.0, 3.0)
    assert envelope.latency_bounds["tick_to_send_us"] == (0.0, 950.0)
    assert envelope.latency_bounds["send_to_ack_us"] == (0.0, 1500.0)
    assert envelope.operating_band == "sub_millisecond_loop"
    assert envelope.async_state_model_required is True
    assert envelope.max_pending_orders == 3
    assert envelope.low_latency_execution_path_status["status"] == "pass"


def test_model_behavior_engine_green_yellow_red() -> None:
    scorecard = build_post_robustness_scorecard(_inputs())
    envelope = generate_behavior_envelope(_inputs(), scorecard)
    engine = ModelBehaviorRuleEngine()

    green = engine.evaluate(
        envelope,
        ModelRuntimeObservation(
            model_id="FUTURES_MODEL_A",
            regime_id="NORMAL",
            drawdown=1.0,
            slippage_bps=1.0,
            fill_rate=1.0,
            latency_order_to_ack=1.0,
            tick_to_send_us=900.0,
            decision_to_send_us=100.0,
            send_to_ack_us=1000.0,
            alpha_half_life=10.0,
            feature_values={"basis_zscore": 0.5},
        ),
    )
    placement_red = engine.evaluate(
        envelope,
        {
            "model_id": "FUTURES_MODEL_A",
            "regime_id": "NORMAL",
            "tick_to_send_us": 2_000.0,
            "decision_to_send_us": 100.0,
            "send_to_ack_us": 1_000.0,
            "latency_order_to_ack": 1.0,
            "alpha_half_life": 10.0,
        },
    )
    ack_red = engine.evaluate(
        envelope,
        {
            "model_id": "FUTURES_MODEL_A",
            "regime_id": "NORMAL",
            "tick_to_send_us": 900.0,
            "decision_to_send_us": 100.0,
            "send_to_ack_us": 600_000.0,
            "latency_order_to_ack": 1.0,
            "alpha_half_life": 10.0,
        },
    )
    red = engine.evaluate(
        envelope,
        {
            "model_id": "FUTURES_MODEL_A",
            "regime_id": "HALT",
            "drawdown": -10_000.0,
            "drawdown_velocity": -500.0,
            "slippage_bps": 100.0,
            "fill_rate": 0.1,
            "latency_order_to_ack": 50.0,
            "alpha_half_life": 10.0,
            "feature_values": {"basis_zscore": 99.0},
        },
    )

    assert green.state == "GREEN"
    assert placement_red.state == "RED"
    assert any(trigger["name"] == "placement_exceeds_opportunity_window" for trigger in placement_red.triggers)
    assert ack_red.state == "RED"
    assert any(trigger["name"] == "async_ack_stale_state" for trigger in ack_red.triggers)
    assert red.state == "RED"
    assert {trigger["name"] for trigger in red.triggers} >= {
        "blocked_regime",
        "drawdown_kill_threshold",
        "drawdown_velocity",
        "slippage",
        "feature_training_domain",
    }


def test_model_behavior_engine_red_when_low_latency_audit_failed() -> None:
    inputs = _inputs()
    inputs["latency_operating_envelope"]["execution_path_audit"] = {
        "run_id": "lataudit-fail",
        "status": "fail",
        "reason": "blocking I/O before send was observed",
    }
    scorecard = build_post_robustness_scorecard(inputs)
    envelope = generate_behavior_envelope(inputs, scorecard)
    decision = ModelBehaviorRuleEngine().evaluate(
        envelope,
        ModelRuntimeObservation(
            model_id="FUTURES_MODEL_A",
            regime_id="NORMAL",
            tick_to_send_us=900.0,
            decision_to_send_us=100.0,
            send_to_ack_us=1000.0,
            latency_order_to_ack=1.0,
            alpha_half_life=10.0,
        ),
    )

    assert decision.state == "RED"
    assert any(trigger["name"] == "low_latency_execution_path_audit" for trigger in decision.triggers)


def test_backfill_writes_run_local_metric_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "runtime" / "workbench" / "runs" / "RUN_A"
    (run_dir / "validation_reports").mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "run_id": "RUN_A",
                "scenario": "FUTURES",
                "symbol": "ES",
                "decision": {"evidence_candidate_id": "FUTURES_MODEL_A"},
                "candidates": [{"candidate_id": "FUTURES_MODEL_A", "proxy_net_pnl_bps": 10, "proxy_max_drawdown_bps": -2}],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "robustness_summary.json").write_text(
        json.dumps({"run_id": "ROB_A", "robustness_pack": {"passed": ["transaction_cost_sensitivity"]}}),
        encoding="utf-8",
    )
    (run_dir / "validation_reports" / "FUTURES_MODEL_A.json").write_text(
        json.dumps(
            {
                "candidate_id": "FUTURES_MODEL_A",
                "result": {"trade_pnls": [3.0, -1.0, 2.0], "slippage_bps": 0.5, "fill_rate": 1.0},
            }
        ),
        encoding="utf-8",
    )

    bundle = generate_bundle_for_run_dir(run_dir, root=tmp_path)
    summary = backfill_model_metrics(tmp_path, force=True)

    assert bundle["status"] == "ok"
    assert (run_dir / "model_metrics" / "model_scorecard.json").is_file()
    assert (run_dir / "model_metrics" / "model_behavior_envelope.json").is_file()
    assert summary["models_processed"] == 1
    assert summary["metrics_calculated"] > 20


def test_workbench_campaign_summary_generates_non_crypto_scorecard(tmp_path: Path) -> None:
    run_dir = tmp_path / "runtime" / "workbench" / "runs" / "EQUITY_CAMPAIGN_A"
    run_dir.mkdir(parents=True)
    (run_dir / "campaign.json").write_text(
        json.dumps({"campaign_id": "EQUITY_CAMPAIGN_A", "model_id": "EQUITY_MODEL_A", "symbol": "AAPL"}),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "run_id": "EQUITY_CAMPAIGN_A",
                "campaign_id": "EQUITY_CAMPAIGN_A",
                "model_id": "EQUITY_MODEL_A",
                "asset_class": "EQUITIES",
                "symbol": "AAPL",
                "status": "PASS",
                "robustness_checks": [
                    {"name": "transaction_cost_sensitivity", "status": "PASS"},
                    {"name": "slippage_sensitivity", "status": "PASS"},
                ],
                "periods": [
                    {
                        "name": "WF1",
                        "net_pnl": 2.5,
                        "event_results": [
                            {"net_pnl": 1.5, "gross_pnl": 1.8, "slippage_bps": 0.4},
                            {"net_pnl": -0.5, "gross_pnl": -0.4, "slippage_bps": 0.6},
                        ],
                    },
                    {"name": "WF2", "net_pnl": 3.0, "event_results": [{"net_pnl": 3.0, "slippage_bps": 0.5}]},
                    {"name": "WF3", "net_pnl": 1.25, "event_results": []},
                ],
            }
        ),
        encoding="utf-8",
    )

    inputs = run_inputs_from_run_dir(run_dir)
    bundle = generate_bundle_for_run_dir(run_dir, root=tmp_path)

    assert inputs["context"]["asset_class"] == "EQUITIES"
    assert inputs["context"]["model_id"] == "EQUITY_MODEL_A"
    assert len(inputs["trades"]) == 4
    assert inputs["returns"][-1] == pytest.approx(1.25)
    assert bundle["scorecard"]["asset_class"] == "EQUITIES"
    assert bundle["scorecard"]["symbol"] == "AAPL"


def test_autonomous_artifacts_runs_are_discovered_for_all_models(tmp_path: Path) -> None:
    run_dir = tmp_path / "artifacts" / "runs" / "AUTO_OPTIONS_A"
    run_dir.mkdir(parents=True)
    (run_dir / "config_snapshot.yaml").write_text("data:\n  symbol_universe:\n    - MES.v.0\n", encoding="utf-8")
    (run_dir / "experiment_spec.json").write_text(
        json.dumps([{"alpha_id": "FUTURES_MODEL_AUTO", "symbol": "MES.v.0"}]),
        encoding="utf-8",
    )
    (run_dir / "candidate_rankings.json").write_text(
        json.dumps(
            [
                {
                    "model_id": "FUTURES_MODEL_AUTO",
                    "symbol": "MES.v.0",
                    "campaign_id": "AUTO_FUTURES_A_MES",
                    "summary": {
                        "periods": [
                            {"name": "WF1", "net_pnl": 4.0, "event_results": [{"net_pnl": 4.0, "slippage_bps": 0.3}]},
                            {"name": "WF2", "net_pnl": -1.0, "event_results": [{"net_pnl": -1.0, "slippage_bps": 0.4}]},
                        ]
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (run_dir / "scoring_summary.json").write_text(
        json.dumps(
            {
                "run_id": "AUTO_OPTIONS_A",
                "campaign_id": "AUTO_OPTIONS",
                "decision": "QUARANTINE",
                "selected_candidate": {"model_id": "FUTURES_MODEL_AUTO", "symbol": "MES.v.0"},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "robustness_gates.json").write_text(json.dumps({"gates": []}), encoding="utf-8")

    summary = backfill_model_metrics(tmp_path, force=True)
    scorecard = json.loads((run_dir / "model_metrics" / "model_scorecard.json").read_text(encoding="utf-8"))

    assert summary["models_processed"] == 1
    assert scorecard["model_id"] == "FUTURES_MODEL_AUTO"
    assert scorecard["asset_class"] == "FUTURES"
    assert scorecard["symbol"] == "MES.v.0"


def test_sim_shadow_cannot_reopen_promotion_when_behavior_envelope_inactive(tmp_path: Path) -> None:
    campaign_id = "CAMP_INACTIVE_ENVELOPE"
    config_dir = tmp_path / "workbench" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "walk_forward.yaml").write_text(
        "sim_shadow:\n  anchor_date: '2026-01-01'\n  cme_days: 60\n  host: CHI404\n  lane: test\n",
        encoding="utf-8",
    )
    campaign_dir = tmp_path / "research_cards" / "workbench_runs" / campaign_id
    campaign_dir.mkdir(parents=True)
    (campaign_dir / "summary.json").write_text(
        json.dumps(
            {
                "campaign_id": campaign_id,
                "status": "PASS",
                "robustness_passed": True,
                "wfc_status": "PASS",
                "certification_stamp": {"promotion_eligible": True},
                "institutional_metrics": {
                    "status": "ok",
                    "envelope": {"active": False},
                },
                "promote_candidate": True,
            }
        ),
        encoding="utf-8",
    )

    record_sim_shadow(tmp_path, campaign_id, "PASS")

    summary = json.loads((campaign_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["promote_candidate"] is False
    assert any(gate.get("gate") == "model_behavior_envelope" for gate in summary["blocking_gates"])
    assert any(gate.get("gate") == "campaign_latency_operating_envelope" for gate in summary["blocking_gates"])
