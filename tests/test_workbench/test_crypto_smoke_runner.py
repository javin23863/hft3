"""Crypto smoke loop honesty checks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


def test_crypto_smoke_decision_quarantines_without_venue_submit_ack() -> None:
    from workbench.src.run import crypto_smoke_runner

    decision = crypto_smoke_runner._decision(
        [
            {
                "candidate_id": "crypto_candidate",
                "pass_fail": "pass",
                "deflated_sharpe_cdf": 0.99,
                "oos_ic": 0.12,
                "n_rows": 512,
                "order_ack_status": "INSUFFICIENT crypto venue submit-to-ack samples",
            }
        ],
        validation_reports=[
            {
                "candidate_id": "crypto_candidate",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": ""},
            }
        ],
        robustness_summary={
            "status": "OBSERVED",
            "observed": True,
            "blocking_gates": [],
            "trade_sample_candidate_ids": ["crypto_candidate"],
        },
        vectorbt_summary={
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_candidate"],
        },
    )

    assert decision["action"] == "QUARANTINE"
    assert "crypto venue submit-to-ack" in decision["reason"]
    assert decision["top_smoke_candidate"] == "crypto_candidate"
    assert decision["activation_registry_ready"] is False
    assert any(gate["gate"] == "bitcoin_edge_packets" for gate in decision["blocking_gates"])


def test_crypto_smoke_ranking_keeps_proxy_pnl_diagnostic_only() -> None:
    from workbench.src.run import crypto_smoke_runner

    ranked = crypto_smoke_runner._rank_candidates(
        [
            {
                "candidate_id": "strong_statistical_smoke",
                "pass_fail": "pass",
                "deflated_sharpe_cdf": 1.0,
                "oos_ic": 0.60,
                "proxy_net_pnl_bps": -3000.0,
                "n_rows": 512,
            },
            {
                "candidate_id": "positive_proxy_diagnostic",
                "pass_fail": "pass",
                "deflated_sharpe_cdf": 0.95,
                "oos_ic": 0.20,
                "proxy_net_pnl_bps": 25.0,
                "n_rows": 256,
            },
        ]
    )

    assert ranked[0]["candidate_id"] == "strong_statistical_smoke"
    assert ranked[1]["candidate_id"] == "positive_proxy_diagnostic"


def test_crypto_smoke_decision_quarantines_before_ack_without_hft_replay() -> None:
    from workbench.src.run import crypto_smoke_runner

    decision = crypto_smoke_runner._decision(
        [
            {
                "candidate_id": "crypto_candidate",
                "pass_fail": "pass",
                "deflated_sharpe_cdf": 0.99,
                "oos_ic": 0.12,
                "n_rows": 512,
                "order_ack_status": "INSUFFICIENT crypto venue submit-to-ack samples",
            }
        ],
        edge_packets={"status": "OBSERVED", "observed": True},
        validation_reports=[],
        vectorbt_summary={
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_candidate"],
        },
    )

    assert decision["action"] == "QUARANTINE"
    assert decision["reason"] == "crypto execution replay evidence is incomplete"
    assert decision["top_smoke_candidate"] == "crypto_candidate"
    assert decision["blocking_gates"][0]["gate"] == "hft_replay_validation"
    assert decision["blocking_gates"][0]["status"] == "MISSING"


def test_crypto_smoke_decision_quarantines_before_replay_without_vectorbt() -> None:
    from workbench.src.run import crypto_smoke_runner

    decision = crypto_smoke_runner._decision(
        [
            {
                "candidate_id": "crypto_candidate",
                "pass_fail": "pass",
                "deflated_sharpe_cdf": 0.99,
                "oos_ic": 0.12,
                "n_rows": 512,
            }
        ],
        edge_packets={"status": "OBSERVED", "observed": True},
        vectorbt_summary={
            "status": "BLOCKING",
            "observed": False,
            "reason": "The vectorBT package is not installed in the active Workbench runtime.",
        },
    )

    assert decision["action"] == "QUARANTINE"
    assert decision["reason"] == "vectorBT filter evidence is incomplete"
    assert decision["blocking_gates"][0]["gate"] == "vectorbt_filter"
    assert "vectorBT package" in decision["blocking_gates"][0]["reason"]


def test_crypto_smoke_decision_requires_vectorbt_summary() -> None:
    from workbench.src.run import crypto_smoke_runner

    decision = crypto_smoke_runner._decision(
        [
            {
                "candidate_id": "crypto_candidate",
                "pass_fail": "pass",
                "deflated_sharpe_cdf": 0.99,
                "oos_ic": 0.12,
                "n_rows": 512,
            }
        ],
        edge_packets={"status": "OBSERVED", "observed": True},
    )

    assert decision["action"] == "QUARANTINE"
    assert decision["reason"] == "vectorBT filter evidence is incomplete"
    assert decision["blocking_gates"][0]["status"] == "MISSING"


def test_crypto_smoke_decision_quarantines_before_ack_without_robustness() -> None:
    from workbench.src.run import crypto_smoke_runner

    decision = crypto_smoke_runner._decision(
        [
            {
                "candidate_id": "crypto_candidate",
                "pass_fail": "pass",
                "deflated_sharpe_cdf": 0.99,
                "oos_ic": 0.12,
                "n_rows": 512,
                "execution_ack_measured": True,
                "order_ack_status": "MEASURED",
            }
        ],
        edge_packets={"status": "OBSERVED", "observed": True},
        validation_reports=[
            {
                "candidate_id": "crypto_candidate",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": ""},
            }
        ],
        robustness_summary={
            "status": "BLOCKING",
            "observed": False,
            "blocking_gates": [{"gate": "robustness_pack", "status": "TRADE_SAMPLE_MISSING"}],
        },
        vectorbt_summary={
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_candidate"],
        },
    )

    assert decision["action"] == "QUARANTINE"
    assert decision["reason"] == "robustness evidence is incomplete"
    assert decision["blocking_gates"][0]["gate"] == "robustness_pack"


def test_crypto_smoke_decision_rejects_observed_robustness_failure() -> None:
    from workbench.src.run import crypto_smoke_runner

    decision = crypto_smoke_runner._decision(
        [
            {
                "candidate_id": "crypto_candidate",
                "pass_fail": "pass",
                "deflated_sharpe_cdf": 0.99,
                "oos_ic": 0.12,
                "n_rows": 512,
                "execution_ack_measured": True,
                "order_ack_status": "MEASURED",
            }
        ],
        edge_packets={"status": "OBSERVED", "observed": True},
        validation_reports=[
            {
                "candidate_id": "crypto_candidate",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": ""},
            }
        ],
        robustness_summary={
            "status": "FAIL",
            "observed": True,
            "blocking_gates": [{"gate": "robustness_pack", "status": "FAIL"}],
            "trade_sample_candidate_ids": ["crypto_candidate"],
        },
        vectorbt_summary={
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_candidate"],
        },
    )

    assert decision["action"] == "REJECT"
    assert decision["reason"] == "robustness evidence failed observed gates"
    assert decision["blocking_gates"][0]["gate"] == "robustness_pack"


def test_crypto_smoke_decision_uses_vectorbt_promoted_evidence_candidate() -> None:
    from workbench.src.run import crypto_smoke_runner

    decision = crypto_smoke_runner._decision(
        [
            {
                "candidate_id": "crypto_a",
                "pass_fail": "pass",
                "deflated_sharpe_cdf": 0.99,
                "oos_ic": 0.50,
                "n_rows": 512,
                "execution_ack_measured": True,
                "order_ack_status": "MEASURED",
            },
            {
                "candidate_id": "crypto_b",
                "pass_fail": "pass",
                "deflated_sharpe_cdf": 0.10,
                "oos_ic": 0.05,
                "n_rows": 512,
                "execution_ack_measured": False,
                "order_ack_status": "INSUFFICIENT crypto venue submit-to-ack samples",
            },
        ],
        edge_packets={"status": "OBSERVED", "observed": True},
        validation_reports=[
            {
                "candidate_id": "crypto_b",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": ""},
            }
        ],
        robustness_summary={
            "status": "OBSERVED",
            "observed": True,
            "blocking_gates": [],
            "trade_sample_candidate_ids": ["crypto_b"],
        },
        vectorbt_summary={
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_b"],
        },
    )

    assert decision["action"] == "QUARANTINE"
    assert decision["reason"] == "crypto venue submit-to-ack evidence is insufficient"
    assert decision["evidence_candidate_id"] == "crypto_b"
    assert decision["top_smoke_candidate"] == "crypto_b"


def test_robustness_stage_blocks_without_replay_trade_samples(tmp_path) -> None:
    from workbench.src.run import crypto_smoke_runner

    summary = crypto_smoke_runner._run_robustness_evidence_stage(
        tmp_path / "run",
        [
            {
                "candidate_id": "crypto_candidate",
                "execution_classification": "L2_PROXY_ONLY",
                "npz_path": "data/replay/hftbacktest/crypto/binance/btcusdt/sample.npz",
                "result": {"error": "", "num_trades": 3, "net_pnl": 12.5},
            }
        ],
    )

    assert (tmp_path / "run" / "robustness_summary.json").is_file()
    assert summary["status"] == "BLOCKING"
    assert summary["observed"] is False
    assert summary["trade_sample_count"] == 0
    assert summary["blocking_gates"][0]["gate"] == "robustness_pack"
    assert summary["blocking_gates"][0]["status"] == "TRADE_SAMPLE_MISSING"
    assert summary["blocking_gates"][1]["gate"] == "double_walk_forward_correlation"


def test_vectorbt_stage_writes_run_local_blocker_when_package_missing(tmp_path, monkeypatch) -> None:
    from backtest_pipeline.src import vectorbt_adapter
    from workbench.src.run import crypto_smoke_runner

    class Result:
        def to_dict(self):
            return {
                "run_id": "no_vbt_test",
                "vectorbt_available": False,
                "total_candidates": 1,
                "promoted": [],
                "rejected": [
                    {
                        "candidate_id": "crypto_candidate",
                        "hypothesis_id": "CRYPTO_H1",
                        "reject_reason": "vectorbt_not_installed",
                        "metric_values": {},
                    }
                ],
            }

    monkeypatch.setattr(vectorbt_adapter, "filter_candidates", lambda *args, **kwargs: Result())

    summary = crypto_smoke_runner._run_vectorbt_filter_stage(
        tmp_path / "run",
        [{"candidate_id": "crypto_candidate"}],
        [
            {
                "candidate_id": "crypto_candidate",
                "hypothesis_id": "CRYPTO_H1",
                "features": ["basis_zscore"],
                "target": "forward_basis_change",
            }
        ],
        tmp_path,
    )

    assert (tmp_path / "run" / "vectorbt_summary.json").is_file()
    assert summary["status"] == "BLOCKING"
    assert summary["observed"] is False
    assert summary["adapter_invoked"] is True
    assert summary["vectorbt_available"] is False
    assert summary["candidate_ids"] == ["crypto_candidate"]
    assert summary["rejection_reasons"] == {"vectorbt_not_installed": 1}
    assert "not installed" in summary["reason"]


def test_vectorbt_stage_preserves_registry_source_ids(tmp_path, monkeypatch) -> None:
    from backtest_pipeline.src import vectorbt_adapter
    from workbench.src.run import crypto_smoke_runner

    class Result:
        def to_dict(self):
            return {
                "run_id": "source_id_test",
                "vectorbt_available": False,
                "backend": "numpy_fallback",
                "total_candidates": 1,
                "promoted": [
                    {
                        "candidate_id": "parameterized_hash",
                        "hypothesis_id": "CRYPTO_H2",
                        "pass_reason": "all_gates_passed",
                        "metric_values": {"expectancy": 1.0},
                    }
                ],
                "rejected": [],
            }

    monkeypatch.setattr(vectorbt_adapter, "filter_candidates", lambda *args, **kwargs: Result())

    summary = crypto_smoke_runner._run_vectorbt_filter_stage(
        tmp_path / "run",
        [{"candidate_id": "crypto_source"}],
        [
            {
                "candidate_id": "crypto_source",
                "hypothesis_id": "CRYPTO_H2",
                "features": ["expected_net_funding_after_cost"],
                "target": "forward_net_funding_after_hedge",
            }
        ],
        tmp_path,
    )

    assert summary["observed"] is True
    assert summary["promoted_candidate_ids"] == ["parameterized_hash"]
    assert summary["promoted_source_candidate_ids"] == ["crypto_source"]
    assert summary["promoted"][0]["source_candidate_id"] == "crypto_source"


def test_crypto_after_action_writes_run_local_inputs_and_meta(tmp_path, monkeypatch) -> None:
    from data_layer.pipeline import after_action
    from workbench.src.run import crypto_smoke_runner

    run_dir = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "run_1"
    status = {
        "run_id": "run_1",
        "candidates": [
            {
                "candidate_id": "crypto_candidate",
                "execution_ack_measured": False,
                "order_ack_status": "INSUFFICIENT crypto venue submit-to-ack samples",
            }
        ],
        "decision": {
            "action": "QUARANTINE",
            "reason": "venue ack evidence is insufficient",
            "evidence_candidate_id": "crypto_candidate",
        },
    }

    def fake_after_action(artifact_dir, repo_root):
        (artifact_dir / "after_action_packet.json").write_text("{}", encoding="utf-8")
        (artifact_dir / "after_action_symbolic.json").write_text('{"passed": true}', encoding="utf-8")
        (artifact_dir / "kg_slice.json").write_text('{"nodes": [], "edges": []}', encoding="utf-8")
        return {
            "llm_status": "unavailable",
            "llm_model": None,
            "skip_reasons": [
                "Approved non-API GPT-5.5 xhigh runtime transport is not available to Workbench"
            ],
            "symbolic_passed": True,
            "report_written": False,
            "response_written": False,
        }

    monkeypatch.setattr(after_action, "run_after_action_report", fake_after_action)

    meta = crypto_smoke_runner._run_crypto_after_action(
        tmp_path,
        run_dir,
        status,
        [
            {
                "candidate_id": "crypto_candidate",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"net_pnl": 12.5, "gross_pnl": 20.0, "num_trades": 3},
            }
        ],
        {
            "status": "FAIL",
            "trade_sample_count": 3,
            "robustness_pack": {"failed": ["latency_sensitivity"]},
            "double_walk_forward": {"status": "FAIL"},
        },
        {"status": "OBSERVED", "observed": True},
    )

    diagnostics = json.loads((run_dir / "diagnostics.json").read_text(encoding="utf-8"))
    config = (run_dir / "config.yaml").read_text(encoding="utf-8")

    assert diagnostics["execution_assumptions"] == "crypto_order_book_replay"
    assert diagnostics["latency_authority"] == "crypto_venue_submit_ack"
    assert diagnostics["num_trades"] == 3
    assert "symbol: BTCUSDT" in config
    assert meta["llm_status"] == "unavailable"
    assert meta["gate_status"] == "FAIL"
    assert meta["passed"] is False
    assert "GPT-5.5 xhigh" in meta["blocking_reason"]
    assert (run_dir / "after_action_packet.json").is_file()
    assert "after_action_packet.json" in meta["paths"]["after_action_packet.json"]


def test_crypto_relationship_review_artifacts_are_review_only(tmp_path) -> None:
    from workbench.src.run import crypto_smoke_runner

    run_dir = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "run_1"
    for rel in (
        "smoke_reports/crypto_candidate.json",
        "validation_reports/crypto_candidate.json",
        "robustness_summary.json",
        "status.json",
        "walk_forward_correlation.json",
    ):
        path = run_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    summary = crypto_smoke_runner._write_crypto_relationship_review(
        tmp_path,
        run_dir,
        {
            "run_id": "run_1",
            "decision": {"evidence_candidate_id": "crypto_candidate"},
            "bitcoin_edge_packets": {"observed": False, "status": "STALE", "reason": "packet age exceeded"},
        },
        [
            {
                "candidate_id": "crypto_candidate",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": ""},
            }
        ],
        {
            "status": "FAIL",
            "robustness_pack": {"failed": ["transaction_cost_sensitivity"]},
            "double_walk_forward": {"status": "FAIL", "reason": "WF matrices are identical"},
        },
    )
    payload = json.loads((run_dir / "relationship_candidates.json").read_text(encoding="utf-8"))

    assert summary["candidate_count"] >= 3
    assert summary["kg_write_status"] == "not_attempted"
    assert summary["openfoundry_write_status"] == "not_attempted"
    assert summary["promotion_authority"] is False
    assert all(candidate["kg_written"] is False for candidate in payload["candidates"])
    assert all(candidate["openfoundry_written"] is False for candidate in payload["candidates"])


def test_crypto_vectorbt_stage_does_not_import_feature_to_signal_binding() -> None:
    import inspect
    from pathlib import Path

    from workbench.src.run import crypto_smoke_runner

    repo = Path(__file__).resolve().parents[2]
    src = inspect.getsource(crypto_smoke_runner._run_vectorbt_filter_stage)
    signal_src = inspect.getsource(crypto_smoke_runner._crypto_vectorbt_signal_computer)
    assert "workbench.src.run.crypto_vectorbt_signal" not in src
    assert "make_crypto_vectorbt_binding" not in src
    assert "has_crypto_vectorbt_binding" not in src
    assert "vectorbt_signal_bindings" not in src
    assert "return_proxy_bps" not in signal_src
    assert "net_pnl_bps" not in signal_src
    assert 'row.get("target"' not in signal_src
    assert not (repo / "packages" / "crypto_lane" / "config" / "vectorbt_signal_bindings.yaml").exists()
    for candidate_yaml in (repo / "packages" / "crypto_lane" / "config" / "candidates").glob("crypto_h*.yaml"):
        text = candidate_yaml.read_text(encoding="utf-8")
        assert "vectorbt_signal:" not in text


def test_crypto_vectorbt_signal_computer_uses_run_local_oos_position_tape(tmp_path) -> None:
    import json

    import numpy as np

    from workbench.src.run import crypto_smoke_runner

    class Candidate:
        candidate_id = "crypto_signal"

    run_dir = tmp_path / "run"
    report_dir = run_dir / "smoke_reports"
    report_dir.mkdir(parents=True)
    report_dir.joinpath("crypto_signal.json").write_text(
        json.dumps(
            {
                "candidate_id": "crypto_signal",
                "research_pnl_proxy": {
                    "scope": "purged_walk_forward_oos_diagnostic",
                    "leakage_controls": {
                        "train_rows_only_for_fit": True,
                        "train_predictions_only_for_threshold": True,
                        "test_rows_only_for_reported_pnl": True,
                        "purged_walk_forward": True,
                    },
                    "equity_curve": [
                        {"exchange_timestamp": 1000, "position": 0.0, "net_pnl_bps": 9999.0},
                        {"exchange_timestamp": 2000, "position": 1.0, "return_proxy_bps": -9999.0},
                        {"exchange_timestamp": 3000, "position": 1.0},
                        {"exchange_timestamp": 4000, "position": 0.0},
                        {"exchange_timestamp": 5000, "position": 1.0},
                        {"exchange_timestamp": 6000, "position": 0.0},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    ohlcv = np.array(
        [
            [100.0, 100.0, 100.0, 100.0, 1.0, 1000],
            [100.0, 101.0, 100.0, 101.0, 1.0, 2000],
            [101.0, 102.0, 101.0, 102.0, 1.0, 3000],
            [102.0, 102.0, 101.0, 101.0, 1.0, 4000],
            [101.0, 103.0, 101.0, 103.0, 1.0, 5000],
            [103.0, 103.0, 102.0, 102.0, 1.0, 6000],
        ],
        dtype=float,
    )

    entries, exits = crypto_smoke_runner._crypto_vectorbt_signal_computer(run_dir)(
        Candidate(), ohlcv, object(), tmp_path
    )

    assert entries.tolist() == [0.0, 1.0, 0.0, 0.0, 1.0, 0.0]
    assert exits.tolist() == [0.0, 0.0, 0.0, -1.0, 0.0, -1.0]


def test_crypto_vectorbt_signal_computer_rejects_missing_leakage_controls(tmp_path) -> None:
    import json

    import numpy as np

    from workbench.src.run import crypto_smoke_runner

    class Candidate:
        candidate_id = "crypto_signal"

    run_dir = tmp_path / "run"
    report_dir = run_dir / "smoke_reports"
    report_dir.mkdir(parents=True)
    report_dir.joinpath("crypto_signal.json").write_text(
        json.dumps(
            {
                "candidate_id": "crypto_signal",
                "research_pnl_proxy": {
                    "scope": "purged_walk_forward_oos_diagnostic",
                    "leakage_controls": {"train_rows_only_for_fit": True},
                    "equity_curve": [{"exchange_timestamp": 1000, "position": 1.0}],
                },
            }
        ),
        encoding="utf-8",
    )
    ohlcv = np.array([[100.0, 100.0, 100.0, 100.0, 1.0, 1000]], dtype=float)

    try:
        crypto_smoke_runner._crypto_vectorbt_signal_computer(run_dir)(Candidate(), ohlcv, object(), tmp_path)
    except ValueError as exc:
        assert "missing required leakage controls" in str(exc)
        assert "test_rows_only_for_reported_pnl" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("signal computer accepted a report without leakage controls")


def test_crypto_replay_signal_sequence_uses_oos_position_transitions(tmp_path) -> None:
    import json

    from workbench.src.run import crypto_smoke_runner

    run_dir = tmp_path / "run"
    report_dir = run_dir / "smoke_reports"
    report_dir.mkdir(parents=True)
    report_dir.joinpath("crypto_signal.json").write_text(
        json.dumps(
            {
                "candidate_id": "crypto_signal",
                "research_pnl_proxy": {
                    "scope": "purged_walk_forward_oos_diagnostic",
                    "leakage_controls": {
                        "train_rows_only_for_fit": True,
                        "train_predictions_only_for_threshold": True,
                        "test_rows_only_for_reported_pnl": True,
                        "purged_walk_forward": True,
                    },
                    "equity_curve": [
                        {"exchange_timestamp": 1000, "position": 0.0, "net_pnl_bps": 999.0},
                        {"exchange_timestamp": 2000, "position": 1.0, "return_proxy_bps": -999.0},
                        {"exchange_timestamp": 3000, "position": 1.0},
                        {"exchange_timestamp": 4000, "position": 0.0},
                        {"exchange_timestamp": 5000, "position": 1.0},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    assert crypto_smoke_runner._crypto_replay_signal_sequence(run_dir, "crypto_signal", tmp_path) == [
        0.0,
        1.0,
        0.0,
        -1.0,
        1.0,
    ]


def test_crypto_vectorbt_data_loader_restricts_to_run_local_oos_tape(tmp_path, monkeypatch) -> None:
    import json

    import polars as pl

    from workbench.src.run import crypto_smoke_runner

    candidate = {"candidate_id": "crypto_signal"}
    run_dir = tmp_path / "run"
    report_dir = run_dir / "smoke_reports"
    report_dir.mkdir(parents=True)
    report_dir.joinpath("crypto_signal.json").write_text(
        json.dumps(
            {
                "candidate_id": "crypto_signal",
                "research_pnl_proxy": {
                    "equity_curve": [
                        {"exchange_timestamp": ts, "position": 1.0}
                        for ts in range(20, 40)
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    data_dir = tmp_path / "crypto_data"
    data_dir.mkdir()
    pl.DataFrame(
        {
            "exchange_timestamp": list(range(50)),
            "perp_mid": [100.0 + idx for idx in range(50)],
            "exchange_volume": [1.0] * 50,
        }
    ).write_csv(data_dir / "spot_perp_ticks.csv")

    monkeypatch.setattr(
        "crypto_lane.src.config.data_paths.resolve_lane_data_dir",
        lambda _backtest: data_dir,
    )
    monkeypatch.setattr(
        "crypto_lane.src.types.repo_root_from_lane",
        lambda: tmp_path,
    )

    ohlcv = crypto_smoke_runner._crypto_vectorbt_data_loader(candidate, run_dir)(
        "crypto_signal",
        tmp_path,
    )

    assert ohlcv is not None
    assert ohlcv.shape[0] == 20
    assert ohlcv[:, 5].astype(int).tolist() == list(range(20, 40))


def test_vectorbt_stage_reports_signal_adapter_rejection_contract(tmp_path, monkeypatch) -> None:
    from backtest_pipeline.src import vectorbt_adapter
    from workbench.src.run import crypto_smoke_runner

    class Result:
        def to_dict(self):
            return {
                "run_id": "missing_signal_contract",
                "vectorbt_available": False,
                "backend": "numpy_fallback",
                "total_candidates": 1,
                "promoted": [],
                "rejected": [
                    {
                        "candidate_id": "crypto_h2_funding_capture",
                        "hypothesis_id": "CRYPTO_H2",
                        "reject_reason": "unresolvable_model_id",
                        "metric_values": {"error": "crypto OOS prediction signal tape is empty"},
                    }
                ],
            }

    monkeypatch.setattr(vectorbt_adapter, "filter_candidates", lambda *args, **kwargs: Result())

    summary = crypto_smoke_runner._run_vectorbt_filter_stage(
        tmp_path / "run",
        [{"candidate_id": "crypto_h2_funding_capture"}],
        [
            {
                "candidate_id": "crypto_h2_funding_capture",
                "hypothesis_id": "CRYPTO_H2",
                "features": ["expected_net_funding_after_cost"],
                "target": "forward_net_funding_after_hedge",
            }
        ],
        tmp_path,
    )

    contract = summary["signal_adapter_rejection_contract"]
    source = summary["signal_source_contract"]
    assert "signal_binding_contract" not in summary or summary["signal_binding_contract"] == {}
    assert contract["status"] == "REJECTED"
    assert contract["adapter"] == "crypto_oos_prediction_position_to_vectorbt_signals"
    assert "timestamp-aligned" in contract["why_blocking"]
    assert source["proxy_pnl_used_for_signal"] is False
    assert source["labels_used_for_signal"] is False
    assert "packages/trade_manager/signals.py::ModelSignal" in contract["existing_repo_sources"]
    assert any("exchange_timestamp" in item for item in contract["required_for_adapter_acceptance"])


def test_vectorbt_stage_reports_promotion_gate_failure_before_missing_binding(tmp_path, monkeypatch) -> None:
    from backtest_pipeline.src import vectorbt_adapter
    from workbench.src.run import crypto_smoke_runner

    class Result:
        def __init__(self, event_id: str) -> None:
            self.event_id = event_id

        def to_dict(self):
            if self.event_id == "crypto_bound":
                return {
                    "run_id": "bound_gate_test",
                    "vectorbt_available": False,
                    "backend": "numpy_fallback",
                    "total_candidates": 1,
                    "promoted": [],
                    "rejected": [
                        {
                            "candidate_id": "parameterized_bound",
                            "hypothesis_id": "CRYPTO_H2",
                            "reject_reason": "promotion_gate_failed",
                            "metric_values": {"oos_expectancy": 0.0, "wf_consistency": 0.0},
                        }
                    ],
                }
            return {
                "run_id": "missing_binding_test",
                "vectorbt_available": False,
                "backend": "numpy_fallback",
                "total_candidates": 1,
                "promoted": [],
                "rejected": [
                    {
                        "candidate_id": self.event_id,
                        "hypothesis_id": "CRYPTO_H1",
                        "reject_reason": "unresolvable_model_id",
                        "metric_values": {"error": "signal binding unavailable"},
                    }
                ],
            }

    monkeypatch.setattr(vectorbt_adapter, "filter_candidates", lambda *args, **kwargs: Result(kwargs["event_id"]))

    summary = crypto_smoke_runner._run_vectorbt_filter_stage(
        tmp_path / "run",
        [{"candidate_id": "crypto_bound"}, {"candidate_id": "crypto_unbound"}],
        [
            {
                "candidate_id": "crypto_bound",
                "hypothesis_id": "CRYPTO_H2",
                "features": ["expected_net_funding_after_cost"],
                "target": "forward_net_funding_after_hedge",
            },
            {
                "candidate_id": "crypto_unbound",
                "hypothesis_id": "CRYPTO_H1",
                "features": ["basis_zscore"],
                "target": "forward_basis_change",
            },
        ],
        tmp_path,
    )

    assert summary["observed"] is False
    assert summary["rejection_reasons"] == {"promotion_gate_failed": 1, "unresolvable_model_id": 1}
    assert "promotion gates" in summary["reason"]


def test_crypto_smoke_blocked_gate_sets_blocked_run_state(tmp_path, monkeypatch) -> None:
    from crypto_lane.src.ingest import edge_status
    from crypto_lane.src.ml import candidate_registry, walk_forward_runner
    from workbench.src.run import crypto_smoke_runner

    monkeypatch.setattr(
        edge_status,
        "load_edge_packet_status",
        lambda repo: {"status": "OBSERVED", "observed": True},
    )
    monkeypatch.setattr(
        candidate_registry,
        "discover_candidates",
        lambda: [{"candidate_id": "crypto_candidate", "hypothesis_id": "CRYPTO_H1"}],
    )
    monkeypatch.setattr(
        walk_forward_runner,
        "run_smoke",
        lambda candidate_id: {
            "candidate_id": candidate_id,
            "hypothesis_id": "CRYPTO_H1",
            "target": "forward_basis_change",
            "pass_fail": "pass",
            "runs": {"with_btc_node": {"oos_ic_baseline_mean": 0.1, "n_rows": 100, "n_folds": 3, "n_splits": 3}},
            "holdout_gate": {"status": "PASS"},
            "negative_controls": {"shuffled_degraded": True, "shifted_degraded": True},
            "research_pnl_proxy": {"summary": {"net_pnl_bps": 10.0}},
            "execution_ack_status": "INSUFFICIENT",
            "execution_ack_measured": False,
        },
    )
    monkeypatch.setattr(
        crypto_smoke_runner,
        "_run_vectorbt_filter_stage",
        lambda run_dir, ranked, registry_candidates=None, repo=None: {
            "status": "BLOCKING",
            "observed": False,
            "reason": "missing vectorBT",
        },
    )
    def fail_if_hft_replay_runs(*args):
        raise AssertionError("crypto execution replay must not run before vectorBT promotion artifacts")

    monkeypatch.setattr(crypto_smoke_runner, "_validate_ranked_candidates", fail_if_hft_replay_runs)
    monkeypatch.setattr(
        crypto_smoke_runner,
        "_run_robustness_evidence_stage",
        lambda *args: {"status": "BLOCKING", "observed": False, "blocking_gates": []},
    )

    result = crypto_smoke_runner.run_crypto_smoke(tmp_path)
    stages = {stage["name"]: stage["status"] for stage in result["stages"]}

    assert result["state"] == "blocked"
    assert result["decision"]["action"] == "QUARANTINE"
    assert stages["vectorbt_filter"] == "blocked"
    assert stages["hft_replay_validation"] == "blocked"
    assert stages["robustness_evidence"] == "blocked"
    assert stages["decision_gate"] == "blocked"
    assert result["hft_validation"]["status"] == "BLOCKED_BY_VECTORBT"
    assert result["hft_validation"]["reports"] == []
    assert result["robustness_evidence"]["source"] == "vectorbt_filter"
    assert (tmp_path / "runtime" / "workbench" / "crypto_smoke" / result["run_id"] / "smoke_reports" / "crypto_candidate.json").is_file()


def test_crypto_smoke_replay_uses_only_vectorbt_promoted_sources(tmp_path, monkeypatch) -> None:
    from crypto_lane.src.ingest import edge_status
    from crypto_lane.src.ml import candidate_registry, walk_forward_runner
    from workbench.src.run import crypto_smoke_runner

    candidates = [
        {"candidate_id": "crypto_a", "hypothesis_id": "CRYPTO_H1"},
        {"candidate_id": "crypto_b", "hypothesis_id": "CRYPTO_H2"},
    ]
    seen_replay_ranked: list[list[dict[str, object]]] = []

    monkeypatch.setattr(edge_status, "load_edge_packet_status", lambda repo: {"status": "OBSERVED", "observed": True})
    monkeypatch.setattr(candidate_registry, "discover_candidates", lambda: candidates)

    def smoke(candidate_id: str) -> dict[str, object]:
        oos = 0.20 if candidate_id == "crypto_a" else 0.05
        return {
            "candidate_id": candidate_id,
            "hypothesis_id": "CRYPTO_H1" if candidate_id == "crypto_a" else "CRYPTO_H2",
            "target": "forward_basis_change",
            "pass_fail": "pass",
            "runs": {"with_btc_node": {"oos_ic_baseline_mean": oos, "n_rows": 100, "n_folds": 3, "n_splits": 3}},
            "holdout_gate": {"status": "PASS"},
            "negative_controls": {"shuffled_degraded": True, "shifted_degraded": True},
            "research_pnl_proxy": {"summary": {"net_pnl_bps": 10.0}},
            "execution_ack_status": "INSUFFICIENT crypto venue submit-to-ack samples",
            "execution_ack_measured": False,
        }

    monkeypatch.setattr(walk_forward_runner, "run_smoke", smoke)
    monkeypatch.setattr(
        crypto_smoke_runner,
        "_run_vectorbt_filter_stage",
        lambda run_dir, ranked, registry_candidates=None, repo=None: {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_b"],
            "promoted_candidate_ids": ["parameterized_b"],
            "promoted": [{"candidate_id": "parameterized_b", "source_candidate_id": "crypto_b"}],
            "rejected": [{"candidate_id": "parameterized_a", "source_candidate_id": "crypto_a"}],
        },
    )

    def validate(repo, run_dir, ranked, registry_candidates):
        seen_replay_ranked.append(ranked)
        return [
            {
                "candidate_id": str(ranked[0]["candidate_id"]),
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": ""},
            }
        ]

    monkeypatch.setattr(crypto_smoke_runner, "_validate_ranked_candidates", validate)
    monkeypatch.setattr(
        crypto_smoke_runner,
        "_run_robustness_evidence_stage",
        lambda *args: {
            "status": "OBSERVED",
            "observed": True,
            "blocking_gates": [],
            "trade_sample_candidate_ids": ["crypto_b"],
        },
    )

    result = crypto_smoke_runner.run_crypto_smoke(tmp_path)

    assert [[row["candidate_id"] for row in batch] for batch in seen_replay_ranked] == [["crypto_b"]]
    assert [row["candidate_id"] for row in result["vectorbt_promoted_order"]] == ["crypto_b"]
    assert result["hft_validation"]["reports"][0]["candidate_id"] == "crypto_b"
    assert result["decision"]["evidence_candidate_id"] == "crypto_b"


def test_crypto_smoke_blocks_run_when_after_action_required_gate_fails(tmp_path, monkeypatch) -> None:
    from crypto_lane.src.ingest import edge_status
    from crypto_lane.src.ml import candidate_registry, walk_forward_runner
    from workbench.src.run import crypto_smoke_runner

    monkeypatch.setattr(edge_status, "load_edge_packet_status", lambda repo: {"status": "OBSERVED", "observed": True})
    monkeypatch.setattr(candidate_registry, "discover_candidates", lambda: [{"candidate_id": "crypto_b", "hypothesis_id": "CRYPTO_H2"}])
    monkeypatch.setattr(
        walk_forward_runner,
        "run_smoke",
        lambda candidate_id: {
            "candidate_id": candidate_id,
            "hypothesis_id": "CRYPTO_H2",
            "target": "forward_basis_change",
            "pass_fail": "pass",
            "runs": {"with_btc_node": {"oos_ic_baseline_mean": 0.2, "n_rows": 100, "n_folds": 3, "n_splits": 3}},
            "holdout_gate": {"status": "PASS"},
            "negative_controls": {"shuffled_degraded": True, "shifted_degraded": True},
            "research_pnl_proxy": {"summary": {"net_pnl_bps": 10.0}},
            "execution_ack_status": "MEASURED",
            "execution_ack_measured": True,
        },
    )
    monkeypatch.setattr(
        crypto_smoke_runner,
        "_run_vectorbt_filter_stage",
        lambda run_dir, ranked, registry_candidates=None, repo=None: {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_b"],
            "promoted_candidate_ids": ["parameterized_b"],
            "promoted": [{"candidate_id": "parameterized_b", "source_candidate_id": "crypto_b"}],
            "rejected": [],
        },
    )
    monkeypatch.setattr(
        crypto_smoke_runner,
        "_validate_ranked_candidates",
        lambda repo, run_dir, ranked, registry_candidates: [
            {
                "candidate_id": "crypto_b",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": ""},
            }
        ],
    )
    monkeypatch.setattr(
        crypto_smoke_runner,
        "_run_robustness_evidence_stage",
        lambda *args: {
            "status": "OBSERVED",
            "observed": True,
            "blocking_gates": [],
            "trade_sample_candidate_ids": ["crypto_b"],
        },
    )
    monkeypatch.setattr(
        crypto_smoke_runner,
        "_run_crypto_after_action",
        lambda *args: {
            "llm_status": "unavailable",
            "required": True,
            "gate_status": "FAIL",
            "passed": False,
            "response_written": True,
            "blocking_reason": "GPT-5.5 xhigh after-action did not pass",
            "skip_reasons": ["GPT-5.5 xhigh after-action did not pass"],
        },
    )
    monkeypatch.setattr(
        crypto_smoke_runner,
        "_write_crypto_relationship_review",
        lambda *args: {"candidate_count": 0, "validated_count": 0, "rejected_count": 0},
    )

    result = crypto_smoke_runner.run_crypto_smoke(tmp_path)
    stages = {stage["name"]: stage["status"] for stage in result["stages"]}

    assert result["state"] == "blocked"
    assert stages["after_action"] == "blocked"
    assert result["decision"]["activation_registry_ready"] is False
    assert result["decision"]["after_action_blocking_gate"]["gate"] == "after_action_gpt55_xhigh"


def test_extract_replay_trade_pnls_reads_validator_result_shape() -> None:
    from workbench.src.run import crypto_smoke_runner

    trade_pnls, sources = crypto_smoke_runner._extract_replay_trade_pnls(
        [
            {
                "candidate_id": "crypto_candidate",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": "", "trade_pnls": [10.0, -2.5, 3.25]},
            }
        ]
    )

    assert trade_pnls == [10.0, -2.5, 3.25]
    assert sources == ["crypto_candidate:result.trade_pnls"]


@dataclass
class _FakeResult:
    net_pnl: float = 1.5
    num_trades: int = 2
    error: str = ""


@dataclass
class _FakeValidationReport:
    candidate_id: str
    model_id: str = "CRYPTO_H1"
    asset_class: str = "CRYPTO"
    execution_classification: str = "L2_PROXY_ONLY"
    validation_path: str = "L2_PROXY_VALIDATION"
    npz_path: str = "data/replay/hftbacktest/crypto/binance/btcusdt/sample.npz"
    result: _FakeResult = field(default_factory=_FakeResult)
    notes: list[str] = field(default_factory=list)


def test_hft_validation_stage_writes_run_local_reports(tmp_path, monkeypatch) -> None:
    from crypto_lane.src.validation import crypto_validation_workflow
    from workbench.src.run import crypto_smoke_runner

    monkeypatch.setattr(
        crypto_smoke_runner,
        "_candidate_model_from_registry",
        lambda candidate: object(),
    )
    monkeypatch.setattr(
        crypto_validation_workflow,
        "validate_crypto_candidate",
        lambda candidate, repo, **kwargs: _FakeValidationReport(candidate_id="crypto_candidate"),
    )
    monkeypatch.setattr(
        crypto_smoke_runner,
        "_crypto_replay_signal_sequence",
        lambda run_dir, candidate_id, repo: [1.0, -1.0],
    )

    reports = crypto_smoke_runner._validate_ranked_candidates(
        tmp_path,
        tmp_path / "run",
        [{"candidate_id": "crypto_candidate"}],
        [{"candidate_id": "crypto_candidate"}],
    )

    report_path = tmp_path / "run" / "validation_reports" / "crypto_candidate.json"
    summary_path = tmp_path / "run" / "hft_validation_summary.json"
    assert report_path.is_file()
    assert summary_path.is_file()
    assert reports[0]["candidate_id"] == "crypto_candidate"
    assert reports[0]["execution_classification"] == "L2_PROXY_ONLY"
    assert reports[0]["replay_signal_source"]["signal_count"] == 2
    assert reports[0]["replay_signal_source"]["max_steps"] == 2000
    assert crypto_smoke_runner._hft_validation_passed(reports[0]) is False
