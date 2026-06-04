"""Workbench evidence snapshot coverage."""

from __future__ import annotations

from pathlib import Path

from workbench.src.run.evidence_snapshot import (
    _crypto_after_action,
    _crypto_pipeline_coverage,
    _crypto_reports,
    _crypto_relationships,
    _crypto_robustness_explanation,
    _crypto_self_learning_loop,
    _crypto_validation_reports,
    _positive_proxy_pnl_count,
    load_run_evidence,
)


REPO = Path(__file__).resolve().parents[2]


def test_crypto_snapshot_surfaces_bitcoin_edge_packet_gate() -> None:
    snapshot = load_run_evidence(REPO, "crypto_lane")

    edge_data = snapshot.data["bitcoin_edge_packets"]
    edge_latency = snapshot.latency["bitcoin_edge_packets"]

    assert edge_data["configured"] is True
    assert edge_data["transport"] == "length_prefixed_protobuf_tcp"
    assert edge_data["chicago_addr"] == "64.44.98.219:9876"
    assert edge_data["bitcoin_node_source_ip"] == "213.199.46.118"
    assert edge_latency["status"] == edge_data["status"]
    assert snapshot.decision["bitcoin_edge_packet_status"] == edge_data["status"]
    decision_action = str(snapshot.decision.get("action") or "").upper()
    if not edge_data["observed"]:
        if decision_action == "REJECT":
            assert not any(gate["gate"] == "bitcoin_edge_packets" for gate in snapshot.decision["blocking_gates"])
            assert "failed_gates" in snapshot.decision
        else:
            assert any(gate["gate"] == "bitcoin_edge_packets" for gate in snapshot.decision["blocking_gates"])
    assert snapshot.diagnostics["edge_packet_schema"]
    assert "proxy_leaderboard" in snapshot.backtest
    assert "equity_curves" in snapshot.backtest
    assert "smoke_pass_count" in snapshot.decision
    assert "research_pass_count" not in snapshot.decision
    assert "economic_diagnostic_pass_count" in snapshot.decision


def test_crypto_pipeline_coverage_does_not_overclaim_unwired_replay(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/crypto_lane/src/validation/crypto_validation_workflow.py",
        "packages/crypto_lane/src/validation/crypto_execution_validator.py",
        "packages/backtest_pipeline/src/crypto_hft_builder.py",
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    reports = [
        {
            "candidate_id": "crypto_example",
            "purged_cv_implemented": True,
            "runs": {"with_btc_node": {"n_splits": 3}},
            "holdout_gate": {"status": "PASS"},
            "negative_controls": {"shuffled_degraded": True, "shifted_degraded": True},
            "research_pnl_proxy": {"summary": {"net_pnl_bps": 10.0}},
        }
    ]
    candidate_rows = [
        {
            "candidate_id": "crypto_example",
            "purged_splits": 3,
            "holdout_status": "PASS",
            "negative_controls_ok": True,
            "proxy_net_pnl_bps": 10.0,
            "execution_ack_measured": False,
        }
    ]
    coverage = _crypto_pipeline_coverage(
        tmp_path,
        reports,
        [],
        candidate_rows,
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["purged_walk_forward_oos"]["status"] == "OBSERVED"
    assert by_stage["vectorbt_filter"]["status"] == "PRESENT_NOT_WIRED"
    assert by_stage["robustness_pack"]["status"] == "PRESENT_NOT_WIRED"
    assert by_stage["double_walk_forward_correlation"]["status"] == "PRESENT_NOT_WIRED"
    assert by_stage["research_pnl_proxy"]["status"] == "OBSERVED_DIAGNOSTIC_ONLY"
    assert by_stage["research_pnl_proxy"]["role"] == "diagnostic_only"
    assert by_stage["bitcoin_edge_packets"]["role"] == "market_state_only"
    assert by_stage["full_backtest_readiness"]["role"] == "aggregate_readiness_gate"
    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert "upstream VectorBT" in by_stage["hftbacktest_replay"]["reason"]
    assert by_stage["execution_realism"]["status"] == "BLOCKING"
    assert by_stage["full_backtest_readiness"]["status"] == "BLOCKING"
    assert "validation_report.json" in by_stage["hftbacktest_replay"]["artifact_contract"]


def test_positive_proxy_pnl_count_is_defensive() -> None:
    assert _positive_proxy_pnl_count(
        [
            {"proxy_net_pnl_bps": 10.0},
            {"proxy_net_pnl_bps": "-1.5"},
            {"proxy_net_pnl_bps": None},
            {"proxy_net_pnl_bps": "bad"},
            {"proxy_net_pnl_bps": True},
        ]
    ) == 1


def test_crypto_after_action_and_relationship_snapshot_sections_load_run_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "run_1"
    run_dir.mkdir(parents=True)
    (run_dir / "after_action_meta.json").write_text(
        '{"llm_status":"unavailable","skip_reasons":["NO_KEY"],"symbolic_passed":true,'
        '"report_written":false,"required":true,"gate_status":"FAIL","passed":false,'
        '"blocking_reason":"GPT-5.5 xhigh after-action did not pass"}',
        encoding="utf-8",
    )
    (run_dir / "after_action_symbolic.json").write_text('{"passed": true}', encoding="utf-8")
    (run_dir / "after_action_packet.json").write_text('{"skip_reasons":["NO_KEY"]}', encoding="utf-8")
    (run_dir / "kg_slice.json").write_text('{"nodes":[{}],"edges":[{}]}', encoding="utf-8")
    (run_dir / "relationship_summary.json").write_text(
        '{"candidate_count":2,"validated_count":1,"rejected_count":1,'
        '"kg_write_status":"not_attempted","openfoundry_write_status":"not_attempted","promotion_authority":false}',
        encoding="utf-8",
    )
    (run_dir / "relationship_candidates.json").write_text(
        '{"candidates":[{"status":"validated"},{"status":"rejected"}]}',
        encoding="utf-8",
    )

    after_action = _crypto_after_action(run_dir)
    relationships = _crypto_relationships(run_dir)

    assert after_action["llm_status"] == "unavailable"
    assert after_action["gate_status"] == "FAIL"
    assert after_action["passed"] is False
    assert after_action["symbolic_passed"] is True
    assert after_action["kg_slice"]["nodes"]
    assert relationships["candidate_count"] == 2
    assert relationships["kg_write_status"] == "not_attempted"
    assert relationships["promotion_authority"] is False


def test_crypto_robustness_explanation_separates_smoke_pass_from_required_failure() -> None:
    explanation = _crypto_robustness_explanation(
        {
            "status": "FAIL",
            "observed": True,
            "robustness_pack": {
                "checks": [
                    {"name": "negative_control", "status": "PASS", "passed": True},
                    {"name": "latency_sensitivity", "status": "FAIL", "passed": False},
                ],
                "failed": ["transaction_cost_sensitivity"],
            },
            "blocking_gates": [
                {
                    "gate": "robustness_pack",
                    "status": "FAIL",
                    "failed": ["model_combination_degradation"],
                }
            ],
        },
        [{"candidate_id": "crypto_candidate", "pass_fail": "pass"}],
    )

    assert explanation["aggregate_status"] == "FAIL"
    assert explanation["smoke_pass_count"] == 1
    assert explanation["smoke_pass_is_robustness_pass"] is False
    assert explanation["required_fail_count"] == 3
    assert "latency_sensitivity" in explanation["failed_required_checks"]
    assert "Smoke pass is only a prerequisite" in explanation["operator_explanation"]


def test_crypto_self_learning_loop_keeps_llm_advisory_only() -> None:
    loop = _crypto_self_learning_loop(
        {
            "stages": [
                {"name": "walk_forward_smokes", "status": "done"},
                {"name": "vectorbt_filter", "status": "done"},
                {"name": "hft_replay_validation", "status": "done"},
                {"name": "robustness_evidence", "status": "done"},
                {"name": "decision_gate", "status": "blocked"},
            ],
            "decision": {"action": "REJECT", "reason": "robustness evidence failed observed gates"},
        },
        {"llm_status": "unavailable"},
        {"candidate_count": 3},
        {"aggregate_status": "FAIL", "operator_explanation": "required checks failed"},
    )

    assert loop["llm_status"] == "unavailable"
    assert loop["llm_can_promote"] is False
    assert loop["relationship_review_only"] is True
    assert [row["step"] for row in loop["stages"]] == [
        "smoke",
        "VectorBT",
        "HFT replay",
        "robustness",
        "decision",
        "after-action",
        "relationship review",
        "LLM status",
    ]


def test_crypto_reports_prefers_selected_run_local_smoke_reports(tmp_path: Path) -> None:
    global_report = tmp_path / "research_cards" / "crypto" / "old_candidate" / "smoke_report.json"
    global_report.parent.mkdir(parents=True, exist_ok=True)
    global_report.write_text('{"candidate_id":"old_candidate","pass_fail":"pass"}', encoding="utf-8")

    run_report = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "run_1" / "smoke_reports" / "current_candidate.json"
    run_report.parent.mkdir(parents=True, exist_ok=True)
    run_report.write_text('{"candidate_id":"current_candidate","pass_fail":"fail"}', encoding="utf-8")

    reports = _crypto_reports(tmp_path, run_report.parent.parent)

    assert [report["candidate_id"] for report in reports] == ["current_candidate"]
    assert reports[0]["_path"].endswith("smoke_reports\\current_candidate.json") or reports[0]["_path"].endswith("smoke_reports/current_candidate.json")


def test_crypto_pipeline_coverage_blocks_validation_report_without_vectorbt(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/crypto_lane/src/validation/crypto_validation_workflow.py",
        "packages/crypto_lane/src/validation/crypto_execution_validator.py",
        "packages/backtest_pipeline/src/crypto_hft_builder.py",
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")
    report_path = tmp_path / "research_cards" / "crypto" / "crypto_example" / "validation_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        """
{
  "candidate_id": "crypto_example",
  "model_id": "CRYPTO_H1",
  "asset_class": "CRYPTO",
  "execution_classification": "L2_PROXY_ONLY",
  "validation_path": "L2_PROXY_VALIDATION",
  "npz_path": "data/replay/hftbacktest/crypto/binance/btcusdt/sample.npz",
  "result": {
    "net_pnl": 12.5,
    "num_trades": 3,
    "num_intents": 4,
    "fill_rate": 0.75,
    "error": ""
  },
  "notes": []
}
""".strip(),
        encoding="utf-8",
    )

    validation_reports = _crypto_validation_reports(tmp_path)
    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        validation_reports,
        [{"candidate_id": "crypto_example", "execution_ack_measured": False}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert validation_reports[0]["candidate_id"] == "crypto_example"
    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert by_stage["hftbacktest_replay"]["role"] == "required_execution_replay"
    assert "upstream VectorBT" in by_stage["hftbacktest_replay"]["reason"]
    assert by_stage["execution_realism"]["status"] == "BLOCKING"
    assert by_stage["full_backtest_readiness"]["status"] == "BLOCKING"


def test_crypto_pipeline_coverage_l2_proxy_does_not_complete_execution_realism(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/crypto_lane/src/validation/crypto_validation_workflow.py",
        "packages/crypto_lane/src/validation/crypto_execution_validator.py",
        "packages/backtest_pipeline/src/crypto_hft_builder.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        [
            {
                "candidate_id": "crypto_example",
                "execution_classification": "L2_PROXY_ONLY",
                "npz_path": "data/replay/hftbacktest/crypto/binance/btcusdt/sample.npz",
                "result": {"error": "", "trade_pnls": [1.0, -0.5, 0.25]},
            }
        ],
        [{"candidate_id": "crypto_example", "execution_ack_measured": True}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {},
        {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_example"],
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert "L3/full execution replay evidence" in by_stage["hftbacktest_replay"]["reason"]
    assert by_stage["execution_realism"]["status"] == "BLOCKING"
    assert "L3/full replay evidence" in by_stage["execution_realism"]["reason"]


def test_crypto_pipeline_coverage_l3_plus_ack_completes_execution_realism(tmp_path: Path) -> None:
    for rel in (
        "packages/crypto_lane/src/validation/crypto_validation_workflow.py",
        "packages/crypto_lane/src/validation/crypto_execution_validator.py",
        "packages/backtest_pipeline/src/crypto_hft_builder.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        [
            {
                "candidate_id": "crypto_example",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": "", "trade_pnls": [1.0, -0.5, 0.25]},
            }
        ],
        [{"candidate_id": "crypto_example", "execution_ack_measured": True}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {},
        {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_example"],
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["hftbacktest_replay"]["status"] == "OBSERVED"
    assert by_stage["execution_realism"]["status"] == "OBSERVED"


def test_crypto_validation_reports_use_only_selected_run_artifacts(tmp_path: Path) -> None:
    run_report = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "run_1" / "validation_reports" / "crypto_run.json"
    run_report.parent.mkdir(parents=True, exist_ok=True)
    run_report.write_text(
        '{"candidate_id":"crypto_run","execution_classification":"NO_EXECUTION","result":{"error":"missing npz"}}',
        encoding="utf-8",
    )
    global_report = tmp_path / "research_cards" / "crypto" / "crypto_global" / "validation_report.json"
    global_report.parent.mkdir(parents=True, exist_ok=True)
    global_report.write_text(
        '{"candidate_id":"crypto_global","execution_classification":"L2_PROXY_ONLY","npz_path":"sample.npz","result":{"error":""}}',
        encoding="utf-8",
    )

    reports = _crypto_validation_reports(tmp_path, run_report.parent.parent)

    assert [r["candidate_id"] for r in reports] == ["crypto_run"]
    assert reports[0]["_path"].endswith("validation_reports\\crypto_run.json") or reports[0]["_path"].endswith("validation_reports/crypto_run.json")


def test_crypto_validation_reports_ignore_global_when_selected_run_has_no_validation(tmp_path: Path) -> None:
    run_dir = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "run_1"
    run_dir.mkdir(parents=True, exist_ok=True)
    global_report = tmp_path / "research_cards" / "crypto" / "crypto_global" / "validation_report.json"
    global_report.parent.mkdir(parents=True, exist_ok=True)
    global_report.write_text(
        '{"candidate_id":"crypto_global","execution_classification":"L2_PROXY_ONLY","npz_path":"sample.npz","result":{"error":""}}',
        encoding="utf-8",
    )

    assert _crypto_validation_reports(tmp_path, run_dir) == []


def test_crypto_pipeline_coverage_blocks_failed_validation_attempt(tmp_path: Path) -> None:
    for rel in (
        "packages/crypto_lane/src/validation/crypto_validation_workflow.py",
        "packages/crypto_lane/src/validation/crypto_execution_validator.py",
        "packages/backtest_pipeline/src/crypto_hft_builder.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        [
            {
                "candidate_id": "crypto_example",
                "execution_classification": "NO_EXECUTION",
                "npz_path": "",
                "result": {"error": "No execution data available for this candidate"},
            }
        ],
        [{"candidate_id": "crypto_example", "execution_ack_measured": False}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {},
        {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_example"],
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert "Validation was attempted" in by_stage["hftbacktest_replay"]["reason"]


def test_crypto_pipeline_coverage_blocks_failed_vectorbt_stage(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/crypto_lane/src/validation/crypto_validation_workflow.py",
        "packages/crypto_lane/src/validation/crypto_execution_validator.py",
        "packages/backtest_pipeline/src/crypto_hft_builder.py",
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [
            {
                "candidate_id": "crypto_example",
                "purged_cv_implemented": True,
                "runs": {"with_btc_node": {"n_splits": 3}},
                "holdout_gate": {"status": "PASS"},
                "negative_controls": {"shuffled_degraded": True, "shifted_degraded": True},
            }
        ],
        [
            {
                "candidate_id": "crypto_example",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": "", "trade_pnls": [1.0, -0.5, 0.25]},
            }
        ],
        [{"candidate_id": "crypto_example", "execution_ack_measured": True}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {
            "robustness_pack": {"observed": True},
            "double_walk_forward": {"observed": True},
        },
        {
            "status": "BLOCKING",
            "observed": False,
            "reason": "The vectorBT package is not installed in the active Workbench runtime.",
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["vectorbt_filter"]["status"] == "BLOCKING"
    assert "vectorBT package" in by_stage["vectorbt_filter"]["reason"]
    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert "upstream VectorBT filter" in by_stage["hftbacktest_replay"]["reason"]
    assert by_stage["full_backtest_readiness"]["status"] == "BLOCKING"


def test_crypto_pipeline_coverage_blocks_hft_when_vectorbt_blocks_before_replay(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/crypto_lane/src/validation/crypto_validation_workflow.py",
        "packages/crypto_lane/src/validation/crypto_execution_validator.py",
        "packages/backtest_pipeline/src/crypto_hft_builder.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        [],
        [{"candidate_id": "crypto_example"}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {},
        {
            "status": "BLOCKING",
            "observed": False,
            "reason": "The vectorBT package is not installed in the active Workbench runtime.",
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["vectorbt_filter"]["status"] == "BLOCKING"
    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert "upstream VectorBT filter" in by_stage["hftbacktest_replay"]["reason"]


def test_crypto_pipeline_coverage_full_readiness_requires_observed_vectorbt(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/crypto_lane/src/validation/crypto_validation_workflow.py",
        "packages/crypto_lane/src/validation/crypto_execution_validator.py",
        "packages/backtest_pipeline/src/crypto_hft_builder.py",
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    reports = [
        {
            "candidate_id": "crypto_example",
            "purged_cv_implemented": True,
            "runs": {"with_btc_node": {"n_splits": 3}},
            "holdout_gate": {"status": "PASS"},
            "negative_controls": {"shuffled_degraded": True, "shifted_degraded": True},
        }
    ]
    validation_reports = [
        {
            "candidate_id": "crypto_example",
            "execution_classification": "L3_VALIDATED",
            "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
            "result": {"error": "", "trade_pnls": [1.0, -0.5, 0.25]},
        }
    ]
    candidate_rows = [{"candidate_id": "crypto_example", "execution_ack_measured": True}]
    robustness_summary = {
        "robustness_pack": {"observed": True},
        "double_walk_forward": {"observed": True},
        "trade_sample_candidate_ids": ["crypto_example"],
    }

    without_vectorbt = _crypto_pipeline_coverage(
        tmp_path,
        reports,
        validation_reports,
        candidate_rows,
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        robustness_summary,
    )
    with_vectorbt = _crypto_pipeline_coverage(
        tmp_path,
        reports,
        validation_reports,
        candidate_rows,
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        robustness_summary,
        {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_example"],
        },
    )

    assert {row["stage"]: row for row in without_vectorbt}["full_backtest_readiness"]["status"] == "BLOCKING"
    assert {row["stage"]: row for row in with_vectorbt}["full_backtest_readiness"]["status"] == "OBSERVED"


def test_crypto_pipeline_coverage_blocks_cross_candidate_readiness(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/crypto_lane/src/validation/crypto_validation_workflow.py",
        "packages/crypto_lane/src/validation/crypto_execution_validator.py",
        "packages/backtest_pipeline/src/crypto_hft_builder.py",
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [
            {
                "candidate_id": "crypto_a",
                "purged_cv_implemented": True,
                "runs": {"with_btc_node": {"n_splits": 3}},
                "holdout_gate": {"status": "PASS"},
                "negative_controls": {"shuffled_degraded": True, "shifted_degraded": True},
            }
        ],
        [
            {
                "candidate_id": "crypto_b",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": "", "trade_pnls": [1.0]},
            }
        ],
        [{"candidate_id": "crypto_c", "execution_ack_measured": True}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {
            "robustness_pack": {"observed": True},
            "double_walk_forward": {"observed": True},
            "trade_sample_candidate_ids": ["crypto_b"],
        },
        {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_a"],
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["full_backtest_readiness"]["status"] == "BLOCKING"
    assert "same candidate" in by_stage["full_backtest_readiness"]["reason"]


def test_crypto_pipeline_coverage_blocks_failed_robustness_stage(tmp_path: Path) -> None:
    for rel in (
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        [],
        [{"candidate_id": "crypto_example"}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {
            "status": "BLOCKING",
            "observed": False,
            "robustness_pack": {
                "status": "BLOCKING",
                "observed": False,
                "reason": "No replay trade_pnls or fill_events were emitted by crypto execution validation.",
            },
            "double_walk_forward": {
                "status": "BLOCKING",
                "observed": False,
                "reason": "No independent walk-forward matrices were emitted by crypto replay validation.",
            },
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["robustness_pack"]["status"] == "BLOCKING"
    assert "trade_pnls" in by_stage["robustness_pack"]["reason"]
    assert by_stage["double_walk_forward_correlation"]["status"] == "BLOCKING"
    assert "walk-forward matrices" in by_stage["double_walk_forward_correlation"]["reason"]
    assert by_stage["full_backtest_readiness"]["status"] == "BLOCKING"


def test_crypto_pipeline_coverage_rejects_unknown_status() -> None:
    from workbench.src.run.evidence_snapshot import _coverage_row

    try:
        _coverage_row("bad_layer", "MADE_UP_STATUS", "artifact", "reason")
    except ValueError as exc:
        assert "unknown coverage status" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("coverage row accepted an unknown status")
